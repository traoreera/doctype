"""La source `xlicense` : le catalogue, les modules d'une organisation et sa licence comme DocTypes VIRTUELS.

Rien n'est copié dans la table générique du moteur : les documents restent dans les tables de xlicense
(`license_modules`, `license_tenant_modules`, `licenses`), et chaque lecture est une requête ici. Le moteur apporte
ce qu'il sait faire — droits par grants, schéma strict, filtres typés, tri, recherche, colonnes, export, rapport,
tableau de bord — et xlicense reste juge de ce qu'il accepte d'écrire, exactement comme xauth l'est pour `iam`
(`tests/support/plugins/iam/src/source.py`, le même motif).

Qui écrit quoi :
  • `Module` (le catalogue) : l'administration de la PLATEFORME. Un prix est partagé par toutes les organisations.
  • `Tenant Module` (ce que l'organisation a pris) : créer = prendre un module, supprimer = le rendre. Un module
    payant ne s'active pas par là — il s'achète (`POST /me/modules/{code}/checkout`).
  • `License` : lecture seule. Une licence naît d'un essai, d'un paiement ou d'un geste de la plateforme, jamais
    d'un écran.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from xcore_doctype.context import RequestContext
from xcore_doctype.errors import NotFound, PermissionDenied, ValidationFailed
from xcore_doctype.query import StoredDoc
from xcore_doctype.schema import DocTypeDef
from xcore_doctype.sources import ReadOnlySource, content_version

from .models.license import LicenseTenantModule, ModuleSource
from .repositories.license import (LicenseModuleRepository, LicenseRepository,
                                   LicenseTenantModuleRepository)

PLATFORM_ADMIN = "admin:*"
LICENSE_MANAGE = "license:manage"
NEVER = datetime(1970, 1, 1, tzinfo=UTC)


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Le moteur ne garde jamais un `None` : « aucune valeur » et « champ absent » sont le même état."""
    return {k: v for k, v in data.items() if v is not None}


def _money(value: Any) -> str:
    """Un montant est un Currency du moteur : une CHAÎNE canonique à 2 décimales, jamais un flottant
    (`src/xcore_doctype/decimals.py` — un float ne sait pas représenter 0,10 €)."""
    return f"{float(value or 0):.2f}"


def _instant(value: datetime | None) -> str | None:
    """Un Datetime du moteur : ISO en UTC, largeur fixe."""
    if value is None:
        return None
    moment = value if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def module_doc(m: Any) -> StoredDoc:
    data = _clean(
        {
            "code": m.code,
            "label": m.label,
            "description": m.description,
            "free": bool(m.free),
            "price": _money(m.price),
            "currency": m.currency or "EUR",
            "period": m.period.value if m.period else None,
            "stripe_price_id": m.stripe_price_id,
            "active": bool(m.active),
        }
    )
    return StoredDoc("Module", m.code, data, "system", NEVER, NEVER, content_version(data))


def tenant_module_doc(row: Any, *, label: str | None = None) -> StoredDoc:
    data = _clean(
        {
            "module": row.module_code,
            "label": label,
            "source": row.source.value if row.source else None,
            "started_at": _instant(row.started_at),
            "expires_at": _instant(row.expires_at),
            "subscription": row.stripe_item_id,
            "current": row.is_current(datetime.now(tz=UTC)),
        }
    )
    return StoredDoc(
        "Tenant Module", row.module_code, data, "system", NEVER, NEVER, content_version(data)
    )


def license_doc(lic: Any, *, plan: str | None = None) -> StoredDoc:
    data = _clean(
        {
            "state": lic.state.value if lic.state else None,
            "plan": plan,
            "issued_at": _instant(lic.issued_at),
            "expires_at": _instant(lic.expires_at),
            "last_validation_at": _instant(lic.last_validation_at),
        }
    )
    return StoredDoc("License", str(lic.id), data, "system", NEVER, NEVER, content_version(data))


