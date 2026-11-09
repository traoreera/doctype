"""Vérifie le wheel et l'archive source construits (`dist/`) AVANT toute publication sur PyPI.

Un paquet publié ne se republie pas (PyPI refuse une version déjà envoyée, même retirée) : ce qui manque ou ce qui fuit doit être
vu ici. Trois défauts déjà rencontrés dans ce dépôt motivent chaque contrôle :
- le Desk construit (`ui/dist`) est ignoré par git : sans la ligne `include` du pyproject, le wheel en avait 0 fichier (mesuré) ;
- les shims `plugin_dir/src/main.py` tombaient sous le motif `main.py` du .gitignore : `xcore-doctype install` n'avait rien à poser ;
- les plugins de plateforme et les extensions sont GÉNÉRÉS depuis `vendor/` (`make vendor-sync`) : un build sans eux s'installe
  sans erreur et casse au premier `xcore-doctype install`.

Usage : `python3 scripts/check_dist.py [--version 1.2.3] [dist/]`. `--version` (ou un tag `vX.Y.Z`) exige que les deux fichiers
portent cette version. Sortie non nulle à la première faute trouvée, toutes les fautes listées.
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from pathlib import Path

PKG = "xcore_doctype"
# Ce qui DOIT être dans le wheel (chemins relatifs à la racine du wheel ; une regex quand le nom varie).
REQUIRED = [
    f"{PKG}/__init__.py",
    f"{PKG}/plugin.py",
    f"{PKG}/ui/dist/index.html",
    re.compile(rf"^{PKG}/ui/dist/assets/.+\.js$"),
    f"{PKG}/ui/dist/shared/vue.js",
    f"{PKG}/ui/dist/shared/sdk.js",
    f"{PKG}/plugin_dir/plugin.yaml",
    f"{PKG}/plugin_dir/src/main.py",
    f"{PKG}/system_plugin_dir/src/main.py",
    *(f"{PKG}/platform/{name}/plugin.yaml" for name in ("xflow", "xlicense", "xpulse", "xprint")),
    *(re.compile(rf"^{PKG}/ext/{name}/.+\.py$") for name in ("pubsub", "xstorage", "xwebsocket", "xpay")),
]
# Ce qui ne doit JAMAIS partir : secrets et bases de développement, caches, dépendances du Desk, l'hôte de développement.
FORBIDDEN = [
    re.compile(r"(^|/)\.env(\.|$)"),
    re.compile(r"\.pem$"),
    re.compile(r"\.db(-journal|-wal|-shm)?$"),
    re.compile(r"(^|/)__pycache__/"),
    re.compile(r"\.pyc$"),
    re.compile(r"(^|/)node_modules/"),
    re.compile(r"(^|/)\.git/"),
    re.compile(r"(^|/)outbox\.jsonl$"),
    re.compile(r"^(app|conf)/"),
]


def _matches(names: list[str], wanted: str | re.Pattern[str]) -> bool:
    return wanted in names if isinstance(wanted, str) else any(wanted.search(n) for n in names)


def check_wheel(path: Path, version: str | None) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        meta_name = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        metadata = wheel.read(meta_name).decode() if meta_name else ""
    for wanted in REQUIRED:
        if not _matches(names, wanted):
            problems.append(f"{path.name} : il manque {wanted if isinstance(wanted, str) else wanted.pattern}")
    for name in names:
        if any(p.search(name) for p in FORBIDDEN):
            problems.append(f"{path.name} : ne doit pas contenir {name}")
    if not metadata:
        problems.append(f"{path.name} : pas de METADATA")
    else:
        found = re.search(r"^Version: (.+)$", metadata, re.M)
        if version and (not found or found.group(1).strip() != version):
            problems.append(f"{path.name} : version {found.group(1).strip() if found else '?'} au lieu de {version}")
        if not re.search(r"^Description-Content-Type: text/markdown", metadata, re.M):
            problems.append(f"{path.name} : pas de description (README) pour la page PyPI")
    desk = [n for n in names if n.startswith(f"{PKG}/ui/dist/")]
    print(f"{path.name} : {len(names)} fichiers, dont {len(desk)} du Desk")
    return problems


def check_sdist(path: Path, version: str | None) -> list[str]:
    problems: list[str] = []
    with tarfile.open(path) as sdist:
        names = [m.name.split("/", 1)[1] for m in sdist.getmembers() if "/" in m.name]
    for name in names:
        if any(p.search(name) for p in FORBIDDEN):
            problems.append(f"{path.name} : ne doit pas contenir {name}")
    if not any(n.startswith(f"src/{PKG}/ui/dist/") for n in names):
        problems.append(f"{path.name} : le Desk construit est absent (un build depuis l'archive source n'aurait pas d'interface)")
    if version and f"-{version}.tar.gz" not in path.name:
        problems.append(f"{path.name} : le nom ne porte pas la version {version}")
    print(f"{path.name} : {len(names)} fichiers")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dist", nargs="?", default="dist", type=Path)
    parser.add_argument("--version", help="version attendue (un tag vX.Y.Z est accepté tel quel)")
    args = parser.parse_args(argv)
    version = args.version[1:] if args.version and args.version.startswith("v") else args.version
    wheels = sorted(args.dist.glob("xcore_doctype-*.whl"))
    sdists = sorted(args.dist.glob("xcore_doctype-*.tar.gz"))
    problems: list[str] = []
    if len(wheels) != 1 or len(sdists) != 1:
        problems.append(f"{args.dist} doit contenir exactement un wheel et une archive source (trouvé {len(wheels)} et {len(sdists)})")
    for wheel in wheels:
        problems += check_wheel(wheel, version)
    for sdist in sdists:
        problems += check_sdist(sdist, version)
    for p in problems:
        print(f"  ✗ {p}", file=sys.stderr)
    if not problems:
        print("  ✓ prêt à publier")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
