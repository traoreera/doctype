"""L'annuaire des utilisateurs : ce que xauth répond, mis en cache, et son absence."""

from __future__ import annotations

import pytest

from xcore_doctype.directory import NoDirectory, Person, XauthDirectory


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Log:
    def __init__(self):
        self.lines = []

    def warning(self, message, **fields):
        self.lines.append((message, fields))


def directory(answers, clock=None, **kw):
    calls = []
    queue = list(answers)

    async def call(plugin, action, payload, tenant):
        calls.append((plugin, action, payload, tenant))
        a = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(a, Exception):
            raise a
        return a

    return XauthDirectory(call, clock=clock or Clock(), **kw), calls


USERS = {"status": "ok", "users": [{"id": "u2", "email": "Rita@Acme.test"}, {"id": "u1", "email": "ada@acme.test"}, {"id": "u1", "email": "dup@acme.test"}, {"id": "", "email": "x"}, "junk", {"email": "no-id"}]}


async def test_members_are_asked_of_xauth_for_the_tenant_cleaned_deduped_and_sorted():
    d, calls = directory([USERS])
    people = await d.members("acme")
    assert calls == [("auth", "xauth.users.list.tenant", {"tenant_id": "acme"}, "acme")]
    assert [(p.id, p.email) for p in people] == [("u1", "ada@acme.test"), ("u2", "Rita@Acme.test")]  # trié par libellé, sans doublon ni ligne illisible


async def test_the_answer_is_cached_per_tenant_for_a_while():
    clock = Clock()
    d, calls = directory([USERS], clock, ttl=60)
    await d.members("acme")
    await d.members("acme")
    assert len(calls) == 1  # une frappe dans un champ de recherche ne fait pas un appel de plugin
    await d.members("globex")
    assert len(calls) == 2  # un autre tenant a son propre annuaire
    clock.now += 61
    await d.members("acme")
    assert len(calls) == 3


async def test_a_missing_xauth_is_no_directory_not_an_error_and_is_retried_soon():
    clock = Clock()
    log = Log()
    d, calls = directory([RuntimeError("plugin introuvable"), USERS], clock, ttl=60, logger=log)
    assert await d.members("acme") is None
    assert await d.members("acme") is None and len(calls) == 1  # l'absence aussi est mise en cache : un xauth arrêté ne ralentit pas chaque requête
    clock.now += 16  # …mais pas aussi longtemps qu'un annuaire qu'on a : xauth peut démarrer après nous
    assert [p.id for p in await d.members("acme")] == ["u1", "u2"]
    assert log.lines and log.lines[0][0] == "user directory unavailable"
    assert "plugin" not in log.lines[0][1] and "msg" not in log.lines[0][1]  # champs que le logger d'xcore réserve


@pytest.mark.parametrize("answer", [None, "oops", {"status": "error", "msg": "x"}, {"status": "ok"}, {"status": "ok", "users": "no"}, {"users": {"a": 1}}])
async def test_an_unexpected_answer_is_no_directory(answer):
    d, _ = directory([answer])
    assert await d.members("acme") is None


async def test_a_failing_logger_never_breaks_a_request():
    class Broken:
        def warning(self, *a, **k):
            raise RuntimeError("log down")

    d, _ = directory([RuntimeError("x")], logger=Broken())
    assert await d.members("acme") is None


async def test_no_directory_by_configuration():
    assert await NoDirectory().members("acme") is None


def test_a_person_is_shown_by_email_else_by_id_and_found_by_either():
    p, q = Person("u1", "Ada@acme.test"), Person("u2")
    assert (p.label, q.label) == ("Ada@acme.test", "u2")
    assert p.as_dict() == {"id": "u1", "email": "Ada@acme.test", "name": "Ada@acme.test"}
    assert q.as_dict() == {"id": "u2", "email": None, "name": "u2"}
    assert p.matches("ada") and p.matches("U1") and p.matches("") and not p.matches("zed")
    assert q.matches("u2") and not q.matches("ada")
