"""Critère de sortie de la phase 1 : matrice grant × action × `if_owner` × `match` (doc/doctype.md §15).

Test à modèle : un oracle en mémoire, écrit sans réutiliser `permissions.py`, prédit pour chaque utilisateur,
document et opération le résultat attendu (succès, 403 ou 404) en suivant l'état du document. Le service réel
(SQL sur SQLite et PostgreSQL) doit donner exactement le même résultat, à chaque étape.

Les règles citent des GRANTS (`tickets:ticket:admin`…), vérifiés dans `principal["permissions"]`. `match` est gelé
en production (`schema.MATCH_ENABLED`) : ce module le lève, pour que la machinerie reste testée tant qu'elle existe.

Ordre des contrôles, tel que décidé (doc/phase1.md) :
1. aucune règle n'accorde la lecture ni l'action -> 403 (indépendant du document) ;
2. document hors du périmètre de LECTURE -> 404 (on ne révèle pas son existence) ;
3. action refusée sur la valeur actuelle -> 403 ;
4. action refusée sur la valeur ÉCRITE (création ou déplacement) -> 403.
"""

from __future__ import annotations

import pytest

from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied
from xcore_doctype.query import Query
from xcore_doctype.permissions import Scope
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import parse_definition

ALL = ("read", "write", "create", "delete")


def g(name: str) -> str:
    """Nom lisible -> grant `plugin:ressource:action`."""
    return f"tickets:ticket:{name.lower()}"


@pytest.fixture(autouse=True)
def _match_enabled(monkeypatch):
    from xcore_doctype import schema

    monkeypatch.setattr(schema, "MATCH_ENABLED", True)


def rule(role, *actions, if_owner=False, match=False):
    r = {"grant": g(role), **{a: True for a in actions}}
    if if_owner:
        r["if_owner"] = True
    if match:
        r["match"] = {"field": "country", "principal_attr": "country"}
    return r


RULES = [
    rule("Admin", *ALL),
    rule("Owner", *ALL, if_owner=True),
    rule("Regional", "read", "write", match=True),
    rule("RegionalOwner", "read", "write", "delete", if_owner=True, match=True),
    rule("Reader", "read"),
    rule("Creator", "create"),
    rule("RegionalCreator", "create", match=True),
    rule("Deleter", "read", "delete"),
]

def to_permissions(rules):
    """Lignes de l'oracle {grant, actions…} -> syntaxe du DocType {action: [entrées]}. Une action sans ligne est fermée."""
    out = {a: [] for a in ALL}
    for r in rules:
        entry = {"grant": r["grant"]}
        if r.get("if_owner"):
            entry["if_owner"] = True
        if r.get("match"):
            entry["match"] = r["match"]
        for a in ALL:
            if r.get(a):
                out[a].append(entry if len(entry) > 1 else entry["grant"])
    return out


def definition():
    return parse_definition(
        {
            "schema_version": 1,
            "name": "Ticket",
            "fields": [
                {"fieldname": "title", "fieldtype": "Text", "required": True},
                {"fieldname": "country", "fieldtype": "Text"},
            ],
            "permissions": to_permissions(RULES),
        }
    )


# (propriétaire, pays) des documents semés. Un document sans pays vérifie `None != valeur`.
SEED = {
    "d1": ("alice", "FR"),
    "d2": ("alice", "DE"),
    "d3": ("bob", "FR"),
    "d4": ("bob", "DE"),
    "d5": ("alice", None),
    "d6": ("carol", "FR"),
}
# Utilisateurs : (id, pays du principal). carol n'a pas d'attribut `country`.
USERS = {"alice": "FR", "bob": "DE", "carol": None}

GRANT_SETS = [
    (),
    ("Admin",),
    ("Owner",),
    ("Regional",),
    ("RegionalOwner",),
    ("Reader",),
    ("Creator",),
    ("RegionalCreator",),
    ("Deleter",),
    ("Owner", "Regional"),
    ("Reader", "Creator"),
    ("Regional", "RegionalCreator"),
    ("Owner", "RegionalOwner"),
    ("Reader", "Deleter", "RegionalCreator"),
]


# ── L'oracle : la spécification, réécrite sans `permissions.py` ─────────────────────────────────


