"""`scripts/dev_setup.py` : `.env` (administrateur) et clés JWT de développement, idempotent."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("dev_setup", ROOT / "scripts" / "dev_setup.py")
dev_setup = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(dev_setup)  # type: ignore[union-attr]


def test_the_generated_password_satisfies_xauths_policy_and_is_never_the_same_twice():
    seen = {dev_setup.password() for _ in range(50)}
    assert len(seen) == 50
    for p in seen:
        assert len(p) >= 8 and re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"\d", p)


def test_env_is_created_once_and_never_rewritten(tmp_path):
    env = tmp_path / ".env"
    assert dev_setup.write_env(env) is True
    first = env.read_text()
    assert first.startswith("ADMIN_EMAIL=admin@example.com\n") and "ADMIN_PASSWORD=" in first
    assert oct(env.stat().st_mode & 0o777) == "0o600"  # un secret : lisible par son propriétaire seulement
    assert dev_setup.write_env(env) is False
    assert env.read_text() == first  # le mot de passe ne change pas d'un `make dev` à l'autre


def test_an_older_env_is_completed_not_replaced(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AUTRE=garde-moi")  # sans saut de ligne final
    assert dev_setup.write_env(env) is True
    text = env.read_text()
    assert text.startswith("AUTRE=garde-moi\nADMIN_EMAIL=") and "ADMIN_PASSWORD=" in text
    env.write_text("ADMIN_EMAIL=moi@example.com\n# ADMIN_PASSWORD=commenté\n")
    dev_setup.write_env(env)
    lines = env.read_text().splitlines()
    assert lines[0] == "ADMIN_EMAIL=moi@example.com" and any(line.startswith("ADMIN_PASSWORD=") and not line.startswith("#") for line in lines)  # un commentaire n'est pas une valeur


def test_jwt_keys_are_a_usable_rs256_pair_created_once(tmp_path):
    keys = tmp_path / "conf"
    assert dev_setup.write_keys(keys) is True
    priv = (keys / "private.pem").read_bytes()
    assert dev_setup.write_keys(keys) is False and (keys / "private.pem").read_bytes() == priv
    assert oct((keys / "private.pem").stat().st_mode & 0o777) == "0o600"
    from jose import jwt

    token = jwt.encode({"sub": "x"}, priv.decode(), algorithm="RS256")
    assert jwt.decode(token, (keys / "public.pem").read_text(), algorithms=["RS256"])["sub"] == "x"
