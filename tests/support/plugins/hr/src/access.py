"""Le lien RH -> accès : un employé qui PART désactive son compte.

`Employee.user` est un lien vers le compte (`Member`, DocType virtuel du plugin `iam`, qui vit chez xauth). Quand le statut d'une fiche passe à « Parti », ce hook
(`before_save`, avant l'écriture) demande à xauth de désactiver le compte DANS CETTE ORGANISATION : il ne se connecte plus ici, son compte reste actif ailleurs et ses
rôles et permissions restent notés (le réactiver les retrouve). C'est xauth qui juge l'acteur : seul le propriétaire de l'organisation (ou l'administrateur de la
plateforme) désactive un compte ; sinon l'enregistrement est REFUSÉ avec un message clair, plutôt que de laisser croire qu'un compte est fermé alors qu'il ne l'est pas.

Rien n'est réactivé automatiquement quand le statut revient à « Actif » : rouvrir un accès est une décision, pas un effet de bord.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from xcore_doctype import DocumentValidationError

Call = Callable[[str, dict[str, Any], str], Awaitable[Any]]
LEFT = "Parti"


def employee_access(call: Call):
    async def hook(event) -> None:
        user = event.data.get("user")
        if not user or event.data.get("status") != LEFT or (event.old or {}).get("status") == LEFT:
            return  # pas de compte lié, ou l'employé n'est pas en train de partir
        ctx = event.ctx

        async def ask(action: str, **payload: Any) -> dict[str, Any]:
            answer = await call(action, {"tenant_id": ctx.tenant_id, "actor_id": ctx.user_id, **payload}, ctx.tenant_id)
            return answer if isinstance(answer, dict) else {"status": "error", "code": "error"}

        found = await ask("xauth.iam.member", user_id=user)
        if found.get("status") != "ok":
            if found.get("code") == "not_found":
                return  # ce compte n'est (plus) membre de l'organisation : il n'y a rien à fermer
            _refuse(found)
        member = found["member"]
        if not member.get("is_active", True):
            return
        saved = await ask(
            "xauth.iam.member.save", user_id=user, primary_role_id=member.get("primary_role_id"), role_ids=member.get("role_ids", []),
            permissions=member.get("permissions", []), is_active=False,
        )
        if saved.get("status") != "ok":
            _refuse(saved)

    return hook


def _refuse(answer: dict[str, Any]) -> None:
    code = answer.get("code")
    if code == "forbidden":
        raise DocumentValidationError(
            "Seul le propriétaire de l'organisation peut désactiver le compte lié à cet employé : demandez-lui, ou retirez le lien « Compte » avant d'enregistrer."
        )
    if code == "invalid":
        raise DocumentValidationError(f"Le compte lié ne peut pas être désactivé : {answer.get('msg')}")
    raise DocumentValidationError("Le compte lié n'a pas pu être désactivé (xauth ne répond pas) : réessayez.")
