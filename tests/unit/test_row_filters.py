"""Filtrer un document sur les LIGNES de sa table enfant (traoreera/doctype#58).

Le cas d'école, celui du ticket : « les bons de livraison dont une ligne cite la commande SO-0042 ». Avant, le moteur ne savait
pas le dire, et `traoreera/erp` tenait sur chaque commande un JSON caché (`erp_progress`) mis à jour à chaque soumission et
décrémenté à chaque annulation, pour la seule raison que les lignes ne se filtraient pas.

La clé s'écrit `items.champ` et le sens est l'EXISTENTIEL : « au moins une ligne vérifie ». Ce que ces tests défendent :

- un document dont DEUX lignes répondent ne paraît qu'UNE fois (un EXISTS, pas une jointure) — la faute la plus facile ici ;
- une table absente, vide, ou dont aucune ligne ne répond ne retient pas le document ;
- les opérateurs sont ceux du TYPE du champ de ligne (`>=` sur un Decimal, `like` sans casse, `in`), appliqués DANS la ligne ;
- `!=` est REFUSÉ, en expliquant pourquoi : « au moins une ligne dont l'article n'est pas X » est vrai d'un bon qui contient
  aussi X, alors que qui l'écrit veut « aucune ligne ne cite X ». On ne choisit pas une lecture en silence ;
- le SQL et le jumeau Python (`memory.py`, pour les DocTypes virtuels) répondent PAREIL : même documents, mêmes filtres ;
- rien d'un autre tenant ne remonte, et le filtre sert aussi aux agrégats et à `tx.find` ;
- les fautes se disent au bon endroit : champ de ligne inconnu, table inconnue, type non filtrable, et le champ Table NU
  reste refusé (filtrer « items » n'a pas de sens, c'est `items.champ` qu'on écrit).

Chaque test tourne sur SQLite et, si configuré, sur PostgreSQL : les deux bases déroulent un tableau JSON avec deux fonctions
différentes (`json_each` / `jsonb_array_elements`), et c'est le seul écart entre elles.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.conftest import make_ctx
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, ValidationFailed, VersionConflict
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.query import StoredDoc
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.sources import DocumentSources, ReadOnlySource, content_version

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)

LINE = {
    "schema_version": 1, "name": "Delivery Line", "istable": True,
    "fields": [
        {"fieldname": "item", "fieldtype": "Text"},
        {"fieldname": "against_sales_order", "fieldtype": "Text"},
        {"fieldname": "warehouse", "fieldtype": "Select", "options": ["Nord", "Sud"]},
        {"fieldname": "qty", "fieldtype": "Number"},
        {"fieldname": "amount", "fieldtype": "Decimal", "decimals": 2},
        {"fieldname": "done", "fieldtype": "Boolean"},
        {"fieldname": "comment", "fieldtype": "TextEditor"},  # FILTER_OPS vide : rien ne s'y filtre
    ],
}
DELIVERY = {
    "schema_version": 1, "name": "Delivery", "module": "shop",
    "fields": [
        {"fieldname": "customer", "fieldtype": "Text"},
        {"fieldname": "items", "fieldtype": "Table", "options": "Delivery Line"},
    ],
}
# Le même type, mais VIRTUEL : ses lignes restent dans le document que la source rend (`schema._source_rules`).
DELIVERY_V = {**DELIVERY, "name": "DeliveryV", "source": "memory", "track_changes": False}

GRANTS = [f"shop:{t}:{a}" for t in ("delivery", "deliveryv") for a in ("read", "write", "delete")]
ALICE = make_ctx("acme", "alice", permissions=GRANTS)
ZOE = make_ctx("globex", "zoe", permissions=GRANTS)

# nom, client, lignes
DELIVERIES: list[tuple[str, str, Any]] = [
    # Deux lignes, une seule répond à SO-0042.
    ("BL-1", "Durand", [
        {"item": "A", "against_sales_order": "SO-0042", "warehouse": "Nord", "qty": 2, "amount": "10.00", "done": True},
        {"item": "B", "against_sales_order": "SO-0007", "warehouse": "Sud", "qty": 5, "amount": "50.00", "done": False},
    ]),
    # Aucune ligne ne répond.
    ("BL-2", "Durand", [{"item": "A", "against_sales_order": "SO-0007", "warehouse": "Nord", "qty": 1, "amount": "5.00", "done": False}]),
    # DEUX lignes répondent : le piège du doublon.
    ("BL-3", "Martin", [
        {"item": "C", "against_sales_order": "SO-0042", "warehouse": "Sud", "qty": 3, "amount": "30.00", "done": True},
        {"item": "D", "against_sales_order": "SO-0042", "warehouse": "Sud", "qty": 4, "amount": "40.00", "done": True},
    ]),
    ("BL-4", "Martin", None),       # pas de table du tout
    ("BL-5", "Sans", []),           # table vide (équivalente à absente)
    ("BL-6", "Creux", [{"item": "E"}]),  # une ligne dont les autres cellules n'existent pas
]


class _Log:
    def info(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...
    def error(self, *a: Any, **k: Any) -> None: ...


class MemorySource(ReadOnlySource):
    """Une source en mémoire : de quoi faire répondre un DocType virtuel sans plugin ni réseau."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, StoredDoc]] = {}
        self._ids = itertools.count(1)

    def put(self, tenant: str, doc: StoredDoc) -> None:
        self.docs.setdefault(tenant, {})[doc.name] = doc

    async def fetch_all(self, ctx, doctype):
        return [d for d in self.docs.get(ctx.tenant_id, {}).values() if d.doctype == doctype.name]

    async def insert(self, ctx, doctype, data):
        name = f"{doctype.name.lower()}-{next(self._ids)}"
        doc = StoredDoc(doctype.name, name, dict(data), ctx.owner_id, NOW, NOW, content_version(data))
        self.put(ctx.tenant_id, doc)
        return doc

    async def update(self, ctx, doctype, name, data, version):
        held = self.docs.get(ctx.tenant_id, {}).get(name)
        if held is None:
            raise NotFound("document_not_found", name)
        if held.version != version:
            raise VersionConflict("périmé")
        doc = StoredDoc(doctype.name, name, dict(data), held.owner, held.creation, NOW, content_version(data))
        self.put(ctx.tenant_id, doc)
        return doc

    async def remove(self, ctx, doctype, name):
        self.docs.get(ctx.tenant_id, {}).pop(name, None)


