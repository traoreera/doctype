"""Le VRAI xauth (`app/auth`) comme système d'authentification : connexion, jetons, grants déclarés par les plugins, membres, annuaire.

Aucun jeton fabriqué à la main : tout vient de `POST /app/auth/login`. C'est ce que fait le Desk, et la seule authentification du produit.
"""

from __future__ import annotations

import base64
import json

import yaml

from tests.integration.conftest import XAUTH_ADMIN, boot_host

MAIN = '''
from pathlib import Path
from xcore.sdk import TrustedBase, error

BASE = Path(__file__).resolve().parent.parent


class Plugin(TrustedBase):
    async def on_load(self):
        self.get_service("doctype_registry").load_dir(self.ctx.name, BASE / "doctypes")
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self):
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    async def handle(self, action, payload):
        return error("no actions", "unknown_action")
'''
ORDER = {"schema_version": 1, "name": "Order", "label": "Commande", "module": "shop", "fields": [{"fieldname": "title", "fieldtype": "Text"}]}
MANIFEST = yaml.safe_dump({
    "name": "shop", "version": "0.1.0", "framework_version": ">=2.9", "execution_mode": "trusted", "entry_point": "src/main.py",
    "requires": ["xcore-doctype", "auth"],  # xauth écoute `rbac.declare` : il doit être chargé AVANT, sinon les grants se perdent
})
PLUGINS = {"shop": (MAIN, {"doctypes/order.yaml": yaml.safe_dump(ORDER), "plugin.yaml": MANIFEST})}
RITA = ("rita@example.com", "Rita-Passw0rd!7")


def claims(token: str) -> dict:
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


