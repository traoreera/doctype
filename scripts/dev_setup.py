"""Prépare une installation de DÉVELOPPEMENT : `.env` (compte administrateur) et clés JWT (`conf/private.pem`, `conf/public.pem`).

    make env        (appelé par make dev / make serve)

Idempotent : ce qui existe n'est jamais réécrit. Le mot de passe de l'administrateur est tiré au hasard et écrit dans `.env` (ignoré par git) : c'est
LUI que vous tapez dans l'écran de connexion du Desk (`ADMIN_EMAIL`, `ADMIN_PASSWORD`). xauth crée ce compte au premier démarrage (`seed:`). En
production, ce sont des secrets de l'hôte et les clés viennent de votre gestion de secrets : ce script ne sert qu'au développement.
"""

from __future__ import annotations

import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def password() -> str:
    """24 caractères avec minuscule, majuscule, chiffre et symbole (la politique de mot de passe d'xauth)."""
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(20))
    return f"{secrets.choice(string.ascii_uppercase)}{secrets.choice(string.ascii_lowercase)}{secrets.choice(string.digits)}{secrets.choice('!@#%*+-=?')}{body}"


def write_env(path: Path) -> bool:
    """Complète `.env` : chaque variable manquante est ajoutée (un `.env` ancien, d'avant xauth, reçoit le compte administrateur), aucune n'est réécrite."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    have = {line.split("=", 1)[0].strip() for line in existing.splitlines() if "=" in line and not line.lstrip().startswith("#")}
    wanted = {"ADMIN_EMAIL": "admin@example.com", "ADMIN_PASSWORD": password()}
    missing = {k: v for k, v in wanted.items() if k not in have}
    if not missing:
        return False
    sep = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(existing + sep + "".join(f"{k}={v}\n" for k, v in missing.items()), encoding="utf-8")
    path.chmod(0o600)
    return True


def write_keys(directory: Path) -> bool:
    private, public = directory / "private.pem", directory / "public.pem"
    if private.exists() and public.exists():
        return False
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return True


def main() -> int:
    if write_env(ROOT / ".env"):
        print(".env complété : compte administrateur admin@example.com, mot de passe aléatoire (ignoré par git)", file=sys.stderr)
    if write_keys(ROOT / "conf"):
        print("conf/private.pem et conf/public.pem créés (clés JWT RS256 de développement, ignorées par git)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
