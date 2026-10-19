"""Le plugin `iam` : les permissions, rôles et membres d'xauth comme DocTypes VIRTUELS, contre le VRAI xauth.

Rien n'est copié dans la table des documents : chaque lecture et chaque écriture passe par une action `xauth.iam.*` qu'xauth juge (propriétaire de l'organisation ou
administrateur de la plateforme). Le moteur applique ses droits (`iam:…`), son schéma strict, ses liens, ses filtres ; xauth reste l'autorité.
"""

from __future__ import annotations

from tests.integration.conftest import ROOT, XAUTH_ADMIN, boot_host
from tests.integration.test_xauth_real import RITA, add_member, admin, login

IAM = {"iam": ROOT / "app" / "iam"}


async def api(h, method, path, adm_or_token, *, tenant=None, **kw):
    token = adm_or_token["access_token"] if isinstance(adm_or_token, dict) else adm_or_token
    tenant = tenant or (adm_or_token["tenant_id"] if isinstance(adm_or_token, dict) else None)
    return await h.http(method, path, token=token, tenant=tenant, **kw)


def rows(data, field):
    return [r for r in data.get("data", data).get(field, [])]


async def test_the_permission_catalog_is_a_real_list_with_filters_sorting_and_aggregates(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, operator_doctypes=False) as h:
        adm = await admin(h)
        listed = await api(h, "GET", "/list/Permission", adm, params={"limit": 200, "order_by": "name", "order": "asc"})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        names = [i["name"] for i in body["items"]]
        assert "iam:member:read" in names and "admin:*" in names and body["total"] == len(names) >= 20 and names == sorted(names)
        iam = (await api(h, "GET", "/list/Permission", adm, params={"filters": '{"plugin": "iam"}', "limit": 50})).json()
        assert {i["name"] for i in iam["items"]} == {"iam:member:read", "iam:member:write", "iam:member:delete", "iam:permission:read", "iam:role:read", "iam:role:write", "iam:role:delete", "iam:profile:write", "iam:profile:delete"}
        one = (await api(h, "GET", "/doc/Permission/iam:role:write", adm)).json()
        assert one["data"]["plugin"] == "iam" and one["data"]["tenant_grantable"] is False and one["name"] == "iam:role:write"
        found = (await api(h, "GET", "/search/Permission", adm, params={"q": "iam:role"})).json()
        assert "iam:role:read" in [i["name"] for i in found["items"]]  # la recherche (autocomplétion d'un Link) cherche dans le nom ET le titre
        agg = (await api(h, "GET", "/aggregate/Permission", adm, params={"group_by": "tenant_grantable"})).json()
        assert sum(r["values"][0] for r in agg["rows"]) == body["total"]
        meta = (await api(h, "GET", "/meta/Permission", adm)).json()
        assert meta["virtual"] is True and meta["source"] == "xauth" and meta["rights"]["write"] == "none"


async def test_the_catalog_is_read_only_whatever_the_caller_holds(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, operator_doctypes=False) as h:
        adm = await admin(h)
        for method, path, body in (("POST", "/doc/Permission", {"data": {"description": "x"}}), ("PUT", "/doc/Permission/iam:role:read", {"version": 1, "data": {}}), ("DELETE", "/doc/Permission/iam:role:read", None)):
            r = await api(h, method, path, adm, **({"json": body} if body else {}))
            assert r.status_code == 403, (method, r.text)  # aucun grant n'ouvre l'écriture : le YAML la ferme (`write: []`)