async def login(h, email, password, tenant_id=None) -> dict:
    body = {"email": email, "password": password, **({"tenant_id": tenant_id} if tenant_id else {})}
    r = await h.client.post("/app/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def admin(h) -> dict:
    return await login(h, *XAUTH_ADMIN)


async def add_member(h, adm, email, password, *permissions) -> dict:
    """Inscrit `email`, l'invite dans l'organisation de l'administrateur, accepte l'invitation, lui accorde `permissions`, et le connecte à cette organisation."""
    tenant = adm["tenant_id"]
    auth = {"Authorization": f"Bearer {adm['access_token']}"}
    reg = await h.client.post("/app/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    first = await login(h, email, password)
    inv = await h.client.post("/app/auth/invites/", json={"tenant_id": tenant, "email": email}, headers=auth)
    assert inv.status_code == 201, inv.text
    acc = await h.client.post("/app/auth/invites/accept", json={"token": inv.json()["token"], "user_id": first["user_id"]}, headers={"Authorization": f"Bearer {first['access_token']}"})
    assert acc.status_code in (200, 201), acc.text
    for perm in permissions:
        g = await h.client.post(f"/app/auth/rbac/tenants/{tenant}/members/{first['user_id']}/permissions", json={"permission_name": perm}, headers=auth)
        assert g.status_code == 200, g.text
    return await login(h, email, password, tenant)


async def test_the_administrator_created_at_startup_logs_in_and_the_engine_accepts_the_token(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        c = claims(adm["access_token"])
        assert c["email"] == XAUTH_ADMIN[0] and c["tenant_id"] == adm["tenant_id"] and c["sub"] == adm["user_id"]
        token, tenant = adm["access_token"], adm["tenant_id"]
        # L'administrateur a reçu les grants que le plugin `shop` a déclarés (rbac.declare → xauth → rôle admin).
        types = (await h.http("GET", "/meta", token=token, tenant=tenant)).json()
        assert [d["name"] for d in types] == ["Order"]
        made = await h.http("POST", "/doc/Order", token=token, tenant=tenant, json={"data": {"title": "o1"}})
        assert made.status_code == 201 and made.json()["owner"] == adm["user_id"]  # le propriétaire est l'identifiant xauth (un UUID)
        # Son jeton ne vaut que pour SON organisation.
        assert (await h.http("GET", "/meta", token=token, tenant="une-autre-organisation")).status_code == 403
        # Un mauvais mot de passe, un jeton absent ou trafiqué : refusés.
        assert (await h.client.post("/app/auth/login", json={"email": XAUTH_ADMIN[0], "password": "Mauvais-Passw0rd!"})).status_code == 401
        assert (await h.http("GET", "/meta", token=None, tenant=tenant)).status_code in (401, 403)
        assert (await h.http("GET", "/meta", token=token[:-3] + "AAA", tenant=tenant)).status_code in (401, 403)


async def test_a_member_has_no_access_until_the_administrator_grants_it(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        rita = await add_member(h, adm, *RITA)
        assert (await h.http("GET", "/meta", token=rita["access_token"], tenant=tenant)).json() == []  # membre, mais aucun grant de `shop`
        assert (await h.http("POST", "/doc/Order", token=rita["access_token"], tenant=tenant, json={"data": {"title": "x"}})).status_code == 403
        # L'administrateur lui accorde la lecture ; son prochain jeton la porte.
        auth = {"Authorization": f"Bearer {adm['access_token']}"}
        grant = await h.client.post(f"/app/auth/rbac/tenants/{tenant}/members/{rita['user_id']}/permissions", json={"permission_name": "shop:order:read"}, headers=auth)
        assert grant.status_code == 200, grant.text
        fresh = await login(h, *RITA, tenant)
        assert [d["name"] for d in (await h.http("GET", "/meta", token=fresh["access_token"], tenant=tenant)).json()] == ["Order"]
        assert (await h.http("POST", "/doc/Order", token=fresh["access_token"], tenant=tenant, json={"data": {"title": "x"}})).status_code == 403  # lire n'est pas écrire


async def test_the_directory_is_xauth_members_by_email_and_assignment_is_checked_against_it(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant, token = adm["tenant_id"], adm["access_token"]
        rita = await add_member(h, adm, *RITA, "shop:order:read")
        order = (await h.http("POST", "/doc/Order", token=token, tenant=tenant, json={"data": {"title": "o"}})).json()["name"]

        people = (await h.http("GET", "/people/Order", token=token, tenant=tenant)).json()
        assert people["directory"] is True  # l'annuaire d'xauth répond (allowed_callers de app/auth nomme xcore-doctype)
        assert {(p["id"], p["email"]) for p in people["items"]} == {(adm["user_id"], XAUTH_ADMIN[0]), (rita["user_id"], RITA[0])}
        assert [p["email"] for p in (await h.http("GET", "/people/Order?q=rit", token=token, tenant=tenant)).json()["items"]] == [RITA[0]]

        # On assigne par e-mail : c'est l'UUID qui est stocké. Un inconnu est refusé.
        ok = await h.http("POST", f"/assignments/Order/{order}", token=token, tenant=tenant, json={"assignee": RITA[0].upper(), "description": "À relire"})
        assert ok.status_code == 201 and ok.json()["assignee"] == rita["user_id"]
        assert (await h.http("POST", f"/assignments/Order/{order}", token=token, tenant=tenant, json={"assignee": "personne@ailleurs.com"})).status_code == 422

        # Rita (qui lit Order) voit sa tâche, avec le NOM de celui qui la lui a confiée.
        mine = (await h.http("GET", "/my-assignments", token=rita["access_token"], tenant=tenant)).json()["items"]
        assert [(i["assignee"], i["assigned_by"]) for i in mine] == [(rita["user_id"], adm["user_id"])]
        names = (await h.http("GET", "/users", token=rita["access_token"], tenant=tenant, params=[("ids", adm["user_id"]), ("ids", "inconnu")])).json()
        assert names["items"] == {adm["user_id"]: {"id": adm["user_id"], "email": XAUTH_ADMIN[0], "name": XAUTH_ADMIN[0]}}


async def test_an_assignment_and_a_mention_ring_the_bell_of_the_member_concerned(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant, token = adm["tenant_id"], adm["access_token"]
        rita = await add_member(h, adm, *RITA, "shop:order:read")
        order = (await h.http("POST", "/doc/Order", token=token, tenant=tenant, json={"data": {"title": "o"}})).json()["name"]
        bell = lambda who: h.client.get("/app/auth/notifications", headers={"Authorization": f"Bearer {who['access_token']}"})  # noqa: E731
        assert (await bell(rita)).json() == []

        assert (await h.http("POST", f"/assignments/Order/{order}", token=token, tenant=tenant, json={"assignee": RITA[0], "description": "À relire"})).status_code == 201
        [note] = (await bell(rita)).json()
        assert (note["type"], note["is_read"], note["message"]) == ("ASSIGNMENT", False, "À relire")
        assert note["title"] == f"{XAUTH_ADMIN[0]} vous a assigné Commande {order}" and note["link"] == f"#/app/Order/{order}"
        assert [n["type"] for n in (await bell(adm)).json()] == []  # celui qui assigne n'est pas prévenu

        # Une mention par e-mail : Rita (qui lit la commande) commente et cite l'administrateur.
        c = await h.http("POST", f"/comments/Order/{order}", token=rita["access_token"], tenant=tenant, json={"content": f"à voir avec @{XAUTH_ADMIN[0]}"})
        assert c.status_code == 201
        [mention] = (await bell(adm)).json()
        assert mention["type"] == "MENTION" and mention["title"].startswith(f"{RITA[0]} vous a mentionné dans Commande")

        # `xauth.notify` : un identifiant qui n'est pas membre de l'organisation est ignoré en silence, un lien douteux ou un titre vide est refusé.
        ask = lambda **kw: h.action("xauth.notify", {"tenant_id": tenant, "title": "T", **kw}, token=token, tenant=tenant, plugin="auth")  # noqa: E731
        assert (await ask(user_ids=[rita["user_id"], "00000000-0000-0000-0000-000000000000"], link="https://exemple.fr/x")).get("sent") == 1
        for bad in ({"user_ids": [rita["user_id"]], "link": "javascript:alert(1)"}, {"user_ids": []}, {"user_ids": [rita["user_id"]], "title": " "}):
            assert (await ask(**bad)).get("status") == "error", bad
        links = [n["link"] for n in (await bell(rita)).json()]
        assert "https://exemple.fr/x" in links and not any(l and l.startswith("javascript") for l in links)


async def test_invitations_go_out_through_the_mail_extension_which_simulates_without_smtp(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        mail = h.xcore.services.get("ext.email")
        assert mail is not None and mail.status()["simulated"] is True and mail.status()["configured"] is False  # aucun SMTP : envoi simulé, pas d'échec
        before = mail.status()["sent"]
        await add_member(h, adm, *RITA)
        assert mail.status()["sent"] > before  # l'invitation (et la bienvenue) ont bien passé par l'extension


async def test_refresh_renews_and_logout_revokes(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        tenant = adm["tenant_id"]
        renewed = await h.client.post("/app/auth/refresh", json={"refresh_token": adm["refresh_token"]})
        assert renewed.status_code == 200, renewed.text
        new = renewed.json()
        assert new["access_token"] != adm["access_token"] and new["refresh_token"] != adm["refresh_token"]  # le jeton de renouvellement tourne
        assert (await h.client.post("/app/auth/refresh", json={"refresh_token": adm["refresh_token"]})).status_code == 401  # et ne sert qu'une fois
        assert (await h.http("GET", "/meta", token=new["access_token"], tenant=tenant)).status_code == 200
        out = await h.client.post("/app/auth/logout", json={"refresh_token": new["refresh_token"]}, headers={"Authorization": f"Bearer {new['access_token']}"})
        assert out.status_code == 204, out.text
        assert (await h.client.post("/app/auth/refresh", json={"refresh_token": new["refresh_token"]})).status_code == 401


async def test_the_desk_logs_in_against_xauth_by_default(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        cfg = await h.client.get("/app/xcore-doctype/desk/config.json")
        assert cfg.status_code == 200 and cfg.json()["login_url"] == "/app/auth/login"


async def test_the_mail_status_and_test_send_are_for_the_platform_administrator_only_and_never_leak_a_secret(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        auth = {"Authorization": f"Bearer {adm['access_token']}"}
        status = await h.client.get("/app/auth/mail/status", headers=auth)
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["simulated"] is True and body["configured"] is False and body["smtp_host"] == ""
        assert "smtp_password" not in body and "smtp_user" not in body.get("secrets", {})
        before = body["sent"]
        sent = await h.client.post("/app/auth/mail/test", json={"to": "moi@example.com"}, headers=auth)
        assert sent.status_code == 200, sent.text
        assert sent.json() == {"sent": True, "simulated": True}
        assert (await h.client.get("/app/auth/mail/status", headers=auth)).json()["sent"] == before + 1
        assert (await h.client.post("/app/auth/mail/test", json={"to": "pas une adresse"}, headers=auth)).status_code == 422

        rita = await add_member(h, adm, *RITA)
        weak = {"Authorization": f"Bearer {rita['access_token']}"}
        assert (await h.client.get("/app/auth/mail/status", headers=weak)).status_code == 403
        assert (await h.client.post("/app/auth/mail/test", json={"to": "moi@example.com"}, headers=weak)).status_code == 403
        assert (await h.client.get("/app/auth/mail/status")).status_code in (401, 403)  # anonyme : refusé


async def _login_raw(h, identifier_key, identifier, password):
    return await h.client.post("/app/auth/login", json={identifier_key: identifier, "password": password})


async def test_the_administrator_logs_in_with_a_username_derived_from_the_address_whatever_the_case(tmp_path):
    # NB : xauth limite la connexion à 5 appels par minute (dans son code, aussi en test) : quatre connexions ici, pas une de plus.
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        email, password = XAUTH_ADMIN
        by_mail = await login(h, email, password)
        explicit = await _login_raw(h, "username", "ADMIN", password)  # `admin@example.com` -> `admin` ; la casse ne compte pas
        legacy = await _login_raw(h, "email", "  Admin  ", password)  # l'ancienne forme (ce qu'on a tapé dans le champ e-mail), espaces compris
        assert explicit.status_code == legacy.status_code == 200, (explicit.text, legacy.text)
        assert explicit.json()["user_id"] == legacy.json()["user_id"] == by_mail["user_id"]
        assert (await login(h, email.upper(), password))["user_id"] == by_mail["user_id"]  # l'adresse aussi, sans tenir compte de la casse
        me = await h.client.get("/app/auth/me", headers={"Authorization": f"Bearer {by_mail['access_token']}"})
        assert me.json()["username"] == "admin"


async def test_a_wrong_password_or_an_unknown_name_is_refused_the_same_way(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        wrong = await _login_raw(h, "username", "admin", "Mauvais-Passw0rd!")
        unknown = await _login_raw(h, "username", "personne", XAUTH_ADMIN[1])
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()  # rien ne dit si le nom existe
        empty = await h.client.post("/app/auth/login", json={"password": XAUTH_ADMIN[1]})
        assert empty.status_code == 422  # ni adresse ni nom


async def test_registering_with_a_username_then_logging_in_with_it(tmp_path):
    # NB : l'inscription est limitée à 3 appels par minute : les règles du nom, une à une, sont dans tests/unit/test_username.py.
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        reg = await h.client.post("/app/auth/register", json={"email": RITA[0], "password": RITA[1], "username": "  Rita.M "})
        assert reg.status_code == 201, reg.text
        assert reg.json()["username"] == "rita.m"  # rangé en minuscules, sans espaces
        ok = await _login_raw(h, "username", "RITA.M", RITA[1])
        assert ok.status_code == 200, ok.text
        taken = await h.client.post("/app/auth/register", json={"email": "autre@example.com", "password": RITA[1], "username": "RITA.M"})
        assert taken.status_code == 400 and "déjà pris" in taken.text  # pris, quelle que soit la casse
        invalid = await h.client.post("/app/auth/register", json={"email": "autre@example.com", "password": RITA[1], "username": "a@b.c"})
        assert invalid.status_code == 400 and "3 à 32 caractères" in invalid.text  # ni adresse ni caractère interdit


async def test_changing_the_username_needs_the_password_and_keeps_names_unique(tmp_path):
    async with boot_host(tmp_path, xauth=True, inline_plugins=PLUGINS, operator_doctypes=False) as h:
        adm = await admin(h)
        auth = {"Authorization": f"Bearer {adm['access_token']}"}
        await h.client.post("/app/auth/register", json={"email": RITA[0], "password": RITA[1], "username": "rita"})

        async def change(name, password=XAUTH_ADMIN[1]):
            return await h.client.patch("/app/auth/account/username", json={"username": name, "password": password}, headers=auth)

        assert (await change("chef", "Mauvais-Passw0rd!")).status_code == 400  # mot de passe faux
        assert (await change("rita")).status_code == 409  # pris par un autre compte
        assert (await change("x")).status_code == 422  # invalide
        done = await change("Chef")
        assert done.status_code == 200 and done.json() == {"username": "chef"}
        assert (await _login_raw(h, "username", "chef", XAUTH_ADMIN[1])).status_code == 200
        assert (await _login_raw(h, "username", "admin", XAUTH_ADMIN[1])).status_code == 401  # l'ancien nom ne marche plus
        assert (await change("chef")).status_code == 200  # reprendre son propre nom n'est pas un conflit
        assert (await h.client.get("/app/auth/admin/users", headers=auth)).json()[0]["username"] in ("chef", "rita")
