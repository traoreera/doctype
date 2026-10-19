"""Se connecte à xauth et imprime un champ de la réponse : pratique pour les scripts (e2e, curl).

    make token                      # le jeton d'accès de l'administrateur de `.env`
    make token FIELD=tenant_id      # l'identifiant de son organisation (à envoyer en `X-Tenant-ID`)

Lit ADMIN_EMAIL et ADMIN_PASSWORD (`.env`) sauf --email/--password. Seule la valeur demandée va sur la sortie standard.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--email", default=os.environ.get("ADMIN_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
    ap.add_argument("--field", default="access_token", help="access_token (défaut), refresh_token, tenant_id, user_id")
    args = ap.parse_args()
    if not args.email or not args.password:
        print("ADMIN_EMAIL et ADMIN_PASSWORD sont requis (make env les crée dans .env).", file=sys.stderr)
        return 1
    req = urllib.request.Request(
        f"{args.base.rstrip('/')}/app/auth/login",
        data=json.dumps({"email": args.email, "password": args.password}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            body = json.load(res)
    except urllib.error.HTTPError as e:
        print(f"connexion refusée ({e.code}) : {e.read().decode(errors='replace')[:200]}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"serveur injoignable ({args.base}) : {e}. Lancez make dev ou make serve.", file=sys.stderr)
        return 1
    if body.get("mfa_required"):
        print("ce compte exige un code MFA : connectez-vous depuis le Desk.", file=sys.stderr)
        return 1
    value = body.get(args.field)
    if not isinstance(value, str) or not value:
        print(f"champ {args.field!r} absent de la réponse", file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
