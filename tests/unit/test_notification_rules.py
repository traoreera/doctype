"""Règles de notification par DocType (C4, notification_rules.py) : quoi, quand, à qui, et jamais au prix d'une écriture.

Le service est réel (base SQLite/PostgreSQL du fixture `db`), l'annuaire, la cloche et l'e-mail sont des faux qui enregistrent : on regarde
ce qui PART. Les règles sont des documents `Notification Rule` du plugin `system`, vérifiés par le hook `doctype.notification_rule`.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from xcore_doctype import system_plugin_dir
from xcore_doctype.context import RequestContext
from xcore_doctype.directory import Person
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DocumentValidationError, ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.notification_rules import (
    RULE_DOCTYPE, RULE_HOOK, DocEvent, ExtensionMailer, NotificationRules, make_rule_hook, render, rule_problems, template_problems,
)
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sealing import Sealer

ADA, RITA, LEO = "u-ada", "u-rita", "u-leo"
MEMBERS = {"acme": [Person(ADA, "ada@acme.test"), Person(RITA, "rita@acme.test"), Person(LEO, "leo@acme.test")], "globex": [Person("u-gus", "gus@globex.test")]}
TICKET = [f"helpdesk:ticket:{a}" for a in ("read", "write", "delete", "submit", "cancel")]
RULES = ["system:notification_rule:read", "system:notification_rule:write", "system:notification_rule:delete"]
ADMIN = make_ctx("acme", ADA, permissions=TICKET + RULES)
AGENT = make_ctx("acme", LEO, permissions=TICKET)


def ticket_def():
    return parse_definition({
        "schema_version": 1, "name": "Ticket", "label": "Ticket", "module": "helpdesk", "submittable": True,
        "fields": [
            {"fieldname": "title", "fieldtype": "Text", "required": True},
            {"fieldname": "status", "fieldtype": "Select", "options": ["Ouvert", "Fermé"], "default": "Ouvert"},
            {"fieldname": "priority", "fieldtype": "Select", "options": ["Basse", "Haute"], "default": "Basse"},
            {"fieldname": "approver", "fieldtype": "Text", "format": "email"},
            {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2},
            {"fieldname": "pin", "fieldtype": "Password"},
            {"fieldname": "notes", "fieldtype": "TextEditor"},
        ],
    })


def memo_def():
    """Un type qu'on ne lit que pour SES documents (`if_owner`) : on n'en diffuse pas le contenu des autres."""
    return parse_definition({
        "schema_version": 1, "name": "Memo", "module": "helpdesk",
        "fields": [{"fieldname": "body", "fieldtype": "Text"}],
        "permissions": {"read": [{"grant": "helpdesk:memo:own", "if_owner": True}], "write": ["helpdesk:memo:own"], "create": ["helpdesk:memo:own"]},
    })


class Log:
    def __init__(self):
        self.infos, self.errors, self.warnings = [], [], []

    def info(self, msg, **kw):
        self.infos.append((msg, kw))

    def error(self, msg, **kw):
        self.errors.append((msg, kw))

    def warning(self, msg, **kw):
        self.warnings.append((msg, kw))


class People:
    def __init__(self, members):
        self.by_tenant = members

    async def members(self, tenant_id):
        return self.by_tenant.get(tenant_id) if self.by_tenant is not None else None


class Bell:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def notify(self, tenant_id, user_ids, *, title, message=None, link=None, kind="DOCUMENT"):
        if self.fail:
            raise ConnectionError("xauth est arrêté")
        self.sent.append({"tenant": tenant_id, "users": list(user_ids), "title": title, "message": message, "link": link, "kind": kind})


class Mail:
    def __init__(self):
        self.sent = []

    async def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


class Env:
    def __init__(self, db, tables, *, members=MEMBERS, bell=None, desk_url="https://erp.acme.test/app/xcore-doctype/desk"):
        self.reg = DocTypeRegistry()
        self.reg.load_dir(system_plugin_dir() / "doctypes", owner="system")
        self.reg.register(ticket_def(), owner="helpdesk")
        self.reg.register(memo_def(), owner="helpdesk")
        self.log, self.hooks = Log(), HookRegistry()
        self.svc = DocumentService(self.reg, db, tables, self.log, hooks=self.hooks, sealer=Sealer("clé-de-test-assez-longue"))
        self.hooks.register(RULE_HOOK, make_rule_hook(self.svc), owner="xcore-doctype")
        self.bell, self.mail = bell or Bell(), Mail()
        self.rules = NotificationRules(self.svc, directory=People(members), notifier=self.bell, mailer=self.mail, desk_url=desk_url, logger=self.log)
        self.svc.notifications = self.rules

    async def rule(self, ctx=ADMIN, **data):
        base = {"rule_name": "Règle", "document_type": "Ticket", "event": "Création", "subject": "{{ doctype }} {{ name }} {{ event }} par {{ actor }}",
                "recipients": [{"email": "rita@acme.test"}]}
        return await self.svc.create(ctx, RULE_DOCTYPE, {**base, **data})

    async def ticket(self, ctx=AGENT, **data):
        doc = await self.svc.create(ctx, "Ticket", {"title": "Imprimante", **data})
        await self.rules.settled()
        return doc


@pytest.fixture
def env(db, tables):
    return lambda **kw: Env(db, tables, **kw)


# ── Ce qui part ─────────────────────────────────────────────────────────────────────────────────


async def test_a_creation_rule_rings_the_named_member_with_the_rendered_subject_and_a_link(env):
    e = env()
    await e.rule()
    doc = await e.ticket()
    [one] = e.bell.sent
    assert one["tenant"] == "acme" and one["users"] == [RITA] and one["kind"] == "RULE"
    assert one["title"] == f"Ticket {doc['name']} créé par leo@acme.test"
    assert one["link"] == f"#/app/Ticket/{doc['name']}"
    assert e.mail.sent == []  # canal « Cloche » : pas d'e-mail


async def test_the_email_channel_sends_plain_text_to_the_members_address_with_the_desk_link(env):
    e = env()
    await e.rule(channel="E-mail", subject="Nouveau : {{ doc.title }}", message="{{ doc.title }} ({{ doc.priority }})\nVoir {{ link }}")
    doc = await e.ticket(priority="Haute")
    assert e.bell.sent == []
    [(to, subject, body)] = e.mail.sent
    assert (to, subject) == ("rita@acme.test", "Nouveau : Imprimante")
    url = f"https://erp.acme.test/app/xcore-doctype/desk/#/app/Ticket/{doc['name']}"
    assert body.startswith(f"Imprimante (Haute)\nVoir {url}") and url in body.split("\n\n")[1]


async def test_both_channels_and_every_recipient_source_reach_members_only_never_the_author_of_the_change(env):
    e = env()
    await e.rule(channel="Cloche et e-mail", notify_owner=True, recipient_field="approver",
                 recipients=[{"email": "RITA@acme.test"}, {"email": "inconnu@ailleurs.test"}, {"email": "leo@acme.test"}])
    await e.ticket(approver="ada@acme.test")  # Léo crée : l'auteur du document ET du changement, jamais prévenu
    [one] = e.bell.sent
    assert sorted(one["users"]) == sorted([ADA, RITA])
    assert sorted(to for to, _, _ in e.mail.sent) == ["ada@acme.test", "rita@acme.test"]


async def test_the_owner_is_rung_when_somebody_else_changes_their_document(env):
    e = env()
    await e.rule(event="Modification", notify_owner=True, recipients=[])
    doc = await e.ticket()
    assert e.bell.sent == []  # la règle vise la modification
    await e.svc.update(ADMIN, "Ticket", doc["name"], {**doc["data"], "priority": "Haute"}, doc["version"])
    await e.rules.settled()
    [one] = e.bell.sent
    assert one["users"] == [LEO] and one["title"].endswith("modifié par ada@acme.test")


async def test_assignees_are_rung_while_their_assignment_is_open(env):
    e = env()
    await e.rule(event="Modification", notify_assignees=True, recipients=[])
    doc = await e.ticket()
    async with e.svc._query(RequestContext.system("acme", "test")) as q:
        await q.add_assignment("Ticket", doc["name"], assignee=RITA, description=None, due_date=None)
    await e.svc.update(AGENT, "Ticket", doc["name"], {**doc["data"], "status": "Fermé"}, doc["version"])
    await e.rules.settled()
    assert [s["users"] for s in e.bell.sent] == [[RITA]]


async def test_a_value_change_rule_fires_only_when_its_field_changes(env):
    e = env()
    await e.rule(event="Changement de valeur", value_field="status", subject="{{ name }} : {{ doc.status }}")
    doc = await e.ticket()
    assert e.bell.sent == []  # une création n'est pas un changement de valeur
    doc = await e.svc.update(AGENT, "Ticket", doc["name"], {**doc["data"], "priority": "Haute"}, doc["version"])
    await e.rules.settled()
    assert e.bell.sent == []
    await e.svc.update(AGENT, "Ticket", doc["name"], {**doc["data"], "status": "Fermé"}, doc["version"])
    await e.rules.settled()
    assert [s["title"] for s in e.bell.sent] == [f"{doc['name']} : Fermé"]


async def test_submit_cancel_and_delete_rules_follow_their_event(env):
    e = env()
    for event in ("Soumission", "Annulation", "Suppression"):
        await e.rule(event=event, subject=f"{event} {{{{ name }}}} ({{{{ event }}}})")
    doc = await e.ticket()
    doc = await e.svc.submit(AGENT, "Ticket", doc["name"], doc["version"])
    await e.rules.settled()
    doc = await e.svc.cancel(AGENT, "Ticket", doc["name"], doc["version"])
    await e.rules.settled()
    draft = await e.ticket(title="Brouillon")
    await e.svc.delete(AGENT, "Ticket", draft["name"])
    await e.rules.settled()
    assert [(s["title"], s["link"]) for s in e.bell.sent] == [
        (f"Soumission {doc['name']} (soumis)", f"#/app/Ticket/{doc['name']}"),
        (f"Annulation {doc['name']} (annulé)", f"#/app/Ticket/{doc['name']}"),
        (f"Suppression {draft['name']} (supprimé)", None),  # le document n'existe plus : pas de lien
    ]


async def test_the_condition_reads_the_document(env):
    e = env()
    await e.rule(condition="priority == 'Haute' and status == 'Ouvert'")
    await e.ticket()
    assert e.bell.sent == []
    await e.ticket(priority="Haute")
    assert len(e.bell.sent) == 1


async def test_a_disabled_rule_a_rule_of_another_type_or_of_another_organisation_does_nothing(env):
    e = env()
    await e.rule(enabled=False)
    await e.rule(ctx=RequestContext.system("acme", "test"), document_type="Memo", subject="{{ doc.body }}")  # le système n'a pas à LIRE pour écrire
    gus = make_ctx("globex", "u-gus", permissions=TICKET + RULES)
    await e.rule(ctx=gus, recipients=[{"email": "gus@globex.test"}], notify_owner=True)
    await e.ticket()
    assert e.bell.sent == []


async def test_an_owner_who_left_the_organisation_is_not_rung(env):
    e = env()
    await e.rule(event="Modification", notify_owner=True, recipients=[])
    doc = await e.ticket(ctx=make_ctx("acme", "u-parti", permissions=TICKET))  # créé par un membre parti depuis : l'annuaire ne le connaît plus
    await e.svc.update(ADMIN, "Ticket", doc["name"], {**doc["data"], "status": "Fermé"}, doc["version"])
    await e.rules.settled()
    assert e.bell.sent == []


async def test_nobody_outside_the_directory_is_rung_and_without_a_directory_only_identities_go_to_the_bell(env):
    e = env(members=None)
    await e.rule(channel="Cloche et e-mail", notify_owner=True, event="Modification")
    doc = await e.ticket()
    await e.svc.update(ADMIN, "Ticket", doc["name"], {**doc["data"], "status": "Fermé"}, doc["version"])
    await e.rules.settled()
    assert [s["users"] for s in e.bell.sent] == [[LEO]]  # l'auteur, par son identifiant (xauth ne prévient que ses membres)
    assert e.mail.sent == []  # sans annuaire, aucune adresse : pas d'e-mail ; l'adresse nommée par la règle ne désigne personne


# ── Ce qui ne sort jamais ───────────────────────────────────────────────────────────────────────


async def test_a_template_never_renders_a_secret_and_values_are_one_line_and_bounded(env):
    e = env()
    await e.rule(subject="{{ doc.title }}", message="titre={{ doc.title }}")
    await e.ticket(title="A\r\nBcc: x@evil.test" + "z" * 400, pin="1234")
    [one] = e.bell.sent
    assert "\n" not in one["title"] and "\r" not in one["title"] and len(one["title"]) <= 255
    assert one["message"].startswith("titre=A Bcc: x@evil.test")
    assert render("{{ x }} {{ doc.y }} {{doc.title}}", {}, {"title": "t"}) == "{{ x }}  t"  # rien n'est évalué : l'inconnu reste tel quel
    assert len(one["message"]) < 300  # une valeur fait 200 caractères au plus
    assert render("{{ doc.pin }}", {}, {"pin": "********"}) == "********"  # même cité, un secret est déjà masqué à la source


async def test_a_masked_document_reaches_the_rules(env):
    e = env()
    seen = []
    real = e.rules.evaluate

    async def spy(event: DocEvent):
        seen.append(event)
        return await real(event)

    e.rules.evaluate = spy
    await e.rule()
    await e.ticket(pin="1234")
    assert seen[0].data["pin"] == "********" and seen[0].actor == LEO and seen[0].owner == LEO


# ── Une notification ne défait rien ─────────────────────────────────────────────────────────────


async def test_a_failing_bell_or_mailer_never_fails_the_write_and_is_logged(env):
    e = env(bell=Bell(fail=True))
    await e.rule(channel="Cloche et e-mail")
    doc = await e.ticket()
    assert (await e.svc.get(AGENT, "Ticket", doc["name"]))["name"] == doc["name"]
    assert any(m == "notification rule failed" for m, _ in e.log.warnings)  # journalisé règle par règle

    class Broken:
        async def send(self, **kw):
            raise TimeoutError("smtp")

    log = Log()
    assert await ExtensionMailer(Broken(), logger=log).send("a@b.test", "s", "b") is False
    assert log.warnings and log.warnings[0][0] == "notification e-mail not sent"


async def test_evaluation_runs_after_the_commit_in_the_background(env):
    e = env()
    await e.rule()
    gate = asyncio.Event()
    real = e.rules.evaluate

    async def slow(event):
        await gate.wait()
        return await real(event)

    e.rules.evaluate = slow
    await e.svc.create(AGENT, "Ticket", {"title": "Lent"})  # rend la main sans attendre la règle
    assert e.bell.sent == [] and e.rules._pending
    gate.set()
    await e.rules.settled()
    assert len(e.bell.sent) == 1


async def test_after_close_nothing_is_dispatched_and_pending_work_is_dropped(env):
    e = env()
    await e.rule()
    await e.rules.close()
    await e.ticket()
    assert e.bell.sent == [] and not e.rules._pending


async def test_without_the_rule_doctype_nothing_is_evaluated(db, tables):
    reg = DocTypeRegistry()
    reg.register(ticket_def(), owner="helpdesk")
    svc = DocumentService(reg, db, tables, Log())
    rules = NotificationRules(svc, notifier=Bell())
    svc.notifications = rules
    await svc.create(AGENT, "Ticket", {"title": "x"})
    assert not rules._pending


async def test_a_rule_made_invalid_by_a_definition_change_is_skipped_and_logged(env):
    e = env()
    rule = {"rule_name": "r", "document_type": "Ticket", "event": "Création", "subject": "{{ doc.disparu }}", "recipients": [{"email": "rita@acme.test"}]}
    event = DocEvent("acme", "Ticket", "T-1", "create", {"title": "x"}, None, LEO, LEO)
    assert e.rules._applies({**rule, "_name": "r1"}, event) is False
    assert e.log.warnings[-1][0] == "notification rule skipped: no longer valid"


# ── Une règle se vérifie à l'enregistrement ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"document_type": "Nope"}, "document_type"),
        ({"document_type": RULE_DOCTYPE}, "document_type"),
        ({"document_type": "Notification Rule Recipient"}, "document_type"),
        ({"document_type": "Memo", "event": "Soumission"}, "event"),
        ({"event": "Changement de valeur"}, "value_field"),
        ({"event": "Changement de valeur", "value_field": "pin"}, "value_field"),
        ({"condition": "inconnu == 1"}, "condition"),
        ({"condition": "amount == '1.00'"}, "condition"),
        ({"condition": "status =="}, "condition"),
        ({"recipient_field": "amount"}, "recipient_field"),
        ({"recipients": [], "notify_owner": False}, "recipients"),
        ({"subject": "{{ doc.pin }}"}, "subject"),
        ({"message": "{{ doc.notes }}"}, "message"),
        ({"message": "{{ doc.inconnu }}"}, "message"),
        ({"subject": "{{ title }}"}, "subject"),
    ],
)
async def test_an_incoherent_rule_is_refused_field_by_field(env, data, field):
    e = env()
    with pytest.raises(ValidationFailed) as err:
        await e.rule(**data)
    # Le schéma refuse d'abord ce qu'il sait juger seul (un champ rendu obligatoire par l'événement) ; le hook, tout le reste.
    assert err.value.code in ("invalid_rule", "validation_failed")
    assert field in {d.get("field") or d.get("loc", [None])[-1] for d in err.value.details}, err.value.details


async def test_the_author_must_read_every_document_of_the_type_to_diffuse_it(env):
    e = env()
    own = make_ctx("acme", ADA, permissions=RULES + ["helpdesk:memo:own"])
    with pytest.raises(ValidationFailed) as err:
        await e.rule(ctx=own, document_type="Memo", subject="{{ doc.body }}")
    assert err.value.code == "invalid_rule" and "TOUS" in err.value.details[0]["msg"]
    blind = make_ctx("acme", ADA, permissions=RULES)
    with pytest.raises(ValidationFailed):
        await e.rule(ctx=blind)


def test_rule_problems_is_empty_for_a_good_rule():
    reg = DocTypeRegistry()
    reg.load_dir(system_plugin_dir() / "doctypes", owner="system")
    reg.register(ticket_def(), owner="helpdesk")
    good = {"document_type": "Ticket", "event": "Soumission", "subject": "{{ doc.title }} {{ link }}", "notify_owner": True,
            "condition": "status != 'Fermé'", "recipient_field": "approver"}
    assert rule_problems(reg, good) == []


# ── Le réglage `doctype.notifications` ──────────────────────────────────────────────────────────


class _Host:
    """Ce que `Plugin._rule_delivery` lit de l'instance : le service d'e-mail de l'hôte et le journal."""

    def __init__(self, email=None):
        self.logger, self._email = Log(), email

    def get_service(self, name):
        if name != "ext.email" or self._email is None:
            raise KeyError(name)
        return self._email


