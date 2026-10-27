"""Critère 9 de 14 bis.8 : test de fuite entre tenants sur CHAQUE méthode publique de `Query`.

Trois garde-fous :
1. `test_every_public_method_has_a_scenario` échoue si on ajoute une méthode publique sans scénario ;
2. chaque scénario agit en tant que `globex` sur des données qui existent aussi chez `acme`
   (y compris un `name` identique, autorisé par D13), puis vérifie que `acme` est intact ;
3. le SQL réellement émis est capturé : chaque instruction sur la table doit porter `tenant_id`
   et ne lier que le tenant de l'appelant.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, text

from tests.conftest import make_ctx
from xcore_doctype.aggregation import Group, Metric
from xcore_doctype.db.tables import (
    ASSIGNMENT_TABLE, ATTACHMENT_TABLE, COMMENT_TABLE, DOCUMENT_TABLE, FILE_TABLE, NAMING_TABLE, OVERRIDE_TABLE, SAVED_VIEW_TABLE, SHARE_TABLE, TAG_TABLE, UNIQUE_TABLE,
    VERSION_TABLE,
)
from xcore_doctype.errors import AlreadyExists, DocumentLocked, InvalidDocStatus, NotFound, VersionConflict
from xcore_doctype.builder import Filter
from xcore_doctype.permissions import USER_PERMISSION, Facts, Scope
from xcore_doctype.query import FileCiters, Query, UniqueViolation

A, B = "acme", "globex"
ALL = Scope.everything("read")
PUBLIC = {n for n, m in inspect.getmembers(Query, inspect.isfunction) if not n.startswith("_")}


class Env:
    def __init__(self, db, tables, customer):
        self.db, self.tables, self.customer = db, tables, customer
        self.captured: list[tuple[str, object]] = []
        self.ids: dict[str, dict[str, str]] = {}
        self.recording = False
        event.listen(db.engine.sync_engine, "before_cursor_execute", self._capture)

    def _capture(self, conn, cursor, statement, parameters, context, executemany):
        if self.recording and any(t in statement for t in (DOCUMENT_TABLE, NAMING_TABLE, VERSION_TABLE, SAVED_VIEW_TABLE, OVERRIDE_TABLE, COMMENT_TABLE, ASSIGNMENT_TABLE, ATTACHMENT_TABLE, UNIQUE_TABLE, FILE_TABLE, TAG_TABLE, SHARE_TABLE)):
            self.captured.append((statement, parameters))

    def close(self):
        event.remove(self.db.engine.sync_engine, "before_cursor_execute", self._capture)

    def session(self, tenant: str):
        """La session de ce tenant. `test_rls.py` la surcharge pour y poser le tenant de la RLS."""
        return self.db.session()

    @asynccontextmanager
    async def q(self, tenant: str):
        async with self.session(tenant) as s:
            yield Query(s, make_ctx(tenant, user=f"user-{tenant}"), self.tables)

    async def rows(self, tenant: str) -> list[tuple]:
        """Documents ET compteurs d'un tenant : tout ce que le test doit retrouver intact chez A."""
        async with self.session(tenant) as s:
            docs = await s.execute(
                text(f"SELECT name, version, owner, docstatus FROM {DOCUMENT_TABLE} WHERE tenant_id = :t ORDER BY name"),
                {"t": tenant},
            )
            counters = await s.execute(
                text(f"SELECT doctype, series_key, last_number FROM {NAMING_TABLE} WHERE tenant_id = :t ORDER BY doctype, series_key"),
                {"t": tenant},
            )
            history = await s.execute(
                text(f"SELECT docname, version, action, changed_by FROM {VERSION_TABLE} WHERE tenant_id = :t ORDER BY id"),
                {"t": tenant},
            )
            views = await s.execute(
                text(f"SELECT name, owner, label, is_default FROM {SAVED_VIEW_TABLE} WHERE tenant_id = :t ORDER BY name"),
                {"t": tenant},
            )
            overrides = await s.execute(
                text(f"SELECT kind, target, owner FROM {OVERRIDE_TABLE} WHERE tenant_id = :t ORDER BY kind, target"),
                {"t": tenant},
            )
            collab = [
                tuple(x)
                for table, cols in ((COMMENT_TABLE, "id, docname, owner, content, edited"), (ASSIGNMENT_TABLE, "id, docname, assignee, status, description"), (ATTACHMENT_TABLE, "id, docname, file_id, owner"))
                for x in (await s.execute(text(f"SELECT {cols} FROM {table} WHERE tenant_id = :t ORDER BY id"), {"t": tenant})).all()
            ]
            uniques = await s.execute(
                text(f"SELECT doctype, fieldname, value, docname FROM {UNIQUE_TABLE} WHERE tenant_id = :t ORDER BY doctype, fieldname, value"), {"t": tenant}
            )
            shares = await s.execute(
                text(f"SELECT doctype, docname, member, write, owner FROM {SHARE_TABLE} WHERE tenant_id = :t ORDER BY doctype, docname, member"), {"t": tenant}
            )
            tags = await s.execute(
                text(f"SELECT doctype, docname, folded, tag, owner FROM {TAG_TABLE} WHERE tenant_id = :t ORDER BY doctype, docname, folded"), {"t": tenant}
            )
            return (
                [tuple(x) for x in docs.all()]
                + collab
                + [tuple(x) for x in uniques.all()]
                + [tuple(x) for x in tags.all()]
                + [tuple(x) for x in shares.all()]
                + [tuple(x) for x in counters.all()]
                + [tuple(x) for x in history.all()]
                + [tuple(x) for x in views.all()]
                + [tuple(x) for x in overrides.all()]
            )

    async def seed(self):
        for tenant, names in ((A, ["shared", "only-a", "needle", "refers-fr"]), (B, ["shared", "only-b"])):
            async with self.q(tenant) as q:
                for n in names:
                    # Le même `name` ("shared") existe chez les deux tenants : pas un conflit (D13).
                    data = {"customer_name": f"{tenant}:{n}", **({"country": "FR"} if n == "refers-fr" else {})}
                    await q.insert("Customer", n, data)
                # Une vue sauvegardée par tenant, même nom de DocType : chacune doit rester invisible à l'autre.
                await q.put_saved_view(
                    "Customer", f"view-{tenant}", owner=f"user-{tenant}", label=tenant, kind="list", config={}, is_default=False
                )
                # Un habillage par tenant (même cible) : celui de A ne doit jamais apparaître chez B.
                await q.put_override("form", "Customer", owner=f"user-{tenant}", config={"label": tenant})
        for tenant in (A, B):
            # Collaboration : un commentaire, une assignation (à l'utilisateur de CE tenant) et une pièce jointe par tenant, sur « shared ».
            async with self.q(tenant) as q:
                comment = await q.add_comment("Customer", "shared", f"note-{tenant}")
                task = await q.add_assignment("Customer", "shared", assignee=f"user-{tenant}", description=f"todo-{tenant}", due_date="2026-12-01")
                file_id = ("a" if tenant == A else "b") * 32
                await q.add_file(file_id, doctype="Customer", fieldname="_attachment", filename=f"{tenant}.pdf", content_type="application/pdf", size=1, namespace="n", blob_id="b", stored_name="b")
                att = await q.add_attachment("Customer", "shared", file_id)
                self.ids[tenant] = {"comment": comment["id"], "assignment": task["id"], "attachment": att["id"], "file": file_id}
                # Étiquettes : « Urgent » sur « shared » chez les deux ; A seul a « Secret » (sur « shared » et « needle ») et l'écrit « URGENT » ailleurs.
                await q.add_tag("Customer", "shared", "Urgent")
                # Partages : « shared » ouvert à « friend » chez les deux (en écriture chez A seulement) ; A seul ouvre « needle ».
                await q.put_share("Customer", "shared", "friend", write=tenant == A)
                if tenant == A:
                    await q.put_share("Customer", "needle", "friend", write=True)
                if tenant == A:
                    await q.add_tag("Customer", "shared", "Secret")
                    await q.add_tag("Customer", "needle", "Secret")
                    await q.add_tag("Customer", "only-a", "URGENT")
        async with self.q(A) as q:
            # L'historique de A : une création de « shared » (même nom que chez B) et la suppression d'un document disparu.
            hist = dict(version=1, user="user-acme", doc_owner="user-acme")
            await q.add_version("Customer", "shared", action="create", changes={"customer_name": {"old": None, "new": "acme:shared"}}, **hist)
            await q.add_version("Customer", "gone-a", action="delete", changes={"customer_name": {"old": "acme:gone", "new": None}}, **hist)
            await q.insert("Order", "a-order", {"lines": [{"item": "X"}]})  # une LIGNE de A cite l'article « X »
            # Les valeurs uniques de A : B peut porter les mêmes sans conflit, et ne les voit ni ne les libère.
            await q.sync_unique("Customer", "shared", {"code": "X1"})
            await q.sync_unique("Customer", "only-a", {"code": "A2"})
            # Une permission utilisateur chez A pour un identifiant qui existe aussi chez B : elle ne doit jamais restreindre B.
            await q.insert(USER_PERMISSION, "up-a", {"user": "friend", "allow": "Customer", "for_value": "only-a"})
            for _ in range(3):  # A en est à 3 : si B partageait ce compteur, son premier numéro serait 4
                await q.next_number("Customer", "C-{#}")


