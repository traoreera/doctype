"""Le nom d'utilisateur d'xauth : sa règle, et l'évolution d'une base ANCIENNE (colonne et index ajoutés, rien d'autre touché).

Le code d'xauth (`app/auth`) vit sur le poste de développement (hors dépôt) : sans lui, ce module saute.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest
import sqlalchemy as sa

from tests.support import XAUTH_DIR

pytestmark = pytest.mark.skipif(not XAUTH_DIR.is_dir(), reason="le vrai xauth (app/auth) est absent de ce poste")


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, XAUTH_DIR / relative)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Chargé sous un autre nom, comme un hôte xcore chargerait le plugin : sans xauth sur le poste, rien ne s'exécute et le module saute.
username = load("xauth_username", "src/services/auth/username.py") if XAUTH_DIR.is_dir() else None
db_upgrade = load("xauth_db_upgrade", "src/utils/db_upgrade.py") if XAUTH_DIR.is_dir() else None


@pytest.mark.parametrize("raw,expected", [("alice", "alice"), ("  Alice  ", "alice"), ("jean.dupont", "jean.dupont"), ("a_b-c", "a_b-c"), ("007", "007"), ("x" * 32, "x" * 32)])
def test_valid_names_are_stored_lowercase(raw, expected):
    assert username.normalize_username(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "ab", "x" * 33, "-alice", ".alice", "_alice", "al ice", "alice@acme.test", "alicé", "ali/ce", None])
def test_invalid_names_are_refused_with_the_rule(raw):
    with pytest.raises(ValueError, match="3 à 32 caractères"):
        username.normalize_username(raw)  # type: ignore[arg-type]


def test_an_at_sign_is_what_tells_an_address_from_a_name():
    assert username.is_email("a@b.c") and not username.is_email("alice")
    assert not username.USERNAME_RE.fullmatch("a@b.c")  # un nom ne peut JAMAIS ressembler à une adresse : la connexion n'a pas d'ambiguïté


@pytest.mark.parametrize("email,expected", [("admin@example.com", "admin"), ("Jean.Dupont@acme.test", "jean.dupont"), ("a+tag@x.test", "a-tag"), ("ab@x.test", None), ("@x.test", None), ("é@x.test", None)])
def test_a_name_is_proposed_from_an_address_when_one_is_valid(email, expected):
    assert username.derive_username(email) == expected


def old_database():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE xauth_users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE)"))
        conn.execute(sa.text("INSERT INTO xauth_users VALUES ('u1', 'a@x.test'), ('u2', 'b@x.test')"))
    return engine


def test_an_old_database_gains_the_column_and_a_unique_index_and_keeps_its_rows():
    engine = old_database()
    with engine.begin() as conn:
        assert db_upgrade.add_missing_columns(conn) == ["xauth_users.username", "index ux_xauth_users_username"]
        assert conn.execute(sa.text("SELECT id, username FROM xauth_users ORDER BY id")).all() == [("u1", None), ("u2", None)]  # comptes anciens : sans nom, connexion par adresse
        conn.execute(sa.text("UPDATE xauth_users SET username = 'alice' WHERE id = 'u1'"))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("UPDATE xauth_users SET username = 'alice' WHERE id = 'u2'"))  # unique


def test_the_upgrade_is_idempotent_and_ignores_a_missing_table():
    engine = old_database()
    with engine.begin() as conn:
        db_upgrade.add_missing_columns(conn)
        assert db_upgrade.add_missing_columns(conn) == []  # rien à refaire
    with sa.create_engine("sqlite://").begin() as empty:
        assert db_upgrade.add_missing_columns(empty) == []  # table absente : `create_all` s'en charge
        # Plusieurs comptes SANS nom coexistent (les NULL ne se heurtent pas).
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM xauth_users WHERE username IS NULL")).scalar() == 2