class XlicenseSource(ReadOnlySource):
    """Lit les tables de xlicense ; n'écrit que ce que xlicense accepte.

    `session_factory` est le `db.session` du plugin : une session par appel, comme partout ailleurs ici.
    """

    def __init__(self, session_factory: Any, invalidate: Any = None) -> None:
        self._session = session_factory
        self._invalidate = invalidate  # LicenseService.invalidate_tenant : le gate lit un cache

    # ── Lectures ──────────────────────────────────────────────────────────────

    async def fetch_all(self, ctx: RequestContext, doctype: DocTypeDef) -> list[StoredDoc]:
        async with self._session() as session:
            if doctype.name == "Module":
                # Le catalogue est le même pour tout le monde : un prix ne dépend pas de l'organisation.
                # Les modules retirés du rayon restent lisibles — une organisation peut encore en avoir un.
                return [module_doc(m) for m in await LicenseModuleRepository(session).in_catalog(only_active=False)]

            if doctype.name == "Tenant Module":
                catalog = {m.code: m.label for m in await LicenseModuleRepository(session).in_catalog(only_active=False)}
                rows = await LicenseTenantModuleRepository(session).for_tenant(ctx.tenant_id)
                return [tenant_module_doc(r, label=catalog.get(r.module_code)) for r in rows]

            if doctype.name == "License":
                licenses = await LicenseRepository(session).get_all_by(tenant_id=ctx.tenant_id) or []
                return [license_doc(l, plan=getattr(getattr(l, "plan", None), "name", None)) for l in licenses]

        return []

    # ── Écritures ─────────────────────────────────────────────────────────────

    @staticmethod
    def _require_platform(ctx: RequestContext) -> None:
        """Le catalogue est partagé : seule la plateforme y touche.

        Le moteur a déjà vérifié le grant `xlicense:module:write` — qui n'est PAS délégable, donc il n'arrive
        qu'au rôle de la plateforme. On le revérifie ici parce qu'une source reste juge : c'est la règle du
        motif (le moteur autorise, le propriétaire de la vérité décide).
        """
        held = ctx.permissions
        if PLATFORM_ADMIN not in held and LICENSE_MANAGE not in held:
            raise PermissionDenied()  # le message n'apprendrait rien d'utile : « réservé », point

    async def insert(self, ctx: RequestContext, doctype: DocTypeDef, data: dict[str, Any]) -> StoredDoc:
        if doctype.name == "Module":
            self._require_platform(ctx)
            return await self._upsert_module(ctx, data, creating=True)
        if doctype.name == "Tenant Module":
            return await self._take(ctx, data)
        raise ValidationFailed(f"{doctype.name} ne se crée pas depuis un écran", code="source_read_only")

    async def update(
        self, ctx: RequestContext, doctype: DocTypeDef, name: str, data: dict[str, Any], version: int
    ) -> StoredDoc:
        if doctype.name == "Module":
            self._require_platform(ctx)
            return await self._upsert_module(ctx, {**data, "code": name}, creating=False)
        raise ValidationFailed(f"{doctype.name} ne se modifie pas depuis un écran", code="source_read_only")

    async def remove(self, ctx: RequestContext, doctype: DocTypeDef, name: str) -> None:
        if doctype.name == "Tenant Module":
            return await self._drop(ctx, name)
        if doctype.name == "Module":
            # Un module du catalogue ne se supprime pas : des organisations l'ont peut-être payé, et son
            # historique doit rester lisible. On le retire du rayon (`active: false`).
            raise ValidationFailed(
                "Un module ne se supprime pas : décochez « Au catalogue »", code="not_deletable"
            )
        raise ValidationFailed(f"{doctype.name} ne se supprime pas", code="source_read_only")

    # ── Le détail des écritures ───────────────────────────────────────────────

    async def _upsert_module(self, ctx: RequestContext, data: dict[str, Any], *, creating: bool) -> StoredDoc:
        code = (data.get("code") or "").strip()
        if not code:
            raise ValidationFailed("Le code du module est obligatoire", code="required", details=[{"loc": "code"}])
        free = bool(data.get("free", True))
        stripe_price = (data.get("stripe_price_id") or "").strip() or None
        if not free and not stripe_price:
            # Sinon le module serait proposé à l'achat puis impossible à encaisser.
            raise ValidationFailed(
                "Un module payant exige son tarif chez le prestataire",
                code="required",
                details=[{"loc": "stripe_price_id"}],
            )
        async with self._session() as session:
            repo = LicenseModuleRepository(session)
            module = await repo.get_by_code(code)
            if module is None:
                if not creating:
                    raise NotFound("Module introuvable")
                from .models.license import LicenseModule

                module = LicenseModule(code=code, label=data.get("label") or code)
                await repo.save(module)
            module.label = data.get("label") or module.label
            module.description = data.get("description") or None
            module.free = free
            module.price = 0.0 if free else float(data.get("price") or 0)
            module.currency = (data.get("currency") or module.currency or "EUR")[:3].upper()
            if data.get("period"):
                from .models.license import BillingPeriod

                module.period = BillingPeriod(data["period"])
            module.stripe_price_id = stripe_price
            module.active = bool(data.get("active", True))
            await session.commit()
            return module_doc(module)

    async def _take(self, ctx: RequestContext, data: dict[str, Any]) -> StoredDoc:
        code = (data.get("module") or "").strip()
        if not code:
            raise ValidationFailed("Quel module ?", code="required", details=[{"loc": "module"}])
        async with self._session() as session:
            module = await LicenseModuleRepository(session).get_by_code(code)
            if module is None or not module.active:
                raise ValidationFailed("Module introuvable au catalogue", code="link_not_found", details=[{"loc": "module"}])
            if not module.free:
                # Payer est un parcours, pas une case à cocher : l'écran doit passer par la caisse.
                raise ValidationFailed(
                    f"« {module.label} » est payant : passez par le paiement pour l'activer",
                    code="payment_required",
                    details=[{"loc": "module"}],
                )
            repo = LicenseTenantModuleRepository(session)
            row = await repo.get_one(ctx.tenant_id, code)
            if row is None:
                row = LicenseTenantModule(
                    tenant_id=ctx.tenant_id, module_code=code, source=ModuleSource.FREE
                )
                await repo.save(row)
            else:
                row.source = ModuleSource.FREE
                row.expires_at = None
            await self._forget(session, ctx.tenant_id)
            await session.commit()
            return tenant_module_doc(row, label=module.label)

    async def _drop(self, ctx: RequestContext, code: str) -> None:
        async with self._session() as session:
            repo = LicenseTenantModuleRepository(session)
            row = await repo.get_one(ctx.tenant_id, code)
            if row is None:
                raise NotFound("Ce module n'est pas pris par votre organisation")
            if row.source == ModuleSource.PAID:
                raise ValidationFailed(
                    "Module payé : résiliez l'abonnement chez le prestataire, l'accès court jusqu'à la fin de la période réglée",
                    code="subscription_active",
                )
            await repo.delete(row)
            await self._forget(session, ctx.tenant_id)
            await session.commit()

    async def _forget(self, session: Any, tenant_id: str) -> None:
        """Le gate du middleware lit un cache de 5 minutes : sans cela, un module pris resterait refusé."""
        if self._invalidate is None:
            return
        try:
            await self._invalidate(session, tenant_id)
        except Exception:  # noqa: BLE001 - un cache qui ne s'efface pas ne doit pas défaire l'écriture
            pass
