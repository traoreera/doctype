"""Ce que fait un profil : `@authenticated` (tout utilisateur identifié, sans rôle ni grant) et `naming: owner` (un document par utilisateur, nommé comme lui)."""

from __future__ import annotations

import pytest

from tests.conftest import make_ctx
from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import AlreadyExists, ContextError, InvalidDefinition, NotFound, PermissionDenied
from xcore_doctype.permissions import scope_for
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

PROFILE = {
    "schema_version": 1, "name": "Profile", "module": "iam", "naming": "owner",
    "fields": [{"fieldname": "full_name", "fieldtype": "Text", "required": True}, {"fieldname": "phone", "fieldtype": "Text"}],
    "permissions": {
        "read": ["@authenticated"],
        "create": ["@authenticated"],
        "write": ["iam:profile:write", {"grant": "@authenticated", "if_owner": True}],
        "delete": ["iam:profile:delete"],
    },
}
NOBODY = lambda user: make_ctx("acme", user, permissions=[])  # noqa: E731 - un membre sans le moindre grant
ANONYMOUS = RequestContext("acme", None, "http")


@pytest.fixture
def svc(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition(PROFILE))

    class Log:
        def info(self, *a, **k): ...
        def error(self, *a, **k): ...

    return DocumentService(reg, db, tables, Log())


async def test_a_member_without_any_grant_creates_reads_and_edits_only_their_own_profile(svc):
    alice, bob = NOBODY("alice"), NOBODY("bob")
    mine = await svc.create(alice, "Profile", {"full_name": "Alice A."})
    assert mine["name"] == "alice" and mine["owner"] == "alice"  # nommé comme son propriétaire
    await svc.create(bob, "Profile", {"full_name": "Bob B."})
    with pytest.raises(AlreadyExists):
        await svc.create(alice, "Profile", {"full_name": "Encore"})  # un seul par utilisateur
    assert {i["name"] for i in (await svc.list(bob, "Profile"))["items"]} == {"alice", "bob"}  # l'annuaire des collègues
    done = await svc.update(alice, "Profile", "alice", {"full_name": "Alice Aubert", "phone": "0102030405"}, mine["version"])
    assert done["data"]["full_name"] == "Alice Aubert"
    with pytest.raises(PermissionDenied):
        await svc.update(bob, "Profile", "alice", {"full_name": "Piraté"}, done["version"])  # il la LIT, il ne l'écrit pas
    with pytest.raises(PermissionDenied):
        await svc.delete(alice, "Profile", "alice")  # supprimer exige un vrai grant


async def test_a_grant_still_opens_what_the_authenticated_rule_does_not(svc):
    boss = make_ctx("acme", "boss", permissions=["iam:profile:write", "iam:profile:delete"])
    await svc.create(NOBODY("alice"), "Profile", {"full_name": "Alice"})
    got = await svc.get(boss, "Profile", "alice")
    edited = await svc.update(boss, "Profile", "alice", {"full_name": "Alice (RH)"}, got["version"])
    assert edited["data"]["full_name"] == "Alice (RH)"
    await svc.delete(boss, "Profile", "alice")
    with pytest.raises(NotFound):
        await svc.get(boss, "Profile", "alice")


async def test_an_unidentified_caller_gets_nothing(svc):
    with pytest.raises(ContextError):  # sans identité : refusé avant même d'examiner les droits
        await svc.list(ANONYMOUS, "Profile")
    assert not scope_for(parse_definition(PROFILE), ANONYMOUS, "read").granted
    assert scope_for(parse_definition(PROFILE), NOBODY("alice"), "read").unrestricted
    assert scope_for(parse_definition(PROFILE), NOBODY("alice"), "write").level == "restricted"  # sa propre fiche seulement


def test_authenticated_is_never_declared_to_auth_and_is_the_only_special_grant():
    d = parse_definition(PROFILE)
    assert sorted(d.grant_specs()) == ["iam:profile:delete", "iam:profile:write"]
    for bad in ("@admin", "@Authenticated", "@authenticated:read", "authenticated"):
        with pytest.raises(InvalidDefinition):
            parse_definition({**PROFILE, "permissions": {"read": [bad]}})


@pytest.mark.parametrize("patch", [{"istable": True, "module": None, "permissions": {}}, {"single": True}])
def test_owner_naming_has_no_meaning_for_a_child_or_a_single_document(patch):
    with pytest.raises(InvalidDefinition):
        parse_definition({**PROFILE, **patch})


def test_the_meta_summary_says_a_profile_is_one_document_per_user(db, tables):
    """`per_user` : le Desk ne doit pas offrir « Créer un profil » dans l'accueil (il n'y en a qu'un, et c'est sa page qui le crée)."""
    reg = DocTypeRegistry()
    reg.register(parse_definition(PROFILE))
    reg.register(parse_definition({"schema_version": 1, "name": "Order", "module": "shop", "fields": [{"fieldname": "ref", "fieldtype": "Text"}]}))

    class Log:
        def info(self, *a, **k): ...
        def error(self, *a, **k): ...

    # Un grant sur la commande : `list_meta` ne liste que les types sur lesquels l'appelant a au moins un droit (le profil, lui, est `@authenticated`).
    who = make_ctx("acme", "alice", permissions=["shop:order:read", "shop:order:write"])
    summaries = {d["name"]: d for d in DocumentService(reg, db, tables, Log()).list_meta(who)}
    assert summaries["Profile"]["per_user"] is True and summaries["Profile"]["create"] is True  # créable, mais une seule fois
    assert "per_user" not in summaries["Order"]  # un document ordinaire ne porte pas le drapeau