class Model:
    def __init__(self, user, names, country):
        self.user, self.grants, self.country = user, {g(n) for n in names}, country
        self.docs = {n: {"owner": o, "country": c, "version": 1} for n, (o, c) in SEED.items()}

    def _rows(self, action):
        return [r for r in RULES if r["grant"] in self.grants and r.get(action)]

    def can_ever(self, action) -> bool:
        """Une ligne pourrait-elle accorder l'action à cet utilisateur (conditions évaluables) ?"""
        for r in self._rows(action):
            if r.get("if_owner") and self.user is None:
                continue
            if r.get("match") and not self.country:
                continue
            return True
        return False

    def allowed(self, action, owner, country) -> bool:
        for r in self._rows(action):
            if r.get("if_owner") and owner != self.user:
                continue
            if r.get("match") and (not self.country or country != self.country):
                continue
            return True
        return False

    # Résultats attendus : "ok", "403" ou "404".
    def get(self, n):
        if not self.can_ever("read"):
            return "403"
        d = self.docs.get(n)
        return "ok" if d and self.allowed("read", d["owner"], d["country"]) else "404"

    def listing(self, country_filter=None):
        if not self.can_ever("read"):
            return "403"
        return sorted(
            n for n, d in self.docs.items()
            if self.allowed("read", d["owner"], d["country"]) and (country_filter is None or d["country"] == country_filter)
        )

    def create(self, country):
        if not self.can_ever("create"):
            return "403"
        return "ok" if self.allowed("create", self.user, country) else "403"

    def change(self, action, n, new_country=None):
        if not (self.can_ever("read") and self.can_ever(action)):
            return "403"
        d = self.docs.get(n)
        if not d or not self.allowed("read", d["owner"], d["country"]):
            return "404"
        if not self.allowed(action, d["owner"], d["country"]):
            return "403"
        if action == "write" and not self.allowed("write", d["owner"], new_country):
            return "403"
        return "ok"


async def outcome(coro):
    try:
        await coro
    except PermissionDenied:
        return "403"
    except NotFound:
        return "404"
    return "ok"


# ── Mise en place ───────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def seeded(db, tables):
    reg = DocTypeRegistry()
    reg.register(definition())
    svc = DocumentService(reg, db, tables, logger=_Log())
    for name, (owner, country) in SEED.items():
        data = {"title": name, **({"country": country} if country else {})}
        async with db.session() as s:
            await Query(s, ctx_of(owner, ["Admin"], USERS[owner]), tables).insert("Ticket", name, data)
    return svc


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def ctx_of(user, names, country, tenant="acme"):
    """Principal comme le fabrique xauth : `permissions` = les grants du tenant ; `roles` = noms libres (ignorés)."""
    principal = {"sub": user, "permissions": [g(n) for n in names], "roles": ["un-role-libre"]}
    if country is not None:
        principal["user"] = {"country": country}
    return RequestContext(tenant, principal, "http")


CASES = [pytest.param(u, r, id=f"{u}-{'+'.join(r) or 'nogrant'}") for u in USERS for r in GRANT_SETS]


