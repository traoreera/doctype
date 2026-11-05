"""Chaîne Alembic de xflows et démarrage du plugin sur une base PostgreSQL PROTÉGÉE PAR MOT DE PASSE.

Le rôle jetable reçoit un mot de passe généré à la volée (secrets.token_urlsafe), jamais littéral. Nécessite
XFLOWS_TEST_PG_URL (PostgreSQL jetable, compte superutilisateur, base dont le nom contient « test » : le schéma
`public` est réinitialisé et le rôle temporaire est supprimé en fin de test), sinon sauté. Le serveur doit imposer le mot de passe à ce rôle
(règle pg_hba `host all xflows_pwd_test 127.0.0.1/32 scram-sha-256` avant toute règle trust), sinon sauté.
"""
from __future__ import annotations

import os
import secrets
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


URL = os.environ.get("XFLOWS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="XFLOWS_TEST_PG_URL absent (PostgreSQL jetable)")

VERSION_TABLE = "alembic_version_xflows"
ROLE = "xflows_pwd_test"


@pytest_asyncio.fixture
async def protegee():
    """Base vide + rôle à mot de passe ; rend (engine admin, URL du rôle avec mot de passe, mot de passe)."""
    admin_url = make_url(URL)
    base = admin_url.database or ""
    if "test" not in base:
        pytest.fail(f"XFLOWS_TEST_PG_URL doit viser une base jetable dont le nom contient « test » (reçu : {base!r})")
    secret = secrets.token_urlsafe(16).replace("-", "a").replace("_", "b")   # alphanumérique : sûr dans du SQL
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        if await _role(conn):
            await conn.execute(text(f"DROP OWNED BY {ROLE}"))
        await conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        await conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{secret}'"))
        await conn.execute(text(f"GRANT ALL ON SCHEMA public TO {ROLE}"))
    role_url = admin_url.set(username=ROLE, password=secret)
    # Un serveur en authentification « trust » (CI jetable) n'exige aucun mot de passe : le test ne prouverait rien.
    mauvais = create_async_engine(role_url.set(password=secrets.token_urlsafe(12)))
    try:
        async with mauvais.connect():
            enforce = False
    except Exception:
        enforce = True
    finally:
        await mauvais.dispose()
    if not enforce:
        pytest.skip("le serveur PostgreSQL n'exige pas de mot de passe pour le rôle (pg_hba en trust) ; "
                    "ajouter « host all xflows_pwd_test 127.0.0.1/32 scram-sha-256 » avant la règle trust")
    yield admin, role_url, secret
    async with admin.connect() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
    await admin.dispose()


async def _role(conn):
    return (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE})).scalar()


async def _scalaires(engine, sql):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalars().all()


async def test_le_role_exige_bien_un_mot_de_passe(protegee):
    """Garde du test : avec un mauvais mot de passe, la connexion du rôle est refusée (sinon le test ne prouverait rien)."""
    _, url, _ = protegee
    bon = create_async_engine(url)
    sans = create_async_engine(url.set(password=secrets.token_urlsafe(12)))
    try:
        async with bon.connect():                  # avec le mot de passe : accepté
            pass
        with pytest.raises(Exception):
            async with sans.connect():
                pass
    finally:
        await bon.dispose()
        await sans.dispose()


async def test_demarrage_reel_en_production_sur_base_protegee_puis_rejeu(protegee, monkeypatch):
    from xcore_doctype.platform.xflow.src.main import Plugin

    _, url, secret = protegee
    monkeypatch.setenv("MODE", "production")
    engine = create_async_engine(url)                # str(engine.url) rendrait « *** » : le correctif est nécessaire
    assert "***" in str(engine.url) and secret not in str(engine.url)
    try:
        await Plugin._initialize_tables(SimpleNamespace(), SimpleNamespace(engine=engine))
        assert await _scalaires(engine, f"SELECT count(*) FROM {VERSION_TABLE}") == [1]
        assert await _scalaires(engine, "SELECT to_regclass('xflow_webhook_secrets') IS NOT NULL") == [True]
        await Plugin._initialize_tables(SimpleNamespace(), SimpleNamespace(engine=engine))   # idempotent
        assert await _scalaires(engine, f"SELECT count(*) FROM {VERSION_TABLE}") == [1]
    finally:
        await engine.dispose()


async def test_mauvais_mot_de_passe_fait_echouer_le_demarrage_en_production_seulement(protegee, monkeypatch):
    from xcore_doctype.platform.xflow.src.main import Plugin

    _, url, _ = protegee
    faux = url.set(password=secrets.token_urlsafe(12))
    engine = create_async_engine(faux)
    try:
        monkeypatch.setenv("MODE", "production")
        with pytest.raises(RuntimeError, match="démarrage refusé en production"):
            await Plugin._initialize_tables(SimpleNamespace(), SimpleNamespace(engine=engine))
        monkeypatch.setenv("MODE", "test")
        await Plugin._initialize_tables(SimpleNamespace(), SimpleNamespace(engine=engine))   # poursuivi
    finally:
        await engine.dispose()
