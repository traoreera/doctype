"""Row Level Security PostgreSQL (process/doctype.md D14) : un filet de sécurité SOUS la couche de requêtes unique.

`Query` impose déjà `tenant_id` à chaque instruction (D3) et `tests/unit/test_query_leak.py` le vérifie. La RLS ajoute la même règle
dans la base : une requête qui oublierait un jour son filtre ne verrait quand même que les lignes de SON tenant.

Mécanisme :
- chaque table système porte la politique `tenant_id = current_setting('xdt.tenant_id', true)` (lecture ET écriture) ;
- `FORCE ROW LEVEL SECURITY` : elle s'applique aussi au propriétaire des tables, qui est le rôle de connexion du plugin ;
- le tenant est posé par `set_config('xdt.tenant_id', …, true)`, LOCAL à la transaction, à l'ouverture de chaque session du service :
  il ne survit pas au `COMMIT`, donc une connexion rendue au pool (même derrière pgbouncer en mode transaction) ne garde rien. Il vient du
  `RequestContext`, jamais de l'en-tête ; sans lui, `current_setting` est nul ou vide et la politique ne laisse AUCUNE ligne (échec fermé).

La RLS ne protège pas contre un rôle qui la contourne : superutilisateur et `BYPASSRLS` ne sont pas soumis aux politiques, même forcées.
`ensure_rls` REFUSE donc de démarrer avec un tel rôle plutôt que de laisser croire à une protection qui n'existe pas.
Réglage : `doctype.rls: true` (défaut : false) ; PostgreSQL seulement. Voir process/phase4.md.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ConfigurationError
from .tables import SystemTables

POLICY = "xdt_tenant_isolation"
SETTING = "xdt.tenant_id"
_PREDICATE = f"tenant_id = current_setting('{SETTING}', true)"


async def ensure_rls(db: Any, tables: SystemTables, dialect: str) -> list[str]:
    """Active la RLS (idempotent) sur toutes les tables système. Retourne leurs noms. Lève `ConfigurationError` sinon."""
    if dialect != "postgresql":
        raise ConfigurationError(
            f"doctype.rls est vrai mais la base est {dialect!r} : la RLS n'existe que sur PostgreSQL. "
            "Désactivez doctype.rls (la couche de requêtes isole déjà les tenants) ou utilisez PostgreSQL"
        )
    names = [t.name for t in tables.metadata.sorted_tables]
    try:
        async with db.session() as session:
            role = (
                await session.execute(
                    text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
            if role.rolsuper or role.rolbypassrls:
                raise ConfigurationError(
                    f"doctype.rls est vrai mais le rôle de connexion {role.rolname!r} est "
                    f"{'superutilisateur' if role.rolsuper else 'BYPASSRLS'} : PostgreSQL ne lui applique aucune politique, la RLS "
                    "ne protégerait rien. Connectez xcore avec un rôle ordinaire, propriétaire des tables xdt_*"
                )
            for name in names:  # noms issus de nos propres métadonnées, jamais d'une entrée
                await session.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
                await session.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
                await session.execute(text(f"DROP POLICY IF EXISTS {POLICY} ON {name}"))
                await session.execute(
                    text(f"CREATE POLICY {POLICY} ON {name} FOR ALL USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})")
                )
    except DBAPIError as e:
        raise ConfigurationError(
            f"impossible d'activer la RLS ({type(e.orig).__name__ if e.orig else type(e).__name__}) : le rôle de connexion doit être "
            "propriétaire des tables xdt_* (celui qui les crée au premier démarrage)"
        ) from e
    return names


async def bind_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Pose le tenant pour la transaction en cours (et elle seule). À appeler avant toute autre instruction de la session."""
    await session.execute(text("SELECT set_config(:setting, :tenant, true)"), {"setting": SETTING, "tenant": tenant_id})
