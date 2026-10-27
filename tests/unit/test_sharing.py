"""Permissions utilisateur et partage (groupe 2 du programme, `process/programme.md` §2.1), de bout en bout par le service.

- un PARTAGE ouvre UN document à un membre, en lecture ou en écriture, même sans rôle sur le type et malgré ses restrictions ; jamais la
  suppression ; seul qui ÉCRIT le document partage ; le membre peut renoncer au sien ; « Partagés avec moi » ; il suit le document ;
- une PERMISSION UTILISATEUR (document `User Permission`, plugin `system`) resserre ses rôles : sur le type visé et sur tout type qui le cite par
  un Lien (un lien vide passe), à la lecture comme à l'écriture ; ses hooks la gardent cohérente et traduisent l'e-mail du membre.

La matrice complète (rôles × restrictions × partages, SQL et Python contre un oracle) est dans `test_permissions_matrix.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.conftest import make_ctx
from tests.unit.test_files import MemoryStore, _Log
from xcore_doctype import files as F
from xcore_doctype.collaboration import CollaborationService
from xcore_doctype.directory import Person
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import DocumentValidationError, NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.file_service import FileService
from xcore_doctype.hooks import HookRegistry
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition
from xcore_doctype.user_permissions import make_user_permission_hooks

SYSTEM = Path(__file__).resolve().parents[2] / "src" / "xcore_doctype" / "system_plugin_dir" / "doctypes"
WAREHOUSE = {"schema_version": 1, "name": "Warehouse", "module": "shop", "naming": "prompt", "allow_rename": True, "title_field": "label",
             "fields": [{"fieldname": "label", "fieldtype": "Text"}]}
MOVE = {"schema_version": 1, "name": "Move", "module": "shop", "title_field": "title",
        "fields": [{"fieldname": "title", "fieldtype": "Text"}, {"fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse"}]}
NOTE = {"schema_version": 1, "name": "Note", "module": "shop", "title_field": "title", "fields": [{"fieldname": "title", "fieldtype": "Text"}],
        "permissions": {"read": [{"grant": "shop:note:own", "if_owner": True}, "shop:note:read"], "write": [{"grant": "shop:note:own", "if_owner": True}, "shop:note:write"],
                        "create": ["shop:note:own", "shop:note:write"], "delete": ["shop:note:write"]}}
PREFS = {"schema_version": 1, "name": "Prefs", "module": "shop", "single": True, "fields": [{"fieldname": "a", "fieldtype": "Text"}]}

ALL_SHOP = [f"shop:{t}:{a}" for t in ("warehouse", "move", "note", "prefs") for a in ("read", "write", "delete")]
ADMIN = make_ctx("acme", "admin-id", permissions=[*ALL_SHOP, "system:user_permission:read", "system:user_permission:write", "system:user_permission:delete"])
HANA = make_ctx("acme", "hana-id", permissions=["shop:note:read", "shop:note:write", "shop:note:delete"])
OLGA = make_ctx("acme", "olga-id", permissions=["shop:note:own"])  # ne lit et n'écrit que ses notes
SAM = make_ctx("acme", "sam-id", permissions=[])  # aucun rôle sur rien
AWA = make_ctx("acme", "awa-id", permissions=[f"shop:{t}:{a}" for t in ("warehouse", "move") for a in ("read", "write")])
MEMBERS = [Person("hana-id", "hana@acme.test"), Person("olga-id", "olga@acme.test"), Person("sam-id", "sam@acme.test"),
           Person("awa-id", "awa@acme.test"), Person("admin-id", "admin@acme.test")]


class Directory:
    def __init__(self, people):
        self.people = people

    async def members(self, tenant_id):
        return self.people


class Notifier:
    def __init__(self):
        self.sent = []

    async def notify(self, tenant_id, user_ids, *, title, message=None, link=None, kind="DOCUMENT"):
        self.sent.append((list(user_ids), title, kind))


def _system(name: str) -> dict:
    return yaml.safe_load((SYSTEM / name).read_text())


@pytest.fixture
def env(db, tables):
    return _env(db, tables, Directory(MEMBERS))


def _env(db, tables, directory):
    reg = DocTypeRegistry()
    for d in (WAREHOUSE, MOVE, NOTE, PREFS, _system("user_permission.yaml")):
        reg.register(parse_definition(d))
    hooks = HookRegistry()
    docs = DocumentService(reg, db, tables, _Log(), hooks=hooks)
    for name, hook in make_user_permission_hooks(docs, directory).items():
        hooks.register(name, hook)
    files = FileService(docs, MemoryStore(), F.UrlSigner("s" * 32))
    docs.files = files
    notifier = Notifier()
    return type("Env", (), {"docs": docs, "collab": CollaborationService(docs, files, directory, notifier), "notifier": notifier})


async def outcome(coro):
    try:
        await coro
    except PermissionDenied:
        return "403"
    except NotFound:
        return "404"
    return "ok"


# ── Partage ────────────────────────────────────────────────────


async def test_a_share_opens_one_document_without_any_role(env):
    note = await env.docs.create(HANA, "Note", {"title": "secret"})
    other = await env.docs.create(HANA, "Note", {"title": "autre"})
    assert await outcome(env.docs.get(SAM, "Note", note["name"])) == "404"  # rien : introuvable, comme un nom qui n'existe pas
    await env.collab.share(HANA, "Note", note["name"], member="sam@acme.test")  # par e-mail : l'annuaire le traduit
    assert (await env.docs.get(SAM, "Note", note["name"]))["data"]["title"] == "secret"
    assert await outcome(env.docs.get(SAM, "Note", other["name"])) == "404"  # CE document seulement
    assert await outcome(env.docs.list(SAM, "Note")) == "403"  # pas la liste du type : ses partages sont dans « Partagés avec moi »
    assert await outcome(env.docs.update(SAM, "Note", note["name"], {"title": "x"}, note["version"])) == "403"  # en lecture seulement
    assert await outcome(env.docs.delete(SAM, "Note", note["name"])) == "403"
    meta = await env.docs.fetch_meta(SAM, "Note")  # sa fiche s'ouvre : elle a besoin de la définition
    assert (meta["rights"]["read"], meta["rights"]["write"], meta["rights"]["delete"]) == ("restricted", "none", "none")
    assert env.notifier.sent[-1][0] == ["sam-id"] and "partagé avec vous" in env.notifier.sent[-1][1]


async def test_a_write_share_lets_one_edit_but_never_delete(env):
    note = await env.docs.create(HANA, "Note", {"title": "brouillon"})
    await env.collab.share(HANA, "Note", note["name"], member="sam-id", write=True)
    done = await env.docs.update(SAM, "Note", note["name"], {"title": "relu"}, note["version"])
    assert done["data"]["title"] == "relu"
    assert (await env.docs.fetch_meta(SAM, "Note"))["rights"]["write"] == "restricted"
    assert await outcome(env.docs.delete(SAM, "Note", note["name"])) == "403"
    # Écrire le document, c'est aussi ce qui l'entoure : il peut joindre et assigner (le même SQL que la modification).
    assert (await env.collab.get(SAM, "Note", note["name"]))["can_attach"] is True


async def test_a_share_widens_an_owner_only_role(env):
    """Olga ne voit que ses notes (`if_owner`) : une note de Hana partagée avec elle s'ajoute à SA liste."""
    mine = await env.docs.create(OLGA, "Note", {"title": "à moi"})
    theirs = await env.docs.create(HANA, "Note", {"title": "à Hana"})
    await env.docs.create(HANA, "Note", {"title": "privée"})
    await env.collab.share(HANA, "Note", theirs["name"], member="olga-id")
    listed = sorted(d["name"] for d in (await env.docs.list(OLGA, "Note", limit=50))["items"])
    assert listed == sorted([mine["name"], theirs["name"]])


