"""Tâches de fond fournies par le moteur, avec reprise (traoreera/doctype#71).

Un travail long — installer le plan comptable d'une société : une centaine de comptes et leurs clés — dépasse le délai d'un
hook. Avant ce module le moteur n'offrait rien : l'ERP lançait lui-même une tâche depuis `after_insert`, et si le serveur
s'arrêtait au milieu l'échec était seulement journalisé — il fallait rouvrir la société et l'enregistrer pour reprendre.

Ce que le moteur donne :

    jobs = self.get_service("doctype_jobs")
    jobs.register(self.ctx.name, "install_chart", self.install_chart)            # au chargement du plugin

    job = await jobs.enqueue(tenant, "accounts.install_chart", {"company": name}, key=f"chart:{name}", owner=user)

    async def install_chart(self, job):                                             # exécutée en tâche de fond
        for i, account in enumerate(accounts):
            await job.writer.ensure("Account", account, unique="code")             # rejouable : pas de doublon à la reprise
            await job.progress(f"{i + 1}/{len(accounts)} comptes")

- **Une clé d'unicité.** Tant qu'une tâche de cette clé est en attente ou en cours, `enqueue` rend CELLE-LÀ : pas deux
  installations simultanées. C'est la base qui tranche (`uq_xdt_job_active`), pas une lecture préalable.
- **Un état persistant** : `pending` → `running` → `done` | `failed`, avec un message (l'avancement, puis l'erreur), le nombre
  d'essais et les dates. Lisible par l'API (`GET /jobs`) et donc au Desk.
- **Une reprise.** Le processus qui exécute tient un BAIL (`locked_until`) qu'il renouvelle. Le serveur s'arrête : le bail
  expire, et au démarrage suivant (ou sur un autre processus) la tâche est reprise. Les reprises sont BORNÉES
  (`max_attempts`) : une tâche qui fait tomber le serveur à chaque fois finit `failed`, elle ne tourne pas en boucle.
- **Le contexte système du module** : `job.writer` est `doctype_writer.system(<plugin>, tenant, "job:<nom>")`, donc limité
  en écriture aux DocTypes du plugin, historisé `system:job:<nom>` — la même porte qu'une tâche planifiée.
- **Dans la transaction d'un hook** : `event.tx.enqueue(nom, payload, key=…)` écrit la tâche avec le document. La
  transaction est annulée : la tâche n'a jamais existé. C'est la file d'envoi durable que #59 laissait ouverte — un
  courriel « après validation » ne part plus pour un document resté brouillon.

Au moins une fois : un processus qui meurt après avoir fini mais avant de l'écrire fera rejouer la tâche. Une tâche est donc
REJOUABLE par construction (`writer.ensure`, une clé `unique`, un nom choisi) — la règle des tâches planifiées, déjà écrite
dans `writer.py`.

**Tâches RÉCURRENTES** (25 nov. 2026). Rien ne tournait seul : la file savait exécuter, pas déclencher, et le moteur ne
connaissait aucune liste d'organisations. Une tâche se déclare maintenant avec son rythme :

    jobs.register(self.ctx.name, "daily", self.daily, daily_at="02:30")      # chaque jour, à l'heure de `doctype.timezone`
    jobs.register(self.ctx.name, "fetch", self.fetch, every=900)             # toutes les 15 minutes

    async def daily(self, job):                     # une fois par organisation et par période ; `job.period` dit laquelle
        ...                                         # écrite sur l'ÉTAT (« tout ce qui est échu »), jamais sur l'événement

- **Pour qui.** Les organisations qui ont au moins un document d'un type du plugin (`presence.py`) : l'arrêté d'une
  comptabilité ne tourne pas là où personne n'en tient.
- **Une fois par période, quel que soit le nombre de processus.** `xdt_job_schedule` garde la dernière période déposée de
  chaque (organisation, tâche) ; la prendre est une écriture CONDITIONNELLE, dans la transaction qui dépose la tâche : un
  seul processus l'obtient, et la tâche existe si et seulement si la période est prise. Aucun verrou réparti à installer.
- **Rattrapage, sans empilement.** La période est celle qui est DUE maintenant : un serveur arrêté trois nuits dépose UNE
  tâche à son retour, pas trois. Une tâche encore en cours quand la période suivante arrive n'est pas doublée (la même clé
  `@recurring` : la base rend celle qui tourne). C'est pourquoi une tâche récurrente se lit sur l'état, pas sur sa période.
- **Un journal borné.** Les tâches récurrentes finies depuis plus de `keep_days` (30 par défaut) sont retirées.

Pour la lancer à la main (un bouton) : `enqueue(tenant, nom, {"period": …}, key=RECURRING_KEY)` — elle ne doublera pas
celle qui tourne.

Le tenant est une colonne, et tout ce qui LIT ou ÉCRIT une tâche passe par `JobStore._scope`, qui l'impose. Trois
instructions ne le portent pas, à dessein, parce que l'exécuteur et l'ordonnanceur n'agissent pour personne :
`JobStore.ready` (quelles tâches sont prêtes : elle ne rend que (tenant, id), la tâche se lit ensuite sous son tenant),
`JobStore.claim_period` (prendre la période de PLUSIEURS organisations d'un coup : elle n'écrit que l'échéancier) et
`JobStore.purge` (retirer les tâches récurrentes finies depuis longtemps). Aucune ne lit ni ne rend un `payload`. C'est
aussi pourquoi `xdt_job` et `xdt_job_schedule` sont hors de la RLS optionnelle (`db/rls.py`) : une politique par tenant
rendrait cette découverte impossible.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite

from .context import RequestContext
from .db.tables import SystemTables
from .errors import DoctypeError, NotFound, PermissionDenied, ValidationFailed
from .presence import Presence

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
ACTIVE = (PENDING, RUNNING)
JOB_NAME = re.compile(r"^([a-z][a-z0-9_]{0,39})\.([a-z][a-z0-9_]{0,59})$")
_TASK = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
MAX_KEY = 255
MAX_MESSAGE = 2000
MAX_PAYLOAD_BYTES = 64 * 1024  # une tâche porte des identifiants, pas des données : elle relit ce dont elle a besoin
MAX_LIST = 100
RECURRING_KEY = "@recurring"  # la clé de toute tâche déposée par l'ordonnanceur : une seule active par (organisation, tâche)
MIN_EVERY, MAX_EVERY = 60.0, 31 * 86400.0  # secondes : sous la minute ce n'est plus une tâche de fond, au-delà du mois une tâche du jour suffit
MAX_KEEP_DAYS = 3650
SCHEDULE_CHUNK = 200  # organisations par transaction de dépôt
_DAILY_AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

Handler = Callable[["JobRun"], Awaitable[Any]]
Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Recurrence:
    """Le rythme d'une tâche récurrente : `every` secondes, OU chaque jour à `daily_at` (heure du fuseau du moteur)."""

    every: float | None
    daily_at: time | None
    keep_days: int  # ses tâches finies sont gardées ce nombre de jours

    def period(self, now: datetime, tz: tzinfo) -> str:
        """La période DUE à cet instant — la dernière qui a commencé, jamais une liste de périodes manquées. Deux périodes d'une
        même tâche se comparent comme des textes : c'est ce que fait la base pour n'en prendre qu'une plus récente."""
        if self.every is not None:
            start = (now.timestamp() // self.every) * self.every  # des créneaux alignés sur l'horloge : tous les processus voient le même
            return datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert self.daily_at is not None
        local = now.astimezone(tz)
        due_today = (local.hour, local.minute) >= (self.daily_at.hour, self.daily_at.minute)
        return (local.date() if due_today else local.date() - timedelta(days=1)).isoformat()

    def describe(self) -> dict[str, Any]:
        return {"every": self.every, "daily_at": self.daily_at.strftime("%H:%M") if self.daily_at else None, "keep_days": self.keep_days}


def _recurrence(name: str, every: Any, daily_at: Any, keep_days: Any) -> Recurrence | None:
    if every is None and daily_at is None:
        return None
    if every is not None and daily_at is not None:
        raise ValueError(f"tâche {name} : `every` OU `daily_at`, pas les deux")
    if not isinstance(keep_days, int) or isinstance(keep_days, bool) or not 1 <= keep_days <= MAX_KEEP_DAYS:
        raise ValueError(f"keep_days : de 1 à {MAX_KEEP_DAYS} jours")
    if every is not None:
        if isinstance(every, bool) or not isinstance(every, (int, float)) or not MIN_EVERY <= every <= MAX_EVERY:
            raise ValueError(f"every : de {MIN_EVERY:.0f} à {MAX_EVERY:.0f} secondes")
        return Recurrence(float(every), None, keep_days)
    found = _DAILY_AT.match(daily_at) if isinstance(daily_at, str) else None
    if found is None:
        raise ValueError('daily_at : une heure « HH:MM » sur 24 heures (ex. "02:30")')
    return Recurrence(None, time(int(found[1]), int(found[2])), keep_days)


@dataclass(frozen=True)
class JobSpec:
    """Une tâche qu'un plugin sait exécuter."""

    name: str  # `<plugin>.<tâche>`
    plugin: str
    handler: Handler
    max_attempts: int
    timeout: float  # secondes : au-delà l'essai est abandonné et compté
    grant: str | None  # qui, en plus de son auteur, lit l'état de ces tâches (un grant que le plugin déclare déjà)
    recurrence: Recurrence | None = None  # son rythme, si le moteur la déclenche lui-même


class JobRun:
    """Ce qu'une tâche reçoit : ce qu'on lui demande, et la porte par laquelle elle écrit."""

    def __init__(self, service: "JobService", row: Mapping[str, Any], spec: JobSpec, writer: Any) -> None:
        self._svc, self._spec = service, spec
        self.id: str = row["id"]
        self.name: str = row["name"]
        self.tenant_id: str = row["tenant_id"]
        self.key: str | None = row["key"]
        self.payload: dict[str, Any] = dict(row["payload"] or {})
        self.attempt: int = row["attempts"]  # 1 au premier essai ; > 1 : c'est une REPRISE, ne refaites pas ce qui est fait
        self.owner: str = row["owner"]
        self.writer = writer  # `doctype_writer.system(<plugin>, tenant, "job:<nom>")`, ou None sans service d'écriture
        # Tâche récurrente : la période qui l'a déclenchée (`2026-11-26`, ou le début de son créneau). Pour dire ce qu'elle a traité,
        # pas pour choisir quoi traiter : une période manquée ne revient pas, c'est l'état qu'il faut lire.
        period = self.payload.get("period")
        self.period: str | None = period if self.key == RECURRING_KEY and isinstance(period, str) else None

    async def progress(self, message: str) -> None:
        """Dit où en est la tâche (« 42/100 comptes ») : lisible tout de suite par qui regarde son état."""
        await self._svc._store.note(self.tenant_id, self.id, self._svc._owner_id, str(message)[:MAX_MESSAGE])


class JobStore:
    """Tout le SQL des tâches. `_scope` est le point de passage du tenant, comme `Query._scope` pour les documents."""

    def __init__(self, db: Any, tables: SystemTables, clock: Clock = _now) -> None:
        self._db, self._t, self._schedules, self._clock = db, tables.jobs, tables.schedules, clock

    def session(self) -> Any:
        """Une transaction à soi (l'ordonnanceur y prend une période ET y dépose ses tâches : les deux, ou rien)."""
        return self._db.session()

    def _scope(self, tenant_id: str, *conds: Any) -> Any:
        return and_(self._t.c.tenant_id == tenant_id, *conds)

    # ── Écriture ──────────────────────────────────────────────
    async def insert(self, session: Any, tenant_id: str, spec: JobSpec, payload: Mapping[str, Any], *, key: str | None, owner: str,
                     delay: float = 0.0) -> tuple[dict[str, Any], bool]:
        """Écrit la tâche dans CETTE session (celle d'un hook : elle part ou tombe avec son document). `(tâche, créée)`.

        Avec une `key`, c'est la base qui refuse le doublon d'une tâche encore active (`ON CONFLICT DO NOTHING` sur
        `uq_xdt_job_active`) : on ne lit pas avant d'écrire, deux appels simultanés liraient « absente » ensemble.
        """
        t, now = self._t, self._clock()
        row = {
            "tenant_id": tenant_id, "id": secrets.token_hex(16), "name": spec.name, "key": key, "active_key": key,
            "payload": dict(payload), "status": PENDING, "attempts": 0, "max_attempts": spec.max_attempts, "message": None,
            "owner": owner, "creation": now, "next_attempt_at": now + timedelta(seconds=max(0.0, delay)),
            "started_at": None, "finished_at": None, "locked_until": None, "locked_by": None,
        }
        insert_for = postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert
        stmt = insert_for(t).values(**row).on_conflict_do_nothing(index_elements=[t.c.tenant_id, t.c.name, t.c.active_key])
        if (await session.execute(stmt)).rowcount > 0:
            return row, True
        found = (
            await session.execute(select(t).where(self._scope(tenant_id, t.c.name == spec.name, t.c.active_key == key)))
        ).mappings().first()
        if found is None:  # elle vient de finir entre les deux instructions : on en ouvre une autre
            return await self.insert(session, tenant_id, spec, payload, key=key, owner=owner, delay=delay)
        return dict(found), False

    async def add(self, tenant_id: str, spec: JobSpec, payload: Mapping[str, Any], *, key: str | None, owner: str,
                  delay: float = 0.0) -> tuple[dict[str, Any], bool]:
        async with self._db.session() as session:
            return await self.insert(session, tenant_id, spec, payload, key=key, owner=owner, delay=delay)

    # ── Lecture ───────────────────────────────────────────────
    async def get(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        async with self._db.session() as session:
            row = (await session.execute(select(self._t).where(self._scope(tenant_id, self._t.c.id == job_id)))).mappings().first()
        return dict(row) if row else None

    async def list(self, tenant_id: str, *, name: str | None = None, key: str | None = None, status: str | None = None,
                   owner: str | None = None, names: tuple[str, ...] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Les tâches de CE tenant, les plus récentes d'abord. `owner` OU `names` : ce que l'appelant a le droit de voir."""
        t = self._t
        conds = []
        if name is not None:
            conds.append(t.c.name == name)
        if key is not None:
            conds.append(t.c.key == key)
        if status is not None:
            conds.append(t.c.status == status)
        seen = []
        if owner is not None:
            seen.append(t.c.owner == owner)
        if names:
            seen.append(t.c.name.in_(names))
        if owner is not None or names is not None:
            conds.append(or_(*seen) if seen else t.c.id.is_(None))
        stmt = select(t).where(self._scope(tenant_id, *conds)).order_by(t.c.creation.desc(), t.c.id.asc()).limit(limit)
        async with self._db.session() as session:
            return [dict(r) for r in (await session.execute(stmt)).mappings()]

    async def ready(self, names: tuple[str, ...], limit: int) -> list[tuple[str, str]]:
        """Les (tenant, id) des tâches à exécuter maintenant : en attente et dues, ou en cours avec un bail EXPIRÉ.

        La SEULE requête qui ne porte pas de tenant (voir l'en-tête du module) : elle ne rend aucun contenu.
        """
        if not names:
            return []
        t, now = self._t, self._clock()
        due = or_(
            and_(t.c.status == PENDING, t.c.next_attempt_at <= now),
            and_(t.c.status == RUNNING, t.c.locked_until < now),
        )
        stmt = select(t.c.tenant_id, t.c.id).where(due, t.c.name.in_(names)).order_by(t.c.next_attempt_at.asc()).limit(limit)
        async with self._db.session() as session:
            return [(r[0], r[1]) for r in await session.execute(stmt)]

    # ── L'exécuteur ───────────────────────────────────────────
    async def claim(self, tenant_id: str, job_id: str, owner_id: str, lease: float) -> dict[str, Any] | None:
        """Prend la tâche : une écriture CONDITIONNELLE, donc un seul processus l'obtient. Compte l'essai."""
        t, now = self._t, self._clock()
        free = or_(
            and_(t.c.status == PENDING, t.c.next_attempt_at <= now),
            and_(t.c.status == RUNNING, t.c.locked_until < now),  # le processus précédent est mort avec elle
        )
        stmt = (
            update(t)
            .where(self._scope(tenant_id, t.c.id == job_id, free))
            .values(status=RUNNING, attempts=t.c.attempts + 1, started_at=now, locked_by=owner_id,
                    locked_until=now + timedelta(seconds=lease))
        )
        async with self._db.session() as session:
            if (await session.execute(stmt)).rowcount != 1:
                return None
            row = (await session.execute(select(t).where(self._scope(tenant_id, t.c.id == job_id)))).mappings().first()
        return dict(row) if row else None

    async def _mine(self, tenant_id: str, job_id: str, owner_id: str, **values: Any) -> bool:
        """Une écriture que seul le processus qui TIENT la tâche peut faire : un bail perdu ne s'écrit plus."""
        t = self._t
        stmt = update(t).where(self._scope(tenant_id, t.c.id == job_id, t.c.status == RUNNING, t.c.locked_by == owner_id)).values(**values)
        async with self._db.session() as session:
            return (await session.execute(stmt)).rowcount == 1

    async def renew(self, tenant_id: str, job_id: str, owner_id: str, lease: float) -> bool:
        return await self._mine(tenant_id, job_id, owner_id, locked_until=self._clock() + timedelta(seconds=lease))

    async def note(self, tenant_id: str, job_id: str, owner_id: str, message: str) -> bool:
        return await self._mine(tenant_id, job_id, owner_id, message=message)

    async def finish(self, tenant_id: str, job_id: str, owner_id: str, *, status: str, message: str | None) -> bool:
        """`done` ou `failed` : la clé d'unicité est rendue (`active_key` NULL), une nouvelle tâche de cette clé peut naître."""
        return await self._mine(tenant_id, job_id, owner_id, status=status, message=message, finished_at=self._clock(),
                                active_key=None, locked_by=None, locked_until=None)

    async def postpone(self, tenant_id: str, job_id: str, owner_id: str, *, message: str | None, delay: float, uncount: bool = False) -> bool:
        """Remet la tâche en attente : après un échec (essai compté), ou à l'arrêt du serveur (`uncount` : l'essai n'a pas eu lieu)."""
        values: dict[str, Any] = {"status": PENDING, "message": message, "locked_by": None, "locked_until": None,
                                  "next_attempt_at": self._clock() + timedelta(seconds=delay)}
        if uncount:
            values["attempts"] = self._t.c.attempts - 1
        return await self._mine(tenant_id, job_id, owner_id, **values)

    # ── L'ordonnanceur ────────────────────────────────────────
    async def claim_period(self, session: Any, name: str, period: str, tenants: Sequence[str]) -> list[str]:
        """Prend `period` pour ces organisations, et rend celles pour qui c'est CET appel qui l'a prise.

        Une écriture CONDITIONNELLE par ligne (`period` plus récente que celle déjà prise) : deux processus qui arrivent ensemble
        ne l'obtiennent pas tous les deux, et la période d'hier ne reprend jamais celle d'aujourd'hui. Elle porte plusieurs
        tenants à la fois — c'est l'ordonnanceur, il n'agit pour personne — et n'écrit que l'échéancier.
        """
        if not tenants:
            return []
        s, now = self._schedules, self._clock()
        insert_for = postgresql.insert if session.get_bind().dialect.name == "postgresql" else sqlite.insert
        stmt = insert_for(s).values([{"tenant_id": t, "name": name, "period": period, "claimed_at": now} for t in tenants])
        stmt = stmt.on_conflict_do_update(
            index_elements=[s.c.tenant_id, s.c.name],
            set_={"period": stmt.excluded.period, "claimed_at": stmt.excluded.claimed_at},
            where=s.c.period < stmt.excluded.period,
        ).returning(s.c.tenant_id)
        return sorted(r[0] for r in await session.execute(stmt))

    async def purge(self, name: str, before: datetime) -> int:
        """Retire les tâches RÉCURRENTES de ce nom finies avant `before`, tous tenants confondus. Jamais une tâche active, ni une
        tâche qu'un plugin a déposée lui-même sous une autre clé : celles-là sont le journal de quelqu'un."""
        t = self._t
        stmt = delete(t).where(t.c.name == name, t.c.key == RECURRING_KEY, t.c.status.in_((DONE, FAILED)), t.c.finished_at < before)
        async with self._db.session() as session:
            return (await session.execute(stmt)).rowcount

    async def revive(self, tenant_id: str, job_id: str) -> bool:
        """Relance une tâche ÉCHOUÉE, compteur à zéro. Refusée (False) si une autre tâche de sa clé est déjà active : la base tranche."""
        t = self._t
        async with self._db.session() as session:
            row = (await session.execute(select(t.c.key).where(self._scope(tenant_id, t.c.id == job_id, t.c.status == FAILED)))).first()
            if row is None:
                return False
            try:
                async with session.begin_nested():
                    done = await session.execute(
                        update(t).where(self._scope(tenant_id, t.c.id == job_id, t.c.status == FAILED))
                        .values(status=PENDING, attempts=0, message=None, finished_at=None, active_key=row[0], next_attempt_at=self._clock())
                    )
            except Exception as e:  # la contrainte d'unicité : une tâche de cette clé tourne déjà
                if "uq_xdt_job_active" in str(e) or "UNIQUE" in str(e).upper():
                    return False
                raise
            return done.rowcount == 1


class JobService:
    """Le service exporté `doctype_jobs` : déclarer, déposer, lire, et exécuter les tâches de fond."""

    def __init__(self, db: Any, tables: SystemTables, log: Any, *, writers: Any = None, clock: Clock = _now, poll_seconds: float = 2.0,
                 lease_seconds: float = 120.0, max_parallel: int = 2, backoff_seconds: float = 30.0, max_backoff_seconds: float = 3600.0,
                 presence: Presence | None = None, types_of: Callable[[str], Sequence[str]] | None = None, timezone: tzinfo = UTC,
                 recurring: bool = True) -> None:
        self._store = JobStore(db, tables, clock)
        self._log, self._writers, self._clock = log, writers, clock
        self._specs: dict[str, JobSpec] = {}
        # Les tâches récurrentes : pour qui (`presence`, et `types_of(plugin)` = les types stockés de ce plugin), dans quel fuseau
        # tombe « chaque jour à 02:30 », et ce que CE processus a déjà déposé (une requête par période, pas par tour de boucle).
        self._presence = presence if presence is not None else Presence(db, tables, log, clock=clock)
        self._types_of, self._tz, self._recurring = types_of, timezone, recurring
        self._scheduled: dict[str, str] = {}
        self._purged: dict[str, str] = {}
        self._poll, self._lease = max(0.05, poll_seconds), max(1.0, lease_seconds)
        self._max_parallel = max(1, max_parallel)
        self._backoff, self._max_backoff = backoff_seconds, max_backoff_seconds
        self._owner_id = secrets.token_hex(16)  # ce processus, pour les baux
        self._loop_task: asyncio.Task | None = None
        self._running: set[asyncio.Task] = set()
        self._wake = asyncio.Event()

    @property
    def store(self) -> JobStore:
        return self._store

    # ── Ce qu'un plugin déclare ───────────────────────────────
    def register(self, plugin: str, task: str, handler: Handler, *, max_attempts: int = 5, timeout: float = 900.0, grant: str | None = None,
                 every: float | None = None, daily_at: str | None = None, keep_days: int = 30) -> str:
        """Déclare une tâche : `register(self.ctx.name, "install_chart", self.install_chart)` → `accounts.install_chart`.

        `max_attempts` borne les essais, reprises après arrêt comprises. `timeout` (secondes) abandonne un essai qui ne rend
        pas la main. `grant` : un grant que le plugin déclare déjà, dont les porteurs lisent l'état de ces tâches (leur auteur
        le lit toujours).

        `every` (secondes) OU `daily_at` (« HH:MM », fuseau `doctype.timezone`) en font une tâche RÉCURRENTE : le moteur la dépose
        lui-même, une fois par période, pour chaque organisation qui a des documents de ce plugin. `keep_days` borne son journal.
        """
        if not isinstance(plugin, str) or not _TASK.match(plugin) or not isinstance(task, str) or not _TASK.match(task):
            raise ValueError(f"tâche {plugin!r}.{task!r} : plugin et tâche s'écrivent [a-z][a-z0-9_]*")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"tâche {plugin}.{task} : il faut une coroutine `async def tâche(job)`")
        if not 1 <= int(max_attempts) <= 50:
            raise ValueError("max_attempts : de 1 à 50")
        name = f"{plugin}.{task}"
        if name in self._specs:
            raise ValueError(f"tâche {name!r} déjà déclarée")
        recurrence = _recurrence(name, every, daily_at, keep_days)
        self._specs[name] = JobSpec(name, plugin, handler, int(max_attempts), float(timeout), grant, recurrence)
        self._wake.set()  # des tâches de ce nom attendaient peut-être leur plugin
        return name

    def unregister_owner(self, plugin: str) -> None:
        """Retire les tâches d'un plugin qui s'en va. Celles déjà déposées restent en attente : elles repartiront avec lui."""
        for name in [n for n, s in self._specs.items() if s.plugin == plugin]:
            del self._specs[name]
            self._scheduled.pop(name, None)  # s'il revient, sa période se rejuge (la base dira qu'elle est déjà prise)
            self._purged.pop(name, None)

    def recurring(self) -> list[dict[str, Any]]:
        """Les tâches que le moteur déclenche lui-même, avec leur rythme (pour un diagnostic : rien n'y est secret)."""
        return [{"name": s.name, **s.recurrence.describe()} for s in sorted(self._specs.values(), key=lambda s: s.name) if s.recurrence]

    def _spec(self, name: Any) -> JobSpec:
        spec = self._specs.get(name) if isinstance(name, str) else None
        if spec is None:
            raise NotFound("job_unknown", f"Tâche {name!r} inconnue : son plugin la déclare par doctype_jobs.register(…)")
        return spec

    @staticmethod
    def _checked(payload: Any, key: Any) -> tuple[dict[str, Any], str | None]:
        import json

        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise ValidationFailed("payload : un objet JSON", code="job_invalid")
        try:
            size = len(json.dumps(dict(payload), allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as e:
            raise ValidationFailed(f"payload : pas du JSON ({e})", code="job_invalid") from None
        if size > MAX_PAYLOAD_BYTES:
            raise ValidationFailed(f"payload : {size} octets, {MAX_PAYLOAD_BYTES} au plus (portez des identifiants, relisez le reste)", code="job_invalid")
        if key is not None and (not isinstance(key, str) or not 0 < len(key) <= MAX_KEY):
            raise ValidationFailed(f"key : un texte de 1 à {MAX_KEY} caractères", code="job_invalid")
        return dict(payload), key

    # ── Déposer ───────────────────────────────────────────────
    async def enqueue(self, tenant_id: str, name: str, payload: Mapping[str, Any] | None = None, *, key: str | None = None,
                      owner: str = "system", delay: float = 0.0) -> dict[str, Any]:
        """Dépose une tâche, validée tout de suite. Avec `key`, une tâche encore active de cette clé est RENDUE au lieu d'en créer une.

        `tenant_id` et `owner` viennent de l'appelant (un hook : `event.ctx.tenant_id`, `event.ctx.owner_id`), jamais du payload.
        """
        spec = self._spec(name)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValidationFailed("tenant_id : requis", code="job_invalid")
        clean, key = self._checked(payload, key)
        row, created = await self._store.add(tenant_id, spec, clean, key=key, owner=owner or "system", delay=delay)
        if created:
            self._wake.set()
        return present(row, created=created)

    async def enqueue_in(self, session: Any, tenant_id: str, name: str, payload: Mapping[str, Any] | None = None, *, key: str | None = None,
                         owner: str = "system", delay: float = 0.0) -> dict[str, Any]:
        """La même, dans une transaction OUVERTE (`event.tx.enqueue`) : la tâche existe si et seulement si la transaction est validée."""
        spec = self._spec(name)
        clean, key = self._checked(payload, key)
        row, created = await self._store.insert(session, tenant_id, spec, clean, key=key, owner=owner or "system", delay=delay)
        return present(row, created=created)

    # ── Lire (l'API et le Desk) ───────────────────────────────
    def _visible(self, ctx: RequestContext) -> tuple[str | None, tuple[str, ...] | None]:
        """Ce que cet appelant voit : `(auteur, noms de tâches)` pour `JobStore.list`. Le système voit tout."""
        if ctx.is_system:
            return None, None
        held = ctx.permissions
        return ctx.owner_id, tuple(n for n, s in self._specs.items() if s.grant is not None and s.grant in held)

    def _may_see(self, ctx: RequestContext, row: Mapping[str, Any]) -> bool:
        owner, names = self._visible(ctx)
        return ctx.is_system or row["owner"] == owner or row["name"] in (names or ())

    async def get(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """L'état d'UNE tâche de l'organisation de l'appelant. Celle qu'il ne peut pas voir est « introuvable », comme un document."""
        ctx.require_identity()
        row = await self._store.get(ctx.tenant_id, job_id) if isinstance(job_id, str) else None
        if row is None or not self._may_see(ctx, row):
            raise NotFound("job_not_found", "Tâche introuvable")
        return present(row)

    async def list(self, ctx: RequestContext, *, name: str | None = None, key: str | None = None, status: str | None = None,
                   limit: int = 50) -> dict[str, Any]:
        """Les tâches que l'appelant peut voir : les siennes, et celles dont il porte le grant. Jamais leur `payload`."""
        ctx.require_identity()
        if status is not None and status not in (PENDING, RUNNING, DONE, FAILED):
            raise ValidationFailed(f"status : {PENDING}, {RUNNING}, {DONE} ou {FAILED}", code="job_invalid")
        owner, names = self._visible(ctx)
        rows = await self._store.list(ctx.tenant_id, name=name, key=key, status=status, owner=owner, names=names,
                                      limit=max(1, min(int(limit), MAX_LIST)))
        return {"items": [present(r) for r in rows]}

    async def retry(self, ctx: RequestContext, job_id: str) -> dict[str, Any]:
        """Relance une tâche échouée. Réservé à qui porte le grant de la tâche (ou au système) : c'est un geste d'exploitation."""
        row = await self._store.get(ctx.tenant_id, job_id) if isinstance(job_id, str) else None
        if row is None or not self._may_see(ctx, row):
            raise NotFound("job_not_found", "Tâche introuvable")
        spec = self._specs.get(row["name"])
        if not ctx.is_system and (spec is None or spec.grant is None or spec.grant not in ctx.permissions):
            raise PermissionDenied()
        if row["status"] != FAILED:
            raise DoctypeError("job_not_failed", "Seule une tâche échouée se relance", status=409)
        if not await self._store.revive(ctx.tenant_id, job_id):
            raise DoctypeError("job_already_active", "Une tâche de cette clé est déjà en attente ou en cours", status=409)
        self._wake.set()
        return present({**row, "status": PENDING, "attempts": 0, "message": None, "finished_at": None})

    # ── Exécuter ──────────────────────────────────────────────
    def wake(self) -> None:
        """Réveille l'exécuteur sans attendre son prochain tour (après le commit d'une transaction qui a déposé une tâche)."""
        self._wake.set()

    def start(self) -> None:
        """Démarre l'exécuteur de ce processus (idempotent). C'est aussi la REPRISE : il prend ce qu'un arrêt a laissé."""
        if self._loop_task is None:
            self._loop_task = asyncio.get_running_loop().create_task(self._run())

    async def close(self) -> None:
        """Arrête l'exécuteur. Une tâche en cours est RENDUE (remise en attente sans compter l'essai) : elle reprendra au démarrage."""
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        running = list(self._running)
        for t in running:
            t.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        self._specs.clear()
        self._scheduled.clear()
        self._purged.clear()

    async def schedule_once(self) -> int:
        """Dépose ce que les tâches récurrentes doivent à cet instant. Rend le nombre de tâches DÉPOSÉES par cet appel.

        Appelée à chaque tour de la boucle, elle ne touche la base que lorsqu'une période commence (ou au démarrage du
        processus) : le reste du temps elle compare deux textes. Une organisation qui apparaît en cours de période attend la
        suivante.
        """
        now, made = self._clock(), 0
        for spec in [s for s in self._specs.values() if s.recurrence is not None]:
            period = spec.recurrence.period(now, self._tz)
            if self._scheduled.get(spec.name) == period:
                continue  # CE processus a déjà déposé cette période
            types = tuple(self._types_of(spec.plugin)) if self._types_of is not None else ()
            if not types:
                continue  # le plugin n'a (encore) aucun type stocké : personne à servir, et rien à retenir de cette période
            await self._presence.backfill()  # une fois par base : les organisations écrites avant la table de présence
            tenants = await self._presence.tenants(types)
            created = 0
            for i in range(0, len(tenants), SCHEDULE_CHUNK):
                created += await self._deposit(spec, period, tenants[i:i + SCHEDULE_CHUNK])
            day = now.astimezone(UTC).date().isoformat()
            if self._purged.get(spec.name) != day:  # au plus une fois par jour : c'est un balayage de la table
                await self._store.purge(spec.name, now - timedelta(days=spec.recurrence.keep_days))
                self._purged[spec.name] = day
            self._scheduled[spec.name] = period
            if created:
                self._log.info("recurring job scheduled", job=spec.name, period=period, organisations=created)
            made += created
        if made:
            self._wake.set()
        return made

    async def _deposit(self, spec: JobSpec, period: str, tenants: Sequence[str]) -> int:
        """Une transaction : la période est prise ET la tâche est déposée, ou rien. Un processus qui meurt au milieu ne laisse pas
        une période « prise » sans sa tâche — elle ne serait plus jamais déposée."""
        created = 0
        async with self._store.session() as session:
            for tenant in await self._store.claim_period(session, spec.name, period, tenants):
                _, new = await self._store.insert(session, tenant, spec, {"period": period}, key=RECURRING_KEY, owner="system")
                created += int(new)  # faux : celle de la période d'avant tourne encore, on ne l'empile pas
        return created

    async def _run(self) -> None:
        while True:
            if self._recurring:
                try:
                    await self.schedule_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - l'ordonnanceur en panne n'empêche pas d'exécuter ce qui est déposé
                    self._log.warning("job scheduler failed", error=str(e))
            try:
                started = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - base injoignable : on réessaiera au tour suivant
                self._log.warning("job runner failed", error=str(e))
                started = 0
            if started:
                continue
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll)

    async def run_once(self) -> int:
        """Lance les tâches prêtes, dans la limite de `max_parallel`. Rend le nombre de tâches LANCÉES (elles tournent ensuite seules)."""
        room = self._max_parallel - len(self._running)
        if room <= 0:
            return 0
        started = 0
        for tenant_id, job_id in await self._store.ready(tuple(self._specs), room):
            row = await self._store.claim(tenant_id, job_id, self._owner_id, self._lease)
            if row is None:
                continue  # un autre processus l'a prise
            task = asyncio.get_running_loop().create_task(self._execute(row))
            self._running.add(task)
            task.add_done_callback(self._settled)
            started += 1
        return started

    def _settled(self, task: asyncio.Task) -> None:
        self._running.discard(task)
        self._wake.set()  # une place vient de se libérer

    async def drain(self) -> None:
        """Attend la fin des tâches en cours (tests, arrêt ordonné)."""
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)

    async def _heartbeat(self, row: Mapping[str, Any]) -> None:
        """Renouvelle le bail tant que la tâche tourne : une tâche longue n'est pas « reprise » par un autre processus."""
        while True:
            await asyncio.sleep(self._lease / 3)
            try:
                if not await self._store.renew(row["tenant_id"], row["id"], self._owner_id, self._lease):
                    return  # bail perdu : un autre l'a reprise, nos écritures de fin seront refusées
            except Exception as e:  # noqa: BLE001 - une coupure ne tue pas la tâche
                self._log.warning("job lease renewal failed", job=row["id"], error=str(e))

    async def _execute(self, row: Mapping[str, Any]) -> None:
        tenant, job_id, name = row["tenant_id"], row["id"], row["name"]
        spec = self._specs.get(name)
        if spec is None:  # le plugin est parti entre la découverte et l'exécution
            await self._store.postpone(tenant, job_id, self._owner_id, message=row["message"], delay=self._poll, uncount=True)
            return
        if row["attempts"] > row["max_attempts"]:
            # Reprise de trop : elle a été prise `max_attempts` fois sans jamais finir (le serveur tombe avec elle).
            await self._fail(row, f"abandonnée après {row['max_attempts']} essais interrompus")
            return
        writer = self._writers.system(spec.plugin, tenant, f"job:{name}") if self._writers is not None else None
        beat = asyncio.get_running_loop().create_task(self._heartbeat(row))
        try:
            await asyncio.wait_for(spec.handler(JobRun(self, row, spec, writer)), timeout=spec.timeout if spec.timeout > 0 else None)
        except asyncio.CancelledError:
            # Arrêt du serveur : on REND la tâche, sans compter l'essai. Elle reprendra au démarrage.
            await asyncio.shield(self._store.postpone(tenant, job_id, self._owner_id, message=row["message"], delay=0.0, uncount=True))
            raise
        except Exception as e:  # noqa: BLE001 - tout ce qu'une tâche lève est un échec d'essai
            reason = "délai dépassé" if isinstance(e, asyncio.TimeoutError) else (str(e) or type(e).__name__)
            await self._failed_attempt(row, reason[:MAX_MESSAGE])
            return
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        if await self._store.finish(tenant, job_id, self._owner_id, status=DONE, message=None):
            self._log.info("job done", job=name, id=job_id, tenant=tenant, attempt=row["attempts"])

    async def _failed_attempt(self, row: Mapping[str, Any], reason: str) -> None:
        if row["attempts"] >= row["max_attempts"]:
            await self._fail(row, reason)
            return
        delay = min(self._max_backoff, self._backoff * (2 ** max(0, row["attempts"] - 1)))
        await self._store.postpone(row["tenant_id"], row["id"], self._owner_id, message=reason, delay=delay)
        self._log.warning("job attempt failed, will retry", job=row["name"], id=row["id"], tenant=row["tenant_id"],
                          attempt=row["attempts"], retry_in_s=delay, reason=reason)

    async def _fail(self, row: Mapping[str, Any], reason: str) -> None:
        await self._store.finish(row["tenant_id"], row["id"], self._owner_id, status=FAILED, message=reason)
        self._log.error("job failed, giving up", job=row["name"], id=row["id"], tenant=row["tenant_id"],
                        attempts=row["attempts"], reason=reason)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def present(row: Mapping[str, Any], *, created: bool | None = None) -> dict[str, Any]:
    """Ce qu'on rend d'une tâche. JAMAIS son `payload` : il est écrit par du code de confiance, pour du code de confiance."""
    out = {
        "id": row["id"], "name": row["name"], "key": row["key"], "status": row["status"], "attempts": row["attempts"],
        "max_attempts": row["max_attempts"], "message": row["message"], "owner": row["owner"],
        "creation": _iso(row["creation"]), "started_at": _iso(row["started_at"]), "finished_at": _iso(row["finished_at"]),
    }
    if created is not None:
        out["created"] = created
    return out