# ── La matrice ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("user, names", CASES)
async def test_matrix_against_model(seeded, user, names):
    svc, model = seeded, Model(user, names, USERS[user])
    ctx = ctx_of(user, names, USERS[user])

    # LISTE : jamais de filtrage après coup, le total ne compte que les documents lisibles.
    exp = model.listing()
    if exp == "403":
        assert await outcome(svc.list(ctx, "Ticket")) == "403"
    else:
        page = await svc.list(ctx, "Ticket", order_by="name", descending=False, limit=2)  # page plus petite que le résultat
        assert page["total"] == len(exp)
        assert sorted(d["name"] for d in (await svc.list(ctx, "Ticket", limit=200))["items"]) == exp
        assert [d["name"] for d in page["items"]] == exp[:2]
        # Un filtre ne permet pas de voir hors périmètre.
        for c in ("FR", "DE"):
            got = await svc.list(ctx, "Ticket", filters={"country": c}, limit=200)
            assert sorted(d["name"] for d in got["items"]) == model.listing(c) and got["total"] == len(model.listing(c))

    # LECTURE de chaque document, y compris un nom inexistant (même réponse que hors périmètre).
    for n in [*SEED, "ghost"]:
        assert await outcome(svc.get(ctx, "Ticket", n)) == model.get(n), f"get {n}"

    # CRÉATION avec chaque valeur de pays.
    for country in ("FR", "DE", None):
        data = {"title": "new", **({"country": country} if country else {})}
        assert await outcome(svc.create(ctx, "Ticket", data)) == model.create(country), f"create {country}"

    # MISE À JOUR : valeur inchangée puis déplacement vers l'autre pays, en suivant les versions.
    for n in SEED:
        d = model.docs[n]
        same = {"title": "edited", **({"country": d["country"]} if d["country"] else {})}
        expect = model.change("write", n, d["country"])
        assert await outcome(svc.update(ctx, "Ticket", n, same, d["version"])) == expect, f"update {n}"
        if expect == "ok":
            d["version"] += 1

        other = "DE" if d["country"] == "FR" else "FR"
        expect = model.change("write", n, other)
        assert await outcome(svc.update(ctx, "Ticket", n, {"title": "moved", "country": other}, d["version"])) == expect, f"move {n}"
        if expect == "ok":
            d["version"] += 1
            d["country"] = other

    # SUPPRESSION.
    for n in SEED:
        expect = model.change("delete", n)
        assert await outcome(svc.delete(ctx, "Ticket", n)) == expect, f"delete {n}"
        if expect == "ok":
            del model.docs[n]
    # Ce qui reste visible correspond au modèle (suppression effective, rien d'autre supprimé).
    system = RequestContext.system("acme", "verification de la matrice")
    left = sorted(d["name"] for d in (await svc.list(system, "Ticket", limit=200))["items"] if d["name"] in SEED)
    assert left == sorted(model.docs)


# ── Cas qui ne tiennent pas dans la matrice ─────────────────────────────────────────────────────


async def test_missing_principal_attribute_never_matches_a_missing_value(seeded):
    """`None == None` ne doit jamais accorder l'accès : d5 n'a pas de pays, carol n'a pas d'attribut."""
    ctx = ctx_of("carol", ["Regional"], None)
    assert await outcome(seeded.list(ctx, "Ticket")) == "403"  # la seule règle est inévaluable : rien n'est accordé


async def test_user_with_attribute_does_not_see_documents_without_the_field(seeded):
    page = await seeded.list(ctx_of("bob", ["Regional"], "DE"), "Ticket", limit=200)
    assert sorted(d["name"] for d in page["items"]) == ["d2", "d4"]  # d5 (sans pays) n'y est pas


async def test_a_role_named_like_a_grant_grants_nothing(seeded):
    """Les noms de rôle sont libres chez auth : seule l'appartenance du grant à `permissions` ouvre un droit."""
    ctx = RequestContext("acme", {"sub": "mallory", "roles": [g("Admin")], "permissions": []}, "http")
    assert await outcome(seeded.list(ctx, "Ticket")) == "403"


@pytest.mark.parametrize("permissions", [g("Admin"), {g("Admin"): True}, [[g("Admin")]], None, 5, True])
async def test_malformed_permissions_grant_nothing(seeded, permissions):
    ctx = RequestContext("acme", {"sub": "mallory", "permissions": permissions}, "http")
    assert await outcome(seeded.list(ctx, "Ticket")) == "403"


async def test_unrelated_grants_of_other_plugins_are_ignored(seeded):
    """Un principal xauth porte les grants de TOUS les plugins ; seuls ceux cités par le DocType comptent."""
    ctx = RequestContext("acme", {"sub": "u", "permissions": ["xsales:customer:read", "stock:item:write", "admin:*", "user:list"]}, "http")
    assert await outcome(seeded.list(ctx, "Ticket")) == "403"
    mixed = RequestContext("acme", {"sub": "u", "permissions": ["xsales:customer:read", g("Reader")]}, "http")
    assert (await seeded.list(mixed, "Ticket", limit=200))["total"] == len(SEED)


async def test_system_context_is_not_subject_to_permissions(seeded):
    system = RequestContext.system("acme", "import")
    assert (await seeded.list(system, "Ticket", limit=200))["total"] == len(SEED)
    assert await outcome(seeded.delete(system, "Ticket", "d1")) == "ok"


