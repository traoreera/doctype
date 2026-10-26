"""Étiquettes (programme groupe 1, élément 3) : tout se juge sur le document, comme la collaboration.

- les VOIR demande de lire le document (hors périmètre `if_owner` : introuvable) ; en poser ou en retirer, de l'ÉCRIRE (un document soumis
  s'étiquette encore) ;
- une étiquette a une forme repliée (`casefold`) : « urgent » et « Urgent » sont la même, et une écriture déjà en usage est reprise, mais
  seulement depuis un document que l'appelant lit ;
- 20 au plus par document, comptées APRÈS la pose : au-delà, rien de la demande n'est gardé ;
- le filtre `_tag` de la liste est du SQL (`=`, `!=`, `in`), jugé dans le périmètre de lecture, aussi pour les agrégats et la recherche ;
- elles disparaissent avec le document et le suivent quand il est renommé.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import make_ctx
from tests.unit.test_files import MemoryStore, _Log
from xcore_doctype import files as F
from xcore_doctype.collaboration import CollaborationService
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.file_service import FileService
from xcore_doctype.query import Query
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.tags import MAX_TAGS_PER_DOC, normalize_tag

NOTE = {
    "schema_version": 1, "name": "Note", "module": "crm", "naming": "prompt", "allow_rename": True,
    "fields": [{"fieldname": "title", "fieldtype": "Text"}, {"fieldname": "kind", "fieldtype": "Select", "options": ["A", "B"]}],
    "permissions": {
        "read": ["crm:note:read", {"grant": "crm:note:mine", "if_owner": True}],
        "create": ["crm:note:write", "crm:note:own", "crm:note:mine"],
        "write": ["crm:note:write", {"grant": "crm:note:own", "if_owner": True}, {"grant": "crm:note:mine", "if_owner": True}],
        "delete": ["crm:note:write"],
    },
}
INVOICE = {"schema_version": 1, "name": "Invoice", "module": "crm", "submittable": True, "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
PREFS = {"schema_version": 1, "name": "Prefs", "module": "crm", "single": True, "fields": [{"fieldname": "title", "fieldtype": "Text"}]}

HANA = make_ctx("acme", "hana", permissions=["crm:note:read", "crm:note:write", "crm:invoice:read", "crm:invoice:write", "crm:invoice:submit", "crm:prefs:read", "crm:prefs:write"])
OLGA = make_ctx("acme", "olga", permissions=["crm:note:read", "crm:note:own"])  # lit toutes les notes, n'écrit que les siennes
PAUL = make_ctx("acme", "paul", permissions=["crm:note:mine"])  # ne lit (et n'écrit) que les siennes
RITA = make_ctx("acme", "rita", permissions=["crm:note:read"])  # lit tout, n'écrit rien


@pytest.fixture
def env(db, tables):
    reg = DocTypeRegistry()
    for d in (NOTE, INVOICE, PREFS):
        reg.register(parse_definition(d))
    docs = DocumentService(reg, db, tables, _Log())
    files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
    docs.files = files
    return type("Env", (), {"docs": docs, "collab": CollaborationService(docs, files)})


async def note(env, name, ctx=HANA, **data):
    return (await env.docs.create(ctx, "Note", {"title": name, **data}, name=name))["name"]


async def tags(env, name, ctx=HANA):
    return (await env.collab.tags(ctx, "Note", name))["tags"]


async def names(env, ctx=HANA, **filters):
    return [d["name"] for d in (await env.docs.list(ctx, "Note", filters=filters, order_by="name", descending=False))["items"]]


# ── Ce qu'est une étiquette ───────────────────────────────────


def test_a_tag_is_a_short_line_of_text():
    assert normalize_tag("  Salon \t 2026  ") == "Salon 2026"  # bords retirés, blancs intérieurs réduits
    assert normalize_tag("É" * 50) == "É" * 50
    for bad in ("", "   ", "x" * 51, "a,b", "a\x00b", "a\x7fb", 12, None, ["a"]):
        with pytest.raises(ValueError):
            normalize_tag(bad)


async def test_a_request_is_checked_before_anything_is_written(env):
    await note(env, "n1")
    for bad in ({"add": ["a,b"]}, {"add": "Urgent"}, {"add": ["x"] * (MAX_TAGS_PER_DOC + 1)}, {}, {"add": [], "remove": []}):
        with pytest.raises(ValidationFailed) as e:
            await env.collab.tag(HANA, "Note", "n1", **bad)
        assert e.value.code == "invalid_tag"
    assert await tags(env, "n1") == []


# ── Qui les voit, qui les pose ─────────────────────────────────


async def test_seeing_tags_needs_to_read_the_document(env):
    await note(env, "n1")
    await env.collab.tag(HANA, "Note", "n1", add=["Urgent"])
    assert await tags(env, "n1", RITA) == ["Urgent"]
    with pytest.raises(NotFound):  # Paul ne lit que ses notes : celle de Hana n'existe pas pour lui
        await env.collab.tags(PAUL, "Note", "n1")
    assert (await env.collab.tags_of(PAUL, "Note", ["n1", "nope"]))["items"] == {}  # ni dans une page de liste


async def test_adding_or_removing_a_tag_needs_to_write_the_document(env):
    await note(env, "theirs")
    await note(env, "hers", OLGA)
    with pytest.raises(PermissionDenied):
        await env.collab.tag(RITA, "Note", "theirs", add=["Lu"])  # elle lit, elle n'écrit pas
    with pytest.raises(PermissionDenied):
        await env.collab.tag(OLGA, "Note", "theirs", add=["Lu"])  # elle n'écrit que les siennes
    await env.collab.tag(HANA, "Note", "theirs", add=["Urgent"])
    with pytest.raises(PermissionDenied):
        await env.collab.tag(OLGA, "Note", "theirs", remove=["Urgent"])
    assert (await env.collab.tag(OLGA, "Note", "hers", add=["Lu"]))["tags"] == ["Lu"]
    assert ((await env.collab.tags(OLGA, "Note", "theirs"))["can_tag"], (await env.collab.tags(OLGA, "Note", "hers"))["can_tag"]) == (False, True)
    assert await tags(env, "theirs") == ["Urgent"]


async def test_a_submitted_document_is_still_tagged(env):
    made = await env.docs.create(HANA, "Invoice", {"title": "f"})
    await env.docs.submit(HANA, "Invoice", made["name"], made["version"])
    assert (await env.collab.tag(HANA, "Invoice", made["name"], add=["Payée"]))["tags"] == ["Payée"]
    assert [d["name"] for d in (await env.docs.list(HANA, "Invoice", filters={"_tag": "payée"}))["items"]] == [made["name"]]


async def test_a_single_document_has_no_tags(env):
    with pytest.raises(ValidationFailed) as e:
        await env.collab.tag(HANA, "Prefs", "Prefs", add=["x"])
    assert e.value.code == "tags_not_supported"
    with pytest.raises(ValidationFailed):  # et sa « liste » ne connaît pas le filtre
        await env.docs.list(HANA, "Prefs", filters={"_tag": "x"})


# ── Une étiquette, quelle que soit la casse ─────────────────────


async def test_case_never_makes_two_tags_and_the_spelling_in_use_is_kept(env):
    await note(env, "n1")
    await note(env, "n2")
    await env.collab.tag(HANA, "Note", "n1", add=["Urgent"])
    assert (await env.collab.tag(HANA, "Note", "n1", add=["URGENT", "urgent"]))["tags"] == ["Urgent"]  # déjà là : rien
    assert (await env.collab.tag(HANA, "Note", "n2", add=["urgent"]))["tags"] == ["Urgent"]  # l'écriture en usage
    assert (await env.collab.tag(HANA, "Note", "n1", remove=["uRgEnT"]))["tags"] == []
    assert (await env.collab.tag(HANA, "Note", "n1", remove=["absente"]))["tags"] == []  # retirer une absente ne fait rien


async def test_the_spelling_of_a_document_one_cannot_read_is_never_borrowed(env):
    await note(env, "hanas")
    await env.collab.tag(HANA, "Note", "hanas", add=["Projet X"])
    await note(env, "pauls", PAUL)
    # Reprendre « Projet X » dirait à Paul qu'une note qu'il ne lit pas porte cette étiquette.
    assert (await env.collab.tag(PAUL, "Note", "pauls", add=["projet x"]))["tags"] == ["projet x"]


# ── Le plafond ─────────────────────────────────────────────────


async def test_at_most_twenty_tags_and_a_refused_request_keeps_nothing(env):
    await note(env, "n1")
    await env.collab.tag(HANA, "Note", "n1", add=[f"t{i}" for i in range(MAX_TAGS_PER_DOC - 1)])
    with pytest.raises(ValidationFailed) as e:
        await env.collab.tag(HANA, "Note", "n1", add=["avant-dernière", "de trop"], remove=["t0"])  # 19 - 1 + 2 = 20 : passe
        await env.collab.tag(HANA, "Note", "n1", add=["vingt et une"])
    assert e.value.code == "too_many_tags"
    assert len(await tags(env, "n1")) == MAX_TAGS_PER_DOC and "vingt et une" not in await tags(env, "n1")
    with pytest.raises(ValidationFailed):  # le retrait de la même demande est défait aussi
        await env.collab.tag(HANA, "Note", "n1", add=["a", "b"], remove=["t1"])
    assert "t1" in await tags(env, "n1")


async def test_two_taggers_at_the_cap_never_make_twenty_one(env, db, monkeypatch):
    """Le plafond se compte après la pose, sous le verrou du document (`FOR UPDATE`) : à 19, deux poses simultanées ne passent pas toutes les deux.
    SQLite n'a pas de verrou de ligne (un seul écrivain à la fois) : l'entrelacement précis n'est exigé que sur PostgreSQL."""
    if db.backend != "postgres":
        pytest.skip("SQLite n'a pas de verrou de ligne : un seul écrivain à la fois")
    await note(env, "n1")
    await env.collab.tag(HANA, "Note", "n1", add=[f"t{i}" for i in range(MAX_TAGS_PER_DOC - 1)])
    posed, proceed = asyncio.Event(), asyncio.Event()
    real_add = Query.add_tag

    async def paused_add(self, doctype, docname, tag):
        added = await real_add(self, doctype, docname, tag)
        if tag == "premier":
            posed.set()  # posée, pas encore comptée ni validée
            await proceed.wait()
        return added

    monkeypatch.setattr(Query, "add_tag", paused_add)
    first = asyncio.create_task(env.collab.tag(HANA, "Note", "n1", add=["premier"]))
    second = None
    try:
        await asyncio.wait_for(posed.wait(), 5)
        second = asyncio.create_task(env.collab.tag(HANA, "Note", "n1", add=["second"]))
        await asyncio.sleep(0.5)
        assert not second.done(), "la seconde pose aurait dû attendre le verrou du document"
    finally:
        proceed.set()  # même en cas d'échec : une transaction restée ouverte bloquerait le nettoyage de la base
    assert len((await first)["tags"]) == MAX_TAGS_PER_DOC
    with pytest.raises(ValidationFailed) as e:
        await second
    assert e.value.code == "too_many_tags"
    assert len(await tags(env, "n1")) == MAX_TAGS_PER_DOC


