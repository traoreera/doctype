"""Règles de notification par DocType (phase C4 de `.plan/plan.md`) : « quand une facture est soumise, prévenir le comptable ».

Une règle est un document `Notification Rule` du plugin `system` (embarqué avec xcore-doctype), écrit par l'organisation. Le moteur les évalue
APRÈS le commit de chaque création, modification, soumission, annulation ou suppression : une règle n'annule ni ne retarde jamais une écriture.
L'évaluation part en tâche de fond (`dispatch`), suivie par l'instance (`settled()` pour les tests, `close()` au déchargement), et tout échec
(règle devenue invalide, annuaire absent, xauth arrêté, serveur SMTP en panne) est JOURNALISÉ, jamais levé : la politique de `notifier.py`.

Ce qui part, et vers qui :
- la cloche (`Notifier`, l'action `xauth.notify`) et/ou un e-mail (`Mailer`, l'extension `ext.email` de l'hôte ; absente, pas d'e-mail) ;
- seulement à des MEMBRES de l'organisation (l'annuaire, `directory.py`) : l'auteur du document, les personnes assignées (« À faire » ouverts),
  un champ du document qui contient l'e-mail ou l'identifiant d'un membre, des membres nommés par la règle. Jamais à qui a fait le changement ;
- un objet et un message en TEXTE BRUT, rendus par une substitution fermée (`{{ name }}`, `{{ doc.champ }}`… : aucun code, aucune expression).
  Un modèle ne cite que des champs « lisibles » (ni secret, ni table, ni fichier, ni HTML) et la règle n'est acceptée que si son auteur peut lire
  TOUS les documents du type (`validate_rule`) : on ne diffuse pas ce qu'on ne lit pas soi-même.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote

from .context import RequestContext
from .directory import Directory, NoDirectory, Person
from .errors import DocumentValidationError, DoctypeError
from .expr import ExprError, compile_expr, evaluate, referenced_fields
from .notifier import MAX_RECIPIENTS, NoNotifier, Notifier
from .permissions import scope_for
from .schema import DECIMAL_TYPES, FILE_TYPES

if TYPE_CHECKING:
    from .document import DocumentService
    from .hooks import HookEvent

RULE_DOCTYPE = "Notification Rule"
RULE_HOOK = "doctype.notification_rule"
MAX_RULES = 100  # par type et par tenant : au-delà, les suivantes sont ignorées (et on le journalise)
MAX_TENANT_RULES = 1000  # toutes les règles actives d'une organisation, lues d'un coup (au-delà : ignorées, journalisé)
RULES_TTL = 30.0  # secondes : les règles d'une organisation sont relues au plus une fois par période (et aussitôt après qu'une règle change ici)
MAX_VALUE = 200  # caractères d'une valeur de champ dans un modèle
MAX_SUBJECT = 255  # le titre d'une notification d'xauth
MAX_MESSAGE = 2000
EMAIL_TIMEOUT = 15.0

# Ce que l'utilisateur choisit (en français dans le formulaire) -> l'événement du moteur.
EVENTS = {
    "Création": "create",
    "Modification": "update",
    "Changement de valeur": "value_change",
    "Soumission": "submit",
    "Annulation": "cancel",
    "Suppression": "delete",
}
EVENT_WORDS = {"create": "créé", "update": "modifié", "submit": "soumis", "cancel": "annulé", "delete": "supprimé"}
CHANNELS = {"Cloche": (True, False), "E-mail": (False, True), "Cloche et e-mail": (True, True)}
# Un modèle ne cite pas ce qui n'a pas de sens en texte brut, ni ce qui ne doit pas sortir : un secret, une table, un fichier, du HTML.
UNQUOTABLE_TYPES = ("Password", "Table", "JSON", "TextEditor", *FILE_TYPES)
# Un champ qui désigne un destinataire contient un texte : une adresse ou un identifiant.
RECIPIENT_FIELD_TYPES = ("Text", "Link", "Select")
SPECIALS = ("doctype", "name", "event", "actor", "link")
# Linéaire : aucun quantificateur qui se chevauche (un `\s*` de part et d'autre d'un groupe qui accepte aussi les blancs revenait en arrière en
# O(n³) sur « {{ » suivi de blancs : une seule règle figeait la boucle d'événements de tout le processus). Le nom se nettoie ensuite (`strip`).
_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")
_DOC_FIELD = re.compile(r"doc\.([a-z][a-z0-9_]*)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


# ── E-mail ──────────────────────────────────────────────────────────────────────────────────────


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> bool:
        """Envoie un e-mail en texte brut. Ne lève jamais : `False` si rien n'est parti."""


