"""Présence : quelles organisations ont des documents de quels types (`xdt_presence`).

Le moteur ne connaissait aucune liste d'organisations : le tenant arrive avec chaque requête, et rien ne les énumère. Une tâche
RÉCURRENTE (jobs.py) doit pourtant tourner pour chacune — et seulement pour celles que son plugin concerne : l'arrêté quotidien
d'une comptabilité n'a rien à faire dans une organisation qui n'en tient pas. Cette table le dit, une ligne par (organisation,
type de document).

- **Poser** (`record`) : APRÈS le commit d'une écriture, dans une petite transaction à part. Jamais dans celle du document : deux
  écritures simultanées qui créent leurs premiers documents de deux types dans l'ordre inverse s'y attendraient l'une l'autre (les
  verrous d'index unique se croisent). Le prix : un processus qui meurt entre le commit et cette ligne ne l'a pas posée. La
  prochaine écriture de ce type la posera — création OU modification, et la mémoire du processus est vide au redémarrage. Une
  erreur ici est journalisée, jamais rendue : le document est écrit, la présence est une commodité.
- **Reprendre** (`backfill`) : les documents écrits AVANT cette table n'ont pas de ligne. Une instruction les reprend, une fois par
  base (la ligne (`*`, `*`) le marque). C'est la SEULE lecture de `xdt_document` sans tenant de tout le moteur : elle ne lit que
  `tenant_id` et `doctype`, jamais un contenu, et ne rend rien à personne. Sous la RLS optionnelle elle ne verrait rien (sans
  tenant posé, la politique ne laisse passer aucune ligne) : elle n'y est pas tentée, la présence vient alors des écritures seules.
- **Énumérer** (`tenants`) : les organisations qui ont au moins un des types donnés.

Une ligne n'est jamais retirée : une organisation qui a vidé un type ne coûte qu'une tâche qui ne trouve rien.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Sequence

from sqlalchemy import DateTime, literal, select
from sqlalchemy.dialects import postgresql, sqlite

from .db.tables import SystemTables

MARK = "*"  # (`*`, `*`) : la reprise a eu lieu. Ni un tenant ni un DocType ne peuvent s'appeler ainsi.
MAX_KNOWN = 50_000  # la mémoire du processus est bornée : au-delà on l'oublie, et les lignes se reposent (sans effet) au besoin
CHUNK = 500  # noms par instruction : sous la limite de paramètres de SQLite


def _now() -> datetime:
    return datetime.now(UTC)


def _insert_for(session: Any) -> Any:
    return postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert


class Presence:
    """La table `xdt_presence`, et la mémoire de ce que ce processus y a déjà posé."""

    def __init__(self, db: Any, tables: SystemTables, log: Any, *, rls: bool = False, clock: Callable[[], datetime] = _now) -> None:
        self._db, self._t, self._documents, self._log, self._rls, self._clock = db, tables.presence, tables.documents, log, rls, clock
        self._known: set[tuple[str, str]] = set()
        self._backfilled = False

    async def record(self, tenant_id: str, doctypes: Iterable[str]) -> None:
        """Note que cette organisation a écrit des documents de ces types. Sans effet, et sans requête, pour ce qui est déjà noté."""
        new = sorted({d for d in doctypes if (tenant_id, d) not in self._known})  # triés : deux poses simultanées prennent leurs verrous dans le même ordre
        if not new:
            return
        t, now = self._t, self._clock()
        try:
            async with self._db.session() as session:
                rows = [{"tenant_id": tenant_id, "doctype": d, "first_seen": now} for d in new]
                await session.execute(_insert_for(session)(t).values(rows).on_conflict_do_nothing(index_elements=[t.c.tenant_id, t.c.doctype]))
        except Exception as e:  # noqa: BLE001 - jamais l'échec de l'écriture qui vient de réussir
            self._log.warning("presence not recorded", tenant=tenant_id, doctypes=new, error=str(e))
            return
        if len(self._known) > MAX_KNOWN:
            self._known.clear()
        self._known.update((tenant_id, d) for d in new)

    async def backfill(self) -> bool:
        """Reprend les documents d'avant la table, une fois par base. Vrai si c'est CET appel qui l'a fait."""
        if self._backfilled:
            return False
        t, d, now = self._t, self._documents, self._clock()
        async with self._db.session() as session:
            if (await session.execute(select(t.c.tenant_id).where(t.c.tenant_id == MARK, t.c.doctype == MARK))).first() is not None:
                self._backfilled = True
                return False
            if self._rls:
                # Sans tenant posé, la politique de `xdt_document` ne laisse rien lire : il n'y a rien à reprendre ainsi, et rien à
                # marquer (une base dont on retire la RLS sera reprise alors).
                self._backfilled = True
                self._log.info("presence backfill skipped under RLS: organisations are learnt from their next write")
                return False
            insert_for = _insert_for(session)
            every_pair = select(d.c.tenant_id, d.c.doctype, literal(now, type_=DateTime(timezone=True))).where(d.c.tenant_id.is_not(None)).distinct()
            await session.execute(
                insert_for(t).from_select(["tenant_id", "doctype", "first_seen"], every_pair)
                .on_conflict_do_nothing(index_elements=[t.c.tenant_id, t.c.doctype])
            )
            await session.execute(
                insert_for(t).values(tenant_id=MARK, doctype=MARK, first_seen=now).on_conflict_do_nothing(index_elements=[t.c.tenant_id, t.c.doctype])
            )
        self._backfilled = True
        self._log.info("presence backfilled from existing documents")
        return True

    async def tenants(self, doctypes: Sequence[str]) -> list[str]:
        """Les organisations qui ont au moins un document d'un de ces types, triées. Aucune sans type à chercher."""
        names, t, found = sorted(set(doctypes)), self._t, set()
        async with self._db.session() as session:
            for i in range(0, len(names), CHUNK):
                stmt = select(t.c.tenant_id).where(t.c.doctype.in_(names[i:i + CHUNK]), t.c.tenant_id != MARK).distinct()
                found.update(r[0] for r in await session.execute(stmt))
        return sorted(found)
