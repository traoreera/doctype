"""Crée un utilisateur de DÉVELOPPEMENT dans l'organisation de l'administrateur (inscription, invitation, acceptation, grants).

    make member EMAIL=bob@example.com PASSWORD='Bob-Passw0rd!1'            # tous les grants des plugins chargés (comme un administrateur)
    make member EMAIL=rita@example.com PASSWORD='…' GRANTS='sales:invoice:read stock:item:read'

Le serveur doit tourner. Idempotent : un compte qui existe déjà est repris, ses grants sont (ré)accordés. Sert aux scripts de démonstration à deux utilisateurs
(`ui/e2e/look-collab.mjs`, `look-layouts.mjs`) ; en production, les membres sont invités depuis xauth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(base: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, object]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--grants", default="", help="grants séparés par des espaces (défaut : tous ceux de l'administrateur)")
    ap.add_argument("--admin-email", default=os.environ.get("ADMIN_EMAIL"))
    ap.add_argument("--admin-password", default=os.environ.get("ADMIN_PASSWORD"))
    a = ap.parse_args()
    base = a.base.rstrip("/")
    if not a.admin_email or not a.admin_password:
        print("ADMIN_EMAIL et ADMIN_PASSWORD sont requis (.env).", file=sys.stderr)
        return 1
    st, admin = call(base, "POST", "/app/auth/login", {"email": a.admin_email, "password": a.admin_password})
    if st != 200 or not isinstance(admin, dict):
        print(f"connexion de l'administrateur refusée ({st}) : {admin}", file=sys.stderr)
        return 1
    tenant, token = admin["tenant_id"], admin["access_token"]

    st, body = call(base, "POST", "/app/auth/register", {"email": a.email, "password": a.password})
    if st not in (201, 400):  # 400 : « Email already registered »
        print(f"inscription refusée ({st}) : {body}", file=sys.stderr)
        return 1
    st, first = call(base, "POST", "/app/auth/login", {"email": a.email, "password": a.password})
    if st != 200 or not isinstance(first, dict):
        print(f"connexion de {a.email} refusée ({st}) : {first} (mot de passe différent de celui d'origine ?)", file=sys.stderr)
        return 1
    st, members = call(base, "GET", f"/app/auth/rbac/tenants/{tenant}/members", None, token)
    already = isinstance(members, list) and any(isinstance(m, dict) and m.get("user_id") == first["user_id"] for m in members) or (isinstance(members, list) and first["user_id"] in members)
    if not already:
        st, inv = call(base, "POST", "/app/auth/invites/", {"tenant_id": tenant, "email": a.email}, token)
        if st != 201 or not isinstance(inv, dict):
            print(f"invitation refusée ({st}) : {inv}", file=sys.stderr)
            return 1
        st, acc = call(base, "POST", "/app/auth/invites/accept", {"token": inv["token"], "user_id": first["user_id"]}, first["access_token"])
        if st not in (200, 201):
            print(f"acceptation refusée ({st}) : {acc}", file=sys.stderr)
            return 1
    st, mine = call(base, "GET", "/app/auth/rbac/me/permissions", None, token)
    wanted = a.grants.split() or [p for p in (mine if isinstance(mine, list) else []) if p != "admin:*" and ":" in p]
    done = 0
    for perm in wanted:
        st, _ = call(base, "POST", f"/app/auth/rbac/tenants/{tenant}/members/{first['user_id']}/permissions", {"permission_name": perm}, token)
        done += st == 200
    print(f"{a.email} : membre de l'organisation {tenant}, {done}/{len(wanted)} grants accordés", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