async def test_an_organisation_owner_manages_roles_and_the_platform_roles_are_read_only(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, operator_doctypes=False) as h:
        adm = await admin(h)
        roles = (await api(h, "GET", "/list/Role", adm, params={"limit": 50})).json()["items"]
        by_name = {r["data"]["role_name"]: r for r in roles}
        assert {"admin", "user", "tenant_admin"} <= set(by_name) and all(r["data"]["scope"] == "Plateforme" for r in by_name.values())
        # `iam:*` va au rôle du propriétaire d'une organisation : il ouvre ces écrans sans qu'on les lui délègue.
        assert {"iam:member:read", "iam:role:write"} <= {r["permission"] for r in by_name["tenant_admin"]["data"]["permissions"]}

        made = await api(h, "POST", "/doc/Role", adm, json={"data": {"role_name": "Comptable", "description": "Factures", "permissions": [{"permission": "iam:role:write"}]}})
        # `iam:role:write` n'est PAS délégable : xauth refuse (un rôle d'organisation ne reçoit que des permissions que le propriétaire peut déléguer).
        assert made.status_code == 422 and "non délégable" in made.text, made.text
        grantable = (await api(h, "GET", "/list/Permission", adm, params={"filters": '{"tenant_grantable": true}', "limit": 50})).json()["items"]
        assert grantable == [] or all(i["data"]["tenant_grantable"] for i in grantable)  # le catalogue est lisible, filtré comme une liste

        platform = by_name["user"]
        # L'administrateur de la PLATEFORME règle un rôle de la plateforme (description, permissions) mais ne le renomme ni ne le supprime.
        body = lambda **over: {"version": platform["version"], "data": {"role_name": "user", "description": "Accès standard", "scope": "Plateforme", **over}}  # noqa: E731
        edited = await api(h, "PUT", f"/doc/Role/{platform['name']}", adm, json=body())
        assert edited.status_code == 200, edited.text
        assert edited.json()["data"]["description"] == "Accès standard"
        renamed = await api(h, "PUT", f"/doc/Role/{platform['name']}", adm, json={**body(role_name="visiteur"), "version": edited.json()["version"]})
        assert renamed.status_code == 422 and "ne change pas" in renamed.text
        assert (await api(h, "DELETE", f"/doc/Role/{platform['name']}", adm)).status_code == 422
        granted = await api(h, "PUT", f"/doc/Role/{platform['name']}", adm, json={**body(permissions=[{"permission": "iam:member:read"}]), "version": edited.json()["version"]})
        assert granted.status_code == 200 and granted.json()["data"]["permissions"] == [{"permission": "iam:member:read"}]  # même une permission NON délégable : c'est la plateforme

        # Un propriétaire qui n'est PAS administrateur de la plateforme (il crée sa propre organisation) gère ses rôles, pas ceux de la plateforme.
        rita = await add_member(h, adm, *RITA)
        own = await h.client.post("/app/auth/tenants/", json={"name": "Chez Rita", "slug": "chez-rita"}, headers={"Authorization": f"Bearer {rita['access_token']}"})
        assert own.status_code == 201, own.text
        hers = await login(h, *RITA, own.json()["id"])
        listed = await api(h, "GET", "/list/Role", hers, params={"limit": 50})
        assert listed.status_code == 200, listed.text  # `iam:*` est dans le rôle `tenant_admin` : le propriétaire ouvre ses écrans sans délégation
        theirs = {r["data"]["role_name"]: r for r in listed.json()["items"]}
        refused = await api(h, "PUT", f"/doc/Role/{theirs['user']['name']}", hers, json={"version": theirs["user"]["version"], "data": {"role_name": "user", "description": "piraté", "scope": "Plateforme"}})
        assert refused.status_code == 422 and "administrateur de la plateforme" in refused.text
        seen = {i["name"] for i in (await api(h, "GET", "/list/Permission", hers, params={"filters": '{"plugin": "iam"}'})).json()["items"]}
        assert seen == {"iam:member:read", "iam:profile:write", "iam:profile:delete"}  # elle ne voit que ce qu'elle peut déléguer : gérer les accès ne l'est pas


