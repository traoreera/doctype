"""Droits par champ de bout en bout (traoreera/doctype#74) : un vrai hôte xcore, les routes HTTP et les actions IPC.

Les règles se jugent dans `DocumentService`, que les deux portes traversent : ce fichier prouve qu'AUCUNE des deux ne le contourne,
que les refus arrivent au client avec leur code nommé (`field_not_writable`, `field_not_readable`, 403) et que deux organisations
s'ignorent. Le détail de chaque porte est dans `tests/unit/test_field_permissions.py`.
"""

from __future__ import annotations

import json

from tests.integration.conftest import boot_host

SAL_R, SAL_W = "paie:salarie:salary_read", "paie:salarie:salary_write"
PRIME = {
    "schema_version": 1, "name": "Prime", "istable": True,
    "fields": [{"fieldname": "label", "fieldtype": "Text"}, {"fieldname": "amount", "fieldtype": "Currency"}],
}
SALARIE = {
    "schema_version": 1, "name": "Salarie", "module": "paie", "title_field": "full_name", "naming": "prompt",
    "fields": [
        {"fieldname": "full_name", "fieldtype": "Text", "required": True},
        {"fieldname": "service", "fieldtype": "Select", "options": ["Ventes", "Atelier"]},
        {"fieldname": "salary", "fieldtype": "Currency", "permissions": {"read": [SAL_R], "write": [SAL_W]}},
        {"fieldname": "primes", "fieldtype": "Table", "options": "Prime", "permissions": {"read": [SAL_R]}},
    ],
}
BASE = ["paie:salarie:read", "paie:salarie:write", "paie:salarie:delete"]


def rh(h, tenant="acme"):
    return h.token(sub="hana", tenant=tenant, permissions=[*BASE, SAL_R, SAL_W])


def commis(h):
    return h.token(sub="carl", permissions=BASE)