# ── Le filtre `_tag` de la liste ───────────────────────────────


async def test_the_list_filters_on_tags_in_sql(env):
    for n in ("n1", "n2", "n3"):
        await note(env, n)
    await env.collab.tag(HANA, "Note", "n1", add=["Urgent", "Client"])
    await env.collab.tag(HANA, "Note", "n2", add=["Client"])
    assert await names(env, _tag="urgent") == ["n1"]  # la casse ne compte pas
    assert await names(env, _tag=["!=", "Urgent"]) == ["n2", "n3"]  # sans l'étiquette, y compris sans aucune
    assert await names(env, _tag=["in", ["urgent", "client"]]) == ["n1", "n2"]
    assert await names(env, _tag="Client", title="n2") == ["n2"]  # avec les autres filtres
    assert (await env.docs.aggregate(HANA, "Note", filters={"_tag": "Client"}))["rows"] == [{"key": None, "values": [2]}]  # les agrégats aussi
    found = await env.docs.search(HANA, "Note", "n", filters={"_tag": "Urgent"})
    assert [d["name"] for d in found["items"]] == ["n1"]
    for bad in ("", "a,b", ["like", "Urg"], ["in", []], ["in", "Urgent"], [">", "a"], 3):
        with pytest.raises(ValidationFailed):
            await names(env, _tag=bad)


