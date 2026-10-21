"""Contrôle rétroactif de l'unicité (`unique: true` ajouté sur un champ qui a déjà des documents).

La table `xdt_unique` ne contient que les valeurs réservées DEPUIS l'activation du champ : un champ rendu unique
après coup n'a aucune ligne pour les documents anciens, qui peuvent donc être en double sans que rien ne le dise.
Ce module relit la colonne JSON des documents du type et regroupe en mémoire (portabilité : pas de `GROUP BY` sur
du JSON, et les valeurs sont comparées EXACTEMENT comme `Query.sync_unique` les stocke : `str(valeur)`, `None` hors jeu).

VOLONTAIREMENT pas une méthode de `Query` : le scan croise les organisations (un double dans un autre tenant compte
pour l'opérateur qui contrôle son installation), or `tests/unit/test_query_leak.py` exige que chaque requête publique
de `Query` soit bornée au tenant de l'appelant. C'est l'appelant (le service, l'action `doc.check_unique`) qui sait
s'il a le droit de regarder partout ; la session est passée telle quelle.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import select

from .db.tables import DOCUMENT_TABLE


async def _pages(session: Any, doctype: str, tenant: str | None, page_size: int = 1000) -> AsyncIterator[list[Any]]:
    """Les documents d'un type, par paquets, avec pagination par clé (tenant, name) : ni `OFFSET`, ni charge en mémoire."""
    last: tuple[str, str] | None = None
    while True:
        conds = [DOCUMENT_TABLE.c.doctype == doctype]
        if tenant is not None:
            conds.append(DOCUMENT_TABLE.c.tenant_id == tenant)
        if last is not None:
            conds.append((DOCUMENT_TABLE.c.tenant_id, DOCUMENT_TABLE.c.name) > last)  # type: ignore[operator]
        stmt = select(DOCUMENT_TABLE.c.tenant_id, DOCUMENT_TABLE.c.name, DOCUMENT_TABLE.c.data).where(*conds).order_by(
            DOCUMENT_TABLE.c.tenant_id, DOCUMENT_TABLE.c.name
        ).limit(page_size)
        rows = (await session.execute(stmt)).all()
        if not rows:
            return
        yield rows
        if len(rows) < page_size:
            return
        last = (rows[-1][0], rows[-1][1])


async def unique_conflicts(
    session: Any, doctype: str, fieldnames: Sequence[str], *, tenant: str | None = None
) -> list[dict[str, Any]]:
    """Les valeurs `unique` en double pour `doctype` : `[{field, value, tenant, names}]`, une entrée par valeur.

    Un double compte dans son organisation (`tenant` renseigné) ; avec `tenant=None`, le contrôle est global (installation).
    Les valeurs sont regroupées EXACTEMENT comme `Query.sync_unique` les réserve (`str(valeur)`, `None` hors jeu).
    """
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    async for rows in _pages(session, doctype, tenant):
        for row_tenant, name, data in rows:
            if not isinstance(data, dict):
                continue
            for field in fieldnames:
                value = data.get(field)
                if value is None:
                    continue
                groups[(row_tenant, field, str(value))].append(name)
    return sorted(
        (
            {"doctype": doctype, "tenant": t, "field": field, "value": value, "names": sorted(names)}
            for (t, field, value), names in groups.items()
            if len(names) > 1
        ),
        key=lambda c: (c["field"], c["tenant"], c["value"]),
    )