async def hire(h, token, name="ada", tenant="acme", **data):
    body = {"name": name, "data": {"full_name": "Ada", "service": "Ventes", "salary": "5200.00", "primes": [{"label": "Prime", "amount": "300.00"}], **data}}
    r = await h.http("POST", "/doc/Salarie", token=token, tenant=tenant, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def refusal(r, code):
    assert r.status_code == 403, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert detail["code"] == code, detail
    return sorted(d["field"] for d in detail["details"])


async def test_a_reserved_field_never_reaches_a_caller_without_the_grant_over_http(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(PRIME, SALARIE)) as h:
        hr, clerk = rh(h), commis(h)
        made = await hire(h, hr)
        assert made["data"]["salary"] == "5200.00"
        answers = [
            await h.http("GET", "/doc/Salarie/ada", token=clerk),
            await h.http("GET", "/list/Salarie", token=clerk),
            await h.http("GET", "/history/Salarie/ada", token=clerk),
            await h.http("GET", "/print/Salarie/ada", token=clerk),
            await h.http("GET", "/search/Salarie?q=a", token=clerk),
            await h.http("GET", "/meta/Salarie", token=clerk),
            await h.http("GET", "/aggregate/Salarie?group_by=service", token=clerk),
        ]
        for r in answers:
            assert r.status_code == 200, (r.request.url, r.text)
            # Les VALEURS nulle part ; le nom même du champ nulle part non plus, sauf sur le papier (le gabarit fabriqué garde le libellé, vide).
            names = () if "/print/" in str(r.request.url) else ("primes", "salary")
            for secret in ("5200", "5\u00a0200", "300.00", "300,00", SAL_R, *names):
                assert secret not in r.text, (str(r.request.url), secret)
        # Celle qui a les grants lit tout, par les mêmes routes.
        assert (await h.http("GET", "/doc/Salarie/ada", token=hr)).json()["data"]["primes"] == [{"label": "Prime", "amount": "300.00"}]
        meta = (await h.http("GET", "/meta/Salarie", token=hr)).json()
        assert {f["fieldname"]: f.get("access") for f in meta["fields"]} == {"full_name": None, "service": None, "salary": "write", "primes": "write"}
        assert SAL_R not in json.dumps(meta)  # le Desk sait ce qu'il peut faire, jamais avec quel grant


async def test_a_direct_request_cannot_write_or_probe_a_reserved_field(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(PRIME, SALARIE)) as h:
        hr, clerk = rh(h), commis(h)
        await hire(h, hr)
        seen = (await h.http("GET", "/doc/Salarie/ada", token=clerk)).json()
        # La bonne valeur comme la mauvaise : même refus (aucun moyen de sonder), et les lignes d'une table réservée aussi.
        bodies = []
        for salary in ("5200.00", "1.00"):
            r = await h.http("PUT", "/doc/Salarie/ada", token=clerk, json={"version": seen["version"], "data": {**seen["data"], "salary": salary}})
            assert refusal(r, "field_not_writable") == ["salary"]
            bodies.append(r.text)
        assert bodies[0] == bodies[1]
        r = await h.http("PUT", "/doc/Salarie/ada", token=clerk, json={"version": seen["version"], "data": {**seen["data"], "primes": [{"label": "x", "amount": "9.00"}]}})
        assert refusal(r, "field_not_writable") == ["primes"]
        r = await h.http("POST", "/doc/Salarie", token=clerk, json={"name": "zed", "data": {"full_name": "Zed", "salary": "1.00"}})
        assert refusal(r, "field_not_writable") == ["salary"]
        r = await h.http("POST", "/bulk/Salarie", token=clerk, json={"action": "update", "names": ["ada"], "data": {"salary": "1.00"}})
        assert refusal(r, "field_not_writable") == ["salary"]
        r = await h.http("POST", "/import/Salarie", token=clerk, json={"content": "name;salary\nada;1.00\n", "mode": "update"})
        assert refusal(r, "field_not_writable") == ["salary"]
        # Enregistrer la fiche telle qu'il la voit passe, et ne vide rien de ce qu'il ne voit pas.
        r = await h.http("PUT", "/doc/Salarie/ada", token=clerk, json={"version": seen["version"], "data": {**seen["data"], "service": "Atelier"}})
        assert r.status_code == 200 and "salary" not in r.json()["data"], r.text
        kept = (await h.http("GET", "/doc/Salarie/ada", token=hr)).json()["data"]
        assert (kept["service"], kept["salary"], kept["primes"]) == ("Atelier", "5200.00", [{"label": "Prime", "amount": "300.00"}])
        # Déduire : filtre, filtre de ligne, tri, mesure, regroupement — refus nommé, quelle que soit la donnée.
        for path in (
            '/list/Salarie?filters={"salary":[">","1000"]}', '/list/Salarie?filters={"primes.amount":[">","1"]}', "/list/Salarie?order_by=salary",
            "/aggregate/Salarie?metrics=sum:salary", '/aggregate/Salarie?filters={"salary":"5200.00"}', '/search/Salarie?q=a&filters={"salary":"5200.00"}',
        ):
            assert refusal(await h.http("GET", path, token=clerk), "field_not_readable")[0] in ("salary", "primes.amount"), path
            assert (await h.http("GET", path, token=hr)).status_code == 200, path


async def test_the_ipc_actions_apply_the_same_rules(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(PRIME, SALARIE)) as h:
        hr, clerk = rh(h), commis(h)
        await hire(h, hr)
        doc = (await h.action("doc.get", {"doctype": "Salarie", "name": "ada"}, token=clerk))["doc"]
        assert set(doc["data"]) == {"full_name", "service"}
        listed = await h.action("doc.list", {"doctype": "Salarie"}, token=clerk)
        assert "salary" not in json.dumps(listed)
        for name, payload in (
            ("doc.list", {"doctype": "Salarie", "filters": {"salary": [">", "1000"]}}),
            ("doc.list", {"doctype": "Salarie", "order_by": "salary"}),
            ("doc.aggregate", {"doctype": "Salarie", "metrics": ["avg:salary"]}),
        ):
            assert (await h.action(name, payload, token=clerk))["code"] == "field_not_readable", name
        saved = await h.action("doc.save", {"doctype": "Salarie", "name": "ada", "version": doc["version"], "data": {**doc["data"], "salary": "1.00"}}, token=clerk)
        assert saved["code"] == "field_not_writable"
        assert (await h.action("doc.get", {"doctype": "Salarie", "name": "ada"}, token=hr))["doc"]["data"]["salary"] == "5200.00"


async def test_field_grants_do_not_cross_organisations(tmp_path):
    async with boot_host(tmp_path, extra_doctypes=(PRIME, SALARIE)) as h:
        await hire(h, rh(h))
        elsewhere = rh(h, tenant="globex")  # tous les grants, mais chez globex
        assert (await h.http("GET", "/doc/Salarie/ada", token=elsewhere, tenant="globex")).status_code == 404
        assert (await h.http("GET", "/list/Salarie", token=elsewhere, tenant="globex")).json()["total"] == 0
        total = (await h.http("GET", "/aggregate/Salarie?metrics=sum:salary", token=elsewhere, tenant="globex")).json()
        assert "5200" not in json.dumps(total)
        # Un jeton de globex présenté chez acme est refusé avant toute lecture (D17) : ses grants de champ n'y valent rien.
        assert (await h.http("GET", "/doc/Salarie/ada", token=elsewhere, tenant="acme")).status_code in (401, 403)
        await hire(h, elsewhere, tenant="globex", salary="42.00")
        assert (await h.http("GET", "/doc/Salarie/ada", token=elsewhere, tenant="globex")).json()["data"]["salary"] == "42.00"
        assert (await h.http("GET", "/doc/Salarie/ada", token=rh(h))).json()["data"]["salary"] == "5200.00"