async def test_the_tag_filter_stays_inside_the_read_scope(env):
    await note(env, "hanas")
    await note(env, "pauls", PAUL)
    for n in ("hanas", "pauls"):
        await env.collab.tag(HANA, "Note", n, add=["Commun"])
    assert await names(env, PAUL, _tag="Commun") == ["pauls"]
    assert await names(env, PAUL, _tag=["!=", "Commun"]) == []
    assert (await env.collab.tag_counts(PAUL, "Note"))["items"] == [{"tag": "Commun", "count": 1}]


# ── Les comptes (barre latérale, propositions) ─────────────────


async def test_counts_follow_the_list_filters_and_the_typed_text(env):
    for n, kind in (("n1", "A"), ("n2", "A"), ("n3", "B")):
        await note(env, n, kind=kind)
    await env.collab.tag(HANA, "Note", "n1", add=["Urgent", "Client"])
    await env.collab.tag(HANA, "Note", "n2", add=["Client"])
    await env.collab.tag(HANA, "Note", "n3", add=["Client", "Écrit"])
    assert (await env.collab.tag_counts(HANA, "Note"))["items"] == [
        {"tag": "Client", "count": 3}, {"tag": "Urgent", "count": 1}, {"tag": "Écrit", "count": 1},
    ]
    assert (await env.collab.tag_counts(HANA, "Note", filters={"kind": "A"}))["items"] == [{"tag": "Client", "count": 2}, {"tag": "Urgent", "count": 1}]
    assert (await env.collab.tag_counts(HANA, "Note", filters={"_tag": "Urgent"}))["items"] == [{"tag": "Client", "count": 1}, {"tag": "Urgent", "count": 1}]
    assert (await env.collab.tag_counts(HANA, "Note", q="ÉCR"))["items"] == [{"tag": "Écrit", "count": 1}]  # repli Unicode, pas seulement ASCII
    assert (await env.collab.tag_counts(HANA, "Note", q="%"))["items"] == []  # un joker est un caractère
    assert (await env.collab.tag_counts(HANA, "Note", limit=1))["items"] == [{"tag": "Client", "count": 3}]
    with pytest.raises(ValidationFailed):
        await env.collab.tag_counts(HANA, "Note", limit=0)


