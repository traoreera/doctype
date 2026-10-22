"""Couche de requêtes unique (D3, annexe A). Aucune requête ne se construit ailleurs.

Règles (doc/doctype.md, annexe A) :
- le seul constructeur est `Query(session, ctx, tables)` ; le tenant vient de `ctx`, jamais d'un argument ;
- toute instruction passe par `_scope()`, qui impose `tenant_id` et le périmètre de permission (`Scope`) ;
- `scope` est un paramètre OBLIGATOIRE des lectures, mises à jour et suppressions : l'oublier est une
  erreur, jamais un accès sans restriction (`Scope.everything(...)` est explicite) ;
- tout opérateur propre à un dialecte reste ici.

Chaque méthode publique doit avoir un scénario dans `tests/unit/test_query_leak.py`
(le test échoue si une méthode publique n'est pas couverte).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Collection, Mapping, Sequence

from sqlalchemy import and_, cast, delete, exists, false, func, insert, literal, literal_column, or_, select, true, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .aggregation import BUCKET_WIDTH, SYSTEM_CATEGORIES, SYSTEM_TIMES, Group, Metric
from .builder import FILTER_OPS, SYSTEM_FILTERS, Filter
from .decimals import MAX_DIGITS
from .context import RequestContext
from .db.tables import SystemTables
from .errors import AlreadyExists, DocumentLocked, InvalidDocStatus, NotFound, ValidationFailed, VersionConflict
from .permissions import Scope
from .schema import DECIMAL_TYPES, DocTypeDef

# Colonnes système triables. Les champs d'un DocType s'y ajoutent, jamais l'inverse
# (leurs noms réservés interdisent toute collision, voir schema.RESERVED_FIELDNAMES).
SYSTEM_SORTABLE = ("name", "creation", "modified", "owner", "docstatus")

# États d'un document soumettable (colonne `docstatus`).
DRAFT, SUBMITTED, CANCELLED = 0, 1, 2
STATUS_WORDS = {DRAFT: "un brouillon", SUBMITTED: "soumis", CANCELLED: "annulé"}


class UniqueViolation(Exception):
    """Une valeur `unique` est déjà portée par un autre document : `field` dit lequel de ses champs."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


def _typed(ftype: str, value: Any) -> Any:
    """Un filtre Decimal arrive en chaîne canonique (la forme stockée) mais se compare en NUMERIC : il faut un `Decimal` à lier."""
    if ftype not in DECIMAL_TYPES or value is None:
        return value
    if isinstance(value, Filter):
        operand = [Decimal(v) for v in value.value] if value.op == "in" else Decimal(value.value)
        return Filter(value.op, operand)
    return Decimal(value)


def _utc(dt: datetime) -> datetime:
    # SQLite restitue des datetimes naïfs : on les relit comme de l'UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class StoredDoc:
    doctype: str
    name: str
    data: dict[str, Any]
    owner: str
    creation: datetime
    modified: datetime
    version: int
    docstatus: int = DRAFT
    amended_from: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "doctype": self.doctype,
            "name": self.name,
            "owner": self.owner,
            "creation": self.creation.isoformat(),
            "modified": self.modified.isoformat(),
            "version": self.version,
            "docstatus": self.docstatus,
            "amended_from": self.amended_from,
            "data": self.data,
        }


