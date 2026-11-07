"""Les nœuds et les événements qu'un plugin ouvre à l'éditeur d'automatisations (la section `xflow:` de son `plugin.yaml`).

xflow exécute une étape « action » comme un appel IPC quelconque (`{plugin, action, payload}`), se déclenche sur n'importe quel
événement du bus, et résout `{{ steps.<étape>.result.<chemin> }}` sur le résultat de n'importe quelle étape. Ce qui manquait, c'est que
l'ÉCRAN sache quoi proposer : l'éditeur ne connaissait que les gestes du moteur (`doc.save`, `doc.submit`…). Un plugin décrit donc,
dans son manifeste, ce qu'il offre :

    xflow:
      nodes:                         # ses actions IPC utilisables comme étape, chacune devient un nœud de la palette
        - action: mail.send          # le nom de SON action (le plugin est le déclarant : on n'ouvre jamais l'action d'un autre)
          label: Envoyer un e-mail
          icon: envelope             # nom PrimeIcons nu
          fields:                    # la charge utile, champ par champ : l'écran en fait un formulaire
            - {name: to, label: Destinataire, type: email, required: true}
            - {name: body, label: Message, type: longtext}
          outputs:                   # ce que son RÉSULTAT contient, qu'une étape suivante peut reprendre
            - {path: message_id, label: Identifiant du message}
      events:                        # les événements qu'il émet sur le bus, utilisables comme déclencheur
        - name: mailer.received      # préfixé de son nom ; la charge DOIT porter `tenant_id` (sinon xflow lance le flux de toutes les
          label: E-mail reçu         # organisations qui l'écoutent)
          fields:                    # les clés de la charge, que les étapes reprennent comme `trigger.<clé>`
            - {name: sender, label: Expéditeur}

et la passe au moteur dans son `on_load` (le moteur ne lit pas les manifestes des autres plugins) :

    self.get_service("doctype_registry").declare_xflow(self.ctx.name, self.ctx.config.get("xflow"))

Ce n'est QUE de la description pour l'écran : rien n'est exécuté ici, aucun droit n'est accordé. L'appel part d'xflow au nom de
l'utilisateur du flux, l'action visée garde ses propres contrôles, et le plugin doit nommer `xflow` dans ses `allowed_callers` (sinon
xcore répond `ipc_denied` à l'exécution). Une section fautive est une erreur de CHARGEMENT du plugin, comme un DocType fautif.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from .errors import InvalidDefinition

FIELD_TYPES = ("text", "longtext", "email", "number", "boolean", "select", "date")
_NAME = r"^[a-z][a-z0-9_]{0,59}$"
_ACTION = r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,99}$"
_PATH = r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,7}$"
_OWNER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_NODES = 50
MAX_EVENTS = 50
MAX_FIELDS = 30

Label = Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
Description = Annotated[str, StringConstraints(max_length=300, strip_whitespace=True)]


class NodeField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(pattern=_NAME)]
    label: Label
    type: Literal["text", "longtext", "email", "number", "boolean", "select", "date"] = "text"
    required: bool = False
    options: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=50)
    description: Description | None = None

    @model_validator(mode="after")
    def _options_only_for_select(self) -> "NodeField":
        if self.type == "select" and not self.options:
            raise ValueError(f"le champ {self.name} est une liste (select) sans options")
        if self.type != "select" and self.options:
            raise ValueError(f"le champ {self.name} a des options sans être une liste (select)")
        return self


class NodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Annotated[str, StringConstraints(pattern=_PATH)]
    label: Label


class FlowNodeDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Annotated[str, StringConstraints(pattern=_ACTION)]
    label: Label
    description: Description | None = None
    icon: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")] = "bolt"
    fields: list[NodeField] = Field(default_factory=list, max_length=MAX_FIELDS)
    outputs: list[NodeOutput] = Field(default_factory=list, max_length=MAX_FIELDS)

    @model_validator(mode="after")
    def _distinct(self) -> "FlowNodeDef":
        for kind, names in (("champ", [f.name for f in self.fields]), ("sortie", [o.path for o in self.outputs])):
            twice = sorted({n for n in names if names.count(n) > 1})
            if twice:
                raise ValueError(f"{self.action} : {kind} déclaré deux fois ({', '.join(twice)})")
        return self


class EventField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(pattern=_NAME)]
    label: Label


class FlowEventDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,99}$")]
    label: Label
    description: Description | None = None
    fields: list[EventField] = Field(default_factory=list, max_length=MAX_FIELDS)


class XflowSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: list[FlowNodeDef] = Field(default_factory=list, max_length=MAX_NODES)
    events: list[FlowEventDef] = Field(default_factory=list, max_length=MAX_EVENTS)


def parse_section(owner: str, raw: Any) -> XflowSection:
    """La section d'un plugin, vérifiée. Les fautes (une par ligne) font échouer le chargement du plugin, comme un DocType fautif."""
    if not isinstance(owner, str) or not _OWNER.match(owner):
        raise InvalidDefinition(f"xflow : nom de plugin invalide ({owner!r})")
    if raw is None:
        return XflowSection()
    try:
        section = XflowSection.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc']) or 'xflow'} : {err['msg']}" for err in e.errors()]
        raise InvalidDefinition(f"section xflow du plugin {owner} :\n" + "\n".join(lines)) from None
    problems: list[str] = []
    actions = [n.action for n in section.nodes]
    problems += [f"l'action {a} est déclarée deux fois" for a in sorted({a for a in actions if actions.count(a) > 1})]
    for event in section.events:
        # Le nom d'un événement est préfixé de celui du plugin : on ne prête pas à un plugin les événements d'un autre (ni ceux du moteur,
        # `doctype.<Type>.…`, que l'éditeur propose déjà).
        if not event.name.startswith(f"{owner}."):
            problems.append(f"l'événement {event.name} doit commencer par « {owner}. »")
    names = [e.name for e in section.events]
    problems += [f"l'événement {n} est déclaré deux fois" for n in sorted({n for n in names if names.count(n) > 1})]
    if problems:
        raise InvalidDefinition(f"section xflow du plugin {owner} :\n" + "\n".join(problems))
    return section


class FlowCatalog:
    """Ce que les plugins chargés ont déclaré, par plugin. Servi tel quel à l'éditeur (`GET /flow-catalog`)."""

    def __init__(self) -> None:
        self._sections: dict[str, XflowSection] = {}

    def declare(self, owner: str, raw: Any) -> XflowSection:
        section = parse_section(owner, raw)
        if section.nodes or section.events:
            self._sections[owner] = section
        else:
            self._sections.pop(owner, None)
        return section

    def unregister(self, owner: str) -> None:
        self._sections.pop(owner, None)

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        nodes: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for owner in sorted(self._sections):
            section = self._sections[owner]
            nodes += [{"plugin": owner, **n.model_dump(mode="json", exclude_none=True)} for n in section.nodes]
            events += [{"plugin": owner, **e.model_dump(mode="json", exclude_none=True)} for e in section.events]
        return {"nodes": nodes, "events": events}
