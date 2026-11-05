"""Les canaux de notification des plugins (`channels.py`, service `doctype_channels`) : WhatsApp & co.

Ce que ces tests défendent :
- un canal `listen_all` reçoit ce que la cloche reçoit (assignation, mention, règle), APRÈS elle, avec le document et les adresses ;
- une règle qui NOMME un canal le lui envoie, avec ou sans cloche (« Canal du plugin seul »), une seule fois même s'il écoute tout ;
- un canal inconnu est refusé à l'enregistrement de la règle (en citant ceux qui existent), un canal parti est journalisé à l'envoi ;
- un canal lent, en panne ou qui lève ne fait jamais échouer l'écriture, ne bloque pas la requête, et se journalise ;
- les noms appartiennent au plugin qui les déclare, et partent avec lui.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.unit.test_notification_rules import AGENT, MEMBERS, RITA, Bell, Env, People
from xcore_doctype.channels import ChannelError, ChannelNotifier, ChannelRegistry, Notification, document_of
from xcore_doctype.errors import ValidationFailed


class Phone:
    """Un faux WhatsApp : enregistre, ou échoue, ou traîne."""

    def __init__(self, *, fail=False, slow=False):
        self.sent: list[Notification] = []
        self.fail, self.slow = fail, slow

    async def __call__(self, n: Notification) -> None:
        if self.slow:
            await asyncio.sleep(10)
        if self.fail:
            raise ConnectionError("l'API WhatsApp ne répond pas")
        self.sent.append(n)


class Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, **kw):
        self.warnings.append((msg, kw))

    info = error = warning


@pytest.fixture
def env(db, tables):
    def make(**kw):
        channels = ChannelRegistry(Log())
        e = Env(db, tables, channels=channels, **kw)
        e.channels = channels
        return e

    return make


async def _settle(e) -> None:
    await e.rules.settled()
    await e.channels.settled()


# ── Le registre ─────────────────────────────────────────────────────────────────────────────────


def test_names_are_owned_checked_and_leave_with_their_plugin():
    reg = ChannelRegistry()
    reg.register("whatsapp", "whatsapp", Phone(), label="WhatsApp", listen_all=True)
    reg.register("whatsapp", "whatsapp", Phone())  # le même plugin le redéclare (rechargement) : il le remplace
    with pytest.raises(ChannelError, match="appartient déjà"):
        reg.register("sms", "whatsapp", Phone())
    for bad in ("WhatsApp", "1sms", "a-b", "", "x" * 33):
        with pytest.raises(ChannelError):
            reg.register("sms", bad, Phone())
    with pytest.raises(ChannelError):
        reg.register("", "sms", Phone())
    reg.register("sms", "sms", Phone())
    assert reg.names() == ["sms", "whatsapp"] and reg.has("sms")
    assert reg.unregister_owner("whatsapp") == ["whatsapp"] and reg.names() == ["sms"]


def test_document_of_reads_only_the_links_the_engine_builds():
    assert document_of("#/app/Sales%20Invoice/SI-1") == ("Sales Invoice", "SI-1")
    assert document_of("#/tasks") == (None, None) and document_of(None) == (None, None)


# ── Ce que la cloche reçoit, les canaux qui écoutent tout le reçoivent ──────────────────────────


async def test_a_listening_channel_gets_every_bell_notification_after_the_bell_with_the_addresses():
    bell, phone, order = Bell(), Phone(), []
    reg = ChannelRegistry()

    async def first(*a, **kw):
        order.append("bell")
        await Bell.notify(bell, *a, **kw)

    bell_first = type("B", (), {"notify": staticmethod(first)})()

    async def second(n):
        order.append("phone")
        await phone(n)

    reg.register("whatsapp", "whatsapp", second, listen_all=True)
    reg.register("sms", "sms", Phone())  # n'écoute pas tout : rien
    notifier = ChannelNotifier(bell_first, reg, People(MEMBERS))
    await notifier.notify("acme", [RITA, "u-inconnu", RITA], title="Léo vous a assigné Ticket T-1", link="#/app/Ticket/T-1", kind="ASSIGNMENT")
    await reg.settled()
    [n] = phone.sent
    assert order == ["bell", "phone"]
    assert (n.tenant_id, n.user_ids, n.kind, n.doctype, n.name) == ("acme", (RITA, "u-inconnu"), "ASSIGNMENT", "Ticket", "T-1")
    assert n.emails == ("rita@acme.test",)  # l'annuaire ne connaît que Rita


async def test_without_a_listening_channel_the_directory_is_not_even_asked():
    class Counting(People):
        asked = 0

        async def members(self, tenant_id):
            Counting.asked += 1
            return await super().members(tenant_id)

    reg = ChannelRegistry()
    reg.register("sms", "sms", Phone())
    await ChannelNotifier(Bell(), reg, Counting(MEMBERS)).notify("acme", [RITA], title="x")
    assert Counting.asked == 0


async def test_a_failing_slow_or_raising_channel_never_reaches_the_caller_and_is_logged():
    log = Log()
    reg = ChannelRegistry(log, timeout=0.05)
    reg.register("a", "broken", Phone(fail=True), listen_all=True)
    reg.register("b", "slow", Phone(slow=True), listen_all=True)
    good = Phone()
    reg.register("c", "good", good, listen_all=True)
    await ChannelNotifier(Bell(), reg).notify("acme", [RITA], title="x")  # rend la main tout de suite : les envois sont en tâche de fond
    await reg.settled()
    assert len(good.sent) == 1
    assert sorted(m for m, _ in log.warnings) == ["notification channel failed", "notification channel timed out"]


async def test_after_close_nothing_leaves():
    reg = ChannelRegistry()
    phone = Phone()
    reg.register("w", "whatsapp", phone, listen_all=True)
    await reg.close()
    reg.broadcast(Notification("acme", (RITA,), "x"))
    await reg.settled()
    assert phone.sent == [] and reg.names() == []


# ── Les règles de notification ──────────────────────────────────────────────────────────────────


async def test_a_rule_naming_a_channel_sends_it_with_the_rule_and_the_rendered_text(env):
    e = env()
    phone = Phone()
    e.channels.register("whatsapp", "whatsapp", phone)
    await e.rule(plugin_channel="whatsapp", subject="Nouveau ticket {{ doc.title }}")
    doc = await e.ticket()
    await _settle(e)
    assert len(e.bell.sent) == 1  # la cloche part toujours (canal « Cloche »)
    [n] = phone.sent
    assert (n.kind, n.doctype, n.name, n.user_ids, n.emails, n.title) == ("RULE", "Ticket", doc["name"], (RITA,), ("rita@acme.test",), "Nouveau ticket Imprimante")
    assert n.rule and n.link == f"#/app/Ticket/{doc['name']}"


async def test_plugin_channel_only_rings_no_bell_and_reaches_no_listening_channel(env):
    e = env()
    named, listening = Phone(), Phone()
    e.channels.register("whatsapp", "whatsapp", named)
    e.channels.register("slack", "slack", listening, listen_all=True)
    await e.rule(channel="Canal du plugin seul", plugin_channel="whatsapp")
    await e.ticket()
    await _settle(e)
    assert e.bell.sent == [] and len(named.sent) == 1 and listening.sent == []


async def test_a_listening_channel_named_by_a_rule_gets_it_once_and_others_get_the_bell(env):
    e = env()
    named, other = Phone(), Phone()
    e.channels.register("whatsapp", "whatsapp", named, listen_all=True)
    e.channels.register("slack", "slack", other, listen_all=True)
    await e.rule(plugin_channel="whatsapp")
    await e.ticket()
    await _settle(e)
    assert len(named.sent) == 1 and len(other.sent) == 1 and other.sent[0].rule


async def test_an_unknown_channel_is_refused_when_the_rule_is_saved_and_the_known_ones_are_named(env):
    e = env()
    e.channels.register("whatsapp", "whatsapp", Phone())
    with pytest.raises(ValidationFailed) as err:
        await e.rule(plugin_channel="telegram")
    [d] = [d for d in err.value.details if d.get("field") == "plugin_channel"]
    assert "telegram" in d["msg"] and "whatsapp" in d["msg"]
    with pytest.raises(ValidationFailed) as err:
        await e.rule(channel="Canal du plugin seul")  # « seul » sans canal : personne ne recevrait rien
    assert "plugin_channel" in {d.get("field") or d.get("loc", [None])[-1] for d in err.value.details}


async def test_a_channel_gone_since_the_rule_was_saved_is_logged_and_the_bell_still_rings(env):
    e = env()
    e.channels.register("whatsapp", "whatsapp", Phone())
    await e.rule(plugin_channel="whatsapp")
    e.channels.unregister_owner("whatsapp")
    await e.ticket()
    await _settle(e)
    assert len(e.bell.sent) == 1
    assert any(m == "notification channel missing" for m, _ in e.channels._log.warnings)


async def test_a_failing_channel_never_fails_the_write(env):
    e = env()
    e.channels.register("whatsapp", "whatsapp", Phone(fail=True), listen_all=True)
    await e.rule(plugin_channel="whatsapp")
    doc = await e.ticket(ctx=AGENT)
    await _settle(e)
    assert doc["name"] and any(m == "notification channel failed" for m, _ in e.channels._log.warnings)
