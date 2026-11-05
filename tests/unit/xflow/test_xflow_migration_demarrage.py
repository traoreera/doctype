"""Démarrage de xflows : le runner Alembic reçoit l'URL complète (mot de passe compris) et un échec de
migration fait échouer le démarrage en production, comme dans auth. Sans base PostgreSQL."""
from __future__ import annotations

import secrets
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from xcore_doctype.platform.xflow.src import main as xflows_main
from xcore_doctype.platform.xflow.src.main import Plugin


class _Runner:
    urls: list[str] = []
    echec = True

    def __init__(self, db_url, migrations_dir=None, **kw):
        self.urls.append(db_url)

    async def init(self, *a, **kw):
        return None

    async def upgrade(self, *a, **kw):
        if self.echec:
            raise RuntimeError("alembic en panne : détail précis")


class _Journal:
    def __init__(self):
        self.vus = []

    def __getattr__(self, nom):
        niveau = "ERROR" if nom in {"exception", "error"} else nom.upper()
        return lambda msg, *a, **kw: self.vus.append((niveau, msg % a if a else msg, kw))


@pytest.fixture
def journal(monkeypatch):
    j = _Journal()
    monkeypatch.setattr(xflows_main, "logger", j)
    return j.vus


@pytest.fixture(autouse=True)
def runner(monkeypatch):
    _Runner.urls = []
    _Runner.echec = True
    monkeypatch.setattr("xcore.services.database.migrations.MigrationRunner", _Runner)
    return _Runner


def _db(secret=None):
    url = make_url("postgresql+asyncpg://erp@127.0.0.1:1/erp_test")
    if secret:
        url = url.set(password=secret)
    return SimpleNamespace(engine=SimpleNamespace(url=url))


async def _demarrer(db):
    await Plugin._initialize_tables(SimpleNamespace(), db)


async def test_le_runner_recoit_le_mot_de_passe_et_pas_la_version_masquee(monkeypatch, journal):
    """str(url) rend « *** » : l'upgrade échouait alors en silence sur toute base protégée par mot de passe."""
    secret = secrets.token_urlsafe(12)
    monkeypatch.setenv("MODE", "test")
    await _demarrer(_db(secret))
    assert make_url(_Runner.urls[0]).password == secret
    assert "***" not in _Runner.urls[0]


@pytest.mark.parametrize("mode", ["production", None])
async def test_en_production_l_echec_de_migration_fait_echouer_le_demarrage(monkeypatch, journal, mode):
    if mode is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", mode)
    with pytest.raises(RuntimeError, match="démarrage refusé en production.*détail précis") as info:
        await _demarrer(_db())
    assert isinstance(info.value.__cause__, RuntimeError)
    erreur = next(e for e in journal if e[0] == "ERROR")
    assert "détail précis" in erreur[1]


@pytest.mark.parametrize("mode", ["test", "dev"])
async def test_hors_production_l_echec_est_journalise_et_le_demarrage_continue(monkeypatch, journal, mode):
    monkeypatch.setenv("MODE", mode)
    await _demarrer(_db())
    assert any(n == "ERROR" and "détail précis" in m for n, m, _ in journal)
    assert any(n == "WARNING" and "poursuivi" in m for n, m, _ in journal)


async def test_une_migration_reussie_n_arrete_rien_en_production(monkeypatch, journal):
    _Runner.echec = False
    monkeypatch.setenv("MODE", "production")
    await _demarrer(_db())
    assert not [e for e in journal if e[0] in ("ERROR", "WARNING")]