@pytest.fixture
async def env(db, tables, customer):
    e = Env(db, tables, customer)
    await e.seed()
    e.snapshot_a = await e.rows(A)
    e.recording = True
    try:
        yield e
    finally:
        e.recording = False
        e.close()


# ── Un scénario par méthode publique : on agit en tant que B sur des objets de A ──────────────


async def scenario_get(env: Env):
    async with env.q(B) as q:
        assert await q.get("Customer", "only-a", scope=ALL) is None  # n'existe que chez A
        assert await q.get("Customer", "needle", scope=ALL) is None
        shared = await q.get("Customer", "shared", scope=ALL)  # existe chez les deux : on voit celui de B
        assert shared is not None and shared.data["customer_name"] == f"{B}:shared"
        # Verrouiller ne change pas ce qu'on voit : toujours rien pour un document qui n'existe que chez A.
        assert await q.get("Customer", "only-a", scope=ALL, lock=True) is None
        assert (await q.get("Customer", "shared", scope=ALL, lock=True)).data["customer_name"] == f"{B}:shared"


async def scenario_list(env: Env):
    defn = env.customer.definition
    page = dict(order_by="name", descending=False, limit=200, offset=0, scope=ALL)
    async with env.q(B) as q:
        docs, total = await q.list(defn, filters={}, **page)
        assert [d.name for d in docs] == ["only-b", "shared"] and total == 2
        assert all(d.data["customer_name"].startswith(f"{B}:") for d in docs)
        # Un filtre qui ne correspond qu'à des documents de A ne ramène rien (ni dans total).
        docs, total = await q.list(defn, filters={"customer_name": f"{A}:needle"}, **page)
        assert docs == [] and total == 0
        # Pagination : l'offset ne « déborde » pas sur les lignes de A.
        docs, total = await q.list(defn, filters={}, **{**page, "offset": 2})
        assert docs == [] and total == 2
        # Le filtre `_tag` ne regarde que les étiquettes de B : « Secret » (posée chez A sur un « shared » homonyme) ne retient rien.
        docs, total = await q.list(defn, filters={"_tag": "Secret"}, **page)
        assert docs == [] and total == 0
        docs, _ = await q.list(defn, filters={"_tag": Filter("!=", "Secret")}, **page)
        assert [d.name for d in docs] == ["only-b", "shared"]
        docs, _ = await q.list(defn, filters={"_tag": Filter("in", ["Secret", "Urgent"])}, **page)
        assert [d.name for d in docs] == ["shared"]
        # Restrictions et partages en sous-requêtes : la restriction de « friend » posée chez A (« only-a ») ne restreint rien chez B,
        # et ses partages chez A n'ouvrent rien ; le contrôle du SQL émis (plus bas) vérifie que ces sous-requêtes ne lient que B.
        docs, total = await q.list(defn, filters={}, **{**page, "scope": _friend_scope()})
        assert [d.name for d in docs] == ["only-b", "shared"] and total == 2
        none = Scope("read", (), doctype="Customer", user="friend", restrict=(("Customer", None),), share=True)  # aucun rôle : les partages seuls
        docs, _ = await q.list(defn, filters={}, **{**page, "scope": none})
        assert [d.name for d in docs] == ["shared"]