def test_email_comes_from_the_hosts_extension_and_can_be_switched_off():
    from xcore_doctype.errors import ConfigurationError
    from xcore_doctype.notification_rules import NoMailer
    from xcore_doctype.plugin import Plugin

    class Ext:
        async def send(self, **kw):
            return True

    assert isinstance(Plugin._rule_delivery(_Host(Ext()), None)["mailer"], ExtensionMailer)
    assert isinstance(Plugin._rule_delivery(_Host(Ext()), {"email": False})["mailer"], NoMailer)
    host = _Host()
    assert isinstance(Plugin._rule_delivery(host, {})["mailer"], NoMailer) and host.logger.infos  # absente : dit une fois, sans erreur
    assert Plugin._rule_delivery(_Host(), {"desk_url": "https://erp.acme.test/desk"})["desk_url"] == "https://erp.acme.test/desk"
    for bad in ({"email": "oui"}, {"desk_url": "javascript:alert(1)"}, {"desk_url": 3}):
        with pytest.raises(ConfigurationError):
            Plugin._rule_delivery(_Host(), bad)


# ── Revue adversariale de C4.1 : chaque constat a son test ──────────────────────────────────────


def _order_and_log():
    """Un type soumettable dont le hook `on_submit` écrit un `Log` par `event.tx` (comme le stock écrit son grand livre)."""
    order = parse_definition({
        "schema_version": 1, "name": "Order", "module": "helpdesk", "submittable": True,
        "fields": [{"fieldname": "title", "fieldtype": "Text", "required": True}],
        "hooks": {"on_submit": ["helpdesk.write_log"]},
    })
    log = parse_definition({"schema_version": 1, "name": "Log", "module": "helpdesk", "fields": [{"fieldname": "what", "fieldtype": "Text"}]})
    return order, log


