"""Catalogue des grants cités par les DocTypes : source unique, déclarée dans auth par le plugin PROPRIÉTAIRE.

Le YAML d'un DocType suffit à connaître les grants dont il a besoin (convention `<module>:<doctype>:<action>` et entrées
explicites de `permissions`). Plutôt que de les retaper dans `rbac.grants`, le plugin propriétaire les demande ici et les
passe à `rbac.declare` :

    # dans on_load d'un plugin qui déclare `requires: [xcore-doctype]` et dont le nom est le `module` de ses DocTypes
    grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
    await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": [*mes_grants_a_la_main, *grants]})

Un SEUL `rbac.declare` par plugin : auth désactive les grants du plugin absents de la liste reçue, un second appel
désactiverait donc ceux du premier. xcore-doctype n'émet jamais l'événement lui-même : son nom contient un tiret, qui ne
peut pas servir de namespace, et deux émetteurs sous un même namespace se désactiveraient mutuellement.
"""

from __future__ import annotations

from typing import Any

from .registry import DocTypeRegistry
from .schema import GrantSpec


class GrantCatalog:
    """Vue sur le registre. Exportée aux autres plugins comme service `doctype_grants` (liée à l'instance, D8)."""

    def __init__(self, registry: DocTypeRegistry) -> None:
        self._registry = registry

    def all(self) -> dict[str, GrantSpec]:
        """Tous les grants cités. Un grant partagé par plusieurs DocTypes garde le premier libellé (ordre alphabétique) et
        n'est délégable par un tenant que si TOUS les DocTypes qui le citent l'acceptent : le plus restrictif l'emporte."""
        merged: dict[str, GrantSpec] = {}
        for name in self._registry.names():
            for grant, spec in self._registry.get(name).definition.grant_specs().items():
                prev = merged.get(grant)
                if prev is None:
                    merged[grant] = spec
                elif prev.tenant_grantable and not spec.tenant_grantable:
                    merged[grant] = GrantSpec(prev.name, prev.description, prev.group, False)
        return dict(sorted(merged.items()))

    def cited(self) -> list[str]:
        return list(self.all())

    def for_plugin(self, plugin: str) -> list[dict[str, Any]]:
        """Les grants que `plugin` peut déclarer dans auth (premier segment == son nom), au format de `rbac.declare`.

        Les grants d'un autre namespace (un DocType qui cite `stock:item:read`) sont cités mais pas déclarables ici : ils
        appartiennent au plugin qui porte ce namespace.
        """
        return [spec.as_declaration() for spec in self.all().values() if spec.namespace == plugin]