async def test_only_who_writes_the_document_shares_it(env):
    note = await env.docs.create(HANA, "Note", {"title": "n"})
    await env.collab.share(HANA, "Note", note["name"], member="olga-id")
    with pytest.raises(PermissionDenied):  # elle le lit (partage), elle ne l'écrit pas : elle ne le partage pas plus loin
        await env.collab.share(OLGA, "Note", note["name"], member="sam-id")
    with pytest.raises(PermissionDenied):
        await env.collab.unshare(OLGA, "Note", note["name"], member="hana-id")
    seen = await env.collab.shares(OLGA, "Note", note["name"])
    assert seen["can_share"] is False and [i["member"] for i in seen["items"]] == ["olga-id"]  # le sien seulement
    await env.collab.unshare(OLGA, "Note", note["name"], member="olga-id")  # mais elle peut y renoncer
    assert await outcome(env.docs.get(OLGA, "Note", note["name"])) == "404"
    with pytest.raises(ValidationFailed) as e:
        await env.collab.share(HANA, "Note", note["name"], member="hana-id")
    assert e.value.code == "share_self"
    with pytest.raises(ValidationFailed) as e:
        await env.collab.share(HANA, "Note", note["name"], member="inconnu@ailleurs.test")
    assert e.value.code == "unknown_user"