LOG_GRANTS = [f"helpdesk:{dt}:{a}" for dt in ("order", "log") for a in ("read", "write", "delete", "submit", "cancel")]


async def test_what_a_hook_writes_in_the_transaction_fires_the_rules_after_the_commit_and_never_on_rollback(env):
    e = env()
    order, log = _order_and_log()
    e.reg.register(log, owner="helpdesk")
    e.reg.register(order, owner="helpdesk")
    refuse = {"on": False}

    async def write_log(event):
        await event.tx.create("Log", {"what": f"commande {event.name} soumise"})
        if refuse["on"]:
            raise DocumentValidationError("stock insuffisant")

    e.hooks.register("helpdesk.write_log", write_log, owner="helpdesk")
    boss, clerk = make_ctx("acme", ADA, permissions=RULES + LOG_GRANTS), make_ctx("acme", LEO, permissions=LOG_GRANTS)
    await e.svc.create(boss, RULE_DOCTYPE, {"rule_name": "Alerte", "document_type": "Log", "event": "Création", "subject": "{{ doc.what }}",
                                            "recipients": [{"email": "rita@acme.test"}]})
    first = await e.svc.create(clerk, "Order", {"title": "CMD-1"})
    await e.svc.submit(clerk, "Order", first["name"], first["version"])
    await e.rules.settled()
    assert [s["title"] for s in e.bell.sent] == [f"commande {first['name']} soumise"]
    refuse["on"] = True  # le hook écrit, puis refuse : la transaction est défaite, le Log n'a jamais existé
    second = await e.svc.create(clerk, "Order", {"title": "CMD-2"})
    with pytest.raises(ValidationFailed):
        await e.svc.submit(clerk, "Order", second["name"], second["version"])
    await e.rules.settled()
    assert len(e.bell.sent) == 1