async def scenario_aggregate(env: Env):
    """Un total, un regroupement ou une série de temps ne comptent jamais les documents d'un autre tenant."""
    defn = env.customer.definition
    count = [Metric("count")]
    async with env.q(B) as q:
        assert (await q.aggregate(defn, filters={}, group=None, metrics=count, keys=None, limit=50, scope=ALL))[0] == [(2,)]
        # A a un client qui cite « FR » : ce groupe n'existe pas chez B, dont les clients n'ont aucun pays.
        rows, truncated = await q.aggregate(defn, filters={}, group=Group("country"), metrics=count, keys=None, limit=50, scope=ALL)
        assert rows == [(None, 2)] and not truncated
        # Un instant système : les quatre documents de A, créés le même jour, ne s'ajoutent pas aux deux de B.
        rows, _ = await q.aggregate(defn, filters={}, group=Group("creation", "year"), metrics=count, keys=None, limit=50, scope=ALL)
        assert [r[1] for r in rows] == [2]
        rows, _ = await q.aggregate(defn, filters={}, group=Group("owner"), metrics=count, keys=None, limit=50, scope=ALL)
        assert rows == [(f"user-{B}", 2)]
        # Un filtre qui ne correspond qu'à un document de A ne ramène rien.
        assert (await q.aggregate(defn, filters={"customer_name": f"{A}:needle"}, group=None, metrics=count, keys=None, limit=50, scope=ALL))[0] == [(0,)]
        # Même une somme sur un champ que seul A renseigne : zéro, pas la valeur de A.
        total = await q.aggregate(defn, filters={}, group=None, metrics=[Metric("sum", "credit_limit")], keys=None, limit=50, scope=ALL)
        assert total[0][0][0] in (0, None)


async def scenario_files(env: Env):
    """Les fichiers d'un tenant n'existent pas pour un autre, même sous le même identifiant ; un fichier d'un autre tenant ne se supprime pas."""
    same = "f" * 32
    mine = dict(doctype="Customer", fieldname="customer_name", filename="a.png", content_type="image/png", size=3, namespace="n", blob_id="b", stored_name="b")
    env.recording = False  # (ce que A prépare n'est pas le SQL de B)
    async with env.q(A) as q:
        await q.add_file(same, **mine)
        await q.add_file("e" * 32, **mine)  # un fichier que A est seul à avoir
    env.recording = True
    async with env.q(B) as q:
        assert await q.get_files({same, "e" * 32}) == {}  # ni l'un ni l'autre n'existe pour B
        await q.add_file(same, **{**mine, "filename": "b.png"})  # le même identifiant chez B : pas un conflit
        assert {r["filename"] for r in (await q.get_files({same})).values()} == {"b.png"}
        assert await q.remove_files({same, "e" * 32}) == 1  # B retire SON fichier, pas celui de A
        # Un document de A cite un fichier : B ne le voit pas comme cité (le document n'est pas à lui).
        assert await q.file_cited("Customer", "customer_name", f"{A}:needle", scope=ALL) is False
        assert await q.file_cited("Customer", "customer_name", f"{B}:only-b", scope=ALL) is True
    env.recording = False
    async with env.q(A) as q:
        assert set(await q.get_files({same, "e" * 32})) == {same, "e" * 32}  # intacts chez A
        await q.remove_files({same, "e" * 32})  # (on rend la table comme on l'a trouvée)
    env.recording = True


async def scenario_cited_files(env: Env):
    """L'écran « Fichiers » de B : ses fichiers cités, jamais ceux de A, ni dans la page, ni dans le total, ni dans les comptes, même sous un nom de document commun."""
    cited = dict(doctype="Customer", fieldname="customer_name", content_type="image/png", size=3, namespace="n", blob_id="b", stored_name="b")
    env.recording = False
    async with env.q(A) as q:
        await q.add_file(f"{A}:needle", filename="secret-a.png", **cited)  # « needle » de A cite ce fichier par son champ
        await q.add_file("FR", filename="pays-a.png", **{**cited, "fieldname": "country"})  # « refers-fr » de A le cite par `country` : une place que B n'a pas
    env.recording = True
    async with env.q(B) as q:
        await q.add_file(f"{B}:only-b", filename="mine-b.png", **cited)
        # Les places où il y a des fichiers (de quoi borner l'union) sont celles de B : la place `country` de A n'en est pas une.
        assert await q._file_places() == {("Customer", "customer_name"), ("Customer", "_attachment")}
        page = dict(doctype=None, search=None, order_by="creation", descending=True, limit=50, offset=0)
        everything = [FileCiters("Customer", ALL, Scope.everything("write"), fields=("customer_name", "country"))]
        rows, total, counts = await q.cited_files(everything, **page)
        assert sorted((r["file_id"], r["docname"], r["fieldname"]) for r in rows) == [(env.ids[B]["file"], "shared", "_attachment"), (f"{B}:only-b", "only-b", "customer_name")]
        assert total == 2 and counts == {"Customer": 2} and all(r["writable"] for r in rows)
        # Les données du document citant (pour juger « détacher ») sont celles du document de B.
        assert {r["docname"]: r["data"] for r in rows} == {"only-b": {"customer_name": f"{B}:only-b"}, "shared": None}
        # Le nom d'un fichier que seul A a joint (« acme.pdf ») ne ramène rien chez B ; une page plus loin non plus.
        assert await q.cited_files(everything, **{**page, "search": A}) == ([], 0, {})
        assert (await q.cited_files(everything, **{**page, "offset": 2}))[0] == []
        # Un DocType virtuel : les noms lisibles viennent de sa source, le tenant reste celui de l'appelant (« shared » existe chez les deux).
        virtual = [FileCiters("Customer", Scope("read"), Scope("write"), names=frozenset({"shared", "only-a"}))]
        rows, total, _ = await q.cited_files(virtual, **page)
        assert [(r["file_id"], r["writable"], r["version"]) for r in rows] == [(env.ids[B]["file"], False, None)] and total == 1
        await q.remove_files({f"{B}:only-b"})
    env.recording = False
    async with env.q(A) as q:
        await q.remove_files({f"{A}:needle", "FR"})
    env.recording = True


async def scenario_attachment_doctypes(env: Env):
    """Les types qui ont des pièces jointes sont ceux du tenant : une pièce jointe que seul A a sur une commande n'en fait pas un type « avec pièces jointes » pour B."""
    env.recording = False
    async with env.q(A) as q:
        att = await q.add_attachment("Order", "a-order", "d" * 32)
    env.recording = True
    async with env.q(B) as q:
        assert await q.attachment_doctypes({"Customer", "Order"}) == {"Customer"}
        assert await q.attachment_doctypes(set()) == set()
    env.recording = False
    async with env.q(A) as q:
        assert await q.delete_attachment(att["id"])  # (on rend la table comme on l'a trouvée)
    env.recording = True