# ── En lot ─────────────────────────────────────────────────────


async def test_tagging_a_selection_judges_each_document_alone(env):
    await note(env, "theirs")
    await note(env, "hers", OLGA)
    result = await env.collab.tag_many(OLGA, "Note", ["hers", "theirs", "hers", "nope"], add=["Lot"])
    assert [(r["name"], r["ok"], r.get("code")) for r in result["results"]] == [
        ("hers", True, None), ("theirs", False, "permission_denied"), ("nope", False, "document_not_found"),
    ]
    assert (result["done"], result["failed"]) == (1, 2)
    assert await tags(env, "hers") == ["Lot"] and await tags(env, "theirs") == []
    with pytest.raises(PermissionDenied):  # sans droit d'écrire le type : refusé une fois, d'abord
        await env.collab.tag_many(RITA, "Note", ["theirs"], add=["Lot"])


# ── La vie du document ─────────────────────────────────────────


async def test_tags_follow_a_rename_and_vanish_with_the_document(env):
    await note(env, "old")
    await env.collab.tag(HANA, "Note", "old", add=["Suivie"])
    await env.docs.rename(HANA, "Note", "old", "renamed")
    assert await tags(env, "renamed") == ["Suivie"] and await names(env, _tag="Suivie") == ["renamed"]
    await env.docs.delete(HANA, "Note", "renamed")
    assert (await env.collab.tag_counts(HANA, "Note"))["items"] == []
    await note(env, "renamed")  # un document qui reprend le nom n'hérite de rien
    assert await tags(env, "renamed") == []