async def test_roles_are_created_edited_and_deleted_through_the_desk_api_and_members_get_them(tmp_path):
    from tests.integration.test_iam_support import GRANTS_PLUGIN  # noqa: PLC0415 - un plugin de test qui déclare des grants délégables

    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, inline_plugins={"shop": GRANTS_PLUGIN}, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        made = await api(h, "POST", "/doc/Role", adm, json={"data": {"role_name": "Vendeur", "description": "Commandes", "permissions": [{"permission": "shop:order:read"}]}})
        assert made.status_code == 201, made.text
        role = made.json()
        assert role["data"]["scope"] == "Organisation" and role["data"]["permissions"] == [{"permission": "shop:order:read"}]
        dup = await api(h, "POST", "/doc/Role", adm, json={"data": {"role_name": "vendeur"}})
        assert dup.status_code == 422 and "porte déjà ce nom" in dup.text

        edited = await api(h, "PUT", f"/doc/Role/{role['name']}", adm, json={"version": role["version"], "data": {"role_name": "Vendeur", "description": "Commandes", "scope": "Organisation", "permissions": [{"permission": "shop:order:read"}, {"permission": "shop:order:write"}]}})
        assert edited.status_code == 200, edited.text
        assert {r["permission"] for r in edited.json()["data"]["permissions"]} == {"shop:order:read", "shop:order:write"}
        stale = await api(h, "PUT", f"/doc/Role/{role['name']}", adm, json={"version": role["version"], "data": {"role_name": "Vendeur"}})
        assert stale.status_code == 409  # la version lue n'est plus la bonne

        # Un membre reçoit le rôle PAR SA FICHE ; ses droits suivent à la prochaine connexion.
        rita = await add_member(h, adm, *RITA)
        fiche = (await api(h, "GET", f"/doc/Member/{rita['user_id']}", adm)).json()
        assert fiche["data"]["email"] == RITA[0] and "primary_role" not in fiche["data"] and fiche["owner"] == rita["user_id"]
        given = await api(h, "PUT", f"/doc/Member/{rita['user_id']}", adm, json={"version": fiche["version"], "data": {**fiche["data"], "primary_role": role["name"]}})
        assert given.status_code == 200, given.text
        assert given.json()["data"]["primary_role"] == role["name"]
        fresh = await login(h, *RITA, tenant)
        assert {d["name"] for d in (await h.http("GET", "/meta", token=fresh["access_token"], tenant=tenant)).json()} >= {"Order"}  # ses droits suivent le rôle

        # Un rôle attribué ne se supprime pas ; retiré de la fiche, il part.
        assert (await api(h, "DELETE", f"/doc/Role/{role['name']}", adm)).status_code == 422
        again = (await api(h, "GET", f"/doc/Member/{rita['user_id']}", adm)).json()
        data = {k: v for k, v in again["data"].items() if k != "primary_role"}
        assert (await api(h, "PUT", f"/doc/Member/{rita['user_id']}", adm, json={"version": again["version"], "data": data})).status_code == 200
        assert (await api(h, "DELETE", f"/doc/Role/{role['name']}", adm)).status_code == 204
        assert (await api(h, "GET", f"/doc/Role/{role['name']}", adm)).status_code == 404