async def scenario_search(env: Env):
    """L'autocomplétion d'un champ Link ne doit jamais proposer un document d'un autre tenant."""
    defn = env.customer.definition
    async with env.q(B) as q:
        got = await q.search(defn, "", limit=50, scope=ALL)
        assert sorted(d.name for d in got) == ["only-b", "shared"]  # tous les documents de B, aucun de A
        assert await q.search(defn, "needle", limit=50, scope=ALL) == []  # n'existe que chez A
        assert await q.search(defn, "only-a", limit=50, scope=ALL) == []
        shared = await q.search(defn, "shared", limit=50, scope=ALL)
        assert [d.data["customer_name"] for d in shared] == [f"{B}:shared"]  # pas celui de A, qui a le même nom
        # Le texte cherché se trouve aussi dans les DONNÉES de A (`acme:needle`) : pas plus de fuite par le titre.
        assert await q.search(defn, "acme", limit=50, scope=ALL) == []


async def scenario_insert(env: Env):
    async with env.q(B) as q:
        doc = await q.insert("Customer", "only-a", {"customer_name": "B-copy"})  # nom pris chez A seulement
        assert doc.owner == f"user-{B}"
    async with env.q(B) as q:
        assert (await q.get("Customer", "only-a", scope=ALL)).data["customer_name"] == "B-copy"
    # Et le même nom chez A n'a pas bougé (vérifié plus bas via snapshot).


async def scenario_update(env: Env):
    async with env.q(B) as q:
        # Un document qui n'existe que chez A est introuvable pour B, même avec la bonne version.
        with pytest.raises(NotFound):
            await q.update("Customer", "only-a", {"customer_name": "pwned"}, expected_version=1, scope=ALL)
    async with env.q(B) as q:
        # Un nom commun : seule la ligne de B change.
        updated = await q.update("Customer", "shared", {"customer_name": "B-updated"}, expected_version=1, scope=ALL)
        assert updated.version == 2
    async with env.q(B) as q:
        with pytest.raises(VersionConflict):
            await q.update("Customer", "shared", {"customer_name": "x"}, expected_version=1, scope=ALL)


async def scenario_insert_if_absent(env: Env):
    """« Créer s'il n'existe pas » ne regarde que le tenant : `only-a` n'existe que chez A, donc B le crée sans toucher celui de A."""
    async with env.q(B) as q:
        assert await q.insert_if_absent("Customer", "only-a", {"customer_name": "B-bin"}) is True
        assert await q.insert_if_absent("Customer", "shared", {"customer_name": "x"}) is False  # existe chez B : inchangé
        assert await q.insert_if_absent("Customer", "shared", {"customer_name": "y"}) is False
    async with env.q(B) as q:
        assert (await q.get("Customer", "only-a", scope=ALL)).data["customer_name"] == "B-bin"
        assert (await q.get("Customer", "shared", scope=ALL)).data["customer_name"] == f"{B}:shared"


async def scenario_transition(env: Env):
    """Soumettre ne touche que le document de B, même quand A a un document du même nom (ou un seul exemplaire)."""
    async with env.q(B) as q:
        with pytest.raises(NotFound):  # n'existe que chez A
            await q.transition("Customer", "only-a", source=0, target=1, expected_version=1, scope=ALL)
    async with env.q(B) as q:
        doc = await q.transition("Customer", "shared", source=0, target=1, expected_version=1, scope=ALL)
        assert (doc.docstatus, doc.version) == (1, 2)
    async with env.q(B) as q:
        with pytest.raises(InvalidDocStatus):  # déjà soumis : une seconde soumission ne passe pas
            await q.transition("Customer", "shared", source=0, target=1, expected_version=2, scope=ALL)
        with pytest.raises(DocumentLocked):  # figé : ni modifiable...
            await q.update("Customer", "shared", {"customer_name": "x"}, expected_version=2, scope=ALL)
        assert await q.delete("Customer", "shared", scope=ALL) is False  # ...ni supprimable


async def scenario_workflow_transition(env: Env):
    """Une transition de workflow ne touche que le document de B, et ne passe qu'UNE fois (état + version dans le WHERE)."""
    async with env.q(B) as q:
        with pytest.raises(NotFound):  # n'existe que chez A
            await q.workflow_transition("Customer", "only-a", source_state=None, target_state="Approuvé", target_status=1, expected_version=1, scope=ALL)
    async with env.q(B) as q:
        doc = await q.workflow_transition("Customer", "shared", source_state=None, target_state="Approuvé", target_status=1, expected_version=1, scope=ALL)
        assert (doc.workflow_state, doc.docstatus, doc.version) == ("Approuvé", 1, 2)
    async with env.q(B) as q:
        with pytest.raises(InvalidDocStatus):  # l'état a changé : rejouer ne passe pas
            await q.workflow_transition("Customer", "shared", source_state=None, target_state="Approuvé", target_status=1, expected_version=2, scope=ALL)


async def scenario_delete(env: Env):
    async with env.q(B) as q:
        assert await q.delete("Customer", "only-a", scope=ALL) is False
        assert await q.delete("Customer", "needle", scope=ALL) is False
    async with env.q(B) as q:
        assert await q.delete("Customer", "shared", scope=ALL) is True  # supprime celui de B seulement
    async with env.q(B) as q:
        assert await q.get("Customer", "shared", scope=ALL) is None


async def scenario_next_number(env: Env):
    """Un compteur est propre à son tenant : A en est à 3, B repart de 1, et le compteur de A ne bouge pas (D13)."""
    async with env.q(B) as q:
        assert [await q.next_number("Customer", "C-{#}") for _ in range(2)] == [1, 2]
        assert await q.next_number("Customer", "D-{#}") == 1  # une autre série : un autre compteur
        assert await q.next_number("Invoice", "C-{#}") == 1  # un autre DocType : un autre compteur


async def scenario_existing_names(env: Env):
    """Le contrôle d'un champ Link ne voit que les documents du tenant : jamais l'existence d'un document d'un autre."""
    async with env.q(B) as q:
        names = ["only-a", "needle", "shared", "only-b", "absent"]
        assert await q.existing_names("Customer", names, scope=ALL) == {"shared", "only-b"}
        assert await q.existing_names("Customer", names, scope=ALL, lock=True) == {"shared", "only-b"}  # verrouiller n'élargit rien
        assert await q.existing_names("Customer", ["only-a"], scope=ALL, lock=True) == set()
        assert await q.existing_names("Customer", [], scope=ALL) == set()


async def scenario_existing_docstatus(env: Env):
    """L'état d'une cible de lien ne se lit que chez le tenant de l'appelant : l'existence d'un document de A ne se devine pas."""
    async with env.q(B) as q:
        names = ["only-a", "needle", "shared", "only-b", "absent"]
        assert await q.existing_docstatus("Customer", names, scope=ALL) == {"shared": 0, "only-b": 0}
        assert await q.existing_docstatus("Customer", names, scope=ALL, lock=True) == {"shared": 0, "only-b": 0}  # verrouiller n'élargit rien
        assert await q.existing_docstatus("Customer", ["only-a"], scope=ALL, lock=True) == {}
        assert await q.existing_docstatus("Customer", [], scope=ALL) == {}


