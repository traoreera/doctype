"""Aides de test : le backend d'authentification de TEST (`dev-auth/`, jetons HMAC) et de quoi fabriquer des jetons.

Le produit s'authentifie avec xauth (`app/auth`). Les tests du moteur, eux, fabriquent n'importe quel principal (grants, tenant) sans démarrer xauth : ce
plugin de test le permet. Son fichier est autonome, donc posé tel quel dans un hôte de test (`install_dev_auth`) et importé ici pour `mint_token`.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

DEV_AUTH_DIR = Path(__file__).resolve().parent / "dev-auth"

# Les plugins de démonstration/test versionnés (`sales`, `stock`, `hr`, `iam`) vivent ICI, pas dans `app/` :
# `app/` n'est plus du dépôt (gitignoré, hôte de développement local). Les tests ne dépendent que de ce dossier.
FIXTURES = Path(__file__).resolve().parent / "plugins"

# Le VRAI xauth : sur le poste de développement (`app/auth`), hors dépôt. Sans lui, les tests qui le démarrent sautent.
XAUTH_DIR = Path(__file__).resolve().parents[2] / "app" / "auth"

_spec = importlib.util.spec_from_file_location("xdt_test_devauth", DEV_AUTH_DIR / "src" / "main.py")
assert _spec is not None and _spec.loader is not None
devauth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(devauth)
mint_token = devauth.mint_token


def install_dev_auth(dest: Path, *, link: bool = False) -> None:
    """Pose le plugin de test `dev-auth` dans `dest` (une copie, ou un lien symbolique)."""
    if link:
        dest.symlink_to(DEV_AUTH_DIR)
        return
    shutil.copytree(DEV_AUTH_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