@pytest.fixture
def source():
    return MemorySource()


@pytest.fixture
def svc(db, tables, source):
    reg = DocTypeRegistry()
    for raw in (LINE, DELIVERY, DELIVERY_V):
        reg.register(parse_definition(raw))
    reg.validate_links()
    sources = DocumentSources()
    sources.register("shop", "memory", source)
    return DocumentService(reg, db, tables, _Log(), clock=lambda: NOW, sources=sources)


class Env:
    """Le service et la traduction « nom tiré par le moteur -> nom lisible », pour que les assertions parlent de BL-3."""

    def __init__(self, svc: DocumentService) -> None:
        self.svc = svc
        self.labels: dict[str, str] = {}

    async def list(self, doctype: str, filters=None, ctx=ALICE, **kw) -> dict[str, Any]:
        return await self.svc.list(ctx, doctype, filters=filters, limit=50, **kw)

    async def names(self, doctype: str, filters, ctx=ALICE) -> list[str]:
        """Les bons que ce filtre retient, par leur nom lisible, triés. Le virtuel porte déjà nos noms."""
        page = await self.list(doctype, filters, ctx)
        return sorted(d["name"] if doctype == "DeliveryV" else self.labels[d["name"]] for d in page["items"])


@pytest.fixture
async def filled(svc, source):
    """Les MÊMES six bons dans `Delivery` (table générique, SQL) et dans `DeliveryV` (source, Python)."""
    env = Env(svc)
    for name, customer, rows in DELIVERIES:
        data: dict[str, Any] = {"customer": customer}
        if rows is not None:
            data["items"] = rows
        made = await svc.create(ALICE, "Delivery", data)
        env.labels[made["name"]] = name
        # Le moteur tire le nom du document stocké ; la source, elle, porte le NÔTRE : les deux réponses se comparent.
        source.put("acme", StoredDoc("DeliveryV", name, dict(made["data"]), made["owner"], NOW, NOW, made["version"]))
    return env


