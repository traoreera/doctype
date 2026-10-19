"""`extensions/xmailler/config.py` : repli sur l'environnement `XAUTH_SMTP_*` et mode simulé sans serveur."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("xmailler_config", ROOT / "extensions" / "xmailler" / "config.py")
config = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["xmailler_config"] = config  # un @dataclass se retrouve par son module
_spec.loader.exec_module(config)  # type: ignore[union-attr]

ENV = ("HOST", "PORT", "USER", "PASSWORD", "FROM", "FROM_NAME", "USE_TLS")


def clean(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(f"XAUTH_SMTP_{name}", raising=False)


def test_without_any_host_the_extension_is_not_configured_and_simulates(monkeypatch):
    clean(monkeypatch)
    cfg = config.EmailConfig.from_dict({})
    assert cfg.smtp_host == "" and cfg.configured is False and cfg.smtp_port == 587 and cfg.use_tls is True


def test_the_environment_fills_what_the_yaml_leaves_empty(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setenv("XAUTH_SMTP_HOST", "smtp.acme.test")
    monkeypatch.setenv("XAUTH_SMTP_PORT", "2525")
    monkeypatch.setenv("XAUTH_SMTP_PASSWORD", "s3cret")
    monkeypatch.setenv("XAUTH_SMTP_USE_TLS", "false")
    for yaml_value in ({}, {"smtp_host": ""}, {"smtp_host": "   "}, {"smtp_host": None}, {"smtp_host": "${XAUTH_SMTP_HOST}"}):
        cfg = config.EmailConfig.from_dict(yaml_value)
        assert (cfg.smtp_host, cfg.smtp_port, cfg.smtp_password, cfg.use_tls) == ("smtp.acme.test", 2525, "s3cret", False), yaml_value
        assert cfg.configured is True


def test_the_yaml_wins_over_the_environment(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setenv("XAUTH_SMTP_HOST", "smtp.env.test")
    cfg = config.EmailConfig.from_dict({"smtp_host": "smtp.yaml.test", "smtp_port": 465})
    assert cfg.smtp_host == "smtp.yaml.test" and cfg.smtp_port == 465