class Query:
    def __init__(self, session: AsyncSession, ctx: RequestContext, tables: SystemTables) -> None:
        if not isinstance(ctx, RequestContext):
            raise TypeError("Query exige un RequestContext")
        self._session = session
        self._tenant = ctx.tenant_id
        self._owner = ctx.owner_id
        self._t = tables.documents
        self._naming = tables.naming
        self._versions = tables.versions
        self._files = tables.files
        self._views = tables.saved_views
        self._overrides = tables.overrides
        self._comments = tables.comments
        self._assignments = tables.assignments
        self._attachments = tables.attachments
        self._uniques = tables.uniques

    # ── Lecture ───────────────────────────────────────────────
    async def get(self, doctype: str, name: str, *, scope: Scope, lock: bool = False) -> StoredDoc | None:
        """`lock` : `SELECT … FOR UPDATE`, la ligne est à nous jusqu'au commit (PostgreSQL ; voir `begin_exclusive` pour SQLite).

        Un lecteur qui va écrire d'après ce qu'il lit (le solde d'un stock) verrouille : une lecture simple laisserait deux
        transactions partir de la même valeur. READ COMMITTED : celle qui attend relit la valeur validée par l'autre.
        """
        stmt = select(self._t).where(
            self._scope(self._t.c.doctype == doctype, self._t.c.name == name, scope=scope)
        )
        if lock:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).mappings().one_or_none()
        return self._doc(row) if row is not None else None

    async def list(
        self,
        defn: DocTypeDef,
        *,
        filters: Mapping[str, Any],
        order_by: str,
        descending: bool,
        limit: int,
        offset: int,
        scope: Scope,
    ) -> tuple[list[StoredDoc], int]:
        """`filters` : égalités déjà validées par type (voir `CompiledDocType.coerce_filter`)."""
        conds = self._filter_conds(defn, filters)
        sort = self._sort_expr(defn, order_by)
        sort = sort.desc().nulls_last() if descending else sort.asc().nulls_last()

        page = (
            select(self._t)
            .where(self._scope(*conds, scope=scope))
            # `name` départage les ex æquo : sans lui la pagination peut dupliquer ou perdre des lignes.
            .order_by(sort, self._t.c.name.asc())
            .limit(limit)
            .offset(offset)
        )
        total = select(func.count()).select_from(self._t).where(self._scope(*conds, scope=scope))
        rows = (await self._session.execute(page)).mappings().all()
        count = (await self._session.execute(total)).scalar_one()
        return [self._doc(r) for r in rows], count

    async def aggregate(
        self,
        defn: DocTypeDef,
        *,
        filters: Mapping[str, Any],
        group: Group | None,
        metrics: Sequence[Metric],
        keys: Sequence[str] | None,
        limit: int,
        scope: Scope,
    ) -> tuple[list[tuple[Any, ...]], bool]:
        """Compte ou additionne les documents du périmètre `scope`, regroupés ou non. Retourne `(lignes, tronqué)`.

        Une ligne est `(clé, *mesures)` avec un regroupement, `(*mesures)` sans. `keys` : la fenêtre de temps (ses clés bornent un
        regroupement par date, voir `aggregation.window`). Les filtres sont ceux de la liste. Le périmètre de lecture de l'appelant
        s'applique comme à la liste : on ne compte que ce qu'il pourrait ouvrir.

        Un regroupement par date lit la clé comme une chaîne (`2026-10`) : la même expression sert au SELECT, au GROUP BY, au WHERE et
        au tri, c'est ce qui permet à PostgreSQL de reconnaître le groupe (des paramètres liés différents n'y seraient pas égaux).
        """
        conds = self._filter_conds(defn, filters)
        cols = [self._metric_expr(defn, m) for m in metrics]
        if group is None:
            stmt = select(*cols).where(self._scope(*conds, scope=scope))
            row = (await self._session.execute(stmt)).one()
            return [tuple(row)], False

        key = self._group_expr(defn, group)
        if group.is_time:
            conds.append(key.is_not(None))  # un document sans date n'a pas de place sur une ligne de temps
            if keys:
                conds.append(key.between(keys[0], keys[-1]))
        stmt = select(key, *cols).where(self._scope(*conds, scope=scope)).group_by(key)
        if group.is_time:
            stmt = stmt.order_by(key.desc())  # tronquer garde les périodes les plus récentes
        else:
            stmt = stmt.order_by(cols[0].desc().nulls_last(), key.asc().nulls_last())  # les plus gros groupes d'abord
        rows = [tuple(r) for r in (await self._session.execute(stmt.limit(limit + 1))).all()]
        return rows[:limit], len(rows) > limit

    async def search(self, defn: DocTypeDef, q: str, *, limit: int, scope: Scope, filters: Mapping[str, Any] | None = None) -> list[StoredDoc]:
        """Documents dont le nom ou le `title_field` CONTIENT `q` (sans casse), pour l'autocomplétion d'un champ Link.

        `filters` : déjà validés par type (comme pour `list`), par exemple les `link_filters` du champ qu'on complète (D29).
        """
        pattern = "%" + self._like_escape(q) + "%"
        match = [self._t.c.name.ilike(pattern, escape="\\")]
        order: list[Any] = []
        if defn.title_field is not None:
            title = self._json_expr(defn, defn.title_field)
            match.append(title.ilike(pattern, escape="\\"))
            order.append(title.asc().nulls_last())
        stmt = (
            select(self._t)
            .where(self._scope(self._t.c.doctype == defn.name, or_(*match), *self._filter_conds(defn, filters or {}), scope=scope))
            .order_by(*order, self._t.c.name.asc())
            .limit(limit)
        )
        return [self._doc(r) for r in (await self._session.execute(stmt)).mappings().all()]

    # ── Écriture ──────────────────────────────────────────────
    async def insert(self, doctype: str, name: str, data: dict[str, Any], *, amended_from: str | None = None) -> StoredDoc:
        values = self._new_row(doctype, name, data, amended_from)
        try:
            await self._session.execute(insert(self._t).values(**values))
        except IntegrityError as e:
            raise AlreadyExists(f"{doctype} {name!r} existe déjà") from e
        return self._doc(values)

    async def insert_if_absent(self, doctype: str, name: str, data: dict[str, Any]) -> bool:
        """Crée le document s'il n'existe pas (`ON CONFLICT DO NOTHING`), et dit si c'est ce appel qui l'a créé.

        Une seule instruction, donc sûre sous concurrence : deux transactions qui créent le même document ensemble (le solde d'un
        article jamais reçu) ne se heurtent pas à une erreur d'unicité, la seconde attend le commit de la première (verrou de
        l'index unique) puis ne fait rien. Une ligne existante n'est jamais modifiée.
        """
        insert_for = postgresql.insert if self._session.get_bind().dialect.name == "postgresql" else sqlite.insert
        stmt = insert_for(self._t).values(**self._new_row(doctype, name, data, None)).on_conflict_do_nothing(
            index_elements=[self._t.c.tenant_id, self._t.c.doctype, self._t.c.name]
        )
        return (await self._session.execute(stmt)).rowcount > 0

    def _new_row(self, doctype: str, name: str, data: dict[str, Any], amended_from: str | None) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "tenant_id": self._tenant,
            "doctype": doctype,
            "name": name,
            "data": data,
            "owner": self._owner,
            "creation": now,
            "modified": now,
            "version": 1,
            "docstatus": DRAFT,
            "amended_from": amended_from,
        }

    async def update(
        self, doctype: str, name: str, data: dict[str, Any], expected_version: int, *, scope: Scope, docstatus: int = DRAFT
    ) -> StoredDoc:
        """Remplace `data` si `version` et `scope` correspondent (D15). `UPDATE` conditionnel, pas de contrôle en Python.

        `scope` est le périmètre d'ÉCRITURE sur la ligne actuelle ; la valeur écrite est vérifiée par l'appelant.
        `docstatus` : l'état dans lequel le document doit être (un brouillon par défaut ; `SUBMITTED` pour les champs `allow_on_submit` d'un document soumis).
        """
        stmt = (
            update(self._t)
            .where(
                self._scope(
                    self._t.c.doctype == doctype,
                    self._t.c.name == name,
                    self._t.c.version == expected_version,
                    self._t.c.docstatus == docstatus,  # un document soumis ou annulé est figé : aucune course ne l'écrit
                    scope=scope,
                )
            )
            .values(data=data, modified=datetime.now(UTC), version=self._t.c.version + 1)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            await self._explain_miss(doctype, name, expected_version, locked=True, expected_status=docstatus)
        return await self._reload(doctype, name)

    async def transition(
        self, doctype: str, name: str, *, source: int, target: int, expected_version: int, scope: Scope
    ) -> StoredDoc:
        """Fait passer le document de l'état `source` à `target` (soumission, annulation), `data` inchangé.

        Un seul `UPDATE` conditionnel sur l'état ET la version : deux soumissions simultanées du même brouillon n'en
        passent qu'une, l'autre trouve le document déjà soumis. C'est aussi ce qui rend la soumission idempotente. Appelé EN
        PREMIER dans la transaction : sur SQLite cette écriture prend le verrou de la base pour tout le reste.
        """
        stmt = (
            update(self._t)
            .where(
                self._scope(
                    self._t.c.doctype == doctype,
                    self._t.c.name == name,
                    self._t.c.version == expected_version,
                    self._t.c.docstatus == source,
                    scope=scope,
                )
            )
            .values(docstatus=target, modified=datetime.now(UTC), version=self._t.c.version + 1)
        )
        if (await self._session.execute(stmt)).rowcount == 0:
            await self._explain_miss(doctype, name, expected_version, expected_status=source)
        return await self._reload(doctype, name)

    async def _reload(self, doctype: str, name: str) -> StoredDoc:
        doc = await self.get(doctype, name, scope=Scope.everything("read"))
        assert doc is not None  # la ligne vient d'être écrite dans cette transaction
        return doc

    async def _explain_miss(
        self, doctype: str, name: str, expected_version: int, *, locked: bool = False, expected_status: int = DRAFT
    ) -> None:
        """Un UPDATE conditionnel n'a rien touché : absent, figé / dans un autre état, ou version périmée (même tenant)."""
        found = await self.get(doctype, name, scope=Scope.everything("read"))
        if found is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        if found.docstatus != expected_status:
            if locked:
                raise DocumentLocked(f"{doctype} {name!r} est {STATUS_WORDS[found.docstatus]} : il n'est plus modifiable")
            raise InvalidDocStatus(f"{doctype} {name!r} est {STATUS_WORDS[found.docstatus]}")
        raise VersionConflict(f"{doctype} {name!r} a été modifié (version attendue {expected_version})")

    async def delete(self, doctype: str, name: str, *, scope: Scope) -> bool:
        stmt = delete(self._t).where(
            # Seul un brouillon se supprime : soumis ou annulé, un document reste (et sa trace avec lui).
            self._scope(self._t.c.doctype == doctype, self._t.c.name == name, self._t.c.docstatus == DRAFT, scope=scope)
        )
        return (await self._session.execute(stmt)).rowcount > 0

    async def existing_names(self, doctype: str, names: Collection[str], *, scope: Scope, lock: bool = False) -> set[str]:
        """Parmi `names`, ceux qui existent ET que `scope` permet de lire (contrôle des champs Link à l'écriture).

        `lock` verrouille les lignes trouvées en « partage de clé » (`FOR KEY SHARE` sur PostgreSQL ; SQLite n'a pas de
        verrou de ligne et sérialise déjà ses écritures) : une suppression concurrente de la cible doit attendre la fin de
        cette transaction, puis voit le nouveau document qui la référence (voir `references_to`). Une modification ordinaire
        de la cible n'est pas bloquée.
        """
        if not names:
            return set()
        stmt = select(self._t.c.name).where(
            self._scope(self._t.c.doctype == doctype, self._t.c.name.in_(sorted(names)), scope=scope)
        )
        if lock:
            stmt = stmt.with_for_update(read=True, key_share=True)
        return set((await self._session.execute(stmt)).scalars())

    async def existing_docstatus(self, doctype: str, names: Collection[str], *, scope: Scope, lock: bool = False) -> dict[str, int]:
        """Parmi `names`, les documents lisibles par `scope` AVEC leur `docstatus` (contrôle de l'état d'une cible de lien, D23).

        Même lecture que `existing_names` (existence + périmètre, verrou `lock` identique) : le champ en plus permet de refuser
        un lien vers un document annulé, par exemple.
        """
        if not names:
            return {}
        stmt = select(self._t.c.name, self._t.c.docstatus).where(
            self._scope(self._t.c.doctype == doctype, self._t.c.name.in_(sorted(names)), scope=scope)
        )
        if lock:
            stmt = stmt.with_for_update(read=True, key_share=True)
        return {row[0]: row[1] for row in (await self._session.execute(stmt)).all()}

    async def lock_for_delete(self, doctype: str, name: str) -> None:
        """Prend le verrou exclusif de la ligne avant de chercher ses références (`FOR UPDATE`, sans effet sur SQLite)."""
        stmt = select(self._t.c.name).where(
            self._scope(self._t.c.doctype == doctype, self._t.c.name == name, scope=Scope.everything("delete"))
        )
        await self._session.execute(stmt.with_for_update())

    async def references_to(
        self, target: str, name: str, referrers: Sequence[tuple[Any, ...]]
    ) -> list[tuple[Any, ...]]:
        """Les références parmi `referrers` dont au moins un document pointe vers `target`/`name`.

        Une référence est `(doctype, champ, table)` ou, pour un champ DynamicLink, `(doctype, champ, table, champ_type)` : le champ_type dit
        DANS quel DocType chercher, il doit valoir `target`. `table` est le champ Table dont les LIGNES portent le Link (`None` : le Link est dans le
        document lui-même). La réponse reprend les références telles qu'elles ont été données.
        VOLONTAIREMENT sans périmètre de permission : une référence que l'utilisateur ne peut pas lire empêche quand
        même la suppression, sinon il laisserait un lien orphelin. Le tenant, lui, reste imposé par `_scope`.
        """
        used = []
        for ref in referrers:
            doctype, fieldname, table, *rest = ref
            cond = self._points_to(fieldname, name, table, rest[0] if rest else None, target)
            conds = [self._t.c.doctype == doctype, cond]
            if doctype == target:
                conds.append(self._t.c.name != name)  # un document qui se cite lui-même ne se bloque pas
            stmt = select(literal(1)).where(self._scope(*conds)).limit(1)
            if (await self._session.execute(stmt)).first() is not None:
                used.append(ref)
        return used

    async def connections(
        self, target: str, name: str, referrers: Sequence[tuple[Any, ...]], scopes: Mapping[str, Scope]
    ) -> list[tuple[Any, ...]]:
        """Pour chaque référence de `referrers` (même forme que `references_to`) : combien de documents LISIBLES par l'appelant citent `target`/`name`.

        Contrairement à `references_to` (qui protège l'intégrité et ne regarde donc aucun périmètre), c'est une réponse faite à un utilisateur :
        chaque DocType est compté dans SON périmètre de lecture (`scopes[doctype]`), un document hors de portée n'existe pas. Rend
        `(doctype, champ, table, compte)` pour les seules références dont le compte n'est pas nul.
        """
        found = []
        for ref in referrers:
            doctype, fieldname, table, *rest = ref
            conds = [self._t.c.doctype == doctype, self._points_to(fieldname, name, table, rest[0] if rest else None, target)]
            if doctype == target:
                conds.append(self._t.c.name != name)
            stmt = select(func.count()).select_from(self._t).where(self._scope(*conds, scope=scopes[doctype]))
            if count := (await self._session.execute(stmt)).scalar_one():
                found.append((doctype, fieldname, table, count))
        return found

    def _points_to(self, fieldname: str, name: str, table: str | None, type_field: str | None = None, target: str | None = None) -> Any:
        """Le document (ou une ligne de sa table `table`) a `fieldname == name` ; pour un DynamicLink (`type_field`), son champ de type vaut aussi `target`. Opérateurs JSON propres à chaque base."""
        if table is None:
            cond = self._t.c.data[fieldname].as_string() == name
            return cond if type_field is None else and_(cond, self._t.c.data[type_field].as_string() == target)
        if self._session.get_bind().dialect.name == "postgresql":
            # Contenance JSONB : `data -> 'items' @> '[{"item": "X"}]'`. Les noms de champs viennent d'une définition validée.
            wanted = {fieldname: name, **({type_field: target} if type_field else {})}
            return cast(self._t.c.data[table].as_json(), JSONB).contains([wanted])
        rows = func.json_each(self._t.c.data, f"$.{table}").table_valued("value", joins_implicitly=True)
        match = [func.json_extract(rows.c.value, f"$.{fieldname}") == name]
        if type_field:
            match.append(func.json_extract(rows.c.value, f"$.{type_field}") == target)
        return exists(select(literal(1)).select_from(rows).where(and_(*match)))

    # ── Unicité (`unique: true`) ──────────────────────────────
    async def sync_unique(self, doctype: str, name: str, wanted: Mapping[str, str]) -> None:
        """Fait en sorte que les valeurs `unique` de ce document soient EXACTEMENT `wanted` (`{champ: valeur}`) : retire ce qui a changé, réserve le nouveau.

        La clé primaire de `xdt_unique` est la garantie : si une autre transaction tient la même valeur, l'insertion échoue (`UniqueViolation`), sans course possible
        entre « lire » et « écrire ». À appeler DANS la transaction du document : tout se défait avec lui.
        """
        t = self._uniques
        held = {(r["fieldname"], r["value"]) for r in (await self._session.execute(select(t.c.fieldname, t.c.value).where(self._cscope(t, t.c.doctype == doctype, t.c.docname == name)))).mappings().all()}
        want = set(wanted.items())
        for field, value in sorted(held - want):
            await self._session.execute(delete(t).where(self._cscope(t, t.c.doctype == doctype, t.c.fieldname == field, t.c.value == value, t.c.docname == name)))
        for field, value in sorted(want - held):
            try:
                await self._session.execute(insert(t).values(tenant_id=self._tenant, doctype=doctype, fieldname=field, value=value, docname=name))
            except IntegrityError:
                raise UniqueViolation(field) from None

    async def drop_unique(self, doctype: str, name: str) -> None:
        """Le document disparaît (ou est annulé) : ses valeurs redeviennent libres."""
        t = self._uniques
        await self._session.execute(delete(t).where(self._cscope(t, t.c.doctype == doctype, t.c.docname == name)))

    # ── Renommer ──────────────────────────────────────────────
    async def rename(self, doctype: str, old: str, new: str, *, scope: Scope) -> StoredDoc:
        """Change le NOM d'un brouillon : le document, son historique, ses commentaires, assignations, pièces jointes et valeurs uniques le suivent.

        Un nom déjà pris est un conflit (`AlreadyExists`). Les documents qui le CITENT se réécrivent à part (`referrers_of`, `relink`).
        """
        stmt = (
            update(self._t)
            .where(self._scope(self._t.c.doctype == doctype, self._t.c.name == old, self._t.c.docstatus == DRAFT, scope=scope))
            .values(name=new, modified=datetime.now(UTC), version=self._t.c.version + 1)
        )
        try:
            result = await self._session.execute(stmt)
        except IntegrityError:
            raise AlreadyExists(f"{doctype} {new!r} existe déjà") from None
        if result.rowcount == 0:
            found = await self.get(doctype, old, scope=Scope.everything("read"))
            if found is None:
                raise NotFound("document_not_found", f"{doctype} {old!r} introuvable")
            raise DocumentLocked(f"{doctype} {old!r} est {STATUS_WORDS[found.docstatus]} : il ne se renomme plus") if found.docstatus != DRAFT else NotFound("document_not_found", f"{doctype} {old!r} introuvable")
        for table in (self._comments, self._assignments, self._attachments, self._uniques):
            await self._session.execute(update(table).where(self._cscope(table, table.c.doctype == doctype, table.c.docname == old)).values(docname=new))
        v = self._versions
        await self._session.execute(update(v).where(self._vscope(v.c.doctype == doctype, v.c.docname == old)).values(docname=new))
        return await self._reload(doctype, new)

    async def referrers_of(self, target: str, name: str, referrers: Sequence[tuple[Any, ...]]) -> list[StoredDoc]:
        """Les documents du tenant qui citent `target`/`name` par l'une des références `referrers` (même forme que `references_to`), SANS périmètre de permission."""
        found: dict[tuple[str, str], StoredDoc] = {}
        for doctype, fieldname, table, *rest in referrers:
            conds = [self._t.c.doctype == doctype, self._points_to(fieldname, name, table, rest[0] if rest else None, target)]
            if doctype == target:
                conds.append(self._t.c.name != name)
            for row in (await self._session.execute(select(self._t).where(self._scope(*conds)))).mappings().all():
                doc = self._doc(row)
                found[(doc.doctype, doc.name)] = doc
        return list(found.values())

    async def relink(self, doctype: str, name: str, data: dict[str, Any]) -> StoredDoc:
        """Réécrit le contenu d'un document dont un Link vient d'être renommé, QUEL QUE SOIT son état (un document soumis cite lui aussi l'ancien nom). Réservé à `rename`."""
        stmt = update(self._t).where(self._scope(self._t.c.doctype == doctype, self._t.c.name == name)).values(data=data, modified=datetime.now(UTC), version=self._t.c.version + 1)
        if (await self._session.execute(stmt)).rowcount == 0:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        return await self._reload(doctype, name)

    async def add_version(
        self, doctype: str, docname: str, *, version: int, action: str, user: str, doc_owner: str, changes: Mapping[str, Any]
    ) -> None:
        """Ajoute une entrée d'historique. Dans la transaction du document : tout ou rien (voir `versions.py`)."""
        await self._session.execute(
            insert(self._versions).values(
                tenant_id=self._tenant, doctype=doctype, docname=docname, version=version, action=action, changed_by=user,
                doc_owner=doc_owner, creation=datetime.now(UTC), changes=dict(changes),
            )
        )

    async def versions(self, doctype: str, docname: str, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        """Les entrées d'un document, la plus récente d'abord, et leur nombre total. Les droits se jugent AVANT, sur le document."""
        v = self._versions
        where = self._vscope(v.c.doctype == doctype, v.c.docname == docname)
        page = select(v).where(where).order_by(v.c.id.desc()).limit(limit).offset(offset)
        total = (await self._session.execute(select(func.count()).select_from(v).where(where))).scalar_one()
        rows = (await self._session.execute(page)).mappings().all()
        return [self._version(r) for r in rows], total

    async def last_deletion(self, doctype: str, docname: str) -> dict[str, Any] | None:
        """L'entrée de suppression d'un document, si elle existe : de quoi juger si on pouvait le lire avant qu'il disparaisse."""
        v = self._versions
        stmt = select(v).where(self._vscope(v.c.doctype == doctype, v.c.docname == docname, v.c.action == "delete")).order_by(v.c.id.desc()).limit(1)
        row = (await self._session.execute(stmt)).mappings().first()
        return self._version(row) if row is not None else None

    async def purge_versions(self, doctype: str, docname: str, *, keep: int) -> int:
        """Rétention d'historique : ne garde que les `keep` entrées les plus récentes de CE document, purge le reste (D24).

        À appeler DANS la transaction du document, juste après `add_version` : la conservation est un effet immédiat de
        l'écriture, pas d'un entretien de nuit. Renvoie le nombre d'entrées supprimées.
        """
        v = self._versions
        base = self._vscope(v.c.doctype == doctype, v.c.docname == docname)
        kept = select(v.c.id).where(base).order_by(v.c.id.desc()).limit(keep)
        return (await self._session.execute(delete(v).where(base, v.c.id.not_in(kept)))).rowcount

    async def purge_versions_before(self, before: datetime, *, doctype: str | None = None) -> int:
        """Les entrées d'historique du tenant PLUS VIEILLES que `before` (d'un seul DocType si on le nomme).

        Action opérateur (`doc.history.purge`) : la rétention par document (`purge_versions`) ne rattrape pas le trop-plein
        d'avant une augmentation de `max_versions`. Renvoie le nombre d'entrées supprimées.
        """
        v = self._versions
        conds: list[Any] = [v.c.creation < before]
        if doctype is not None:
            conds.append(v.c.doctype == doctype)
        return (await self._session.execute(delete(v).where(self._vscope(*conds)))).rowcount

    # ── Vues sauvegardées (phase C) ───────────────────────────
    async def saved_views(self, doctype: str, owner: str) -> list[dict[str, Any]]:
        """Les vues sauvegardées d'UN propriétaire. Toujours borné au tenant ET au propriétaire (jamais ceux d'un autre)."""
        v = self._views
        stmt = (
            select(v)
            .where(self._svscope(v.c.doctype == doctype, v.c.owner == owner))
            .order_by(v.c.is_default.desc(), v.c.label.asc())
        )
        return [self._saved_view(r) for r in (await self._session.execute(stmt)).mappings().all()]

    async def get_saved_view(self, doctype: str, name: str, owner: str) -> dict[str, Any] | None:
        v = self._views
        stmt = select(v).where(self._svscope(v.c.doctype == doctype, v.c.name == name, v.c.owner == owner))
        row = (await self._session.execute(stmt)).mappings().one_or_none()
        return self._saved_view(row) if row is not None else None

    async def put_saved_view(
        self, doctype: str, name: str, *, owner: str, label: str, kind: str, config: Mapping[str, Any], is_default: bool
    ) -> dict[str, Any]:
        """Crée ou remplace une vue (même tenant, même propriétaire). Une vue par défaut désarme les autres du même propriétaire."""
        v = self._views
        if is_default:
            await self._session.execute(
                update(v)
                .where(self._svscope(v.c.doctype == doctype, v.c.owner == owner, v.c.name != name))
                .values(is_default=False)
            )
        now = datetime.now(UTC)
        insert_for = postgresql.insert if self._session.get_bind().dialect.name == "postgresql" else sqlite.insert
        stmt = (
            insert_for(v)
            .values(
                tenant_id=self._tenant, doctype=doctype, name=name, owner=owner, label=label, kind=kind,
                config=dict(config), is_default=is_default, creation=now, modified=now,
            )
            .on_conflict_do_update(
                index_elements=[v.c.tenant_id, v.c.doctype, v.c.name],
                set_={"label": label, "kind": kind, "config": dict(config), "is_default": is_default, "modified": now},
            )
        )
        await self._session.execute(stmt)
        found = await self.get_saved_view(doctype, name, owner)
        assert found is not None
        return found

    async def delete_saved_view(self, doctype: str, name: str, owner: str) -> bool:
        v = self._views
        stmt = delete(v).where(self._svscope(v.c.doctype == doctype, v.c.name == name, v.c.owner == owner))
        return (await self._session.execute(stmt)).rowcount > 0

    # ── Habillages (overrides d'espace / de formulaire, phase C) ─────────
    async def get_override(self, kind: str, target: str) -> dict[str, Any] | None:
        """L'habillage d'un espace ou d'un type pour le tenant courant, s'il existe. Jamais celui d'un autre tenant."""
        o = self._overrides
        stmt = select(o).where(self._ovscope(o.c.kind == kind, o.c.target == target))
        row = (await self._session.execute(stmt)).mappings().one_or_none()
        return self._override(row) if row is not None else None

    async def overrides(self, kind: str, *, suffix: str | None = None) -> list[dict[str, Any]]:
        """Tous les habillages d'un genre (ex. tous les espaces) pour le tenant courant. `suffix` : seulement ceux dont la cible s'y termine
        (`@<empreinte>` : les habillages personnels d'UN utilisateur ; l'empreinte est hexadécimale, aucun caractère joker n'y figure)."""
        o = self._overrides
        conds = [o.c.kind == kind]
        if suffix is not None:
            conds.append(o.c.target.like(f"%{suffix}"))
        stmt = select(o).where(self._ovscope(*conds)).order_by(o.c.target.asc())
        return [self._override(r) for r in (await self._session.execute(stmt)).mappings().all()]

    async def put_override(self, kind: str, target: str, *, owner: str, config: Mapping[str, Any]) -> dict[str, Any]:
        o = self._overrides
        now = datetime.now(UTC)
        insert_for = postgresql.insert if self._session.get_bind().dialect.name == "postgresql" else sqlite.insert
        stmt = (
            insert_for(o)
            .values(tenant_id=self._tenant, kind=kind, target=target, owner=owner, config=dict(config), creation=now, modified=now)
            .on_conflict_do_update(
                index_elements=[o.c.tenant_id, o.c.kind, o.c.target],
                set_={"owner": owner, "config": dict(config), "modified": now},
            )
        )
        await self._session.execute(stmt)
        found = await self.get_override(kind, target)
        assert found is not None
        return found

    async def delete_override(self, kind: str, target: str) -> bool:
        o = self._overrides
        stmt = delete(o).where(self._ovscope(o.c.kind == kind, o.c.target == target))
        return (await self._session.execute(stmt)).rowcount > 0

    # ── Fichiers (files.py) ───────────────────────────────────
    async def add_file(
        self, file_id: str, *, doctype: str, fieldname: str, filename: str, content_type: str, size: int, namespace: str, blob_id: str, stored_name: str
    ) -> None:
        await self._session.execute(
            insert(self._files).values(
                tenant_id=self._tenant, id=file_id, doctype=doctype, fieldname=fieldname, filename=filename, content_type=content_type,
                size=size, namespace=namespace, blob_id=blob_id, stored_name=stored_name, owner=self._owner, creation=datetime.now(UTC),
            )
        )

    async def get_files(self, ids: Collection[str]) -> dict[str, dict[str, Any]]:
        """Les lignes de ces fichiers DANS CE TENANT (un identifiant d'un autre tenant est simplement absent)."""
        if not ids:
            return {}
        f = self._files
        stmt = select(f).where(and_(f.c.tenant_id == self._tenant, f.c.id.in_(sorted(ids))))
        return {r["id"]: dict(r) for r in (await self._session.execute(stmt)).mappings().all()}

    async def file_cited(self, doctype: str, fieldname: str, file_id: str, *, scope: Scope) -> bool:
        """Un document que `scope` permet de lire cite-t-il ce fichier dans ce champ ? C'est ce qui donne le droit de le lire."""
        stmt = select(literal(1)).where(
            self._scope(self._t.c.doctype == doctype, self._t.c.data[fieldname].as_string() == file_id, scope=scope)
        ).limit(1)
        return (await self._session.execute(stmt)).first() is not None

    async def remove_files(self, ids: Collection[str]) -> int:
        if not ids:
            return 0
        f = self._files
        result = await self._session.execute(delete(f).where(and_(f.c.tenant_id == self._tenant, f.c.id.in_(sorted(ids)))))
        return result.rowcount

    # ── Collaboration : commentaires, assignations, pièces jointes (collaboration.py) ─────────────────────
    # Aucune de ces méthodes ne juge un DROIT : elles imposent le tenant (`_cscope`) et rien d'autre. Savoir si l'appelant peut lire le document
    # auquel une ligne se rattache est l'affaire du service, qui lit d'abord le document avec son périmètre.
    def _cscope(self, table: Any, *conds: Any) -> Any:
        return and_(table.c.tenant_id == self._tenant, *conds)

    async def add_comment(self, doctype: str, docname: str, content: str) -> dict[str, Any]:
        c, now = self._comments, datetime.now(UTC)
        row = {"tenant_id": self._tenant, "id": secrets.token_hex(16), "doctype": doctype, "docname": docname, "owner": self._owner,
               "content": content, "edited": False, "creation": now, "modified": now}
        await self._session.execute(insert(c).values(**row))
        return self._collab_row(row)

    async def comments(self, doctype: str, docname: str, *, limit: int) -> list[dict[str, Any]]:
        c = self._comments
        stmt = select(c).where(self._cscope(c, c.c.doctype == doctype, c.c.docname == docname)).order_by(c.c.creation.asc(), c.c.id.asc()).limit(limit)
        return [self._collab_row(r) for r in (await self._session.execute(stmt)).mappings().all()]

    async def get_comment(self, comment_id: str) -> dict[str, Any] | None:
        c = self._comments
        row = (await self._session.execute(select(c).where(self._cscope(c, c.c.id == comment_id)))).mappings().one_or_none()
        return self._collab_row(row) if row is not None else None

    async def update_comment(self, comment_id: str, content: str) -> bool:
        c = self._comments
        res = await self._session.execute(update(c).where(self._cscope(c, c.c.id == comment_id)).values(content=content, edited=True, modified=datetime.now(UTC)))
        return res.rowcount > 0

    async def delete_comment(self, comment_id: str) -> bool:
        c = self._comments
        return (await self._session.execute(delete(c).where(self._cscope(c, c.c.id == comment_id)))).rowcount > 0

    async def count_comments(self, doctype: str, docname: str) -> int:
        c = self._comments
        stmt = select(func.count()).select_from(c).where(self._cscope(c, c.c.doctype == doctype, c.c.docname == docname))
        return int((await self._session.execute(stmt)).scalar_one())

    async def add_assignment(self, doctype: str, docname: str, *, assignee: str, description: str | None, due_date: str | None) -> dict[str, Any]:
        a, now = self._assignments, datetime.now(UTC)
        row = {"tenant_id": self._tenant, "id": secrets.token_hex(16), "doctype": doctype, "docname": docname, "assignee": assignee,
               "assigned_by": self._owner, "description": description, "due_date": due_date, "status": "open", "creation": now, "modified": now}
        await self._session.execute(insert(a).values(**row))
        return self._collab_row(row)

    async def assignments(self, doctype: str, docname: str) -> list[dict[str, Any]]:
        a = self._assignments
        stmt = select(a).where(self._cscope(a, a.c.doctype == doctype, a.c.docname == docname)).order_by(a.c.creation.asc(), a.c.id.asc())
        return [self._collab_row(r) for r in (await self._session.execute(stmt)).mappings().all()]

    async def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        a = self._assignments
        row = (await self._session.execute(select(a).where(self._cscope(a, a.c.id == assignment_id)))).mappings().one_or_none()
        return self._collab_row(row) if row is not None else None

    async def assignments_of(self, assignee: str, *, status: str | None, limit: int) -> list[dict[str, Any]]:
        """Ce qu'on a confié à `assignee` dans ce tenant, l'échéance la plus proche d'abord (sans échéance à la fin)."""
        a = self._assignments
        conds = [a.c.assignee == assignee] + ([a.c.status == status] if status else [])
        stmt = select(a).where(self._cscope(a, *conds)).order_by(a.c.due_date.is_(None), a.c.due_date.asc(), a.c.creation.desc()).limit(limit)
        return [self._collab_row(r) for r in (await self._session.execute(stmt)).mappings().all()]

    async def update_assignment(self, assignment_id: str, **values: Any) -> bool:
        a = self._assignments
        res = await self._session.execute(update(a).where(self._cscope(a, a.c.id == assignment_id)).values(**values, modified=datetime.now(UTC)))
        return res.rowcount > 0

    async def add_attachment(self, doctype: str, docname: str, file_id: str) -> dict[str, Any]:
        t = self._attachments
        row = {"tenant_id": self._tenant, "id": secrets.token_hex(16), "doctype": doctype, "docname": docname, "file_id": file_id,
               "owner": self._owner, "creation": datetime.now(UTC)}
        await self._session.execute(insert(t).values(**row))
        return self._collab_row(row)

    async def attachments(self, doctype: str, docname: str) -> list[dict[str, Any]]:
        t = self._attachments
        stmt = select(t).where(self._cscope(t, t.c.doctype == doctype, t.c.docname == docname)).order_by(t.c.creation.asc(), t.c.id.asc())
        return [self._collab_row(r) for r in (await self._session.execute(stmt)).mappings().all()]

    async def get_attachment(self, attachment_id: str) -> dict[str, Any] | None:
        t = self._attachments
        row = (await self._session.execute(select(t).where(self._cscope(t, t.c.id == attachment_id)))).mappings().one_or_none()
        return self._collab_row(row) if row is not None else None

    async def attachment_of_file(self, file_id: str) -> dict[str, Any] | None:
        """À quel document ce fichier est-il joint ? (c'est ce qui donne le droit de le lire : celui de lire ce document)"""
        t = self._attachments
        row = (await self._session.execute(select(t).where(self._cscope(t, t.c.file_id == file_id)).limit(1))).mappings().one_or_none()
        return self._collab_row(row) if row is not None else None

    async def delete_attachment(self, attachment_id: str) -> bool:
        t = self._attachments
        return (await self._session.execute(delete(t).where(self._cscope(t, t.c.id == attachment_id)))).rowcount > 0

    async def remove_collaboration(self, doctype: str, docname: str) -> list[str]:
        """Le document disparaît : ses commentaires, ses assignations et ses pièces jointes aussi. Retourne les fichiers à libérer."""
        t = self._attachments
        files = [r["file_id"] for r in (await self._session.execute(select(t.c.file_id).where(self._cscope(t, t.c.doctype == doctype, t.c.docname == docname)))).mappings().all()]
        for table in (self._attachments, self._comments, self._assignments):
            await self._session.execute(delete(table).where(self._cscope(table, table.c.doctype == doctype, table.c.docname == docname)))
        return files

    async def people(self, *, prefix: str, limit: int) -> list[str]:
        """Les identités vues dans ce tenant (propriétaires de documents, auteurs de commentaires, assignés), pour proposer une personne à assigner.
        Il n'y a pas d'annuaire d'utilisateurs ici : c'est ce que le tenant a déjà fait apparaître."""
        like = f"{self._like_escape(prefix)}%"
        sources = [
            select(self._t.c.owner.label("who")).where(and_(self._t.c.tenant_id == self._tenant, self._t.c.owner.ilike(like, escape="\\"))),
            select(self._comments.c.owner.label("who")).where(self._cscope(self._comments, self._comments.c.owner.ilike(like, escape="\\"))),
            select(self._assignments.c.assignee.label("who")).where(self._cscope(self._assignments, self._assignments.c.assignee.ilike(like, escape="\\"))),
        ]
        union = sources[0].union(*sources[1:]).subquery()
        stmt = select(union.c.who).order_by(union.c.who.asc()).limit(limit)
        return [r[0] for r in (await self._session.execute(stmt)).all()]

    @staticmethod
    def _collab_row(row: Mapping[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in dict(row).items() if k != "tenant_id"}
        for key in ("creation", "modified"):
            if isinstance(out.get(key), datetime):
                out[key] = (out[key] if out[key].tzinfo else out[key].replace(tzinfo=UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return out

    async def next_number(self, doctype: str, series_key: str) -> int:
        """Prochain numéro d'une série, atomiquement : 1 à la première demande, puis +1 (D7).

        Une seule instruction `INSERT … ON CONFLICT DO UPDATE … RETURNING` : la création du compteur et son incrément ne
        peuvent pas se croiser entre deux appels concurrents (pas de « lire puis écrire »). Elle s'exécute dans la
        transaction de l'appelant : un document refusé ou une erreur après coup annule aussi son numéro (pas de trou), et
        les créations simultanées d'une même série s'enchaînent sur le verrou de la ligne du compteur jusqu'au commit.
        """
        insert_for = postgresql.insert if self._session.get_bind().dialect.name == "postgresql" else sqlite.insert
        n = self._naming
        stmt = (
            insert_for(n)
            .values(tenant_id=self._tenant, doctype=doctype, series_key=series_key, last_number=1)
            .on_conflict_do_update(
                index_elements=[n.c.tenant_id, n.c.doctype, n.c.series_key],
                set_={"last_number": n.c.last_number + 1},
            )
            .returning(n.c.last_number)
        )
        return (await self._session.execute(stmt)).scalar_one()

    # ── Interne ───────────────────────────────────────────────
    def _scope(self, *conds: Any, scope: Scope | None = None) -> Any:
        """Point de passage obligé : impose le tenant (et, si fourni, le périmètre de permission)."""
        parts = [self._t.c.tenant_id == self._tenant, *conds]
        if scope is not None:
            parts.append(self._permission_clause(scope))
        return and_(*parts)

    def _vscope(self, *conds: Any) -> Any:
        """Le point de passage de l'historique : le tenant est imposé, comme `_scope` le fait pour les documents."""
        return and_(self._versions.c.tenant_id == self._tenant, *conds)

    def _svscope(self, *conds: Any) -> Any:
        """Le point de passage des vues sauvegardées : le tenant est imposé, jamais ceux d'un autre tenant."""
        return and_(self._views.c.tenant_id == self._tenant, *conds)

    def _ovscope(self, *conds: Any) -> Any:
        """Le point de passage des habillages : le tenant est imposé, jamais ceux d'un autre tenant."""
        return and_(self._overrides.c.tenant_id == self._tenant, *conds)

    @staticmethod
    def _override(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "kind": row["kind"],
            "target": row["target"],
            "owner": row["owner"],
            "config": dict(row["config"]),
            "creation": _utc(row["creation"]).isoformat(),
            "modified": _utc(row["modified"]).isoformat(),
        }

    @staticmethod
    def _saved_view(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "name": row["name"],
            "label": row["label"],
            "kind": row["kind"],
            "config": dict(row["config"]),
            "is_default": bool(row["is_default"]),
            "owner": row["owner"],
            "creation": _utc(row["creation"]).isoformat(),
            "modified": _utc(row["modified"]).isoformat(),
        }

    @staticmethod
    def _version(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "version": row["version"],
            "action": row["action"],
            "user": row["changed_by"],
            "owner": row["doc_owner"],
            "creation": _utc(row["creation"]).isoformat(),
            "changes": dict(row["changes"]),
        }

    def _permission_clause(self, scope: Scope) -> Any:
        if scope.unrestricted:
            return true()
        if not scope.clauses:
            return false()
        ors = []
        for clause in scope.clauses:
            parts = []
            if clause.owner is not None:
                parts.append(self._t.c.owner == clause.owner)
            if clause.match is not None:
                # Les champs de `match` sont validés (schema.MATCHABLE_TYPES) : tous textuels.
                parts.append(self._t.c.data[clause.match[0]].as_string() == clause.match[1])
            ors.append(and_(*parts))
        return or_(*ors)

    def _filter_conds(self, defn: DocTypeDef, filters: Mapping[str, Any]) -> list[Any]:
        """Les conditions du DocType et de ses filtres (déjà validés par type). Partagées par la liste et les agrégats."""
        conds = [self._t.c.doctype == defn.name]
        fields = defn.field_map
        for fieldname, value in filters.items():
            if fieldname not in SYSTEM_FILTERS and fieldname not in fields:
                raise ValidationFailed(f"filtre sur un champ inconnu : {fieldname!r}")
            # Les colonnes système (`name`, `docstatus`) se filtrent comme un champ du type indiqué (SYSTEM_FILTERS).
            expr = self._t.c[fieldname] if fieldname in SYSTEM_FILTERS else self._json_expr(defn, fieldname)
            ftype = SYSTEM_FILTERS[fieldname] if fieldname in SYSTEM_FILTERS else fields[fieldname].fieldtype
            value = _typed(ftype, value)
            if isinstance(value, Filter):
                conds.append(self._filter(expr, value, ftype, fieldname))
            else:
                conds.append(expr.is_(None) if value is None else expr == value)
        return conds

    def _group_expr(self, defn: DocTypeDef, group: Group) -> Any:
        """La clé de regroupement. Un instant système se découpe en UTC (`creation`, `modified`), une date en texte ISO."""
        if group.field in SYSTEM_CATEGORIES:
            return self._t.c[group.field]
        bucket = group.bucket
        if group.field in SYSTEM_TIMES:
            column = self._t.c[group.field]
            if self._session.get_bind().dialect.name == "postgresql":
                # Les formats viennent d'une liste fermée (aucune valeur du client) : inscrits dans le SQL, pas liés.
                fmt = literal_column({"year": "'YYYY'", "month": "'YYYY-MM'", "day": "'YYYY-MM-DD'"}[bucket or "month"])
                return func.to_char(func.timezone(literal_column("'UTC'"), column), fmt)
            return func.strftime(literal_column({"year": "'%Y'", "month": "'%Y-%m'", "day": "'%Y-%m-%d'"}[bucket or "month"]), column)
        node = self._t.c.data[group.field]
        match defn.field_map[group.field].fieldtype:
            case "Date" | "Datetime":
                # Les dates (et les instants UTC, `AAAA-MM-JJThh…`) sont stockées en `AAAA-MM-JJ` (forme canonique imposée à l'écriture) : le début de la chaîne EST le découpage.
                return func.substr(node.as_string(), 1, BUCKET_WIDTH[bucket or "month"])
            case "Boolean":
                return node.as_boolean()
            case _:
                return node.as_string()

    def _metric_expr(self, defn: DocTypeDef, metric: Metric) -> Any:
        if metric.function == "count":
            return func.count()
        # Number en flottant ; Decimal en NUMERIC (exact sur PostgreSQL, en double sur SQLite : voir `_json_expr`).
        return getattr(func, metric.function)(self._json_expr(defn, str(metric.field)))

    def _json_expr(self, defn: DocTypeDef, fieldname: str) -> Any:
        # `as_*` : opérateurs JSON portables (JSON_EXTRACT sur SQLite, ->> sur PostgreSQL).
        # Le nom de champ a été validé contre la définition avant d'arriver ici.
        node = self._t.c.data[fieldname]
        field = defn.field_map[fieldname]
        match field.fieldtype:
            case "Number" | "Rating" | "Percent":
                return node.as_float()
            case "Int" | "Duration":
                return node.as_integer()
            case "Decimal" | "Currency":
                # Exact sur PostgreSQL (NUMERIC). SQLite n'a pas de décimal : il compare en double, exact jusqu'à 15 chiffres.
                return node.as_numeric(MAX_DIGITS, field.decimals)
            case "Boolean":
                return node.as_boolean()
            case _:
                return node.as_string()

    @staticmethod
    def _like_escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _filter(self, expr: Any, f: Filter, ftype: str, fieldname: str) -> Any:
        if f.op not in FILTER_OPS[ftype] or f.op == "=":
            raise ValidationFailed(f"opérateur {f.op!r} impossible sur {fieldname!r} ({ftype})")
        v = f.value
        match f.op:
            case "!=":
                # Un document sans valeur est « différent » (comme `!=` dans une condition) : SQL l'exclurait sinon.
                return or_(expr != v, expr.is_(None))
            case "like":
                return expr.ilike("%" + self._like_escape(v) + "%", escape="\\")
            case ">":
                return expr > v
            case ">=":
                return expr >= v
            case "<":
                return expr < v
            case "<=":
                return expr <= v
            case "in":
                return expr.in_(v) if v else false()
        raise ValidationFailed(f"opérateur inconnu {f.op!r}")  # pragma: no cover

    def _sort_expr(self, defn: DocTypeDef, order_by: str) -> Any:
        if order_by in SYSTEM_SORTABLE:
            return self._t.c[order_by]
        if order_by in defn.field_map:
            return self._json_expr(defn, order_by)
        raise ValidationFailed(f"tri impossible sur {order_by!r}")

    @staticmethod
    def _doc(row: Mapping[str, Any]) -> StoredDoc:
        return StoredDoc(
            doctype=row["doctype"],
            name=row["name"],
            data=dict(row["data"]),
            owner=row["owner"],
            creation=_utc(row["creation"]),
            modified=_utc(row["modified"]),
            version=row["version"],
            docstatus=row["docstatus"],
            amended_from=row["amended_from"],
        )