class TestTheRowsAnswer:
    async def test_the_tickets_own_case(self, filled):
        """« Les bons dont une ligne cite SO-0042 » : exactement BL-1 et BL-3."""
        assert await filled.names("Delivery", {"items.against_sales_order": "SO-0042"}) == ["BL-1", "BL-3"]

    async def test_a_document_with_two_matching_rows_appears_once(self, filled):
        """BL-3 a DEUX lignes qui citent SO-0042. Un EXISTS le rend une fois ; une jointure le rendrait deux."""
        page = await filled.list("Delivery", {"items.against_sales_order": "SO-0042"})
        assert len(page["items"]) == 2
        assert page["total"] == 2

    async def test_nothing_matching_nothing(self, filled):
        assert await filled.names("Delivery", {"items.against_sales_order": "SO-9999"}) == []

    async def test_an_absent_or_empty_table_never_matches(self, filled):
        """BL-4 n'a pas de table, BL-5 l'a vide : aucun filtre de ligne ne les retient, pas même une égalité à rien."""
        for filters in ({"items.item": "A"}, {"items.qty": [">=", 0]}, {"items.item": ["like", "A"]}):
            kept = await filled.names("Delivery", filters)
            assert "BL-4" not in kept and "BL-5" not in kept, filters

    async def test_equality_to_nothing_is_refused_here_as_everywhere(self, filled):
        """`{champ: null}` n'est pas du vocabulaire des filtres — le type du champ refuse `None`. Une ligne ne fait pas
        exception : la même écriture est refusée sur un champ du document et sur une cellule, avec la même raison."""
        for key in ("customer", "items.against_sales_order"):
            with pytest.raises(ValidationFailed) as e:
                await filled.list("Delivery", {key: None})
            assert "filtre invalide" in str(e.value)

    @pytest.mark.parametrize(
        "filters,expected",
        [
            ({"items.item": "A"}, ["BL-1", "BL-2"]),
            ({"items.item": ["like", "a"]}, ["BL-1", "BL-2"]),            # sans casse
            ({"items.item": ["in", ["C", "E"]]}, ["BL-3", "BL-6"]),
            ({"items.warehouse": "Sud"}, ["BL-1", "BL-3"]),
            ({"items.qty": [">", 3]}, ["BL-1", "BL-3"]),
            ({"items.qty": 2}, ["BL-1"]),
            ({"items.amount": [">=", "40.00"]}, ["BL-1", "BL-3"]),        # exact, jamais par un flottant
            ({"items.amount": "30.00"}, ["BL-3"]),
            ({"items.done": True}, ["BL-1", "BL-3"]),
            ({"items.done": False}, ["BL-1", "BL-2"]),                     # BL-1 a une ligne de chaque
        ],
    )
    async def test_the_operators_are_those_of_the_row_field(self, filled, filters, expected):
        assert await filled.names("Delivery", filters) == expected

    async def test_it_combines_with_an_ordinary_filter(self, filled):
        """ET, comme tous les filtres : le document doit vérifier les deux."""
        assert await filled.names("Delivery", {"items.against_sales_order": "SO-0042", "customer": "Martin"}) == ["BL-3"]

    async def test_two_row_filters_are_judged_row_by_row(self, filled):
        """Chaque filtre demande « au moins une ligne », pas « la MÊME ligne » : BL-1 a une ligne A et une ligne de 50.00."""
        assert await filled.names("Delivery", {"items.item": "A", "items.amount": "50.00"}) == ["BL-1"]

    async def test_another_tenant_sees_nothing(self, filled):
        assert await filled.names("Delivery", {"items.against_sales_order": "SO-0042"}, ctx=ZOE) == []


class TestWhatItRefuses:
    async def test_not_equal_is_refused_and_says_why(self, filled):
        """`!=` sur une ligne demanderait « aucune ligne », un ∀ que l'existentiel ne dit pas : refusé, pas deviné."""
        with pytest.raises(ValidationFailed) as e:
            await filled.list("Delivery", {"items.item": ["!=", "A"]})
        assert "aucune ligne" in str(e.value)

    @pytest.mark.parametrize("key", ["items.nope", "nope.item", "items.item.deep", "items."])
    async def test_an_unknown_row_field_or_table_is_refused(self, filled, key):
        with pytest.raises(ValidationFailed) as e:
            await filled.list("Delivery", {key: "x"})
        assert "inconnu" in str(e.value)

    async def test_an_unfilterable_row_type_is_refused(self, filled):
        """Un TextEditor ne se filtre pas plus dans une ligne qu'ailleurs (on chercherait dans les balises)."""
        with pytest.raises(ValidationFailed) as e:
            await filled.list("Delivery", {"items.comment": "x"})
        assert "TextEditor" in str(e.value)

    async def test_the_bare_table_field_is_still_refused(self, filled):
        """Filtrer `items` n'a pas de sens : c'est `items.champ` qu'on écrit. La règle d'avant tient."""
        with pytest.raises(ValidationFailed) as e:
            await filled.list("Delivery", {"items": "x"})
        assert "Table" in str(e.value)

    def test_a_link_filter_cannot_name_a_row(self):
        """`link_filters` (D29) restreint ce qu'un Link propose, sur les champs de la CIBLE : une clé pointée n'y a pas de
        sens (la cible n'a pas de champ « items.so ») et le schéma la refuse avant tout le reste. La frontière est tenue."""
        with pytest.raises(Exception) as e:
            parse_definition({
                "schema_version": 1, "name": "Order", "module": "shop",
                "fields": [{"fieldname": "bl", "fieldtype": "Link", "options": "Delivery", "link_filters": {"items.item": "A"}}],
            })
        assert "pattern" in str(e.value)

    async def test_a_row_field_neither_sorts_nor_groups(self, filled):
        """Un filtre, et rien d'autre : trier ou regrouper sur une ligne n'aurait pas de valeur unique à prendre."""
        with pytest.raises(ValidationFailed):
            await filled.list("Delivery", None, order_by="items.qty")
        with pytest.raises(ValidationFailed):
            await filled.svc.aggregate(ALICE, "Delivery", group_by="items.item")


