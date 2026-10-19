"""Collaboration avec xauth comme annuaire : les personnes à assigner sont les membres de l'organisation, désignées par un identifiant illisible
(UUID) et affichées par leur e-mail. Un plugin `auth` de substitution expose la même action que xauth (`xauth.users.list.tenant`)."""

from __future__ import annotations

import yaml

from tests.integration.conftest import boot_host
from tests.integration.test_collaboration_api import MAIN, ORDER, READER, WRITER, order

MEMBERS = {
    "acme": [{"id": "11111111-aaaa", "email": "ada@acme.test"}, {"id": "22222222-bbbb", "email": "Rita@acme.test"}, {"id": "33333333-cccc", "email": "carl@acme.test"}],
    "globex": [{"id": "99999999-zzzz", "email": "gus@globex.test"}],
}
ADA, RITA, CARL, GUS = "11111111-aaaa", "22222222-bbbb", "33333333-cccc", "99999999-zzzz"


def plugins():
    return {"shop": (MAIN, {"doctypes/order.yaml": yaml.safe_dump(ORDER)})}


async def test_people_to_assign_are_the_members_of_the_organisation_shown_by_email(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada = h.token(sub=ADA, permissions=WRITER)
        body = (await h.http("GET", "/people/Order", token=ada)).json()
        assert body["directory"] is True
        assert [(p["id"], p["email"]) for p in body["items"]] == [(ADA, "ada@acme.test"), (CARL, "carl@acme.test"), (RITA, "Rita@acme.test")]
        assert [p["id"] for p in (await h.http("GET", "/people/Order?q=RIT", token=ada)).json()["items"]] == [RITA]  # par e-mail, sans casse
        # Un autre tenant ne voit que ses membres.
        gus = h.token(sub=GUS, tenant="globex", permissions=WRITER)
        assert [p["email"] for p in (await h.http("GET", "/people/Order", token=gus, tenant="globex")).json()["items"]] == ["gus@globex.test"]


async def test_one_assigns_a_member_by_id_or_by_email_and_nobody_else(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada, rita = h.token(sub=ADA, permissions=WRITER), h.token(sub=RITA, permissions=READER)
        name = await order(h, ada)
        by_id = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": RITA})
        assert by_id.status_code == 201 and by_id.json()["assignee"] == RITA
        by_mail = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "CARL@acme.test"})
        assert by_mail.status_code == 201 and by_mail.json()["assignee"] == CARL  # un e-mail est converti en identifiant : c'est lui qu'on stocke
        for who in ("quelquun@ailleurs.test", GUS, "n'importe quoi"):  # GUS est membre de globex, pas d'acme
            r = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": who})
            assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_user", who
        # « Mes tâches » est celles de MON identifiant.
        mine = (await h.http("GET", "/my-assignments", token=rita)).json()["items"]
        assert [i["assignee"] for i in mine] == [RITA]
        # Un doublon par e-mail est le même doublon.
        dup = await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita@ACME.test"})
        assert dup.status_code == 409


async def test_the_directory_is_not_probed_for_someone_who_may_not_assign(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        ada, rita = h.token(sub=ADA, permissions=WRITER), h.token(sub=RITA, permissions=READER)
        name = await order(h, ada)
        # Un lecteur reçoit 403 quelle que soit la personne demandée : il ne peut pas deviner qui est membre.
        for who in (CARL, "inconnu"):
            assert (await h.http("POST", f"/assignments/Order/{name}", token=rita, json={"assignee": who})).status_code == 403
        assert (await h.http("GET", "/people/Order", token=rita)).status_code == 403


async def test_identifiers_are_shown_by_email_but_only_for_members_of_the_callers_organisation(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        rita = h.token(sub=RITA, permissions=READER)
        body = (await h.http("GET", "/users", token=rita, params=[("ids", ADA), ("ids", GUS), ("ids", "fantôme")])).json()
        assert body["directory"] is True
        assert body["items"] == {ADA: {"id": ADA, "email": "ada@acme.test", "name": "ada@acme.test"}}  # GUS (autre tenant) et le fantôme sont absents
        assert (await h.http("GET", "/users", token=rita)).json()["items"] == {}
        too_many = await h.http("GET", "/users", token=rita, params=[("ids", f"u{i}") for i in range(101)])
        assert too_many.status_code == 422
        assert (await h.http("GET", "/users", token=None)).status_code in (401, 403, 422)


async def test_xauth_that_does_not_name_us_in_allowed_callers_is_refused_and_we_fall_back(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS, auth_callers=["quelquun-d-autre"]) as h:
        ada = h.token(sub="ada", permissions=WRITER)
        assert (await h.http("GET", "/people/Order", token=ada)).json()["directory"] is False  # refusé par xauth (`ipc_denied`) : on retombe, rien ne casse
        name = await order(h, ada)
        assert (await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "rita"})).status_code == 201


async def test_without_a_directory_everything_still_works_on_observed_identities(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins()) as h:  # le plugin de test n'imite pas l'annuaire : l'action est inconnue
        ada = h.token(sub="ada", permissions=WRITER)
        name = await order(h, ada)
        assert (await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "n'importe qui"})).status_code == 201
        body = (await h.http("GET", "/users", token=ada, params=[("ids", "ada")])).json()
        assert body == {"items": {"ada": {"id": "ada", "email": None, "name": "ada"}}, "directory": False}


async def test_the_directory_can_be_switched_off_by_configuration(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), doctype_config={"directory": {"provider": "none"}}) as h:
        ada = h.token(sub=ADA, permissions=WRITER)
        name = await order(h, ada)
        assert (await h.http("GET", "/people/Order", token=ada)).json()["directory"] is False
        assert (await h.http("POST", f"/assignments/Order/{name}", token=ada, json={"assignee": "libre"})).status_code == 201


async def test_the_ipc_action_resolves_too(tmp_path):
    async with boot_host(tmp_path, inline_plugins=plugins(), auth_members=MEMBERS) as h:
        out = await h.action("doc.users.resolve", {"ids": [ADA]}, token=h.token(sub=RITA, permissions=READER))
        assert out["items"][ADA]["email"] == "ada@acme.test"
