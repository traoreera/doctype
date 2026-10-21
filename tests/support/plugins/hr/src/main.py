"""Plugin propriétaire du module `hr` (ressources humaines). Il apporte ses DocTypes et son espace de travail, et déclare à auth les grants que le YAML exige. C'est
tout ce qu'un module simple demande à xcore-doctype : un dossier de YAML et ces quelques lignes. Une seule règle métier en code : un employé qui PART désactive son
compte (`access.py`)."""

from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase

from .access import employee_access

DOCTYPES = Path(__file__).resolve().parent.parent / "doctypes"
AUTH_PLUGIN = "auth"  # le `name:` du plugin d'authentification (app/auth)


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        caller, me = self.ctx.caller, self.ctx.name  # des VALEURS : le hook survit à l'instance du plugin au reload (D8)

        async def call(action: str, payload: dict, tenant_id: str):
            if caller is None:
                raise RuntimeError("aucun appel de plugin disponible")
            return await caller(AUTH_PLUGIN, action, payload, caller=me, tenant_id=tenant_id, principal=None)

        # Le hook d'abord : un DocType dont un hook manque refuse ses sauvegardes (503).
        self.get_service("doctype_hooks").register("hr.employee_access", employee_access(call), owner=me)
        registry = self.get_service("doctype_registry")
        registry.load_dir(me, DOCTYPES)
        registry.load_workspaces(me, DOCTYPES.parent / "workspaces")  # APRÈS les DocTypes : les blocs les visent
        grants = self.get_service("doctype_grants").for_plugin(me)
        await self.ctx.events.emit("rbac.declare", {"plugin": me, "grants": grants}, source=me)

    async def on_unload(self) -> None:
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)