class TestEverywhereFiltersGo:
    async def test_an_aggregate_counts_each_document_once(self, filled):
        """BL-3 a DEUX lignes qui répondent : il compte pour UN. Deux bons retenus, donc 2 — une jointure dirait 3."""
        out = await filled.svc.aggregate(ALICE, "Delivery", group_by="docstatus",
                                         filters={"items.against_sales_order": "SO-0042"})
        assert [(r["key"], r["values"]) for r in out["rows"]] == [(0, [2])]

    async def test_a_hook_finds_by_rows_in_its_transaction(self, db, tables, source):
        """`tx.find` partage la validation des filtres : les lignes y servent sans un mot de plus (M1 + #58)."""
        reg = DocTypeRegistry()
        for raw in (LINE, {**DELIVERY, "submittable": True, "hooks": {"on_submit": ["shop.look"]}}):
            reg.register(parse_definition(raw))
        reg.validate_links()
        seen: list[str] = []
        hooks = HookRegistry()

        async def look(event):
            found = await event.tx.find("Delivery", {"items.against_sales_order": "SO-0042"})
            seen.extend(d["data"]["customer"] for d in found)

        hooks.register("shop.look", look)
        svc = DocumentService(reg, db, tables, _Log(), clock=lambda: NOW, hooks=hooks)
        ctx = make_ctx("acme", "alice", permissions=[*GRANTS, "shop:delivery:submit", "shop:delivery:cancel"])
        kept = await svc.create(ctx, "Delivery", {"customer": "Durand", "items": [{"item": "A", "against_sales_order": "SO-0042"}]})
        await svc.submit(ctx, "Delivery", kept["name"], kept["version"])
        assert seen == ["Durand"]  # le hook voit, dans sa transaction, le document qu'on vient de soumettre


class TestTheTwoInterpretersAgree:
    """Le SQL (`Query._row_cond`) et le Python (`memory._row_matches`) sur les MÊMES documents : changer l'un sans l'autre échoue."""

    @pytest.mark.parametrize(
        "filters",
        [
            {"items.against_sales_order": "SO-0042"},
            {"items.against_sales_order": "SO-9999"},
            {"items.item": "A"},
            {"items.item": ["like", "a"]},
            {"items.item": ["in", ["C", "E"]]},
            {"items.item": ["in", []]},
            {"items.warehouse": "Sud"},
            {"items.qty": [">", 3]},
            {"items.qty": [">=", 2]},
            {"items.qty": ["<", 3]},
            {"items.qty": 2},
            {"items.amount": [">=", "40.00"]},
            {"items.amount": ["<=", "10.00"]},
            {"items.amount": "30.00"},
            {"items.amount": ["in", ["5.00", "30.00"]]},
            {"items.done": True},
            {"items.done": False},
            {"items.item": "A", "items.amount": "50.00"},
            {"items.item": "A", "customer": "Durand"},
        ],
    )
    async def test_same_answer(self, filled, filters):
        assert await filled.names("Delivery", filters) == await filled.names("DeliveryV", filters)

    async def test_the_virtual_one_also_counts_each_document_once(self, filled):
        out = await filled.svc.aggregate(ALICE, "DeliveryV", group_by="docstatus",
                                         filters={"items.against_sales_order": "SO-0042"})
        assert [(r["key"], r["values"]) for r in out["rows"]] == [(0, [2])]
