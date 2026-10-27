"""Critère de sortie de la phase 1 : matrice grant × action × `if_owner` × `match` (process/doctype.md §15).

Test à modèle : un oracle en mémoire, écrit sans réutiliser `permissions.py`, prédit pour chaque utilisateur,
document et opération le résultat attendu (succès, 403 ou 404) en suivant l'état du document. Le service réel
(SQL sur SQLite et PostgreSQL) doit donner exactement le même résultat, à chaque étape.

Les règles citent des GRANTS (`tickets:ticket:admin`…), vérifiés dans `principal["permissions"]`. `match` est gelé
en production (`schema.MATCH_ENABLED`) : ce module le lève, pour que la machinerie reste testée tant qu'elle existe.

Ordre des contrôles, tel que décidé (process/phase1.md) :
1. aucune règle n'accorde la lecture ni l'action -> 403 (indépendant du document) ;
2. document hors du périmètre de LECTURE -> 404 (on ne révèle pas son existence) ;
3. action refusée sur la valeur actuelle -> 403 ;
4. action refusée sur la valeur ÉCRITE (création ou déplacement) -> 403.

Groupe 2 (`process/programme.md` §2.1) : les PERMISSIONS UTILISATEUR resserrent les rôles (ET ; un lien vide passe), le PARTAGE élargit (OU) en
lecture et, s'il le dit, en écriture, même sans rôle. Lire ou modifier UN document nommé ne répond donc plus 403 faute de rôle (un partage
pourrait l'ouvrir) : un document non partagé y est « introuvable » (404), comme un nom qui n'existe pas. La liste, la création et la
suppression gardent la règle 1. Chaque cas de la matrice tourne aussi avec des restrictions, des partages, et les deux.
"""

from __future__ import annotations

import pytest

from xcore_doctype.context import RequestContext
from xcore_doctype.document import DocumentService
from xcore_doctype.errors import NotFound, PermissionDenied
from xcore_doctype.query import Query
from xcore_doctype.permissions import USER_PERMISSION, Scope
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
                {"fieldname": "region", "fieldtype": "Link", "options": "Region"},
            ],
            "permissions": to_permissions(RULES),
        }
    )


# La cible des restrictions : lisible par tout identifié (un lien se vérifie avec les droits de l'appelant).
REGION = {"schema_version": 1, "name": "Region", "naming": "prompt", "fields": [{"fieldname": "label", "fieldtype": "Text"}],
          "permissions": {"read": ["@authenticated"], "write": [], "create": [], "delete": []}}

# (propriétaire, pays, région) des documents semés. Un document sans pays vérifie `None != valeur` ; sans région, il échappe aux restrictions.
SEED = {
    "d1": ("alice", "FR", "R1"),
    "d2": ("alice", "DE", "R2"),
    "d3": ("bob", "FR", "R1"),
    "d4": ("bob", "DE", None),
    "d5": ("alice", None, "R2"),
    "d6": ("carol", "FR", "R1"),
}