async def test_sharing_again_changes_the_level(env):
    note = await env.docs.create(HANA, "Note", {"title": "n"})
    await env.collab.share(HANA, "Note", note["name"], member="sam-id", write=True)
    await env.collab.share(HANA, "Note", note["name"], member="sam-id", write=False)
    assert [(i["member"], i["write"]) for i in (await env.collab.shares(HANA, "Note", note["name"]))["items"]] == [("sam-id", False)]
    assert await outcome(env.docs.update(SAM, "Note", note["name"], {"title": "x"}, note["version"])) == "403"


async def test_shared_with_me_follows_the_document(env):
    note = await env.docs.create(HANA, "Note", {"title": "Compte rendu"})
    await env.collab.share(HANA, "Note", note["name"], member="sam-id", write=True)
    mine = (await env.collab.shared_with_me(SAM))["items"]
    assert [(i["doctype"], i["name"], i["title"], i["write"], i["owner"]) for i in mine] == [("Note", note["name"], "Compte rendu", True, "hana-id")]
    await env.docs.delete(HANA, "Note", note["name"])
    assert (await env.collab.shared_with_me(SAM))["items"] == []  # parti avec le document


async def test_a_single_document_is_not_shared(env):
    with pytest.raises(ValidationFailed) as e:
        await env.collab.share(ADMIN, "Prefs", "Prefs", member="sam-id")
    assert e.value.code == "share_not_supported"


# ── Permissions utilisateur ────────────────────────────────────


async def restrict(env, member, allow, value, applies_to=None):
    data = {"member": member, "allow": allow, "for_value": value, **({"applies_to": applies_to} if applies_to else {})}
    return await env.docs.create(ADMIN, "User Permission", data)


async def seed_moves(env):
    for w in ("W1", "W2"):
        await env.docs.create(ADMIN, "Warehouse", {"label": w}, name=w)
    names = {}
    for title, w in (("in-1", "W1"), ("in-2", "W2"), ("none", None)):
        names[title] = (await env.docs.create(ADMIN, "Move", {"title": title, **({"warehouse": w} if w else {})}))["name"]
    return names


async def titles(env, ctx, doctype, field="title"):
    return sorted(d["data"].get(field) for d in (await env.docs.list(ctx, doctype, limit=50))["items"])


async def test_a_restriction_narrows_the_type_and_every_type_that_links_to_it(env):
    await seed_moves(env)
    up = await restrict(env, "awa@acme.test", "Warehouse", "W1")
    assert up["data"]["user"] == "awa-id"  # l'e-mail traduit par l'annuaire : c'est l'identifiant que le moteur compare
    assert await titles(env, AWA, "Warehouse", "label") == ["W1"]
    assert await titles(env, AWA, "Move") == ["in-1", "none"]  # un lien vide passe
    assert await titles(env, ADMIN, "Move") == ["in-1", "in-2", "none"]  # les autres ne sont pas touchés
    # À l'écriture : on ne crée ni ne déplace hors de ce qu'on voit (le lien vers W2 est d'abord un lien qu'on ne lit pas).
    with pytest.raises((PermissionDenied, ValidationFailed)):
        await env.docs.create(AWA, "Move", {"title": "x", "warehouse": "W2"})
    created = await env.docs.create(AWA, "Move", {"title": "ok", "warehouse": "W1"})
    with pytest.raises((PermissionDenied, ValidationFailed)):
        await env.docs.update(AWA, "Move", created["name"], {"title": "ok", "warehouse": "W2"}, created["version"])
    # Deux valeurs permises = deux documents.
    await restrict(env, "awa-id", "Warehouse", "W2")
    assert await titles(env, AWA, "Warehouse", "label") == ["W1", "W2"]


async def test_applies_to_bounds_a_restriction_to_one_type(env):
    await seed_moves(env)
    await restrict(env, "awa-id", "Warehouse", "W1", applies_to="Move")
    assert await titles(env, AWA, "Move") == ["in-1", "none"]
    assert await titles(env, AWA, "Warehouse", "label") == ["W1", "W2"]  # le type lui-même n'est pas concerné
    # W2 reste LISIBLE (le lien se vérifie), mais un mouvement vers W2 sort de ce qu'elle voit : la valeur écrite est refusée (403),
    # par l'interprète Python du périmètre (`Scope.allows` avec les restrictions chargées).
    with pytest.raises(PermissionDenied):
        await env.docs.create(AWA, "Move", {"title": "x", "warehouse": "W2"})
    made = await env.docs.create(AWA, "Move", {"title": "ok", "warehouse": "W1"})
    with pytest.raises(PermissionDenied):
        await env.docs.update(AWA, "Move", made["name"], {"title": "ok", "warehouse": "W2"}, made["version"])


