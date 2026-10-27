"""Les permissions utilisateur (groupe 2 du programme, `process/programme.md` §2.1) : les documents `User Permission` du plugin `system`.

Un document dit « ce membre ne voit, du type `allow`, que la valeur `for_value` » (plusieurs documents = plusieurs valeurs permises). Le
moteur les lit lui-même, en SQL, à chaque requête (`Query._restriction_conds`) et en Python pour une valeur écrite (`Query.permission_facts`) :
ce module ne fait que GARDER leur écriture cohérente, par deux hooks que xcore-doctype enregistre (comme `doctype.notification_rule`) :

- `doctype.user_permission` (validate) : le type visé est connu et stocké ici, la valeur existe, `applies_to` (facultatif) est ce type ou un
  type qui a un champ Lien vers lui ;
- `doctype.user_permission_member` (before_save) : le membre se désigne par son e-mail (l'identité est un UUID, illisible) ; l'annuaire le
  traduit en identifiant, rangé dans `user` (le champ que le moteur compare à l'appelant). Sans annuaire, la saisie est l'identifiant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from .context import RequestContext
from .directory import Directory
from .errors import DocumentValidationError
from .permissions import USER_PERMISSION, Scope, stored_kind

if TYPE_CHECKING:
    from .document import DocumentService
    from .hooks import HookEvent

CHECK_HOOK = "doctype.user_permission"
MEMBER_HOOK = "doctype.user_permission_member"


def _refuse(field: str, msg: str) -> DocumentValidationError:
    return DocumentValidationError("Permission utilisateur incohérente", code="invalid_user_permission", details=[{"field": field, "msg": msg, "type": "invalid_user_permission"}])


def make_user_permission_hooks(documents: "DocumentService", directory: Directory) -> dict[str, Callable[["HookEvent"], Awaitable[None]]]:
    registry = documents._registry

    async def check(event: "HookEvent") -> None:
        allow, value, applies = event.data.get("allow"), event.data.get("for_value"), event.data.get("applies_to")
        if not isinstance(allow, str) or not registry.has(allow) or not stored_kind(registry.get(allow).definition) or allow == USER_PERMISSION:
            raise _refuse("allow", "Un type de document connu, dont les documents sont gardés ici (ni réglage, ni type virtuel, ni table enfant)")
        if applies:
            if not registry.has(applies) or not stored_kind(registry.get(applies).definition):
                raise _refuse("applies_to", "Un type de document connu, dont les documents sont gardés ici")
            target = registry.get(applies).definition
            if applies != allow and not any(f.fieldtype == "Link" and f.options == allow for f in target.fields):
                raise _refuse("applies_to", f"{applies} n'a aucun champ Lien vers {allow} : la restriction n'y changerait rien")
        system = RequestContext.system(event.ctx.tenant_id, "user permission check")
        async with documents._query(system) as q:
            exists = await q.existing_names(allow, {value}, scope=Scope.everything("read")) if isinstance(value, str) else set()
        if value not in exists:
            raise _refuse("for_value", f"Aucun document {allow} ne s'appelle {value!r}")

    async def member(event: "HookEvent") -> None:
        given = event.data.get("member")
        if not isinstance(given, str) or not given.strip():
            return  # `member` est obligatoire : le schéma le refuse
        wanted = given.strip()
        people = await directory.members(event.ctx.tenant_id)
        if people is None:
            # Pas d'annuaire (ou il ne répond pas) : la saisie EST l'identifiant comparé à l'appelant. Une adresse ne l'est jamais (l'identité est
            # un UUID) : l'accepter garderait une restriction qui ne restreint personne, en silence.
            if "@" in wanted:
                raise _refuse("member", "L'annuaire ne répond pas : désignez le membre par son identifiant, ou réessayez plus tard")
            event.data["user"] = wanted
            return
        for p in people:
            if p.id == wanted or (p.email and p.email.lower() == wanted.lower()):
                event.data["user"] = p.id
                return
        raise _refuse("member", f"{wanted!r} n'est pas membre de cette organisation")

    return {CHECK_HOOK: check, MEMBER_HOOK: member}