async def test_a_member_card_edits_extra_roles_direct_permissions_and_activation_but_not_the_account(tmp_path):
    from tests.integration.test_iam_support import GRANTS_PLUGIN  # noqa: PLC0415

    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, inline_plugins={"shop": GRANTS_PLUGIN}, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        rita = await add_member(h, adm, *RITA)
        fiche = (await api(h, "GET", f"/doc/Member/{rita['user_id']}", adm)).json()
        data = {**fiche["data"], "permissions": [{"permission": "shop:order:read"}]}
        done = await api(h, "PUT", f"/doc/Member/{rita['user_id']}", adm, json={"version": fiche["version"], "data": data})
        assert done.status_code == 200, done.text
        assert done.json()["data"]["permissions"] == [{"permission": "shop:order:read"}]
        visible = {d["name"] for d in (await h.http("GET", "/meta", token=(await login(h, *RITA, tenant))["access_token"], tenant=tenant)).json()}
        assert visible == {"Order", "User Profile"}  # sa permission directe ouvre Order ; le profil s'ouvre à tout utilisateur identifié

        # Le compte (e-mail, nom d'utilisateur, propriétaire) est en LECTURE SEULE : un client ne le change pas.
        tamper = await api(h, "PUT", f"/doc/Member/{rita['user_id']}", adm, json={"version": done.json()["version"], "data": {**done.json()["data"], "email": "pirate@example.com"}})
        assert tamper.status_code == 422 and "read_only" in tamper.text

        # Désactivé : il ne se connecte plus à CETTE organisation ; réactivé, il revient.
        cur = (await api(h, "GET", f"/doc/Member/{rita['user_id']}", adm)).json()
        off = await api(h, "PUT", f"/doc/Member/{rita['user_id']}", adm, json={"version": cur["version"], "data": {**cur["data"], "is_active": False}})
        assert off.status_code == 200 and off.json()["data"]["is_active"] is False
        assert (await h.client.post("/app/auth/login", json={"email": RITA[0], "password": RITA[1], "tenant_id": tenant})).status_code in (401, 403)
        back = await api(h, "PUT", f"/doc/Member/{rita['user_id']}", adm, json={"version": off.json()["version"], "data": {**off.json()["data"], "is_active": True}})
        assert back.status_code == 200
        assert (await h.client.post("/app/auth/login", json={"email": RITA[0], "password": RITA[1], "tenant_id": tenant})).status_code == 200

        # On ne crée pas un membre ici (il rejoint par invitation) ; le retirer est une suppression.
        assert (await api(h, "POST", "/doc/Member", adm, json={"data": {"email": "x@example.com"}})).status_code == 403
        assert (await api(h, "DELETE", f"/doc/Member/{rita['user_id']}", adm)).status_code == 204
        assert (await api(h, "GET", f"/doc/Member/{rita['user_id']}", adm)).status_code == 404


async def test_nobody_removes_their_own_access_or_the_owner(tmp_path):
    from tests.integration.test_iam_support import GRANTS_PLUGIN  # noqa: PLC0415

    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, inline_plugins={"shop": GRANTS_PLUGIN}, operator_doctypes=False) as h:
        adm = await admin(h)
        me = (await api(h, "GET", f"/doc/Member/{adm['user_id']}", adm)).json()
        assert me["data"]["is_owner"] is True and me["data"]["email"]
        # L'administrateur de la plateforme peut s'accorder (le propriétaire aussi) ; mais se désactiver ou retirer le propriétaire est refusé.
        off = await api(h, "PUT", f"/doc/Member/{adm['user_id']}", adm, json={"version": me["version"], "data": {**me["data"], "is_active": False}})
        assert off.status_code == 422 and "désactivés ici" in off.text
        assert (await api(h, "DELETE", f"/doc/Member/{adm['user_id']}", adm)).status_code == 422