async def scenario_purge_versions(env: Env):
    """La rétention d'historique ne garde que les N entrées du document… et ne purge que celles du tenant de l'appelant."""
    async with env.q(B) as q:
        for version in (1, 2):
            await q.add_version("Customer", "shared", version=version, action="update", user="user-globex", doc_owner="user-globex", changes={})
    async with env.q(B) as q:
        assert await q.purge_versions("Customer", "shared", keep=1) == 1  # l'entrée la plus ancienne de B
        items, total = await q.versions("Customer", "shared", limit=50, offset=0)
        assert total == 1 and [i["version"] for i in items] == [2]
        assert await q.purge_versions("Customer", "only-a", keep=1) == 0  # n'existe que chez A : rien à purger chez B


async def scenario_purge_versions_before(env: Env):
    """La purge par âge ne touche que l'historique du tenant de l'appelant, même quand la date est large."""
    soon = datetime.now(UTC) + timedelta(days=1)
    async with env.q(B) as q:
        await q.add_version("Customer", "shared", version=1, action="create", user="user-globex", doc_owner="user-globex", changes={})
    async with env.q(B) as q:
        assert await q.purge_versions_before(soon) == 1  # l'entrée de B, plus vieille que la date
        assert await q.purge_versions_before(soon, doctype="Customer") == 0  # plus rien à purger
        assert await q.purge_versions_before(datetime.now(UTC) - timedelta(days=1)) == 0  # rien n'est plus vieux que ça


async def scenario_lock_for_delete(env: Env):
    async with env.q(B) as q:
        await q.lock_for_delete("Customer", "only-a")  # n'existe que chez A : ne verrouille rien, ne lève rien
        await q.lock_for_delete("Customer", "shared")


async def scenario_references_to(env: Env):
    """Seul A cite le pays « FR » et l'article « X » : pour B, rien ne les référence, et ce que B cite n'est vu que de B."""
    direct, rows = [("Customer", "country", None)], [("Order", "item", "lines")]
    async with env.q(B) as q:
        assert await q.references_to("Country", "FR", direct) == []
        assert await q.references_to("Item", "X", rows) == []  # A a une ligne qui cite X : invisible pour B
        await q.insert("Customer", "b-uses-de", {"customer_name": "b", "country": "DE"})
        await q.insert("Order", "b-order", {"lines": [{"item": "Y"}, {"item": "Z"}]})
    async with env.q(B) as q:
        assert await q.references_to("Country", "DE", direct) == direct
        assert await q.references_to("Country", "FR", direct) == []  # la référence de A reste invisible
        assert await q.references_to("Item", "Z", rows) == rows  # une ligne qui n'est pas la première
        assert await q.references_to("Item", "X", rows) == []
        # Un document qui se cite lui-même n'est pas une référence.
        await q.insert("Customer", "self", {"customer_name": "s", "country": "self"})
        assert await q.references_to("Customer", "self", [("Customer", "country", None)]) == []


async def scenario_sync_unique(env: Env):
    """Une valeur unique est unique PAR organisation : B porte « X1 » alors que A la tient déjà, mais pas deux fois chez lui."""
    async with env.q(B) as q:
        await q.sync_unique("Customer", "shared", {"code": "X1"})  # la valeur de A, chez B : pas un conflit
        await q.sync_unique("Customer", "only-b", {"code": "B2"})
    async with env.q(B) as q:
        with pytest.raises(UniqueViolation) as raised:
            await q.sync_unique("Customer", "only-b", {"code": "X1"})  # chez B, « shared » la tient
        assert raised.value.field == "code"
    async with env.q(B) as q:
        await q.sync_unique("Customer", "only-b", {"code": "B3"})  # il change de valeur : B2 est libérée
        await q.sync_unique("Customer", "shared", {})  # plus de valeur : X1 est libérée chez B
    async with env.q(B) as q:
        await q.sync_unique("Customer", "only-b", {"code": "X1"})  # libre chez B, alors qu'elle reste prise chez A


async def scenario_drop_unique(env: Env):
    async with env.q(B) as q:
        await q.sync_unique("Customer", "shared", {"code": "X1"})
    async with env.q(B) as q:
        await q.drop_unique("Customer", "shared")  # libère celle de B ; « shared » de A garde la sienne
        await q.drop_unique("Customer", "only-a")  # n'existe que chez A : rien à libérer chez B
    async with env.q(B) as q:
        await q.sync_unique("Customer", "only-b", {"code": "X1"})  # libre chez B


async def scenario_rename(env: Env):
    """Renommer chez B ne touche ni au document de A ni à ses commentaires, pièces jointes, valeurs uniques ou historique."""
    async with env.q(B) as q:
        with pytest.raises(NotFound):  # n'existe que chez A
            await q.rename("Customer", "only-a", "only-a-2", scope=ALL)
    async with env.q(B) as q:
        await q.sync_unique("Customer", "shared", {"code": "X1"})
    async with env.q(B) as q:
        doc = await q.rename("Customer", "shared", "renamed", scope=ALL)
        assert (doc.name, doc.version) == ("renamed", 2)
    async with env.q(B) as q:
        assert await q.get("Customer", "shared", scope=ALL) is None
        assert (await q.get("Customer", "renamed", scope=ALL)).data["customer_name"] == f"{B}:shared"
        assert [c["content"] for c in (await q.comments("Customer", "renamed", limit=10))] == [f"note-{B}"]  # ses commentaires l'ont suivi
        assert await q.tags_of("Customer", ["renamed", "shared"]) == {"renamed": ["Urgent"], "shared": []}  # ses étiquettes aussi
        with pytest.raises(AlreadyExists):  # « only-b » existe chez B
            await q.rename("Customer", "renamed", "only-b", scope=ALL)
    async with env.q(B) as q:
        doc = await q.rename("Customer", "renamed", "only-a", scope=ALL)  # ce nom n'est pris que chez A : libre chez B
        assert doc.name == "only-a"
        with pytest.raises(UniqueViolation):  # la valeur « X1 » a suivi le document
            await q.sync_unique("Customer", "only-b", {"code": "X1"})


async def scenario_referrers_of(env: Env):
    """Seul A cite « FR » et « X » : B n'a aucun document à réécrire, et ce qu'il trouve est à lui."""
    direct, rows = [("Customer", "country", None)], [("Order", "item", "lines")]
    async with env.q(B) as q:
        assert await q.referrers_of("Country", "FR", direct) == []
        assert await q.referrers_of("Item", "X", rows) == []
        await q.insert("Customer", "b-uses-de", {"customer_name": "b", "country": "DE"})
        await q.insert("Order", "b-order", {"lines": [{"item": "X"}]})
    async with env.q(B) as q:
        assert [d.name for d in await q.referrers_of("Country", "DE", direct)] == ["b-uses-de"]
        assert [d.name for d in await q.referrers_of("Item", "X", rows)] == ["b-order"]  # pas « a-order »
        assert await q.referrers_of("Country", "FR", direct) == []


