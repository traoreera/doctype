# Scénario de bout en bout du Desk

`desk.e2e.mjs` pilote un vrai Chromium (Playwright) contre un vrai serveur xcore : connexion, création d'un pays puis d'un
client, conditions `depends_on`, champ Link, filtres, conflit de version, suppression, puis articles et **facture à lignes**
(numéro de série, total calculé par le hook du plugin `sales`, lignes incomplètes, garde des modifications non enregistrées,
article cité refusé à la suppression, historique), puis le **module Stock** (palier 40 %, phase A : réception saisie en décimal,
soumission qui fige le document et écrit le journal, sortie refusée « stock insuffisant » sur sa ligne, annulation par mouvement
inverse, amendement, filtre par statut, journal et soldes sans bouton « Nouveau »), déconnexion.

**Le plus simple** : `poetry run pytest tests/e2e` depuis la racine du dépôt. Le test démarre un hôte dans un dossier
temporaire (base SQLite vierge, tenant neuf), fabrique le jeton et lance ce scénario. Il est ignoré, avec la raison, si
Node, `pnpm install`, `pnpm build` ou un Chromium manque. `XCORE_DOCTYPE_E2E_SHOTS=<dossier>` garde les captures d'écran.

**À la main** (hôte déjà lancé avec `make dev` ou `make serve`, connecté à xauth ; le scénario compte sur une liste de clients vide, utilisez une organisation à vous) :

```bash
DESK_URL=http://127.0.0.1:8000/app/xcore-doctype/desk/ \
DESK_API=http://127.0.0.1:8000/app/xcore-doctype \
DESK_TOKEN=$(make -s token) DESK_TENANT=$(make -s token FIELD=tenant_id) SHOTS_DIR=/tmp/shots \
pnpm e2e
```

Le jeton est celui de l'administrateur de `.env` (xauth) ; `DESK_TENANT` est l'identifiant de son organisation (le claim `tenant_id` du jeton, que le Desk envoie en `X-Tenant-ID`). Les scripts à deux
utilisateurs (`look-collab.mjs`, `look-layouts.mjs`) lisent en plus `DESK_TOKEN2` : `make member EMAIL=bob@example.com PASSWORD='Bob-Passw0rd!1'`, puis
`DESK_TOKEN2=$(poetry run python scripts/login.py --email bob@example.com --password 'Bob-Passw0rd!1')`. L'hôte doit charger `app/auth`, `app/sales` et `app/stock`
(les plugins déclarent leurs hooks, sans eux une sauvegarde est refusée en 503 `hook_unavailable`) ; l'administrateur a tous leurs grants.
`E2E_TIMEOUT=8000` (ms) raccourcit les attentes de Playwright (30 s par défaut) : les échecs remontent vite.
`E2E_DEBUG=1` affiche chaque réponse HTTP d'erreur avec son corps : le premier réflexe quand une étape échoue.
`CHROME=<exécutable>` impose un Chromium précis (sinon celui de `playwright-core`).
Le fuseau du navigateur est Pacific/Auckland (UTC+13) exprès : une date locale à minuit y tombe la veille en UTC.