class NoMailer:
    """Pas d'extension `ext.email` (ou `doctype.notifications.email: false`) : aucun e-mail."""

    async def send(self, to: str, subject: str, body: str) -> bool:
        return False


class ExtensionMailer:
    """L'extension `ext.email` de l'hôte (`extensions/xmailler` : SMTP, ou mode SIMULÉ sans serveur configuré)."""

    def __init__(self, service: Any, *, logger: Any = None, timeout: float = EMAIL_TIMEOUT) -> None:
        self._svc, self._log, self._timeout = service, logger, timeout

    async def send(self, to: str, subject: str, body: str) -> bool:
        try:
            sent = await asyncio.wait_for(self._svc.send(to=to, subject=subject, body=body, is_html=False), timeout=self._timeout)
        except Exception as e:  # noqa: BLE001 - y compris TimeoutError : un e-mail ne défait rien
            _warn(self._log, "notification e-mail not sent", error=repr(e))
            return False
        if not sent:
            _warn(self._log, "notification e-mail refused by the mail extension")
        return bool(sent)


# ── Ce que le moteur signale ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DocEvent:
    """Un changement COMMITÉ. `data` : le document (secrets masqués) ; pour une suppression, ce qu'il contenait. `old` : avant une modification."""

    tenant_id: str
    doctype: str
    name: str
    kind: str  # create | update | submit | cancel | delete
    data: Mapping[str, Any]
    old: Mapping[str, Any] | None
    owner: str | None
    actor: str | None
    # Les assignés OUVERTS au moment de l'écriture, quand la base ne les aura plus après le commit (une suppression les retire avec le
    # document) ; `None` : on les lit après le commit.
    assignees: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Delivery:
    """Ce qu'une règle envoie (rendu, destinataires résolus) : ce que les tests regardent."""

    rule: str
    user_ids: tuple[str, ...]
    emails: tuple[str, ...]
    subject: str
    message: str


# ── Vérification d'une règle (hook `validate` de `Notification Rule`) ───────────────────────────


def rule_problems(registry: Any, data: Mapping[str, Any]) -> list[dict[str, str]]:
    """Les défauts d'une règle, champ par champ (vide = la règle est bonne). Pure : sert au hook et, à l'évaluation, à écarter une règle
    que la définition de son type a rendue invalide depuis (un champ retiré)."""
    errors: list[dict[str, str]] = []

    def bad(field: str, message: str) -> None:
        errors.append({"field": field, "msg": message, "type": "invalid_rule"})

    target = data.get("document_type")
    if not isinstance(target, str) or not registry.has(target):
        bad("document_type", "Type de document inconnu")
        return errors
    definition = registry.get(target).definition
    if target == RULE_DOCTYPE or definition.istable or definition.source is not None:
        bad("document_type", "Ce type ne déclenche pas de notification (table enfant, type virtuel ou règle de notification)")
        return errors
    fields = {f.fieldname: f for f in definition.fields}
    event = EVENTS.get(data.get("event"))  # type: ignore[arg-type]
    if event is None:
        bad("event", "Événement inconnu")
    elif event in ("submit", "cancel") and not definition.submittable:
        bad("event", f"{target} ne se soumet pas : choisissez un autre événement")
    if event == "value_change":
        watched = data.get("value_field")
        if not isinstance(watched, str) or watched not in fields:
            bad("value_field", "Champ inconnu dans ce type")
        elif fields[watched].fieldtype in ("Password", "Table"):
            bad("value_field", "Ce champ ne se surveille pas")
    condition = data.get("condition")
    if isinstance(condition, str) and condition.strip():
        try:
            used = referenced_fields(compile_expr(condition, fields))
        except ExprError as e:
            bad("condition", str(e))
        else:
            opaque = sorted(n for n in used if fields[n].fieldtype in ("Table", "Password", "JSON", *DECIMAL_TYPES))
            if opaque:
                bad("condition", f"{', '.join(opaque)} ne peut pas servir dans une condition (table, secret, JSON ou décimal)")
    recipient = data.get("recipient_field")
    if isinstance(recipient, str) and recipient:
        if recipient not in fields or fields[recipient].fieldtype not in RECIPIENT_FIELD_TYPES:
            bad("recipient_field", "Il faut un champ texte ou lien de ce type (qui contient l'e-mail ou l'identifiant d'un membre)")
    if not (data.get("notify_owner") or data.get("notify_assignees") or recipient or data.get("recipients")):
        bad("recipients", "Personne n'est prévenu : cochez l'auteur ou les personnes assignées, nommez un champ ou des membres")
    for key, limit in (("subject", MAX_SUBJECT), ("message", MAX_MESSAGE)):
        text = data.get(key)
        if isinstance(text, str) and len(text) > limit:
            bad(key, f"{limit} caractères au plus")  # avant toute expression régulière : un modèle se juge sur un texte borné
        elif isinstance(text, str):
            for problem in template_problems(text, fields):
                bad(key, problem)
    return errors


