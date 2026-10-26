"""Plugin d'exemple `biblio` : la partie Python d'un plugin métier.

1. il enregistre les hooks de confiance que ses DocTypes citent PAR LEUR NOM (`biblio.verifie_dates`, `biblio.duree`) ;
2. il charge ses DocTypes, son espace et son extension du Desk auprès de xcore-doctype ;
3. il déclare à auth les grants que ses DocTypes exigent, tirés du YAML (rien n'est retapé ici) ;
4. il expose une action IPC (`biblio.en_retard`) qu'un autre plugin ou un script peut appeler.
"""

from datetime import date
from pathlib import Path

from xcore.sdk import AutoDispatchMixin, TrustedBase, action, error, ok

from xcore_doctype import DocumentValidationError, HookEvent

ROOT = Path(__file__).resolve().parent.parent


class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        # Les hooks D'ABORD : un DocType dont un hook manque refuse ses sauvegardes (503 hook_unavailable).
        hooks = self.get_service("doctype_hooks")
        hooks.register("biblio.verifie_dates", self.verifie_dates, owner=self.ctx.name)
        hooks.register("biblio.duree", self.duree, owner=self.ctx.name)

        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, ROOT / "doctypes")  # tout ou rien : une erreur de YAML empêche le chargement
        registry.load_workspaces(self.ctx.name, ROOT / "workspaces")  # après les DocTypes : ses blocs les visent
        registry.load_desk(self.ctx.name, ROOT / "desk")  # servi seulement si l'opérateur l'autorise (doctype.desk.extensions)

        # UN seul `rbac.declare` pour tout le plugin, après le chargement : biblio:livre:read, biblio:emprunt:write…
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self) -> None:
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    # ── Hooks (appelés par le moteur, dans l'ordre : validate, puis before_save, dans la transaction) ──
    async def verifie_dates(self, event: HookEvent) -> None:
        """`validate` : `data` est en lecture seule ; lever `DocumentValidationError` refuse la sauvegarde (422, erreur sur le champ)."""
        sortie, rendu_le = event.data.get("sortie"), event.data.get("rendu_le")
        if sortie and rendu_le and rendu_le < sortie:  # dates ISO : l'ordre des chaînes est celui des jours
            raise DocumentValidationError(
                "Retour avant la sortie", details=[{"field": "rendu_le", "msg": "Ne peut pas précéder la sortie", "type": "hook_rejected"}]
            )

    async def duree(self, event: HookEvent) -> None:
        """`before_save` : `data` est modifiable en place (puis revalidé par le schéma). Ce que le client a envoyé pour `jours` est écrasé."""
        sortie = event.data.get("sortie")
        fin = event.data.get("rendu_le") or date.today().isoformat()
        if sortie:
            event.data["jours"] = (date.fromisoformat(fin) - date.fromisoformat(sortie)).days
        else:
            event.data.pop("jours", None)

    # ── Action IPC d'opérateur : POST <prefix>/ipc/biblio/biblio.en_retard  {"payload": {"tenant": "…"}} ──
    @action("biblio.en_retard")
    async def en_retard(self, payload: dict) -> dict:
        """Les emprunts non rendus dont le retour prévu est passé, pour UN tenant nommé dans la charge (un script de relance, par exemple).

        La lecture se fait en contexte SYSTÈME, nommé par sa raison (journalisée) : c'est un appel d'opérateur (clé du plugin), pas d'un
        utilisateur. Un utilisateur, lui, passe par le Desk ou l'API HTTP, avec ses propres droits."""
        tenant = (payload or {}).get("tenant")
        if not isinstance(tenant, str) or not tenant:
            return error("`tenant` est obligatoire", "missing_field")
        try:
            reader = self.get_service("doctype_documents").system(tenant, "biblio.en_retard")
        except Exception:  # noqa: BLE001 - tenant invalide : le détail n'a rien à faire dans la réponse
            return error("tenant invalide", "invalid_tenant")
        page = await reader.list(
            "Emprunt", filters={"statut": "En cours", "retour_prevu": ["<", date.today().isoformat()]}, order_by="retour_prevu", descending=False, limit=100
        )
        return ok(items=[{"name": d["name"], "livre": d["data"]["livre"], "emprunteur": d["data"]["emprunteur"]} for d in page["items"]])
