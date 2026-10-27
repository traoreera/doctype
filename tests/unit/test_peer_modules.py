"""Un plugin par module, alors que les modules se citent en cycle (`DocTypeChannel.load_dir(…, peers=True)`).

Les modules d'un ERP se citent les uns les autres (accounts ↔ stock ↔ selling ↔ buying…, un cycle de 15 dans `doctypes/`) et se
prêtent des tables enfants (17 tables partagées) : aucun ordre de `requires` ne permet de les charger l'un après l'autre. Ce que ces
tests défendent, chacun par une mutation qui le casse :
- les 23 modules de `doctypes/`, chargés comme 23 plugins dans N'IMPORTE QUEL ordre, donnent exactement le registre d'un chargement unique ;
- un type qui cite un frère absent se LIT mais ne s'ÉCRIT pas (503 `doctype_incomplete`), jusqu'à l'arrivée du frère ;
- un parent dont la table vient d'un frère absent ATTEND, invisible, mais ses grants sont déclarés dès le chargement de son plugin ;
- la faute qu'un frère révèle en arrivant brise le type qui le cite, pas le chargement du frère ;
- un frère qui part remet en attente ce qui dépendait de ses tables ; il revient, tout revient ;
- sans `peers`, une cible inconnue reste une erreur de chargement (une faute de frappe se voit tout de suite).
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DoctypeError, InvalidDefinition, NotFound
from xcore_doctype.grants import GrantCatalog
from xcore_doctype.registry import DocTypeChannel, DocTypeRegistry

ERP = Path(__file__).resolve().parents[2] / "doctypes"


class Log:
    def __init__(self):
        self.errors, self.warnings = [], []

    def info(self, *a, **k): ...
    def warning(self, msg, **k):
        self.warnings.append((msg, k))

    def error(self, msg, **k):
        self.errors.append((msg, k))


def write(directory, **files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc))
    return directory


def dt(name, module, *fields, **extra):
    return {"schema_version": 1, "name": name, "module": module, "fields": [{"fieldname": "title", "fieldtype": "Text"}, *fields], **extra}


# ── Le vrai jeu d'ERP ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _signature(reg: DocTypeRegistry) -> dict[str, list[tuple[str, str, str]]]:
    return {n: [(f.fieldname, f.fieldtype, str(f.options)) for f in reg.get(n).definition.fields] for n in reg.names()}


@pytest.fixture(scope="module")
def single_load():
    reg = DocTypeRegistry()
    reg.load_dir(ERP)
    reg.validate_links()
    return reg


@pytest.mark.parametrize("order", ["alphabetical", "reversed", "shuffled"])
def test_the_erp_loads_as_one_plugin_per_module_in_any_order(single_load, order):
    modules = sorted(p.name for p in ERP.iterdir() if p.is_dir())
    if order == "reversed":
        modules.reverse()
    elif order == "shuffled":
        random.Random(20261027).shuffle(modules)
    reg, log = DocTypeRegistry(), Log()
    channel = DocTypeChannel(reg, log)
    for module in modules:
        channel.load_dir(module, ERP / module, peers=True)
    assert reg.waiting() == {}
    assert {n: why for n in reg.names() if (why := reg.incomplete(n))} == {}
    assert log.errors == []
    assert _signature(reg) == _signature(single_load)
    assert {reg.owner_of(n) for n in reg.names() if not reg.get(n).definition.istable} <= set(modules)
    grants = GrantCatalog(reg)
    for module in modules:  # chaque plugin déclare exactement les grants de SON namespace
        assert {g["name"] for g in grants.for_plugin(module)} == {g["name"] for g in GrantCatalog(single_load).for_plugin(module)}


def test_without_peers_the_cycle_cannot_load_module_by_module():
    """La preuve que le problème est réel : plugin par plugin, sans `peers`, le premier module qui cite un frère échoue."""
    channel = DocTypeChannel(DocTypeRegistry(), Log())
    with pytest.raises(InvalidDefinition):
        for module in sorted(p.name for p in ERP.iterdir() if p.is_dir()):
            channel.load_dir(module, ERP / module)


# ── Les règles, sur un petit cas ───────────────────────────────────────────────────────────────────────────────────────────────────

GRANTS = [f"{m}:{t}:{a}" for m, t in (("sell", "order"), ("acct", "invoice"), ("acct", "account")) for a in ("read", "write")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)


@pytest.fixture
def plugins(tmp_path):
    """`sell` cite `acct` (un compte) et lui emprunte une table (les échéances) ; `acct` cite `sell` en retour : un cycle."""
    sell = write(
        tmp_path / "sell",
        order=dt("Order", "sell",
                 {"fieldname": "account", "fieldtype": "Link", "options": "Account"},
                 {"fieldname": "account_label", "fieldtype": "Text", "fetch_from": "account.label"},
                 {"fieldname": "schedule", "fieldtype": "Table", "options": "Payment Schedule"}),
    )
    acct = write(
        tmp_path / "acct",
        account=dt("Account", "acct", {"fieldname": "label", "fieldtype": "Text"}),
        invoice=dt("Invoice", "acct", {"fieldname": "order", "fieldtype": "Link", "options": "Order"}),
        schedule={"schema_version": 1, "name": "Payment Schedule", "istable": True, "fields": [{"fieldname": "due", "fieldtype": "Date"}]},
    )
    return sell, acct


@pytest.fixture
def env(plugins, db, tables):
    reg, log = DocTypeRegistry(), Log()
    return reg, DocTypeChannel(reg, log), log, DocumentService(reg, db, tables, log)


async def test_a_type_citing_an_absent_peer_reads_but_does_not_write_until_it_arrives(plugins, env):
    sell, acct = plugins
    reg, channel, log, svc = env
    # `acct` d'abord : son Invoice cite Order, qui n'est pas là.
    channel.load_dir("acct", acct, peers=True)
    assert reg.incomplete("Invoice") == ["DocType 'Order' pas encore chargé"]
    assert (await svc.list(ALICE, "Invoice"))["items"] == []  # elle se lit
    with pytest.raises(DoctypeError) as e:
        await svc.create(ALICE, "Invoice", {"title": "F-1"})
    assert (e.value.code, e.value.status) == ("doctype_incomplete", 503)
    assert "Order" in e.value.message
    assert any(msg == "doctypes incomplete until their peer modules load" for msg, _ in log.warnings)
    # `sell` arrive : Invoice devient complète, et Order s'enregistre tout de suite (sa table est déjà là).
    channel.load_dir("sell", sell, peers=True)
    assert reg.incomplete("Invoice") == [] and reg.waiting() == {}
    await svc.create(ALICE, "Invoice", {"title": "F-1"})


async def test_a_parent_waits_for_its_peers_table_invisible_but_with_its_grants_declared(plugins, env, tmp_path):
    sell, acct = plugins
    reg, channel, log, svc = env
    write(sell.parent / "sell_ws", ws={"schema_version": 1, "name": "sell", "label": "Ventes", "blocks": [{"type": "shortcut", "doctype": "Order"}]})
    channel.load_dir("sell", sell, peers=True)  # Order attend la table « Payment Schedule » d'acct
    assert reg.waiting() == {"Order": ["Payment Schedule"]}
    assert not reg.has("Order")
    with pytest.raises(NotFound):
        await svc.list(ALICE, "Order")
    assert {g["name"] for g in GrantCatalog(reg).for_plugin("sell")} == {"sell:order:read", "sell:order:write", "sell:order:delete"}
    channel.load_workspaces("sell", sell.parent / "sell_ws")  # son espace cite Order : accepté, invisible jusqu'à son arrivée
    channel.load_dir("acct", acct, peers=True)
    assert reg.has("Order") and reg.owner_of("Order") == "sell" and reg.waiting() == {}
    assert reg.incomplete("Order") == []
    created = await svc.create(ALICE, "Order", {"title": "C-1", "schedule": [{"due": "2026-11-01"}]})
    assert created["data"]["schedule"] == [{"due": "2026-11-01"}]


async def test_a_fault_revealed_by_an_arriving_peer_breaks_the_citing_type_not_the_peer(plugins, env):
    sell, acct = plugins
    reg, channel, log, svc = env
    # Le compte d'acct n'a pas le champ `label` que sell copie (fetch_from account.label) : une faute que seule l'arrivée d'acct révèle.
    # Order n'emprunte ici aucune table : il est ENREGISTRÉ (pas en attente) quand acct arrive, c'est le recontrôle des citants qui le juge.
    write(acct, account=dt("Account", "acct"))
    write(sell, order=dt("Order", "sell",
                         {"fieldname": "account", "fieldtype": "Link", "options": "Account"},
                         {"fieldname": "account_label", "fieldtype": "Text", "fetch_from": "account.label"}))
    channel.load_dir("sell", sell, peers=True)
    assert reg.has("Order") and reg.incomplete("Order") == ["DocType 'Account' pas encore chargé"]
    channel.load_dir("acct", acct, peers=True)  # acct se charge : il ne répond pas de la faute de sell
    assert reg.has("Account") and reg.has("Order")
    assert any("account.label" in p for p in reg.incomplete("Order"))
    with pytest.raises(DoctypeError) as e:
        await svc.create(ALICE, "Order", {"title": "C-1"})
    assert e.value.code == "doctype_incomplete"
    assert any(msg == "doctype broken by a peer module's definition" for msg, _ in log.errors)


def test_a_peer_leaving_puts_its_dependents_back_to_wait_and_they_return_with_it(plugins, env):
    sell, acct = plugins
    reg, channel, _, _ = env
    channel.load_dir("sell", sell, peers=True)
    channel.load_dir("acct", acct, peers=True)
    assert reg.has("Order")
    channel.unregister_owner("acct")
    assert not reg.has("Order") and reg.waiting() == {"Order": ["Payment Schedule"]}  # sa table est partie avec acct
    channel.load_dir("acct", acct, peers=True)
    assert reg.has("Order") and reg.owner_of("Order") == "sell" and reg.incomplete("Order") == []
    channel.unregister_owner("acct")
    assert reg.waiting() == {"Order": ["Payment Schedule"]}
    channel.unregister_owner("sell")  # le parent EN ATTENTE part avec son plugin
    assert not reg.has("Order") and reg.waiting() == {}
    channel.load_dir("acct", acct, peers=True)  # et ne revient pas avec un autre
    assert not reg.has("Order")


def test_without_peers_an_unknown_target_is_still_a_load_error(plugins, env):
    sell, acct = plugins
    reg, channel, _, _ = env
    with pytest.raises(InvalidDefinition, match="Order"):
        channel.load_dir("acct", acct)
    assert reg.names() == []


def test_a_typo_never_resolves_and_says_which_target_is_missing(env, tmp_path):
    reg, channel, _, _ = env
    d = write(tmp_path / "x", a=dt("Alpha", "x", {"fieldname": "b", "fieldtype": "Link", "options": "Betaa"}))
    channel.load_dir("x", d, peers=True)
    assert reg.incomplete("Alpha") == ["DocType 'Betaa' pas encore chargé"]


def test_a_waiting_parent_is_still_judged_on_its_module_at_load(env, tmp_path):
    reg, channel, _, _ = env
    d = write(tmp_path / "x", a=dt("Alpha", "other", {"fieldname": "rows", "fieldtype": "Table", "options": "Rows Elsewhere"}))
    with pytest.raises(InvalidDefinition, match="`module` doit être 'x'"):
        channel.load_dir("x", d, peers=True)
    assert reg.waiting() == {}