# Les variantes : (régions permises, tickets permis par leur nom, partages {document: "read" | "write"}).
VARIANTS = {
    "plain": (None, None, {}),
    "restricted": ({"R1"}, None, {}),
    "shared": (None, None, {"d2": "read", "d4": "write", "d6": "write"}),
    "both": ({"R1"}, {"d1", "d3", "d4"}, {"d2": "write", "d5": "read"}),
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
    def __init__(self, user, names, country, variant="plain"):
        self.user, self.grants, self.country = user, {g(n) for n in names}, country
        self.docs = {n: {"owner": o, "country": c, "region": r, "version": 1} for n, (o, c, r) in SEED.items()}
        self.regions, self.only, self.shares = VARIANTS[variant]

    def restricted_ok(self, region, name) -> bool:
        if self.regions is not None and region and region not in self.regions:
            return False
        return not (self.only is not None and name is not None and name not in self.only)

    def shared(self, n, action) -> bool:
        level = self.shares.get(n)
        return level == "write" or (action == "read" and level == "read")

    def role(self, action, owner, country, region, name) -> bool:
        return self.allowed(action, owner, country) and self.restricted_ok(region, name)

    def visible(self, n) -> bool:
        d = self.docs.get(n)
        return bool(d) and (self.role("read", d["owner"], d["country"], d["region"], n) or self.shared(n, "read"))

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
        # UN document nommé : un partage pourrait l'ouvrir sans rôle, donc jamais 403 ; non visible = introuvable.
        return "ok" if self.visible(n) else "404"

    def listing(self, country_filter=None):
        if not self.can_ever("read"):
            return "403"
        return sorted(n for n, d in self.docs.items() if self.visible(n) and (country_filter is None or d["country"] == country_filter))

    def create(self, country, region=None):
        if not self.can_ever("create"):
            return "403"
        return "ok" if self.role("create", self.user, country, region, None) else "403"

    def change(self, action, n, new_country=None):
        d = self.docs.get(n)
        if action == "write":  # modifier UN document nommé : le partage en écriture compte, sans rôle sur le type
            if not self.visible(n):
                return "404"
            if not (self.role("write", d["owner"], d["country"], d["region"], n) or self.shared(n, "write")):
                return "403"
            if not (self.role("write", d["owner"], new_country, d["region"], n) or self.shared(n, "write")):
                return "403"
            return "ok"
        if not (self.can_ever("read") and self.can_ever(action)):
            return "403"
        if not self.visible(n):
            return "404"
        return "ok" if self.role(action, d["owner"], d["country"], d["region"], n) else "403"


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
    reg.register(parse_definition(REGION))
    reg.register(definition())
    svc = DocumentService(reg, db, tables, logger=_Log())
    async with db.session() as s:
        q = Query(s, RequestContext.system("acme", "semis"), tables)
        for r in ("R1", "R2"):
            await q.insert("Region", r, {"label": r})
    for name, (owner, country, region) in SEED.items():
        data = {"title": name, **({"country": country} if country else {}), **({"region": region} if region else {})}
        async with db.session() as s:
            await Query(s, ctx_of(owner, ["Admin"], USERS[owner]), tables).insert("Ticket", name, data)
    return svc


async def apply_variant(db, tables, user, variant):
    """Écrit les restrictions (documents `User Permission`) et les partages de la variante, pour `user`. Une restriction bornée à un AUTRE
    type (`applies_to`) et celles d'un autre utilisateur ne doivent rien changer."""
    regions, only, shares = VARIANTS[variant]
    async with db.session() as s:
        q = Query(s, RequestContext.system("acme", "variante"), tables)
        rows = [{"user": user, "allow": "Region", "for_value": r} for r in sorted(regions or ())]
        rows += [{"user": user, "allow": "Ticket", "for_value": n} for n in sorted(only or ())]
        if regions is not None or only is not None:
            rows.append({"user": user, "allow": "Region", "for_value": "R2", "applies_to": "Other"})  # pour un autre type : sans effet ici
        rows.append({"user": f"{user}-other", "allow": "Region", "for_value": "R9"})  # d'un autre utilisateur : sans effet
        for i, row in enumerate(rows):
            await q.insert(USER_PERMISSION, f"up-{i}", row)
    for n, level in shares.items():
        async with db.session() as s:
            await Query(s, ctx_of("carol", ["Admin"], None), tables).put_share("Ticket", n, user, write=level == "write")


class _Log:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...


def ctx_of(user, names, country, tenant="acme"):
    """Principal comme le fabrique xauth : `permissions` = les grants du tenant ; `roles` = noms libres (ignorés)."""
    principal = {"sub": user, "permissions": [g(n) for n in names], "roles": ["un-role-libre"]}
    if country is not None:
        principal["user"] = {"country": country}
    return RequestContext(tenant, principal, "http")


CASES = [pytest.param(u, r, v, id=f"{v}-{u}-{'+'.join(r) or 'nogrant'}") for v in VARIANTS for u in USERS for r in GRANT_SETS]


# ── La matrice ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("user, names, variant", CASES)
async def test_matrix_against_model(seeded, db, tables, user, names, variant):
    await apply_variant(db, tables, user, variant)
    svc, model = seeded, Model(user, names, USERS[user], variant)
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

    # CRÉATION avec chaque valeur de pays, et une région permise ou sans région (la région interdite : voir le test dédié).
    for country in ("FR", "DE", None):
        for region in ("R1", None):
            data = {"title": "new", **({"country": country} if country else {}), **({"region": region} if region else {})}
            assert await outcome(svc.create(ctx, "Ticket", data)) == model.create(country, region), f"create {country} {region}"

    # MISE À JOUR : valeur inchangée puis déplacement vers l'autre pays, en suivant les versions.
    for n in SEED:
        d = model.docs[n]
        keep = {"region": d["region"]} if d["region"] else {}
        same = {"title": "edited", **({"country": d["country"]} if d["country"] else {}), **keep}
        expect = model.change("write", n, d["country"])
        assert await outcome(svc.update(ctx, "Ticket", n, same, d["version"])) == expect, f"update {n}"
        if expect == "ok":
            d["version"] += 1

        other = "DE" if d["country"] == "FR" else "FR"
        expect = model.change("write", n, other)
        assert await outcome(svc.update(ctx, "Ticket", n, {"title": "moved", "country": other, **keep}, d["version"])) == expect, f"move {n}"
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
    tickets = lambda ctx: [m["name"] for m in seeded.list_meta(ctx) if m["name"] != "Region"]  # noqa: E731 - Region : lisible par tout identifié
    assert tickets(ctx_of("alice", ["Creator"], None)) == ["Ticket"]  # un droit suffit
    assert tickets(ctx_of("alice", [], None)) == []
    with pytest.raises(PermissionDenied):
        seeded.get_meta(ctx_of("alice", [], None), "Ticket")


def test_scope_is_equivalent_in_python_for_the_written_value():
    s = Scope.everything("write")
    assert s.allows("anyone", {}) and s.level == "all"


async def test_write_or_delete_without_read_is_forbidden_not_hidden(db, tables):
    """Modifier ou supprimer exige de pouvoir lire. Supprimer sans lire : 403. Modifier UN document nommé passe aussi par le partage (qui
    l'ouvrirait sans rôle) : sans lecture ni partage, il est « introuvable » (404), document existant ou non — la même réponse, rien n'est révélé."""
    reg = DocTypeRegistry()
    reg.register(parse_definition({
        "schema_version": 1, "name": "Blind", "fields": [{"fieldname": "a", "fieldtype": "Text"}],
        "permissions": {"read": [g("Admin")], "write": [g("Admin"), g("WriteOnly")], "create": [g("Admin")],
                        "delete": [g("Admin"), g("DeleteOnly")]},
    }))
    svc = DocumentService(reg, db, tables, logger=_Log())
    doc = await svc.create(ctx_of("alice", ["Admin"], None), "Blind", {"a": "x"})
    for role, op, expected in (("WriteOnly", svc.update, "404"), ("DeleteOnly", svc.delete, "403")):
        ctx = ctx_of("bob", [role], None)
        args = (ctx, "Blind", doc["name"], {"a": "y"}, 1) if op == svc.update else (ctx, "Blind", doc["name"])
        assert await outcome(op(*args)) == expected, role
        assert await outcome(op(*(ctx, "Blind", "ghost", *args[3:]))) == expected, role  # même réponse pour un nom inexistant
    assert (await svc.get(ctx_of("alice", ["Admin"], None), "Blind", doc["name"]))["data"] == {"a": "x"}