async def scenario_connections(env: Env):
    """Seul A cite « FR » et « X » : B n'en compte aucune connexion, et ce qu'il compte est à lui (dans son périmètre)."""
    direct, rows = [("Customer", "country", None)], [("Order", "item", "lines")]
    scopes = {"Customer": ALL, "Order": ALL}
    async with env.q(B) as q:
        assert await q.connections("Country", "FR", direct, scopes) == []
        assert await q.connections("Item", "X", rows, scopes) == []  # A a une ligne qui cite X : invisible pour B
        await q.insert("Customer", "b-uses-de", {"customer_name": "b", "country": "DE"})
        await q.insert("Order", "b-order", {"lines": [{"item": "X"}, {"item": "X"}]})
    async with env.q(B) as q:
        assert await q.connections("Country", "DE", direct, scopes) == [("Customer", "country", None, 1)]
        assert await q.connections("Item", "X", rows, scopes) == [("Order", "item", "lines", 1)]  # un document, pas deux lignes, et pas « a-order »
        assert await q.connections("Country", "FR", direct, scopes) == []


async def scenario_relink(env: Env):
    async with env.q(B) as q:
        with pytest.raises(NotFound):  # n'existe que chez A
            await q.relink("Customer", "only-a", {"customer_name": "pwned"})
    async with env.q(B) as q:
        doc = await q.relink("Customer", "shared", {"customer_name": "relinked"})
        assert (doc.data["customer_name"], doc.version) == ("relinked", 2)


async def scenario_add_version(env: Env):
    """Une entrée écrite par B s'ajoute à l'historique de B seulement, même pour un document de même nom que chez A."""
    async with env.q(B) as q:
        await q.add_version("Customer", "shared", version=2, action="update", user="user-globex", doc_owner="user-globex",
                            changes={"customer_name": {"old": "globex:shared", "new": "b"}})
    async with env.q(B) as q:
        items, total = await q.versions("Customer", "shared", limit=50, offset=0)
        assert total == 1 and [i["user"] for i in items] == ["user-globex"]  # pas l'entrée de A sur « shared »


async def scenario_versions(env: Env):
    async with env.q(B) as q:
        assert await q.versions("Customer", "shared", limit=50, offset=0) == ([], 0)  # A a une entrée sur « shared », B aucune
        assert await q.versions("Customer", "gone-a", limit=50, offset=0) == ([], 0)
        assert await q.versions("Customer", "only-a", limit=50, offset=10) == ([], 0)


async def scenario_last_deletion(env: Env):
    async with env.q(B) as q:
        assert await q.last_deletion("Customer", "gone-a") is None  # supprimé chez A : B n'en voit ni l'existence ni le contenu


async def scenario_saved_views(env: Env):
    """Les vues sauvegardées d'un tenant n'apparaissent jamais chez l'autre, même DocType ou même nom d'utilisateur."""
    async with env.q(B) as q:
        assert [v["name"] for v in await q.saved_views("Customer", f"user-{B}")] == [f"view-{B}"]
        # Le propriétaire de A, vu depuis B, ne ramène rien : c'est le tenant qui borne, pas le seul nom d'utilisateur.
        assert await q.saved_views("Customer", f"user-{A}") == []


async def scenario_get_saved_view(env: Env):
    async with env.q(B) as q:
        assert await q.get_saved_view("Customer", f"view-{A}", f"user-{B}") is None
        assert await q.get_saved_view("Customer", f"view-{A}", f"user-{A}") is None  # même owner, autre tenant
        found = await q.get_saved_view("Customer", f"view-{B}", f"user-{B}")
        assert found is not None and found["label"] == B


async def scenario_put_saved_view(env: Env):
    """Écrire une vue dont le nom existe chez A n'écrit que chez B (nom commun, tenants différents)."""
    async with env.q(B) as q:
        await q.put_saved_view("Customer", f"view-{A}", owner=f"user-{B}", label="B-copy", kind="list", config={}, is_default=False)
    async with env.q(B) as q:
        made = await q.get_saved_view("Customer", f"view-{A}", f"user-{B}")
        assert made is not None and made["label"] == "B-copy"
        # Une vue par défaut de B ne désarme pas celle de A (le nettoyage reste dans le tenant de l'appelant).
        await q.put_saved_view("Customer", "b-default", owner=f"user-{B}", label="B", kind="list", config={}, is_default=True)


async def scenario_delete_saved_view(env: Env):
    async with env.q(B) as q:
        assert await q.delete_saved_view("Customer", f"view-{A}", f"user-{B}") is False  # n'appartient qu'à A
        assert await q.delete_saved_view("Customer", f"view-{B}", f"user-{B}") is True


async def scenario_get_override(env: Env):
    """L'habillage de A (« Customer ») n'est pas celui de B, même cible, même genre."""
    async with env.q(B) as q:
        found = await q.get_override("form", "Customer")
        assert found is not None and found["config"] == {"label": B}  # celui de B, pas celui de A
        assert await q.get_override("form", "Order") is None


async def scenario_overrides(env: Env):
    async with env.q(B) as q:
        assert [o["target"] for o in await q.overrides("form")] == ["Customer"]  # un seul, celui de B


async def scenario_put_override(env: Env):
    """Écrire la cible de A n'écrase que chez B (même genre + même cible, tenants différents)."""
    async with env.q(B) as q:
        await q.put_override("form", "Customer", owner=f"user-{B}", config={"label": "B-copy"})
    async with env.q(B) as q:
        made = await q.get_override("form", "Customer")
        assert made is not None and made["config"] == {"label": "B-copy"}


async def scenario_delete_override(env: Env):
    async with env.q(B) as q:
        assert await q.delete_override("workspace", "shop") is False  # la cible n'existe que chez A
        assert await q.delete_override("form", "Customer") is True


async def scenario_comments(env: Env):
    """Les commentaires d'un document de B sont ceux de B : ceux de A (même type, même nom de document) n'apparaissent jamais."""
    mine, theirs = env.ids[B], env.ids[A]
    async with env.q(B) as q:
        assert [c["content"] for c in await q.comments("Customer", "shared", limit=50)] == [f"note-{B}"]
        assert await q.get_comment(theirs["comment"]) is None
        assert (await q.get_comment(mine["comment"]))["content"] == f"note-{B}"
        assert await q.count_comments("Customer", "shared") == 1
        made = await q.add_comment("Customer", "shared", "second")
        assert await q.count_comments("Customer", "shared") == 2 and made["owner"] == f"user-{B}"