async def test_without_the_iam_grants_the_screens_stay_closed_and_a_delegated_read_only_shows_the_directory(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        rita = await add_member(h, adm, *RITA)
        assert (await api(h, "GET", "/list/Member", rita["access_token"], tenant=tenant)).status_code == 403  # aucun grant `iam:…` : le moteur refuse
        auth = {"Authorization": f"Bearer {adm['access_token']}"}

        def grant(permission):
            return h.client.post(f"/app/auth/rbac/tenants/{tenant}/members/{rita['user_id']}/permissions", json={"permission_name": permission}, headers=auth)

        # Écrire les accès n'est pas délégable (le propriétaire seul) ; lire l'ANNUAIRE l'est.
        assert (await grant("iam:member:write")).status_code == 400
        assert (await grant("iam:member:read")).status_code == 200
        hers = (await login(h, *RITA, tenant))["access_token"]
        directory = await api(h, "GET", "/list/Member", hers, tenant=tenant)
        assert directory.status_code == 200, directory.text
        emails = {i["data"]["email"] for i in directory.json()["items"]}
        assert emails == {XAUTH_ADMIN[0], RITA[0]}
        for item in directory.json()["items"]:  # rôles et permissions de qui que ce soit : réservés au propriétaire (xauth les omet)
            assert not {"primary_role", "roles", "permissions"} & set(item["data"])
        assert (await api(h, "GET", "/list/Role", hers, tenant=tenant)).status_code == 403
        assert (await api(h, "GET", "/list/Permission", hers, tenant=tenant)).status_code == 403
        # Elle lit l'annuaire, elle ne gère rien : l'écriture est fermée par le moteur ET par xauth.
        mine = (await api(h, "GET", f"/doc/Member/{rita['user_id']}", hers, tenant=tenant)).json()
        assert (await api(h, "PUT", f"/doc/Member/{rita['user_id']}", hers, tenant=tenant, json={"version": mine["version"], "data": mine["data"]})).status_code == 403


async def test_comments_work_on_a_virtual_document_and_it_shows_in_the_workspace(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, operator_doctypes=False) as h:
        adm = await admin(h)
        comment = await api(h, "POST", f"/comments/Member/{adm['user_id']}", adm, json={"content": "À revoir avec la RH"})
        assert comment.status_code == 201, comment.text
        thread = (await api(h, "GET", f"/collab/Member/{adm['user_id']}", adm)).json()
        assert [c["content"] for c in thread["comments"]] == ["À revoir avec la RH"]
        spaces = (await api(h, "GET", "/workspaces", adm)).json()
        assert "iam" in [w["name"] for w in spaces]


async def test_every_member_keeps_their_own_profile_without_any_grant(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=IAM, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        rita = await add_member(h, adm, *RITA)  # membre sans le moindre grant : ni rôle, ni permission directe
        types = {d["name"] for d in (await h.http("GET", "/meta", token=rita["access_token"], tenant=tenant)).json()}
        assert types == {"User Profile"}  # son profil, et rien d'autre

        mine = await api(h, "POST", "/doc/User Profile", rita, tenant=tenant, json={"data": {"full_name": "Rita R.", "language": "Français"}})
        assert mine.status_code == 201, mine.text
        assert mine.json()["name"] == rita["user_id"]  # nommé comme son compte xauth
        assert (await api(h, "POST", "/doc/User Profile", rita, tenant=tenant, json={"data": {"full_name": "Encore"}})).status_code == 409  # un seul par utilisateur

        theirs = await api(h, "POST", "/doc/User Profile", adm, tenant=tenant, json={"data": {"full_name": "Chef de projet"}})
        assert theirs.status_code == 201 and theirs.json()["name"] == adm["user_id"]
        directory = (await api(h, "GET", "/list/User Profile", rita, tenant=tenant)).json()
        assert {i["data"]["full_name"] for i in directory["items"]} == {"Rita R.", "Chef de projet"}  # elle lit l'annuaire des collègues
        edit = await api(h, "PUT", f"/doc/User Profile/{adm['user_id']}", rita, tenant=tenant, json={"version": theirs.json()["version"], "data": {"full_name": "Piraté"}})
        assert edit.status_code == 403  # mais n'écrit que le sien
        done = await api(h, "PUT", f"/doc/User Profile/{rita['user_id']}", rita, tenant=tenant, json={"version": mine.json()["version"], "data": {"full_name": "Rita Rey", "phone": "0102030405"}})
        assert done.status_code == 200 and done.json()["data"]["full_name"] == "Rita Rey"
        assert (await api(h, "DELETE", f"/doc/User Profile/{rita['user_id']}", rita, tenant=tenant)).status_code == 403  # supprimer exige un grant
        # Le propriétaire de l'organisation (rôle `tenant_admin`, qui porte `iam:profile:*`) corrige celui d'un autre.
        fixed = await api(h, "PUT", f"/doc/User Profile/{rita['user_id']}", adm, tenant=tenant, json={"version": done.json()["version"], "data": {"full_name": "Rita Rey", "job_title": "Comptable"}})
        assert fixed.status_code == 200 and fixed.json()["data"]["job_title"] == "Comptable"
