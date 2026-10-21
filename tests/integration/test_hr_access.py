"""Le lien RH -> accès : `Employee.user` pointe vers un compte xauth (`Member`), et un employé qui PART désactive ce compte (hook `hr.employee_access`).

Contre le VRAI xauth : le lien est vérifié chez xauth, la désactivation aussi (xauth juge l'acteur : seul le propriétaire de l'organisation ferme un compte).
"""

from __future__ import annotations

from tests.integration.conftest import boot_host
from tests.integration.test_iam import api
from tests.integration.test_xauth_real import RITA, add_member, admin, login
from tests.support import FIXTURES

PLUGINS = {"iam": FIXTURES / "iam", "hr": FIXTURES / "hr"}
HR_MANAGER = ("hrm@example.com", "Hr-Manager-Passw0rd!9")


async def can_login(h, creds, tenant) -> bool:
    r = await h.client.post("/app/auth/login", json={"email": creds[0], "password": creds[1], "tenant_id": tenant})
    return r.status_code == 200


async def test_the_employee_card_links_an_account_and_leaving_closes_it(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        rita = await add_member(h, adm, *RITA)

        # Le lien est vérifié chez xauth : un compte inconnu est refusé, un compte membre est accepté.
        unknown = await api(h, "POST", "/doc/Employee", adm, json={"data": {"full_name": "Fantôme", "user": "00000000-0000-0000-0000-000000000000"}})
        assert unknown.status_code == 422 and unknown.json()["detail"]["details"][0]["type"] == "link_not_found", unknown.text
        made = await api(h, "POST", "/doc/Employee", adm, json={"data": {"full_name": "Rita Rey", "status": "Actif", "user": rita["user_id"]}})
        assert made.status_code == 201, made.text
        assert made.json()["data"]["user"] == rita["user_id"]
        assert await can_login(h, RITA, tenant)

        # Le compte lié ne se supprime pas tant qu'une fiche le cite (l'intégrité vaut aussi pour un DocType virtuel).
        assert (await api(h, "DELETE", f"/doc/Member/{rita['user_id']}", adm)).status_code == 409

        # Passer le statut à « Parti » désactive le compte DANS cette organisation.
        left = await api(h, "PUT", f"/doc/Employee/{made.json()['name']}", adm, json={"version": made.json()["version"], "data": {**made.json()["data"], "status": "Parti"}})
        assert left.status_code == 200, left.text
        assert not await can_login(h, RITA, tenant)
        member = (await api(h, "GET", f"/doc/Member/{rita['user_id']}", adm)).json()
        assert member["data"]["is_active"] is False

        # Revenir à « Actif » ne rouvre PAS l'accès tout seul : c'est une décision, qui se prend sur la fiche du compte.
        back = await api(h, "PUT", f"/doc/Employee/{made.json()['name']}", adm, json={"version": left.json()["version"], "data": {**left.json()["data"], "status": "Actif"}})
        assert back.status_code == 200
        assert not await can_login(h, RITA, tenant)


async def test_leaving_is_refused_when_the_actor_cannot_close_the_account(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        rita = await add_member(h, adm, *RITA)
        await add_member(h, adm, *HR_MANAGER, "hr:employee:read", "hr:employee:write", "iam:member:read")
        hrm = (await login(h, *HR_MANAGER, tenant))["access_token"]

        # Une responsable RH (ni propriétaire, ni administrateur) lit l'annuaire des comptes : elle PEUT lier la fiche…
        made = await api(h, "POST", "/doc/Employee", hrm, tenant=tenant, json={"data": {"full_name": "Rita Rey", "user": rita["user_id"]}})
        assert made.status_code == 201, made.text
        # …mais pas fermer le compte : l'enregistrement est refusé, clairement, et la fiche reste « Actif ».
        refused = await api(h, "PUT", f"/doc/Employee/{made.json()['name']}", hrm, tenant=tenant, json={"version": made.json()["version"], "data": {**made.json()["data"], "status": "Parti"}})
        assert refused.status_code == 422 and "Seul le propriétaire" in refused.text, refused.text
        assert (await api(h, "GET", f"/doc/Employee/{made.json()['name']}", adm)).json()["data"].get("status", "Actif") == "Actif"
        assert await can_login(h, RITA, tenant)


async def test_the_owner_cannot_close_their_own_account_by_leaving(tmp_path):
    async with boot_host(tmp_path, xauth=True, link_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        mine = await api(h, "POST", "/doc/Employee", adm, json={"data": {"full_name": "Moi", "user": adm["user_id"], "status": "Actif"}})
        assert mine.status_code == 201, mine.text
        refused = await api(h, "PUT", f"/doc/Employee/{mine.json()['name']}", adm, json={"version": mine.json()["version"], "data": {**mine.json()["data"], "status": "Parti"}})
        assert refused.status_code == 422 and "désactivés ici" in refused.text, refused.text