def template_problems(text: str, fields: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    for m in _PLACEHOLDER.finditer(text):
        token = m.group(1).strip()
        if token in SPECIALS:
            continue
        cited = _DOC_FIELD.fullmatch(token)
        if cited is None:
            problems.append(f"{{{{ {token} }}}} : variable inconnue ({', '.join(SPECIALS)} ou doc.<champ>)")
        elif cited.group(1) not in fields:
            problems.append(f"{{{{ {token} }}}} : champ inconnu dans ce type")
        elif fields[cited.group(1)].fieldtype in UNQUOTABLE_TYPES:
            problems.append(f"{{{{ {token} }}}} : un champ {fields[cited.group(1)].fieldtype} ne se cite pas dans un message")
    return problems


def render(text: str, values: Mapping[str, str], data: Mapping[str, Any]) -> str:
    """Substitution FERMÉE : `{{ name }}` & co, `{{ doc.champ }}` ; tout le reste reste tel quel. Aucune évaluation."""

    def one(m: re.Match[str]) -> str:
        token = m.group(1).strip()
        if token in values:
            return values[token]
        cited = _DOC_FIELD.fullmatch(token)
        return _text(data.get(cited.group(1))) if cited else m.group(0)

    return _PLACEHOLDER.sub(one, text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if isinstance(value, (list, dict)):
        return ""
    return _CONTROL.sub(" ", str(value))[:MAX_VALUE]


def make_rule_hook(documents: "DocumentService") -> Callable[["HookEvent"], Awaitable[None]]:
    """Le hook `validate` de `Notification Rule` : la règle est cohérente, et son auteur lit TOUS les documents du type qu'elle diffuse."""

    async def check(event: "HookEvent") -> None:
        registry = documents._registry
        problems = rule_problems(registry, event.data)
        if not problems and not event.ctx.is_system:
            target = registry.get(event.data["document_type"]).definition
            if scope_for(target, event.ctx, "read").level != "all":
                problems.append({
                    "field": "document_type", "type": "invalid_rule",
                    "msg": "Il faut pouvoir lire TOUS les documents de ce type pour en diffuser le contenu",
                })
        if problems:
            raise DocumentValidationError("La règle de notification est incomplète ou incohérente", code="invalid_rule", details=problems)

    return check


# ── Évaluation ──────────────────────────────────────────────────────────────────────────────────


class NotificationRules:
    """L'évaluateur : posé sur `DocumentService.notifications` par le plugin. Sans le plugin `system` (pas de DocType de règle), il ne fait rien."""

    def __init__(
        self,
        documents: "DocumentService",
        *,
        directory: Directory | None = None,
        notifier: Notifier | None = None,
        mailer: Mailer | None = None,
        desk_url: str | None = None,
        logger: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._docs = documents
        self._directory: Directory = directory or NoDirectory()
        self._notifier: Notifier = notifier or NoNotifier()
        self._mailer: Mailer = mailer or NoMailer()
        self._desk_url = desk_url.rstrip("/") if desk_url else None
        self._log, self._clock = logger, clock
        self._pending: set[asyncio.Task[None]] = set()
        self._closed = False
        # tenant -> (échéance, type -> règles actives) : une organisation sans règle ne coûte qu'une lecture par période, pas une par écriture.
        self._cache: dict[str, tuple[float, dict[str, list[dict[str, Any]]]]] = {}
        self._generation: dict[str, int] = {}  # +1 à chaque règle changée : une lecture partie avant ne remplace pas le cache vidé après

    def dispatch(self, event: DocEvent) -> None:
        """Appelé après le commit. Ne bloque pas la requête : l'évaluation part en tâche de fond."""
        self.dispatch_many([event])

    def dispatch_many(self, events: Sequence[DocEvent]) -> None:
        """Plusieurs changements d'un même commit (ce que les hooks ont écrit dans la transaction) : UNE tâche de fond, dans l'ordre."""
        if self._closed or not self._docs._registry.has(RULE_DOCTYPE):
            return
        for e in events:
            if e.doctype == RULE_DOCTYPE:
                self._cache.pop(e.tenant_id, None)  # une règle a changé ici : relue à la prochaine écriture
                self._generation[e.tenant_id] = self._generation.get(e.tenant_id, 0) + 1
        wanted = [e for e in events if e.doctype != RULE_DOCTYPE]
        if not wanted:
            return
        task = asyncio.get_running_loop().create_task(self._guarded(wanted))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def settled(self) -> None:
        """Attend les évaluations en cours (tests, arrêt propre)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def close(self) -> None:
        """Au déchargement : plus rien ne part, ce qui était en cours est abandonné."""
        self._closed = True
        for task in list(self._pending):
            task.cancel()
        await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def _guarded(self, events: Sequence[DocEvent]) -> None:
        for event in events:
            try:
                await self.evaluate(event)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - une notification ne défait rien
                _warn(self._log, "notification rules failed", doctype=event.doctype, name=event.name, error=repr(e))

    async def evaluate(self, event: DocEvent) -> list[Delivery]:
        """Les règles du type qui s'appliquent à cet événement, rendues et envoyées. Rend ce qui est parti (vide si rien)."""
        rules = await self._rules(event)
        if not rules:
            return []
        members = await self._directory.members(event.tenant_id)
        assignees: list[str] | None = None
        sent: list[Delivery] = []
        for rule in rules:
            try:  # une règle qui échoue (assignés illisibles, cloche en panne) n'empêche pas les suivantes
                if not self._applies(rule, event):
                    continue
                if rule.get("notify_assignees") and assignees is None:
                    assignees = await self._assignees(event)
                delivery = self._prepare(rule, event, members, assignees or [])
                if delivery is None:
                    continue
                bell, mail = CHANNELS.get(rule.get("channel"), (True, False))  # type: ignore[arg-type]
                if bell and delivery.user_ids:
                    await self._notifier.notify(
                        event.tenant_id, delivery.user_ids, title=delivery.subject, message=delivery.message or None,
                        link=None if event.kind == "delete" else _link(event.doctype, event.name), kind="RULE",
                    )
                if mail:
                    for address in delivery.emails:
                        await self._mailer.send(address, delivery.subject, self._mail_body(delivery, event))
                sent.append(delivery)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - une notification ne défait rien
                _warn(self._log, "notification rule failed", rule=rule.get("_name"), doctype=event.doctype, name=event.name, error=repr(e))
        return sent

    async def _rules(self, event: DocEvent) -> list[dict[str, Any]]:
        now = self._clock()
        hit = self._cache.get(event.tenant_id)
        if hit is None or hit[0] <= now:
            generation = self._generation.get(event.tenant_id, 0)
            by_type = await self._load(event.tenant_id)
            if by_type is None:
                return []
            hit = (now + RULES_TTL, by_type)
            if self._generation.get(event.tenant_id, 0) == generation:
                self._cache[event.tenant_id] = hit
        rules = hit[1].get(event.doctype, [])
        if len(rules) > MAX_RULES:
            _warn(self._log, "too many notification rules, the oldest ones only are evaluated", doctype=event.doctype, limit=MAX_RULES)
        return rules[:MAX_RULES]

    async def _load(self, tenant_id: str) -> dict[str, list[dict[str, Any]]] | None:
        """Toutes les règles ACTIVES de l'organisation, par type, les plus anciennes d'abord. Une seule lecture en contexte système (D20,
        journalisée) par période, quel que soit le nombre d'écritures. `None` : illisibles (on réessaiera à la prochaine écriture)."""
        ctx = RequestContext.system(tenant_id, "notification rules")
        by_type: dict[str, list[dict[str, Any]]] = {}
        offset = 0
        try:
            while offset < MAX_TENANT_RULES:
                page = await self._docs.list(ctx, RULE_DOCTYPE, filters={"enabled": True}, order_by="creation", descending=False, limit=200, offset=offset)
                items = page.get("items", [])
                for item in items:
                    target = item["data"].get("document_type")
                    if isinstance(target, str):
                        by_type.setdefault(target, []).append(dict(item["data"], _name=item["name"]))
                offset += len(items)
                if not items or offset >= page.get("total", 0):
                    break
            else:
                _warn(self._log, "too many notification rules in one organisation, the oldest ones only are evaluated", limit=MAX_TENANT_RULES)
        except DoctypeError as e:
            _warn(self._log, "notification rules unreadable", code=e.code)
            return None
        return by_type

    def _applies(self, rule: Mapping[str, Any], event: DocEvent) -> bool:
        if rule_problems(self._docs._registry, rule):
            # La définition du type a changé depuis (un champ retiré) : la règle n'est plus cohérente, on ne devine pas.
            _warn(self._log, "notification rule skipped: no longer valid", rule=rule.get("_name"), doctype=event.doctype)
            return False
        wanted = EVENTS[rule["event"]]
        if wanted == "value_change":
            field = rule["value_field"]
            if event.kind != "update" or event.old is None or event.old.get(field) == event.data.get(field):
                return False
        elif wanted != event.kind:
            return False
        condition = rule.get("condition")
        if isinstance(condition, str) and condition.strip():
            fields = [f.fieldname for f in self._docs._registry.get(event.doctype).definition.fields]
            return evaluate(compile_expr(condition, fields), event.data)
        return True

    def _prepare(self, rule: Mapping[str, Any], event: DocEvent, members: list[Person] | None, assignees: Sequence[str]) -> Delivery | None:
        by_id = {p.id: p for p in members or []}
        by_email = {p.email.lower(): p for p in members or [] if p.email}
        wanted: list[str] = []
        if rule.get("notify_owner") and event.owner:
            wanted.append(event.owner)
        if rule.get("notify_assignees"):
            wanted.extend(assignees)
        field = rule.get("recipient_field")
        if isinstance(field, str) and field:
            value = event.data.get(field)
            if isinstance(value, str) and value:
                found = by_id.get(value) or by_email.get(value.lower())
                if found is not None:
                    wanted.append(found.id)
                elif members is None:
                    wanted.append(value)  # sans annuaire, un identifiant passe tel quel à la cloche (xauth ne prévient que ses membres)
        for row in rule.get("recipients") or []:
            email = row.get("email") if isinstance(row, Mapping) else None
            found = by_email.get(email.lower()) if isinstance(email, str) else None
            if found is not None:
                wanted.append(found.id)
        ids = [u for u in dict.fromkeys(wanted) if u and u != event.actor and u != "system"]
        if members is not None:
            ids = [u for u in ids if u in by_id]  # l'annuaire répond : seulement ses membres
        ids = ids[:MAX_RECIPIENTS]
        if not ids:
            return None
        emails = tuple(by_id[u].email for u in ids if u in by_id and by_id[u].email)  # type: ignore[misc]
        values = self._values(event, members)
        subject = _CONTROL.sub(" ", render(str(rule.get("subject") or ""), values, event.data)).strip()[:MAX_SUBJECT]
        message = render(str(rule.get("message") or ""), values, event.data).strip()[:MAX_MESSAGE]
        return Delivery(str(rule.get("_name")), tuple(ids), emails, subject or values["doctype"], message)

    def _values(self, event: DocEvent, members: list[Person] | None) -> dict[str, str]:
        actor = next((p.label for p in members or [] if p.id == event.actor), event.actor or "le système")
        definition = self._docs._registry.get(event.doctype).definition
        link = "" if event.kind == "delete" else (self._desk_url + "/" if self._desk_url else "") + _link(event.doctype, event.name)
        return {"doctype": definition.label or event.doctype, "name": event.name, "event": EVENT_WORDS[event.kind], "actor": actor, "link": link}

    def _mail_body(self, delivery: Delivery, event: DocEvent) -> str:
        parts = [delivery.message or delivery.subject]
        if self._desk_url and event.kind != "delete":
            parts.append(f"{self._desk_url}/{_link(event.doctype, event.name)}")
        parts.append("Vous recevez ce message parce qu'une règle de notification de votre organisation vous désigne.")
        return "\n\n".join(parts)

    async def _assignees(self, event: DocEvent) -> list[str]:
        if event.assignees is not None:
            return list(event.assignees)
        ctx = RequestContext.system(event.tenant_id, "notification rules")
        async with self._docs._query(ctx) as q:
            rows = await q.assignments(event.doctype, event.name)
        return [r["assignee"] for r in rows if r.get("status") == "open"]


def _link(doctype: str, name: str) -> str:
    return f"#/app/{quote(doctype, safe='')}/{quote(name, safe='')}"


def _warn(log: Any, message: str, **fields: Any) -> None:
    if log is None:
        return
    try:
        log.warning(message, **fields)  # pas de champ `plugin` ni `msg` : le logger d'xcore les réserve
    except Exception:  # noqa: BLE001
        pass