async def test_a_share_passes_over_a_restriction(env):
    moves = await seed_moves(env)
    await restrict(env, "awa-id", "Warehouse", "W1")
    assert await outcome(env.docs.get(AWA, "Move", moves["in-2"])) == "404"
    await env.collab.share(ADMIN, "Move", moves["in-2"], member="awa-id")
    assert (await env.docs.get(AWA, "Move", moves["in-2"]))["data"]["title"] == "in-2"
    assert await titles(env, AWA, "Move") == ["in-1", "in-2", "none"]


async def test_a_restriction_hides_what_surrounds_a_document_too(env):
    moves = await seed_moves(env)
    await restrict(env, "awa-id", "Warehouse", "W1")
    with pytest.raises(NotFound):
        await env.collab.add_comment(AWA, "Move", moves["in-2"], "je ne devrais pas")
    with pytest.raises(NotFound):
        await env.docs.history(AWA, "Move", moves["in-2"])


async def test_a_user_permission_is_checked_when_written(env):
    await seed_moves(env)
    bad = [
        ({"member": "awa-id", "allow": "Nope", "for_value": "W1"}, "allow"),
        ({"member": "awa-id", "allow": "Prefs", "for_value": "Prefs"}, "allow"),  # un réglage : rien à restreindre
        ({"member": "awa-id", "allow": "Warehouse", "for_value": "W9"}, "for_value"),
        ({"member": "awa-id", "allow": "Warehouse", "for_value": "W1", "applies_to": "Note"}, "applies_to"),  # Note ne cite pas d'entrepôt
        ({"member": "personne@acme.test", "allow": "Warehouse", "for_value": "W1"}, "member"),
    ]
    for data, field in bad:
        with pytest.raises(DocumentValidationError) as e:
            await env.docs.create(ADMIN, "User Permission", data)
        assert e.value.details[0]["field"] == field, data
    # Le client ne choisit pas l'identifiant comparé : le hook le recalcule depuis `member`.
    made = await env.docs.create(ADMIN, "User Permission", {"member": "awa@acme.test", "allow": "Warehouse", "for_value": "W1"})
    assert made["data"]["user"] == "awa-id"


async def test_without_a_directory_the_member_is_the_identity(db, tables):
    env = _env(db, tables, Directory(None))
    await seed_moves(env)
    made = await restrict(env, "awa-id", "Warehouse", "W1")
    assert made["data"]["user"] == "awa-id"
    assert await titles(env, AWA, "Warehouse", "label") == ["W1"]


async def test_restrictions_and_shares_follow_a_rename(env):
    await env.docs.create(ADMIN, "Warehouse", {"label": "a"}, name="old")
    await env.collab.share(ADMIN, "Warehouse", "old", member="sam-id")
    await env.docs.rename(ADMIN, "Warehouse", "old", "fresh")
    assert (await env.docs.get(SAM, "Warehouse", "fresh"))["name"] == "fresh"
    assert [i["name"] for i in (await env.collab.shared_with_me(SAM))["items"]] == ["fresh"]


# ── Constats de la relecture adversariale (chacun échoue sans sa correction) ──


async def test_a_restricted_user_cannot_create_a_forbidden_name_nor_probe_one(env):
    """Awa ne voit que l'entrepôt W1 : elle ne crée pas « W3 » (hors de ce qu'elle voit) et ne sonde pas « W2 » par un 409 (existe déjà)."""
    await seed_moves(env)
    await restrict(env, "awa-id", "Warehouse", "W1")
    with pytest.raises(PermissionDenied):
        await env.docs.create(AWA, "Warehouse", {"label": "x"}, name="W3")
    with pytest.raises(PermissionDenied):  # 403 comme pour W3, jamais « existe déjà »
        await env.docs.create(AWA, "Warehouse", {"label": "x"}, name="W2")


async def test_a_bounded_user_is_not_told_they_see_everything(env):
    """`/meta` disait « all » à Awa sur Move (son rôle n'a pas de condition) alors qu'une permission utilisateur la borne."""
    await seed_moves(env)
    assert (await env.docs.fetch_meta(AWA, "Move"))["rights"]["read"] == "all"
    await restrict(env, "awa-id", "Warehouse", "W1")
    rights = (await env.docs.fetch_meta(AWA, "Move"))["rights"]
    assert (rights["read"], rights["write"]) == ("restricted", "restricted")


