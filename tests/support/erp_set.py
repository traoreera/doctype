"""Charger le jeu ERP (`doctypes/`) dans un registre, la façon dont il se charge VRAIMENT.

Le sous-module `doctypes/` contient, à côté des 22 dossiers de modules, `docs/` et `workspaces/` : or
`load_dir` lit TOUT `.yaml|.yml|.json` de l'arbre comme une définition de DocType (les espaces de travail
se chargent par `load_workspaces`, jamais `load_dir`). Un `load_dir` de la racine du sous-module échoue
donc sur eux : le jeu se charge un dossier de module par appel, `peers=True` (les modules se citent en
cycle, y compris pour leurs tables — `POS Invoice` attend la table `Packed Item` d'un autre dossier),
puis `settle()` enregistre ce qui a attendu et `validate_links()` juge l'arbre complet.
"""

from __future__ import annotations

from pathlib import Path

from xcore_doctype.registry import DocTypeRegistry

# Les dossiers du sous-module qui ne sont PAS des modules de DocTypes. Toute autre arborescence ajoutée
# au sous-module doit être nommée ici — ou un chargement échouera bruyamment, ce qui est le but d'une garde.
NON_MODULES = {"docs", "workspaces"}


def erp_modules(erp_dir: Path) -> list[Path]:
    """Les dossiers de modules du jeu, triés (le tri des noms de fichiers suffit : voir `load_dir`)."""
    return sorted(p for p in erp_dir.iterdir() if p.is_dir() and p.name not in NON_MODULES)


def load_erp_set(registry: DocTypeRegistry, erp_dir: Path) -> list[str]:
    """Charge tout le jeu dans `registry` (`module` libre) et le juge : ce qui reste garé ou brisé est une faute du jeu."""
    loaded: list[str] = []
    for module in erp_modules(erp_dir):
        loaded += registry.load_dir(module, peers=True)
    _, broken = registry.settle()
    assert not broken, broken
    assert not registry.waiting(), registry.waiting()
    registry.validate_links()
    return loaded