async def scenario_update_comment(env: Env):
    async with env.q(B) as q:
        assert await q.update_comment(env.ids[A]["comment"], "pirate") is False  # le commentaire de A n'existe pas pour B
        assert await q.update_comment(env.ids[B]["comment"], "modifié") is True


async def scenario_delete_comment(env: Env):
    async with env.q(B) as q:
        assert await q.delete_comment(env.ids[A]["comment"]) is False
        assert await q.delete_comment(env.ids[B]["comment"]) is True


async def scenario_assignments(env: Env):
    mine, theirs = env.ids[B], env.ids[A]
    async with env.q(B) as q:
        assert [a["description"] for a in await q.assignments("Customer", "shared")] == [f"todo-{B}"]
        assert await q.get_assignment(theirs["assignment"]) is None
        assert (await q.get_assignment(mine["assignment"]))["assignee"] == f"user-{B}"
        # « Ce qu'on m'a confié » : même un utilisateur de MÊME NOM chez A ne ramène rien de A.
        assert [a["id"] for a in await q.assignments_of(f"user-{B}", status=None, limit=50)] == [mine["assignment"]]
        assert await q.assignments_of(f"user-{A}", status=None, limit=50) == []
        made = await q.add_assignment("Customer", "shared", assignee="bob", description=None, due_date=None)
        assert made["status"] == "open" and made["assigned_by"] == f"user-{B}"


async def scenario_update_assignment(env: Env):
    async with env.q(B) as q:
        assert await q.update_assignment(env.ids[A]["assignment"], status="done") is False
        assert await q.update_assignment(env.ids[B]["assignment"], status="done") is True


async def scenario_attachments(env: Env):
    mine, theirs = env.ids[B], env.ids[A]
    async with env.q(B) as q:
        assert [a["id"] for a in await q.attachments("Customer", "shared")] == [mine["attachment"]]
        assert await q.get_attachment(theirs["attachment"]) is None
        assert await q.attachment_of_file(theirs["file"]) is None  # le fichier de A n'est joint à rien, vu de B
        assert (await q.attachment_of_file(mine["file"]))["id"] == mine["attachment"]
        assert await q.add_attachment("Customer", "shared", "c" * 32) is not None


async def scenario_delete_attachment(env: Env):
    async with env.q(B) as q:
        assert await q.delete_attachment(env.ids[A]["attachment"]) is False
        assert await q.delete_attachment(env.ids[B]["attachment"]) is True


async def scenario_remove_collaboration(env: Env):
    """Supprimer les commentaires, assignations et jointes de « shared » chez B laisse ceux de « shared » chez A."""
    async with env.q(B) as q:
        assert await q.remove_collaboration("Customer", "shared") == [env.ids[B]["file"]]
        assert await q.comments("Customer", "shared", limit=50) == [] and await q.assignments("Customer", "shared") == []
        assert await q.tags_of("Customer", ["shared"]) == {"shared": []}


async def scenario_tags(env: Env):
    """Les étiquettes de « shared » chez B sont celles de B : « Secret » (que seul A pose, même nom de document) n'apparaît jamais, et poser
    une étiquette que A porte déjà ne heurte rien chez A."""
    async with env.q(B) as q:
        assert await q.tags_of("Customer", ["shared", "only-a", "needle"]) == {"shared": ["Urgent"], "only-a": [], "needle": []}
        assert await q.add_tag("Customer", "shared", "Secret") is True  # libre chez B, bien que A l'ait sur son « shared »
        assert await q.add_tag("Customer", "shared", "secret") is False  # même étiquette, autre casse : déjà là
        assert (await q.tags_of("Customer", ["shared"]))["shared"] == ["Urgent", "Secret"]


async def scenario_remove_tag(env: Env):
    async with env.q(B) as q:
        assert await q.remove_tag("Customer", "needle", "Secret") is False  # « needle » n'est étiqueté que chez A
        assert await q.remove_tag("Customer", "shared", "URGENT") is True  # la casse écrite ne compte pas


async def scenario_tag_spelling(env: Env):
    """L'écriture d'une étiquette ne vient jamais de A : « Secret » n'existe pas chez B, « urgent » s'y écrit comme B l'a posée."""
    async with env.q(B) as q:
        assert await q.tag_spelling(env.customer.definition, "secret", scope=ALL) is None
        assert await q.tag_spelling(env.customer.definition, "URGENT", scope=ALL) == "Urgent"  # pas « URGENT » (celle de A)


async def scenario_tag_counts(env: Env):
    """Les comptes de B ne comptent que ses documents : « Urgent » 1 (A en a 2), « Secret » absent ; le filtre `_tag` aussi ne voit que B."""
    defn = env.customer.definition
    async with env.q(B) as q:
        assert await q.tag_counts(defn, filters={}, scope=ALL, limit=20) == [{"tag": "Urgent", "count": 1}]
        assert await q.tag_counts(defn, filters={}, scope=ALL, q="sec", limit=20) == []
        assert await q.tag_counts(defn, filters={"_tag": "Urgent"}, scope=ALL, limit=20) == [{"tag": "Urgent", "count": 1}]


async def scenario_has_tags(env: Env):
    """Seul A a une étiquette sur « needle » : vu de B, un type où B n'a rien étiqueté n'en a aucune, quoi que A ait posé."""
    async with env.q(B) as q:
        assert await q.has_tags(env.customer.definition, scope=ALL) is True  # « Urgent » sur le « shared » de B
        await q.remove_tag("Customer", "shared", "Urgent")
        assert await q.has_tags(env.customer.definition, scope=ALL) is False  # A en a trois, B plus aucune


def _friend_scope(action="read"):
    return Scope(action, unrestricted=True, doctype="Customer", user="friend", restrict=(("Customer", None),), share=action in ("read", "write"))


async def scenario_permission_facts(env: Env):
    """Les restrictions et partages de « friend » vus de B : ceux de B seulement. La restriction de A (« only-a ») et le partage en
    écriture de A n'existent pas pour B."""
    async with env.q(B) as q:
        assert await q.permission_facts(_friend_scope("read"), "shared") == Facts({}, True)
        assert await q.permission_facts(_friend_scope("write"), "shared") == Facts({}, False)  # chez B, le partage est en lecture
        assert await q.permission_facts(_friend_scope("read"), "needle") == Facts({}, False)  # partagé chez A seulement


