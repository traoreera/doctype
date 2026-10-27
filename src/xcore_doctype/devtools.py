"""Mode développeur : le constructeur de DocTypes du Desk (process/programme.md §3.5, « le Desk : le mode développeur »).

Créer un type entier, ou en modifier un, À L'ÉCRAN — et en sortir le YAML à poser dans le dossier `doctypes/` d'un plugin. Rien n'est
jamais enregistré ici : une définition vit dans un plugin, chargée au démarrage, avec ses grants déclarés à auth (§3.5 : le registre
est global au processus, un type écrit par une organisation existerait pour toutes ; et le moteur n'écrit jamais sur le disque). Le
serveur ne fait que deux choses, et seulement quand l'opérateur l'a voulu (`doctype.desk.developer_mode: true`, fermé par défaut) :

- `definition` : la définition D'ORIGINE d'un type chargé (son YAML, sans extensions D26 ni champs d'organisation), point de départ
  d'une modification ;
- `check` : juge une définition comme au CHARGEMENT — schéma (`parse_definition`), compilation, puis cibles des liens, des tables, des
  copies et des filtres dans un registre d'essai qui contient tous les autres types — et rend son YAML et les grants qu'elle cite
  (ceux que son plugin devra déclarer). Un refus dit, ligne par ligne, ce qui ne va pas.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from .errors import InvalidDefinition, NotFound, ValidationFailed
from .registry import DocTypeRegistry
from .schema import DocTypeDef, parse_definition

if TYPE_CHECKING:
    from .context import RequestContext
    from .document import DocumentService

SOURCE = "<constructeur>"
_LINE = re.compile(r"^\s*-\s*(?P<where>[^:]+):\s*(?P<msg>.+)$")


def _off() -> NotFound:
    # Comme une route qui n'existe pas : un Desk de production ne doit même pas apprendre que l'outil est là.
    return NotFound("developer_mode_off", "mode développeur désactivé")


def as_yaml(defn: DocTypeDef) -> str:
    """Le YAML qu'un humain aurait écrit : sans les valeurs par défaut, dans l'ordre du schéma, accents tels quels."""
    return yaml.safe_dump(dump(defn), sort_keys=False, allow_unicode=True, width=140)


def dump(defn: DocTypeDef) -> dict[str, Any]:
    return defn.model_dump(mode="json", by_alias=True, exclude_defaults=True)


def _refusal(e: InvalidDefinition) -> ValidationFailed:
    """L'erreur de chargement, rendue au constructeur : une entrée par ligne (`fields.0.fieldname : …`) quand on sait la découper."""
    text = str(e).replace(f"{SOURCE}: ", "")
    details = []
    for line in text.splitlines()[1:] or [text]:
        found = _LINE.match(line)
        details.append({"field": found["where"].strip(), "msg": found["msg"].strip()} if found else {"field": "", "msg": line.strip()})
    head = text.splitlines()[0] if text else "définition invalide"
    return ValidationFailed(head, code="invalid_definition", details=[d for d in details if d["msg"]])


def definition(documents: "DocumentService", ctx: "RequestContext", doctype: str, *, enabled: bool) -> dict[str, Any]:
    """La définition d'origine d'un type, pour la modifier. Il faut pouvoir LIRE le type (comme `/meta`)."""
    if not enabled:
        raise _off()
    documents._enter(ctx, "devtools.definition", doctype)
    compiled = documents._doctype(doctype)
    documents._scope(ctx, compiled, "read")
    origin = documents._registry.origin(doctype)
    return {"doctype": doctype, "owner": documents._registry.owner_of(doctype), "definition": dump(origin), "yaml": as_yaml(origin)}


def check(documents: "DocumentService", ctx: "RequestContext", raw: Any, *, enabled: bool) -> dict[str, Any]:
    """Juge une définition comme au chargement d'un plugin, sans rien enregistrer. Rend `{doctype, yaml, grants}` ou 422 `invalid_definition`."""
    if not enabled:
        raise _off()
    documents._enter(ctx, "devtools.check", str((raw or {}).get("name", "")) if isinstance(raw, dict) else "")
    try:
        defn = parse_definition(raw, source=SOURCE)
        scratch = DocTypeRegistry()
        # Tous les AUTRES types chargés, enfants d'abord (un parent exige ses tables déjà là) : la nouvelle définition est jugée contre
        # le système réel — ses liens, ses tables, ses `fetch_from` visent des types qui existent. Celle qu'on remplace n'y est pas.
        others = [(d, src) for d, src in documents._registry.origins() if d.name != defn.name]
        for d, src in sorted(others, key=lambda item: not item[0].istable):
            scratch.register(d, source=src)
        scratch.register(defn, source=SOURCE)
        scratch.validate_links([defn.name])
    except InvalidDefinition as e:
        raise _refusal(e) from None
    return {"doctype": defn.name, "yaml": as_yaml(defn), "grants": sorted(defn.grant_specs())}