async def test_a_deletion_rule_reaches_the_assignees_the_deletion_removed(env):
    e = env()
    await e.rule(event="Suppression", notify_assignees=True, recipients=[], subject="{{ name }} supprimé")
    doc = await e.ticket()
    async with e.svc._query(RequestContext.system("acme", "test")) as q:
        await q.add_assignment("Ticket", doc["name"], assignee=RITA, description=None, due_date=None)
    await e.svc.delete(AGENT, "Ticket", doc["name"])
    await e.rules.settled()
    assert [(s["title"], s["users"]) for s in e.bell.sent] == [(f"{doc['name']} supprimé", [RITA])]


async def test_a_rename_is_a_value_change_for_the_documents_that_cited_the_old_name(db, tables):
    reg = DocTypeRegistry()
    reg.load_dir(system_plugin_dir() / "doctypes", owner="system")
    reg.register(parse_definition({"schema_version": 1, "name": "Client", "module": "helpdesk", "naming": "prompt", "allow_rename": True,
                                   "fields": [{"fieldname": "label", "fieldtype": "Text"}]}), owner="helpdesk")
    reg.register(parse_definition({"schema_version": 1, "name": "Log", "module": "helpdesk",
                                   "fields": [{"fieldname": "client", "fieldtype": "Link", "options": "Client"}]}), owner="helpdesk")
    hooks, bell = HookRegistry(), Bell()
    svc = DocumentService(reg, db, tables, Log(), hooks=hooks)
    hooks.register(RULE_HOOK, make_rule_hook(svc), owner="xcore-doctype")
    rules = NotificationRules(svc, directory=People(MEMBERS), notifier=bell)
    svc.notifications = rules
    grants = RULES + [f"helpdesk:{dt}:{a}" for dt in ("client", "log") for a in ("read", "write", "delete")]
    boss, clerk = make_ctx("acme", ADA, permissions=grants), make_ctx("acme", LEO, permissions=grants)
    await svc.create(boss, RULE_DOCTYPE, {"rule_name": "client changé", "document_type": "Log", "event": "Changement de valeur", "value_field": "client",
                                          "subject": "{{ name }} : {{ doc.client }}", "recipients": [{"email": "rita@acme.test"}]})
    await svc.create(clerk, "Client", {"label": "x"}, name="ACME")
    entry = await svc.create(clerk, "Log", {"client": "ACME"})
    await svc.rename(clerk, "Client", "ACME", "ACME-SA")
    await rules.settled()
    assert [s["title"] for s in bell.sent] == [f"{entry['name']} : ACME-SA"]