async def scenario_shares(env: Env):
    async with env.q(B) as q:
        assert [(r["docname"], r["member"], r["write"]) for r in await q.shares("Customer", "shared")] == [("shared", "friend", False)]
        assert await q.shares("Customer", "needle") == []
        await q.put_share("Customer", "shared", "friend", write=True)  # change le niveau chez B, jamais chez A
        await q.put_share("Customer", "only-b", "other", write=False)
        assert [r["write"] for r in await q.shares("Customer", "shared")] == [True]


async def scenario_restricted(env: Env):
    """La permission utilisateur de « friend » posée chez A (« only-a ») ne le borne pas chez B."""
    async with env.q(B) as q:
        assert await q.restricted(_friend_scope()) is False
        await q.insert(USER_PERMISSION, "up-b", {"user": "friend", "allow": "Customer", "for_value": "shared"})
        assert await q.restricted(_friend_scope()) is True


async def scenario_share_level(env: Env):
    """Chez B, « friend » n'a que des partages en lecture sur Customer : le partage en écriture de A ne compte pas."""
    async with env.q(B) as q:
        assert await q.share_level("Customer", "friend") == "read"
        assert await q.share_level("Customer", "nobody") is None


async def scenario_remove_share(env: Env):
    async with env.q(B) as q:
        assert await q.remove_share("Customer", "needle", "friend") is False  # n'existe que chez A
        assert await q.remove_share("Customer", "shared", "friend") is True


async def scenario_shared_with(env: Env):
    """« Partagés avec moi » pour « friend » chez B : le « shared » de B, pas les deux partages de A (même membre, même nom de document)."""
    async with env.q(B) as q:
        found = await q.shared_with("friend", limit=50)
        assert [(r["name"], r["write"], r["data"]["customer_name"]) for r in found] == [("shared", False, f"{B}:shared")]


async def scenario_people(env: Env):
    """Les identités proposées sont celles du tenant : « user-acme » n'est pas proposé à globex."""
    async with env.q(B) as q:
        found = await q.people(prefix="user-", limit=20)
        assert f"user-{B}" in found and f"user-{A}" not in found


SCENARIOS = {
    "add_comment": scenario_comments,
    "comments": scenario_comments,
    "get_comment": scenario_comments,
    "count_comments": scenario_comments,
    "update_comment": scenario_update_comment,
    "delete_comment": scenario_delete_comment,
    "add_assignment": scenario_assignments,
    "assignments": scenario_assignments,
    "get_assignment": scenario_assignments,
    "assignments_of": scenario_assignments,
    "update_assignment": scenario_update_assignment,
    "add_attachment": scenario_attachments,
    "attachments": scenario_attachments,
    "get_attachment": scenario_attachments,
    "attachment_of_file": scenario_attachments,
    "delete_attachment": scenario_delete_attachment,
    "remove_collaboration": scenario_remove_collaboration,
    "people": scenario_people,
    "tags_of": scenario_tags,
    "add_tag": scenario_tags,
    "remove_tag": scenario_remove_tag,
    "tag_spelling": scenario_tag_spelling,
    "tag_counts": scenario_tag_counts,
    "has_tags": scenario_has_tags,
    "permission_facts": scenario_permission_facts,
    "shares": scenario_shares,
    "put_share": scenario_shares,
    "remove_share": scenario_remove_share,
    "share_level": scenario_share_level,
    "restricted": scenario_restricted,
    "shared_with": scenario_shared_with,
    "saved_views": scenario_saved_views,
    "get_saved_view": scenario_get_saved_view,
    "put_saved_view": scenario_put_saved_view,
    "delete_saved_view": scenario_delete_saved_view,
    "get_override": scenario_get_override,
    "overrides": scenario_overrides,
    "put_override": scenario_put_override,
    "delete_override": scenario_delete_override,
    "add_version": scenario_add_version,
    "versions": scenario_versions,
    "last_deletion": scenario_last_deletion,
    "existing_names": scenario_existing_names,
    "existing_docstatus": scenario_existing_docstatus,
    "purge_versions": scenario_purge_versions,
    "purge_versions_before": scenario_purge_versions_before,
    "lock_for_delete": scenario_lock_for_delete,
    "references_to": scenario_references_to,
    "connections": scenario_connections,
    "referrers_of": scenario_referrers_of,
    "relink": scenario_relink,
    "rename": scenario_rename,
    "sync_unique": scenario_sync_unique,
    "drop_unique": scenario_drop_unique,
    "next_number": scenario_next_number,
    "get": scenario_get,
    "list": scenario_list,
    "search": scenario_search,
    "aggregate": scenario_aggregate,
    "add_file": scenario_files,
    "get_files": scenario_files,
    "file_cited": scenario_files,
    "remove_files": scenario_files,
    "cited_files": scenario_cited_files,
    "attachment_doctypes": scenario_attachment_doctypes,
    "insert": scenario_insert,
    "update": scenario_update,
    "delete": scenario_delete,
    "insert_if_absent": scenario_insert_if_absent,
    "transition": scenario_transition,
    "workflow_transition": scenario_workflow_transition,
}


def test_every_public_method_has_a_scenario():
    assert PUBLIC == set(SCENARIOS), (
        "Query a changé : ajoutez un scénario de fuite pour "
        f"{sorted(PUBLIC - set(SCENARIOS))} (ou retirez {sorted(set(SCENARIOS) - PUBLIC)})"
    )


async def check_isolation(method: str, env: Env) -> None:
    """Le scénario de `method` en tant que B, puis les deux garde-fous : A est intact, et chaque instruction est bornée à B."""
    await SCENARIOS[method](env)

    # 1. Rien n'a changé chez A.
    env.recording = False
    assert await env.rows(A) == env.snapshot_a, f"{method}: les données de {A} ont été modifiées"

    # 2. Chaque instruction émise est bornée au tenant de l'appelant.
    assert env.captured, f"{method}: aucun SQL capturé, le garde-fou ne vérifie rien"
    for statement, params in env.captured:
        bound = list(params.values()) if isinstance(params, dict) else list(params)
        assert "tenant_id" in statement, f"{method}: instruction sans filtre de tenant :\n{statement}"
        # Égalité exacte, pas de sous-chaîne : une valeur de filtre peut légitimement contenir « acme ».
        assert B in bound, f"{method}: le tenant de l'appelant n'est pas lié :\n{statement}\n{params}"
        assert A not in bound, f"{method}: le tenant {A} est lié dans une instruction de {B} :\n{params}"


@pytest.mark.parametrize("method", sorted(SCENARIOS))
async def test_no_cross_tenant_access(method, env: Env):
    await check_isolation(method, env)
