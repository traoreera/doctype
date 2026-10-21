"""Ligne de commande `xcore-doctype` : installer le plugin dans une instance xcore, la vérifier, valider des DocTypes.

xcore ne découvre aujourd'hui que des DOSSIERS de plugins (`plugins.directory`) : un `pip install` seul ne suffit donc pas, il faut
poser le dossier du plugin (manifeste + shim) dans l'instance. C'est ce que fait `install`, en lisant au passage la configuration
de l'instance pour signaler ce qui empêcherait le plugin de répondre (adaptateur SQLite synchrone, `xcore.setup(app)` absent,
tenancy désactivée). Voir doc/phase4.md.

    xcore-doctype install ./app            # dépose ./app/xcore-doctype/ et vérifie ./integration.yaml
    xcore-doctype doctor                   # revérifie une instance déjà installée
    xcore-doctype check tests/support/plugins/sales/doctypes --owner sales --with tests/support/plugins/stock/doctypes   # valide un dossier (CI d'un plugin métier),
                                                                                                                     # ses espaces, ses extensions (extends/) et son extension du Desk (desk/)
    xcore-doctype new-desk-extension ./app/sales   # pose ./app/sales/desk/ : une extension du Desk prête à charger (D28)
    xcore-doctype path                     # dossier du plugin dans le paquet (pour `xcli plugin local link`)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Sequence

import yaml

from . import __version__, plugin_dir, system_plugin_dir
from .desk_extensions import DeskExtensions
from .errors import InvalidDefinition
from .registry import DocTypeRegistry

Level = Literal["ok", "warn", "error"]
PLUGIN_NAME = "xcore-doctype"
SYSTEM_PLUGIN_NAME = "system"
_MARK = {"ok": "✔", "warn": "⚠", "error": "✖"}


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    message: str

    def __str__(self) -> str:
        return f"{_MARK[self.level]} {self.message}"


# ── Lecture de la configuration de l'instance ─────────────────────────────────────────────────────


def find_config(plugins_dir: Path | None, explicit: Path | None) -> Path | None:
    """`--config`, sinon `integration.yaml` à côté du dossier des plugins (la disposition d'`xcli init`), sinon dans le répertoire courant."""
    if explicit is not None:
        return explicit
    candidates = [Path.cwd() / "integration.yaml"]
    if plugins_dir is not None:
        candidates.insert(0, plugins_dir.resolve().parent / "integration.yaml")
    return next((c for c in candidates if c.is_file()), None)


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("la racine doit être un objet")
    return data


def host_tenancy_enabled(config: dict) -> bool:
    return bool((config.get("tenancy") or {}).get("enabled"))


def diagnose(config_path: Path, plugins_dir: Path | None = None) -> list[Finding]:
    """Ce qui, dans l'instance, empêcherait xcore-doctype de fonctionner (CLAUDE.md : « xcore integration facts »)."""
    try:
        config = _load_yaml(config_path)
    except (OSError, ValueError, yaml.YAMLError) as e:
        return [Finding("error", f"{config_path} illisible : {e}")]
    out: list[Finding] = []
    here = config_path.parent

    db = ((config.get("services") or {}).get("databases") or {}).get("default") or {}
    db_type = str(db.get("type", ""))
    if not db_type.endswith("+aio"):
        fixed = f"{db_type}+aio" if db_type else "sqlite+aio"
        out.append(Finding("error", f"services.databases.default.type est « {db_type or 'absent'} » (adaptateur SYNCHRONE) : "
                                    f"xcore-doctype exige un adaptateur asynchrone, mettez « {fixed} »"))
    else:
        out.append(Finding("ok", f"base asynchrone ({db_type})"))

    main = here / "main.py"
    if main.is_file():
        if re.search(r"\.setup\(\s*app\s*\)", main.read_text(encoding="utf-8")):
            out.append(Finding("ok", "main.py appelle xcore.setup(app)"))
        else:
            out.append(Finding("error", f"{main.name} n'appelle pas xcore.setup(app) : sans lui, TenantMiddleware n'est pas installé et "
                                        "toute requête répond 500 `tenant_middleware_missing`. Ajoutez `xcore.setup(app)` après `app = FastAPI(...)`"))
    else:
        out.append(Finding("warn", f"{main} introuvable : je ne peux pas vérifier l'appel à xcore.setup(app)"))

    tenancy = config.get("tenancy") or {}
    if tenancy.get("isolate_db"):
        out.append(Finding("warn", "tenancy.isolate_db est vrai : mettez-le à false, l'isolation d'xcore-doctype est la colonne tenant_id "
                                   "(le search_path d'xcore ne s'applique pas de façon cohérente à ses routes HTTP)"))

    if (config.get("plugins") or {}).get("strict_trusted", True):
        target = (plugins_dir or Path(str((config.get("plugins") or {}).get("directory") or "./app"))) / PLUGIN_NAME
        out.append(Finding("warn", f"plugins.strict_trusted est actif : xcore refusera ce plugin non signé. Signez-le : "
                                   f"xcli plugin security sign {target}  (ou mettez strict_trusted à false en développement)"))

    if plugins_dir is not None:
        configured = (config.get("plugins") or {}).get("directory")
        if configured and (here / str(configured)).resolve() != plugins_dir.resolve():
            out.append(Finding("warn", f"plugins.directory est « {configured} » mais le plugin est installé dans {plugins_dir} : xcore ne le verra pas"))

    # Le manifeste installé doit s'accorder avec la tenancy de l'instance (doc/doctype.md D17).
    app_dir = plugins_dir or here / str((config.get("plugins") or {}).get("directory") or "./app")
    manifest = app_dir / PLUGIN_NAME / "plugin.yaml"
    desk_login: str | None = None
    if manifest.is_file():
        try:
            manifest_data = _load_yaml(manifest)
            binding = (manifest_data.get("doctype") or {}).get("enforce_tenant_binding", True)
            desk = (manifest_data.get("doctype") or {}).get("desk") or {}
            desk_login = desk.get("login_url") if isinstance(desk, dict) else None
        except (OSError, ValueError, yaml.YAMLError) as e:
            out.append(Finding("error", f"{manifest} illisible : {e}"))
        else:
            on = host_tenancy_enabled(config)
            if on and not binding:
                out.append(Finding("warn", "tenancy activée dans xcore mais doctype.enforce_tenant_binding est false : le tenant demandé "
                                           "(en-tête X-Tenant-ID, non authentifié) ne serait plus lié à l'identité. Remettez-le à true"))
            elif not on and binding:
                out.append(Finding("error", "tenancy désactivée dans xcore mais doctype.enforce_tenant_binding est true : le moteur exige un "
                                            "claim de tenant que rien ne fournit. Mettez doctype.enforce_tenant_binding à false dans le manifeste installé"))
            else:
                out.append(Finding("ok", f"tenancy cohérente (xcore : {'activée' if on else 'désactivée'}, enforce_tenant_binding : {str(binding).lower()})"))

    # Le Desk se connecte à un plugin d'authentification : sans lui, l'adresse de connexion répond 404. On ne peut pas deviner le rôle d'un
    # plugin arbitraire ; on signale l'absence du nom conventionnel `auth` (xauth, `app/auth`) quand le Desk attend une connexion.
    if desk_login is not None and not (app_dir / "auth").is_dir():
        out.append(Finding("warn", f"aucun plugin d'authentification (« auth », c'est-à-dire xauth) dans {app_dir} : l'adresse de connexion du "
                                   f"Desk ({desk_login}) répondra 404. Installez/activez `app/auth`, ou mettez doctype.desk.login_url à null (jeton collé)"))
    elif desk_login is not None:
        out.append(Finding("ok", "plugin d'authentification « auth » présent pour la connexion du Desk"))
    return out


# ── install ───────────────────────────────────────────────────────────────────────────────────────


def _packaged_manifest() -> str:
    return (plugin_dir() / "plugin.yaml").read_text(encoding="utf-8")


def _with_binding(manifest: str, bind: bool) -> str:
    line = r"^(\s*enforce_tenant_binding:\s*)(true|false)(.*)$"
    if bind:
        return manifest
    return re.sub(line, r"\1false # tenancy.enabled est false dans xcore (voir `xcore-doctype doctor`)", manifest, count=1, flags=re.M)


def install(plugins_dir: Path, *, config: Path | None = None, force: bool = False, tenancy: str = "auto") -> list[Finding]:
    """Dépose `<plugins_dir>/xcore-doctype/{plugin.yaml, src/main.py}` et vérifie l'instance. Retourne ce qu'il a fait et trouvé."""
    out: list[Finding] = []
    dest = plugins_dir / PLUGIN_NAME
    src = plugin_dir()
    config_path = find_config(plugins_dir, config)

    bind = True
    if tenancy == "off":
        bind = False
    elif tenancy == "auto" and config_path is not None:
        try:
            bind = host_tenancy_enabled(_load_yaml(config_path))
        except (OSError, ValueError, yaml.YAMLError):
            bind = True  # configuration illisible : le défaut sûr, `diagnose` le dira

    (dest / "src").mkdir(parents=True, exist_ok=True)
    # Le shim ne contient aucun état : on le réécrit toujours, il suit la version installée du paquet.
    shutil.copyfile(src / "src" / "main.py", dest / "src" / "main.py")
    out.append(Finding("ok", f"{dest / 'src' / 'main.py'} écrit (shim : il charge xcore_doctype {__version__})"))

    manifest = dest / "plugin.yaml"
    wanted = _with_binding(_packaged_manifest(), bind)
    if not manifest.exists() or force:
        manifest.write_text(wanted, encoding="utf-8")
        out.append(Finding("ok", f"{manifest} {'réécrit' if force else 'créé'} : c'est ICI que se règle le bloc `doctype:` (DocTypes, fuseau, Desk…)"))
    else:
        installed_version = (_load_yaml(manifest).get("version") if manifest.is_file() else None)
        if str(installed_version) != __version__:
            out.append(Finding("warn", f"{manifest} est en version {installed_version}, le paquet est en {__version__} : comparez-les, "
                                       "ou relancez avec --force (écrase vos réglages du bloc `doctype:`)"))
        else:
            out.append(Finding("ok", f"{manifest} existe déjà : vos réglages sont conservés (--force pour repartir du manifeste du paquet)"))

    # Le plugin `system` (réglages globaux) : ses fichiers appartiennent au paquet, pas à l'opérateur : on les réécrit toujours.
    sys_dest = plugins_dir / SYSTEM_PLUGIN_NAME
    sys_src = system_plugin_dir()
    for rel in ("plugin.yaml", "src/main.py", "doctypes/system_settings.yaml"):
        (sys_dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sys_src / rel, sys_dest / rel)
    out.append(Finding("ok", f"{sys_dest} écrit (plugin `system` : Paramètres > Général, nom, logo, couleur et format de date de l'organisation)"))

    if config_path is not None:
        out.extend(diagnose(config_path, plugins_dir))
    else:
        out.append(Finding("warn", "aucun integration.yaml trouvé (--config) : je n'ai pas pu vérifier l'instance"))
    return out


# ── check ─────────────────────────────────────────────────────────────────────────────────────────


def check(directories: Sequence[Path], owner: str | None, needs: Sequence[Path] = ()) -> tuple[list[str], list[Finding]]:
    """Charge des dossiers de DocTypes comme le ferait le plugin ; retourne un résumé et les problèmes.

    `needs` : les dossiers des plugins dont celui-ci dépend (`requires:`). Leurs DocTypes servent de cibles aux champs Link du dossier
    vérifié (le plugin `sales` cite l'article du plugin `stock`), aux extensions (`extends/`) et aux contributions d'espace, sans être
    eux-mêmes contrôlés ni listés.

    Avec `--owner`, les dossiers FRÈRES de celui des DocTypes sont validés comme le plugin les chargerait (doc/extensions.md) :
    `workspaces/` (espace et contributions `contributes_to`), `extends/` (champs et hooks ajoutés aux DocTypes d'un autre plugin, D26)
    et `desk/` (extension JavaScript du Desk, D28 : point d'entrée, taille).
    """
    registry = DocTypeRegistry()
    lines: list[str] = []
    extended: list[str] = []
    desk_version: str | None = None
    try:
        for d in needs:
            registry.load_dir(d)
        provided = set(registry.names())
        for d in directories:
            registry.load_dir(d, owner=owner)
        mine = sorted(set(registry.names()) - provided)
        registry.validate_links(mine)
        # L'espace de travail du plugin (`<plugin>/workspaces/`, frère du dossier des DocTypes) : validé avec eux, contre ces DocTypes.
        spaces = Path(directories[0]).parent / "workspaces" if directories and owner else None
        loaded_spaces = registry.load_workspaces(spaces, owner=owner) if spaces is not None and spaces.is_dir() and owner else []
        home = Path(directories[0]).parent if directories and owner else None
        if home is not None and (home / "extends").is_dir():
            extended = registry.load_extensions(home / "extends", owner=owner)
        if home is not None and (home / "desk").is_dir():
            desk = DeskExtensions(["*"])
            desk.register(owner, home / "desk")
            desk_version = desk.get(owner).version  # type: ignore[union-attr]  # autorisée : `*`
    except InvalidDefinition as e:
        return [], [Finding("error", str(e))]
    hooks: set[str] = set()
    grants_cited: set[str] = set()
    for name in mine:
        d = registry.get(name).definition
        hooks.update(r for r in d.hooks.all_refs() if not r.startswith("action:"))
        grants_cited.update(d.grant_specs())
        if d.istable:
            kind = "enfant (lignes)"
        else:
            if d.source:
                kind = f"source {d.source}"
            elif d.series:
                kind = f"série {d.series.key(datetime.now())}"
            elif d.naming == "owner":
                kind = "nom = utilisateur"
            elif d.naming == "prompt":
                kind = "nom saisi"
            elif not isinstance(d.naming, str):
                kind = f"nom = champ {d.naming.field}"  # NamingField (la série a été traitée plus haut)
            else:
                kind = "nom aléatoire"
        lines.append(f"  {name:<22} module={d.module or '-':<10} {len(d.fields):>2} champs  {kind}")
    ext_lines = []
    for target in extended:
        mine_ext = next(x for x in registry.extensions_of(target) if x["plugin"] == owner)
        hooks.update(r for r in mine_ext["hooks"] if not r.startswith("action:"))
        ext_lines.append(f"  {target:<22} +{len(mine_ext['fields'])} champ(s), {len(mine_ext['hooks'])} hook(s)")
    grants = sorted(grants_cited)
    summary = [f"{len(mine)} DocType(s) valides :", *lines]
    spaces_own = [n for n in loaded_spaces if not n.startswith("+")]
    contributions = [n[1:] for n in loaded_spaces if n.startswith("+")]
    if spaces_own:
        summary.append(f"espace(s) de travail valides : {', '.join(spaces_own)}")
    if contributions:
        summary.append(f"contribution(s) à l'espace d'un autre module : {', '.join(contributions)}")
    if extended:
        summary += [f"extension(s) de DocType valides ({len(extended)}) :", *ext_lines]
    if desk_version is not None:
        summary.append(f"extension du Desk : desk/index.js (version {desk_version}) — l'opérateur doit l'autoriser (doctype.desk.extensions)")
    summary.append(f"grants cités ({len(grants)}) : {', '.join(grants) or 'aucun'}")
    if hooks:
        summary.append(f"hooks de confiance à enregistrer ({len(hooks)}) : {', '.join(sorted(hooks))}")
    return summary, []


# ── new-desk-extension ────────────────────────────────────────────────────────────────────────────

DESK_TEMPLATE = Path(__file__).resolve().parent / "desk_template"


def new_desk_extension(plugin_home: Path, name: str | None = None, *, label: str | None = None, force: bool = False) -> list[Finding]:
    """Pose `<plugin>/desk/` (index.js + README.md) à partir du modèle : une extension du Desk qui se charge telle quelle (doc/extensions.md)."""
    name = name or plugin_home.resolve().name
    if not re.fullmatch(r"[a-z0-9_]+", name):
        return [Finding("error", f"nom de plugin {name!r} : une extension du Desk se nomme comme son plugin, en minuscules, chiffres et `_` (--name)")]
    target = plugin_home / "desk"
    if (target / "index.js").exists() and not force:
        return [Finding("error", f"{target / 'index.js'} existe déjà (--force pour le remplacer)")]
    target.mkdir(parents=True, exist_ok=True)
    label = label or name.replace("_", " ").capitalize()
    for source in sorted(DESK_TEMPLATE.iterdir()):
        if source.is_file():
            text = source.read_text(encoding="utf-8").replace("__PLUGIN__", name).replace("__LABEL__", label.replace("'", "’"))
            (target / source.name).write_text(text, encoding="utf-8")
    return [
        Finding("ok", f"extension du Desk posée dans {target}"),
        Finding("warn", f"à faire : dans le on_load du plugin, self.get_service(\"doctype_registry\").load_desk(self.ctx.name, <dossier>/desk)"),
        Finding("warn", f"à faire : l'opérateur l'autorise dans le manifeste d'xcore-doctype : doctype.desk.extensions: [{name}]"),
    ]


# ── Ligne de commande ─────────────────────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="xcore-doctype", description="Installer et vérifier xcore-doctype dans une instance xcore.")
    ap.add_argument("--version", action="version", version=f"xcore-doctype {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install", help="déposer le plugin dans le dossier des plugins d'une instance xcore et la vérifier")
    p.add_argument("plugins_dir", type=Path, help="plugins.directory de l'instance (ex. ./app)")
    p.add_argument("--config", type=Path, help="integration.yaml de l'instance (défaut : à côté du dossier des plugins)")
    p.add_argument("--force", action="store_true", help="réécrire plugin.yaml (écrase les réglages du bloc `doctype:`)")
    p.add_argument("--tenancy", choices=("auto", "on", "off"), default="auto",
                   help="lier le tenant à l'identité : auto lit la tenancy d'xcore dans integration.yaml (défaut), off la désactive")

    p = sub.add_parser("doctor", help="vérifier une instance où le plugin est déjà installé")
    p.add_argument("--config", type=Path, default=None, help="integration.yaml (défaut : ./integration.yaml)")
    p.add_argument("--plugins-dir", type=Path, default=None)

    p = sub.add_parser("check", help="valider des dossiers de DocTypes, et avec --owner les espaces, extensions et extension du Desk du plugin (CI d'un plugin métier)")
    p.add_argument("directories", type=Path, nargs="+")
    p.add_argument("--owner", help="nom du plugin propriétaire : impose `module: <ce nom>`, comme le fait le service doctype_registry")
    p.add_argument("--with", dest="needs", type=Path, action="append", default=[], metavar="DIR",
                   help="dossier de DocTypes d'un plugin dont celui-ci dépend (cibles des champs Link), répétable ; non contrôlé")

    p = sub.add_parser("new-desk-extension", help="poser <plugin>/desk/ : une extension du Desk prête à charger (doc/extensions.md)")
    p.add_argument("plugin_home", type=Path, help="dossier du plugin (celui de son plugin.yaml)")
    p.add_argument("--name", help="nom du plugin (défaut : le nom du dossier)")
    p.add_argument("--label", help="libellé de sa page et de son lien (défaut : déduit du nom)")
    p.add_argument("--force", action="store_true", help="remplacer un desk/index.js existant")

    sub.add_parser("path", help="afficher le dossier du plugin dans le paquet installé")

    args = ap.parse_args(argv)
    try:
        if args.command == "path":
            print(plugin_dir())
            return 0
        if args.command == "install":
            findings = install(args.plugins_dir, config=args.config, force=args.force, tenancy=args.tenancy)
            for f in findings:
                print(f)
            errors = [f for f in findings if f.level == "error"]
            print(f"\n{PLUGIN_NAME} installé dans {args.plugins_dir / PLUGIN_NAME}." + (f" {len(errors)} point(s) à corriger avant de lancer l'instance." if errors else ""))
            return 0
        if args.command == "doctor":
            config = find_config(args.plugins_dir, args.config)
            if config is None:
                print("✖ integration.yaml introuvable (--config)", file=sys.stderr)
                return 2
            findings = diagnose(config, args.plugins_dir)
            for f in findings:
                print(f)
            return 1 if any(f.level == "error" for f in findings) else 0
        if args.command == "new-desk-extension":
            findings = new_desk_extension(args.plugin_home, args.name, label=args.label, force=args.force)
            for f in findings:
                print(f, file=sys.stderr if f.level == "error" else sys.stdout)
            return 1 if any(f.level == "error" for f in findings) else 0
        if args.command == "check":
            summary, problems = check(args.directories, args.owner, args.needs)
            for line in summary:
                print(line)
            for f in problems:
                print(f, file=sys.stderr)
            return 1 if problems else 0
    except OSError as e:
        print(f"✖ {e}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
