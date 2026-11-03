"""Les signaux temps réel (realtime.py) : ce que le moteur publie sur `ext.pubsub` après le commit, et ce qu'il n'y met JAMAIS.

Ce que ces tests défendent, chacun par une mutation qui le casse :
- toute écriture validée (création, modification, suppression, soumission) signale son TYPE sur le canal de son organisation ;
- le signal ne porte ni le nom ni les valeurs du document (le canal est commun à tous les membres, lecteurs ou non) ;
- ce qu'un hook écrit dans la transaction est signalé aussi (une facture soumise change le grand livre) ;
- une écriture refusée ne signale rien ;
- un bus en panne ne fait pas échouer l'écriture ;
- la cloche : une notification créée sonne chez chacun de ses destinataires, sur son canal personnel.
"""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import ValidationFailed
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.realtime import BELL_CHANGED, DOC_CHANGED, Realtime, RealtimeNotifier
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

VOUCHER = {
    "schema_version": 1, "name": "Voucher", "module": "erp", "submittable": True,
    "fields": [{"fieldname": "secret_label", "fieldtype": "Text", "required": True}],
    "hooks": {"on_submit": ["erp.post"]},
}
ENTRY = {"schema_version": 1, "name": "Entry", "module": "erp", "track_changes": False,
         "fields": [{"fieldname": "voucher", "fieldtype": "Text"}], "permissions": {"write": [], "create": [], "delete": []}}
ITEM = {"schema_version": 1, "name": "Item", "module": "erp", "allow_rename": True, "naming": "prompt",
        "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
LINE = {"schema_version": 1, "name": "Line", "module": "erp", "fields": [{"fieldname": "item", "fieldtype": "Link", "options": "Item"}]}
GRANTS = [f"erp:{t}:{a}" for t in ("voucher", "entry", "item", "line") for a in ("read", "write", "delete", "submit", "cancel")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)


class Log:
    def __init__(self):
        self.warnings = []

    def info(self, *a, **k): ...
    def error(self, *a, **k): ...

    def warning(self, msg, **k):
        self.warnings.append((msg, k))


class Bus:
    def __init__(self, broken=False):
        self.sent, self.broken = [], broken

    async def publish(self, channel, data):
        if self.broken:
            raise ConnectionError("bus en panne")
        self.sent.append((channel, data))
        return True


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    for d in (ENTRY, VOUCHER, ITEM, LINE):
        reg.register(parse_definition(d))
    reg.validate_links()
    hooks = HookRegistry()

    async def post(event):
        await event.tx.create("Entry", {"voucher": event.name})

    hooks.register("erp.post", post)
    log, bus = Log(), Bus()
    svc = DocumentService(reg, db, tables, log, hooks=hooks)
    svc.realtime = Realtime(bus, log)
    return svc, bus, log


def changed(bus):
    return [(c, d["doctype"]) for c, d in bus.sent if d["event_type"] == DOC_CHANGED]


async def test_every_committed_write_signals_its_type_to_its_organisation(env):
    svc, bus, _ = env
    doc = await svc.create(ALICE, "Voucher", {"secret_label": "Confidentiel"})
    doc = await svc.update(ALICE, "Voucher", doc["name"], {"secret_label": "Toujours"}, doc["version"])
    doc = await svc.submit(ALICE, "Voucher", doc["name"], doc["version"])
    other = await svc.create(ALICE, "Voucher", {"secret_label": "x"})
    await svc.delete(ALICE, "Voucher", other["name"])
    assert ("tenant-acme", "Entry") in changed(bus)  # ce que le hook de soumission a écrit
    assert changed(bus).count(("tenant-acme", "Voucher")) >= 5
    flat = repr(bus.sent)
    assert doc["name"] not in flat and "Confidentiel" not in flat and "Toujours" not in flat  # ni nom ni valeur


async def test_a_refused_write_signals_nothing(env):
    svc, bus, _ = env
    with pytest.raises(ValidationFailed):
        await svc.create(ALICE, "Voucher", {})
    assert bus.sent == []


async def test_a_broken_bus_never_fails_the_write(env):
    svc, _, log = env
    svc.realtime = Realtime(Bus(broken=True), log)
    doc = await svc.create(ALICE, "Voucher", {"secret_label": "ok"})
    assert doc["name"] and log.warnings and log.warnings[0][0] == "realtime signal lost"


async def test_the_bell_rings_for_each_addressee_on_their_own_channel():
    class Inner:
        def __init__(self):
            self.calls = []

        async def notify(self, tenant_id, user_ids, **kw):
            self.calls.append((tenant_id, tuple(user_ids)))

    bus, inner = Bus(), Inner()
    await RealtimeNotifier(inner, Realtime(bus, Log())).notify("acme", ["bob", "carol", "bob"], title="Assigné")
    assert inner.calls == [("acme", ("bob", "carol", "bob"))]
    assert [(c, d["event_type"], d["user_id"]) for c, d in bus.sent] == [("notification", BELL_CHANGED, "bob"), ("notification", BELL_CHANGED, "carol")]


async def test_a_rename_signals_the_types_it_rewrote(env):
    svc, bus, _ = env
    await svc.create(ALICE, "Item", {"label": "Vis"}, name="VIS")
    await svc.create(ALICE, "Line", {"item": "VIS"})
    bus.sent.clear()
    await svc.rename(ALICE, "Item", "VIS", "VIS-M4")
    assert ("tenant-acme", "Line") in changed(bus)  # la ligne qui citait l'article a changé à l'écran aussi