async def test_a_bounded_user_does_not_set_organisation_form_rules(env):
    """Les règles d'un habillage (obligatoire, lecture seule, défaut) lient tout le monde : seul qui écrit TOUT le type les pose."""
    await seed_moves(env)
    admin_like = make_ctx("acme", "awa-id", permissions=ALL_SHOP)
    await env.docs.save_form_override(admin_like, "Move", {"fields": {"title": {"read_only": True}}})  # sans restriction : permis
    await restrict(env, "awa-id", "Warehouse", "W1")
    with pytest.raises(ValidationFailed) as e:
        await env.docs.save_form_override(admin_like, "Move", {"fields": {"title": {"read_only": False}}})
    assert e.value.code == "form_rules_reserved"


async def test_an_address_never_becomes_an_identity_when_the_directory_is_down(db, tables):
    env = _env(db, tables, Directory(None))
    await seed_moves(env)
    with pytest.raises(DocumentValidationError) as e:
        await restrict(env, "awa@acme.test", "Warehouse", "W1")  # garder « awa@… » comme identifiant ne restreindrait personne, en silence
    assert e.value.details[0]["field"] == "member"


async def test_a_share_racing_a_deletion_never_outlives_the_document(env, monkeypatch):
    """Sans verrou ni relecture, le partage écrit après la suppression restait, et ouvrait le PROCHAIN document de ce nom au membre."""
    import asyncio

    from xcore_doctype.query import Query

    await env.docs.create(ADMIN, "Warehouse", {"label": "old"}, name="W9")
    reached, proceed = asyncio.Event(), asyncio.Event()
    real = Query.put_share

    async def paused(self, *a, **kw):
        if not proceed.is_set():
            reached.set()
            await proceed.wait()
        return await real(self, *a, **kw)

    monkeypatch.setattr(Query, "put_share", paused)
    sharing = asyncio.create_task(env.collab.share(ADMIN, "Warehouse", "W9", member="sam-id"))
    deleting = None
    try:
        await asyncio.wait_for(reached.wait(), 5)
        deleting = asyncio.create_task(env.docs.delete(ADMIN, "Warehouse", "W9"))
        await asyncio.sleep(0.3)  # PostgreSQL : la suppression attend notre verrou ; SQLite : elle passe, la pose est défaite
    finally:
        proceed.set()
    outcome_ = (await asyncio.gather(sharing, return_exceptions=True))[0]
    await deleting
    assert not isinstance(outcome_, BaseException) or isinstance(outcome_, NotFound)
    await env.docs.create(ADMIN, "Warehouse", {"label": "un autre, secret"}, name="W9")
    assert await outcome(env.docs.get(SAM, "Warehouse", "W9")) == "404"
    assert (await env.collab.shared_with_me(SAM))["items"] == []


async def test_a_write_share_neither_renames_nor_shares_further(env):
    """Un partage en écriture laisse modifier CE document : ni le renommer (qui réécrit ceux qui le citent), ni l'ouvrir à d'autres."""
    await env.docs.create(ADMIN, "Warehouse", {"label": "a"}, name="R1")
    await env.collab.share(ADMIN, "Warehouse", "R1", member="sam-id", write=True)
    await env.collab.share(ADMIN, "Warehouse", "R1", member="awa-id")
    with pytest.raises(PermissionDenied):
        await env.docs.rename(SAM, "Warehouse", "R1", "R2")
    with pytest.raises(PermissionDenied):
        await env.collab.share(SAM, "Warehouse", "R1", member="hana-id")
    with pytest.raises(PermissionDenied):
        await env.collab.unshare(SAM, "Warehouse", "R1", member="awa-id")
    assert (await env.collab.shares(SAM, "Warehouse", "R1"))["can_share"] is False
    await env.collab.unshare(SAM, "Warehouse", "R1", member="sam-id")  # le sien, toujours


async def test_the_files_of_a_shared_document_open_to_its_member(env):
    """Une pièce jointe se lit comme son document : partagée avec lui, elle s'ouvre à lui (lien signé), et à personne d'autre."""
    note = await env.docs.create(HANA, "Note", {"title": "avec pièce"})
    att = await env.collab.attach(HANA, "Note", note["name"], content=b"bonjour", filename="a.txt", content_type="text/plain")
    assert await env.docs.files.links(SAM, [att["file_id"]]) == {}
    await env.collab.share(HANA, "Note", note["name"], member="sam-id")
    assert att["file_id"] in await env.docs.files.links(SAM, [att["file_id"]])
