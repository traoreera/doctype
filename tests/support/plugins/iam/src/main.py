"""Plugin propriétaire du module `iam` : les accès d'xauth (permissions, rôles, membres) comme DocTypes, et le profil de chaque utilisateur.

Les DocTypes `Permission`, `Role` et `Member` sont VIRTUELS (`source: xauth`) : leurs documents restent chez xauth, ce plugin n'en copie rien. Il enregistre la
source `xauth` (src/source.py), apporte ses DocTypes et son espace de travail, et déclare à auth les grants que le YAML exige. Ces grants vont aussi au rôle
`tenant_admin` (le propriétaire d'une organisation) : sans cela, il ne pourrait pas ouvrir les écrans qui gèrent SES membres.
"""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

from .source import XauthSource

BASE = Path(__file__).resolve().parent.parent
AUTH_PLUGIN = "auth"  # le `name:` du plugin d'authentification (app/auth)


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        caller, me = self.ctx.caller, self.ctx.name  # des VALEURS : la source survit à l'instance du plugin au reload (D8)

        async def call(action: str, payload: dict, tenant_id: str):
            if caller is None:
                raise RuntimeError("aucun appel de plugin disponible")
            return await caller(AUTH_PLUGIN, action, payload, caller=me, tenant_id=tenant_id, principal=None)

        self.get_service("doctype_sources").register(me, "xauth", XauthSource(call))  # AVANT les DocTypes : ils la citent
        registry = self.get_service("doctype_registry")
        registry.load_dir(me, BASE / "doctypes")
        registry.load_workspaces(me, BASE / "workspaces")
        grants = [{**g, "default_roles": ["tenant_admin"]} for g in self.get_service("doctype_grants").for_plugin(me)]
        await self.ctx.events.emit("rbac.declare", {"plugin": me, "grants": grants}, source=me)

    async def on_unload(self) -> None:
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
        self.get_service("doctype_sources").unregister_owner(self.ctx.name)