async def test_doctype_without_rules_is_denied_to_everyone_but_system(db, tables):
    reg = DocTypeRegistry()
    reg.register(parse_definition({"schema_version": 1, "name": "Open", "fields": [{"fieldname": "a", "fieldtype": "Text"}]}))
    svc = DocumentService(reg, db, tables, logger=_Log())
    admin = ctx_of("alice", ["Admin", "Sales Manager", "System Manager"], None)
    assert await outcome(svc.list(admin, "Open")) == "403"
    assert await outcome(svc.create(admin, "Open", {"a": "x"})) == "403"
    assert svc.list_meta(admin) == []
    system = RequestContext.system("acme", "t")
    assert await outcome(svc.create(system, "Open", {"a": "x"})) == "ok"


async def test_update_is_conflict_not_forbidden_when_version_is_stale(seeded):
    """Le contrôle de version reste l'arbitre d'une modification concurrente."""
    from xcore_doctype.errors import VersionConflict

    ctx = ctx_of("alice", ["Admin"], "FR")
    await seeded.update(ctx, "Ticket", "d1", {"title": "a"}, 1)
    with pytest.raises(VersionConflict):
        await seeded.update(ctx, "Ticket", "d1", {"title": "b"}, 1)


async def test_a_scoped_user_cannot_move_a_document_out_of_their_perimeter(seeded):
    """`match` est revérifié sur la valeur écrite : un représentant FR ne peut pas envoyer un document en DE."""
    ctx = ctx_of("alice", ["Regional"], "FR")
    assert await outcome(seeded.update(ctx, "Ticket", "d1", {"title": "x", "country": "DE"}, 1)) == "403"
    assert (await seeded.get(ctx_of("alice", ["Admin"], None), "Ticket", "d1"))["data"]["country"] == "FR"


async def test_meta_exposes_effective_rights_but_never_the_rules(seeded):
    meta = seeded.get_meta(ctx_of("alice", ["Reader", "RegionalCreator"], "FR"), "Ticket")
    assert meta["rights"] == {"read": "all", "write": "none", "create": "restricted", "delete": "none", "submit": "none", "cancel": "none"}
    assert "permissions" not in meta and g("RegionalCreator") not in repr(meta)
    assert [m["name"] for m in seeded.list_meta(ctx_of("alice", ["Creator"], None))] == ["Ticket"]  # un droit suffit
    assert seeded.list_meta(ctx_of("alice", [], None)) == []
    with pytest.raises(PermissionDenied):
        seeded.get_meta(ctx_of("alice", [], None), "Ticket")


def test_scope_is_equivalent_in_python_for_the_written_value():
    s = Scope.everything("write")
    assert s.allows("anyone", {}) and s.level == "all"


async def test_write_or_delete_without_read_is_forbidden_not_hidden(db, tables):
    """Modifier ou supprimer exige de pouvoir lire : sans cela, 403 (et jamais 404 qui révélerait l'absence)."""
    reg = DocTypeRegistry()
    reg.register(parse_definition({
        "schema_version": 1, "name": "Blind", "fields": [{"fieldname": "a", "fieldtype": "Text"}],
        "permissions": {"read": [g("Admin")], "write": [g("Admin"), g("WriteOnly")], "create": [g("Admin")],
                        "delete": [g("Admin"), g("DeleteOnly")]},
    }))
    svc = DocumentService(reg, db, tables, logger=_Log())
    doc = await svc.create(ctx_of("alice", ["Admin"], None), "Blind", {"a": "x"})
    for role, op in (("WriteOnly", svc.update), ("DeleteOnly", svc.delete)):
        ctx = ctx_of("bob", [role], None)
        args = (ctx, "Blind", doc["name"], {"a": "y"}, 1) if op == svc.update else (ctx, "Blind", doc["name"])
        assert await outcome(op(*args)) == "403", role
        assert await outcome(op(*(ctx, "Blind", "ghost", *args[3:]))) == "403", role  # même réponse pour un nom inexistant
    assert (await svc.get(ctx_of("alice", ["Admin"], None), "Blind", doc["name"]))["data"] == {"a": "x"}