async def test_a_template_is_judged_on_a_bounded_text_and_the_placeholder_pattern_is_linear(env):
    import time

    e = env()
    start = time.perf_counter()
    with pytest.raises(ValidationFailed) as err:
        await e.rule(subject="{{" + " " * 100_000)
    assert {d["field"] for d in err.value.details} == {"subject"} and "255" in err.value.details[0]["msg"]
    assert render("{{" + " " * 100_000, {}, {}) == "{{" + " " * 100_000  # rien à remplacer, et sans revenir en arrière
    assert template_problems("{{" + " " * 1990, {}) == []
    assert time.perf_counter() - start < 1.0
    await e.rule(subject="{{   doc.title   }} / {{name}}")  # les blancs autour d'un nom restent permis
    doc = await e.ticket()
    assert e.bell.sent[-1]["title"] == f"Imprimante / {doc['name']}"


async def test_the_rules_of_an_organisation_are_read_once_per_period_and_at_once_after_a_rule_changes_here(db, tables):
    now = [1000.0]
    e = Env(db, tables)
    e.rules = NotificationRules(e.svc, directory=People(MEMBERS), notifier=e.bell, logger=e.log, clock=lambda: now[0])
    e.svc.notifications = e.rules
    reads = lambda: len([kw for m, kw in e.log.infos if m == "system context operation" and kw.get("reason") == "notification rules"])  # noqa: E731
    for _ in range(5):
        await e.ticket()
    assert reads() == 1 and e.bell.sent == []  # une organisation sans règle : une lecture pour cinq écritures
    await e.rule()  # écrite ICI : le cache de l'organisation est vidé, la règle vaut dès l'écriture suivante
    await e.ticket()
    assert reads() == 2 and len(e.bell.sent) == 1
    # Une règle écrite par un AUTRE processus (un service sans évaluateur) n'est vue qu'à la période suivante.
    other = DocumentService(e.reg, db, tables, Log(), hooks=e.hooks)
    await other.create(ADMIN, RULE_DOCTYPE, {"rule_name": "autre", "document_type": "Ticket", "event": "Création", "subject": "bis",
                                             "recipients": [{"email": "ada@acme.test"}]})
    await e.ticket()
    assert [s["title"] for s in e.bell.sent].count("bis") == 0
    now[0] += 31
    await e.ticket()
    assert [s["title"] for s in e.bell.sent].count("bis") == 1


async def test_one_failing_rule_does_not_stop_the_next_one(env):
    class Flaky(Bell):
        async def notify(self, tenant_id, user_ids, **kw):
            if not self.sent and not getattr(self, "failed", False):
                self.failed = True
                raise ConnectionError("xauth vient de tomber")
            await super().notify(tenant_id, user_ids, **kw)

    e = env(bell=Flaky())
    await e.rule(rule_name="première", subject="un")
    await e.rule(rule_name="seconde", subject="deux")
    await e.ticket()
    assert [s["title"] for s in e.bell.sent] == ["deux"]
    assert any(m == "notification rule failed" and kw["rule"] for m, kw in e.log.warnings)


async def test_close_drops_the_evaluation_in_progress(env):
    e = env()
    await e.rule()
    gate = asyncio.Event()
    real = e.rules.evaluate

    async def stuck(event):
        await gate.wait()
        return await real(event)

    e.rules.evaluate = stuck
    await e.svc.create(AGENT, "Ticket", {"title": "en cours"})
    await asyncio.sleep(0)
    assert e.rules._pending
    await e.rules.close()
    gate.set()
    await asyncio.sleep(0)
    assert not e.rules._pending and e.bell.sent == []
