"""Historique des modifications (doc/doctype.md §7.1, §11) : une entrée par création, modification utile et suppression.

Une entrée ne garde que la DIFFÉRENCE : `{champ: {"old": ancienne valeur, "new": nouvelle valeur}}`, sans les champs qui n'ont
pas bougé. Une création est une différence avec « rien » (`old` nul partout), une suppression avec « plus rien » (`new` nul
partout) : son entrée conserve donc le contenu du document supprimé, ce qui permet de savoir qui a supprimé quoi.
Soumettre et annuler s'écrivent comme une différence de la seule colonne d'état : `{"docstatus": {"old": 0, "new": 1}}`. Renommer, comme une différence du nom : `{"name": {"old": "A", "new": "B"}}`.
Un champ Table est comparé en bloc : ses deux tableaux, complets, sont enregistrés s'ils diffèrent.

Ce module ne connaît ni la base ni les permissions (`Query.add_version`, `DocumentService.history`).
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

Action = Literal["create", "update", "delete", "submit", "cancel", "rename"]
ACTIONS: tuple[Action, ...] = ("create", "update", "delete", "submit", "cancel", "rename")
# Ce que la rétention par document (`max_versions`) ne purge jamais : qui a soumis, qui a annulé. C'est la trace qui rend un document figé
# opposable (schema `_submittable_rules`) ; au plus une entrée de chaque par document (un document soumis ne revient pas au brouillon).
KEPT_ACTIONS: tuple[Action, ...] = ("submit", "cancel")


def diff(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Les champs dont la valeur diffère. `None` pour un document absent ; un champ absent vaut `None`."""
    before, after = old or {}, new or {}
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if a != b:
            out[key] = {"old": a, "new": b}
    return out


def snapshot(changes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Le contenu d'un document supprimé, reconstitué depuis son entrée de suppression (les `old`)."""
    return {k: v["old"] for k, v in changes.items() if v.get("old") is not None}
