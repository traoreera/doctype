# Phase 4 : livraison

17 octobre 2026. Contre xcore 2.9.1, xcorecli 2.3.0, SQLite et PostgreSQL 18. Suite de `process/phase3.md` ; `process/doctype.md` n'est pas modifié, les écarts sont proposés ici pour la v4. Rien n'a été modifié dans xcore ni dans orchestra-backend.

## 1. Résultat

Critère de sortie de la feuille de route (§15) : **« `pip install xcore-doctype` puis le plugin se charge dans une instance xcore neuve »**.

| Critère | Résultat | Preuve |
| --- | --- | --- |
| Le wheel s'installe et le plugin se charge dans une instance xcore neuve | **OK, avec une étape d'installation** : `xcore-doctype install` (xcore ne lit pas d'entry points) | `tests/delivery/` : wheel construit, installé dans un venv vierge, instance créée par `xcli init`, plugin posé par `xcore-doctype install`, instance corrigée comme la commande le demande, puis démarrée : le Desk est servi depuis le wheel, une facture à lignes est numérotée et totalisée (8 tests, ≈ 10 s) |
| Wheel avec l'UI | **OK** | 24 fichiers du Desk dans le wheel ; **mesuré** dans un dépôt git : 0 fichier sans la ligne `include` de `pyproject.toml`, 24 avec (`.gitignore` écarte `dist/`) |
| Entry point | **Déclaré, inerte** | `[project.entry-points."xcore.plugins"]` pointe sur `xcore_doctype:plugin_dir` ; xcore ne lit pas ce groupe : voir §2 et `process/xcore-issues.md` |
| Premier DocType réel | **OK** | le plugin `app/sales` apporte ses 5 DocTypes (clients, pays, articles, factures et leurs lignes) par le nouveau service `doctype_registry`, avec `module == nom du plugin` imposé (§3) |
| RLS PostgreSQL (D14, « si validée ») | **OK, optionnelle** (`doctype.rls`, défaut `false`) | `tests/unit/test_rls.py`, `tests/integration/test_rls.py` : avec un vrai rôle ordinaire (§4) |
| CI SQLite + PostgreSQL | **Écrite, non exécutée** | `.github/workflows/ci.yml` : je ne peux pas la lancer ici ; chaque étape est une cible du `makefile`, rejouée en local (§5) |

Suites : voir §8.

Éprouvé par mutation, chacune fait échouer au moins un test (19 mutations de cette phase) : registre et propriété des DocTypes (6), CLI d'installation (7), RLS (6 : politique permissive, `FORCE` retiré, tenant de session au lieu de transaction, rôle privilégié accepté, politique limitée à une table, service sans liaison du tenant).

## 2. Livraison

### 2.1 Ce qu'xcore permet aujourd'hui

`PluginLoader` ne connaît que le scan d'un dossier (`plugins.directory`). Un `pip install` seul ne charge donc rien. Le design prévoyait un groupe d'entry points `xcore.plugins` et une liste blanche `plugins.trusted_entry_points` (D1, D19), c'est-à-dire **des changements dans xcore**. Décision (la vôtre, 17 oct.) : **xcore reste intact**.

### 2.2 La commande `xcore-doctype`

```
xcore-doctype install ./app      # dépose app/xcore-doctype/{plugin.yaml, src/main.py} et vérifie ./integration.yaml
xcore-doctype doctor             # revérifie
xcore-doctype check DIR --owner NOM   # valide un dossier de DocTypes (CI d'un plugin métier)
xcore-doctype path               # le dossier du plugin dans le paquet (pour `xcli plugin local link --path … --name xcore-doctype`)
```

- Le **shim** `src/main.py` est réécrit à chaque `install` (aucun état, il suit la version du paquet). Le **manifeste** `plugin.yaml` est la copie de l'opérateur : créé une fois, jamais écrasé sans `--force` ; une version différente de celle du paquet est signalée. C'est là que se règle le bloc `doctype:`, ce qui répond à la question restée ouverte depuis la phase 1 (« réglages par déploiement »).
- La tenancy d'xcore est lue dans `integration.yaml` : désactivée, le manifeste installé reçoit `enforce_tenant_binding: false` (sans cela le moteur exige un claim de tenant que rien ne fournit) ; activée, il garde le lien du tenant à l'identité (D17). `diagnose` signale les deux incohérences (erreur dans un sens, avertissement dans l'autre, car relâcher le lien dans une instance qui a la tenancy est un risque de sécurité).
- Le message de l'erreur `tenant_middleware_missing` dit maintenant quoi faire (`xcore.setup(app)`), au lieu de n'être qu'un code.

### 2.3 Ce qu'une instance neuve rate (mesuré avec `xcli init`)

| Constat sur l'instance générée | Conséquence pour le plugin | Détecté par `install` |
| --- | --- | --- |
| `services.databases.default.type: sqlite` (adaptateur **synchrone**) avec une URL `sqlite+aiosqlite://` | échec clair au chargement (D11) | oui : « mettez `sqlite+aio` » |
| `main.py` sans `xcore.setup(app)` | `TenantMiddleware` absent : toute requête répond 500 `tenant_middleware_missing` | oui |
| aucune section `tenancy` | tenancy désactivée : le lien du tenant doit l'être aussi | oui (manifeste patché) |
| `strict_trusted: false` | rien à signer | (avertit si actif, avec la commande `xcli plugin security sign`) |

Le test de livraison suit exactement ce parcours : il affirme que ces deux erreurs sont signalées, applique les corrections que la commande demande, vérifie que `doctor` devient vert, puis démarre l'instance.

### 2.4 Le wheel

Contenu vérifié (`TestWheel`) : manifeste, shim, Desk construit (`index.html` + `assets/*.js`), pas de `node_modules` ni de `.map`, entry points `xcore.plugins` et console script. Le garde-fou `include` de `pyproject.toml` est testé statiquement (`tests/unit/test_cli.py`). La version du manifeste est comparée à celle du paquet (une divergence ferait croire à `install` que le manifeste installé est périmé).

**Hors ligne**, le test installe le wheel avec `--no-deps` et prend les dépendances (xcore, FastAPI…) de l'environnement de test par `PYTHONPATH` : il prouve que le wheel est **autosuffisant**, pas que `pip` résout ses dépendances. Avec `XCORE_DOCTYPE_INSTALL_DEPS=1` (la CI), c'est un vrai `pip install` ; **je ne l'ai pas exécuté ici** (pas de réseau garanti, et le paquet n'est pas publié).

## 3. Un module métier apporte ses DocTypes (service `doctype_registry`)

C'était le chantier de sécurité n° 2, reporté à votre demande en phase 2 : « rien ne lie un DocType à son plugin propriétaire ». Il est construit.

```python
# dans on_load d'un plugin qui déclare `requires: [xcore-doctype]`
self.get_service("doctype_registry").load_dir(self.ctx.name, DOCTYPES_DIR)
# dans on_unload
self.get_service("doctype_registry").unregister_owner(self.ctx.name)
```

- **Règle** : le `module` de chaque DocType du dossier doit être **le nom du plugin** (premier segment de ses grants ; `doctype_grants.for_plugin(nom)` ne déclare que ceux-là). Un plugin ne peut donc pas écrire des DocTypes dans le module d'un autre. Le nom du plugin doit pouvoir servir de namespace de grants (`[a-z0-9_]+`) : `my-sales` ne peut rien posséder. Un DocType **enfant** (`istable`) n'a pas de `module` (il hérite de son parent) : il appartient au plugin qui l'apporte. Un nom déjà pris est refusé, quel que soit le propriétaire.
- **Tout ou rien** : un dossier dont un fichier est invalide n'enregistre aucun de ses DocTypes. Une cible de Link inconnue (hors de ce plugin et de ceux dont il dépend par `requires`) fait échouer le chargement et reprend le dossier ; les défauts d'un **autre** plugin ne lui sont pas imputés.
- **Au départ du plugin**, ses DocTypes quittent le registre ; ses documents restent en base (testé : après `unload` puis `load`, un article créé avant est relu).
- Le service n'expose ni la lecture des définitions compilées ni le retrait de celles d'un autre : seulement `load_dir` et `unregister_owner`.
- **Limites assumées** : le propriétaire est le nom que le plugin passe (comme `doctype_hooks.register(owner=…)`) : cela empêche les erreurs et les fichiers mal placés, pas un plugin de confiance hostile (qui exécute déjà du code dans le processus). L'ordre de chargement vient de `requires`. Un plugin qui oublie `unregister_owner` laisse ses DocTypes au registre. Au rechargement d'xcore-doctype, le registre repart vide et les plugins doivent se réenregistrer (même protocole manuel que les hooks).
- **Reste reporté** (chantier n° 1, dans xcore et xauth) : `rbac.declare` ne vérifie pas son émetteur. `process/xcore-issues.md` §6.
- `doctypes_dirs` reste pour les DocTypes de l'**opérateur** (module libre) ; le manifeste livré n'en configure plus aucun (`[]`). L'exemple `app/sales/` a emporté `customer`, `country`, `item`, `invoice` et `invoice_line` dans `app/sales/doctypes/`.

## 4. Row Level Security PostgreSQL (D14), optionnelle

`doctype.rls: true` (défaut : `false`, rien ne change pour les déploiements existants). Un filet **sous** la couche de requêtes unique : une requête qui oublierait un jour son filtre de tenant ne verrait quand même que les lignes de son tenant.

- Chaque table système (`xdt_document`, `xdt_naming_series`, `xdt_version`) reçoit `ENABLE` + **`FORCE ROW LEVEL SECURITY`** (elle s'applique aussi au propriétaire des tables, qui est le rôle de connexion) et la politique `tenant_id = current_setting('xdt.tenant_id', true)`.
- Le tenant est posé par `set_config('xdt.tenant_id', …, true)`, **local à la transaction**, à l'ouverture de chaque session du service, depuis le `RequestContext` (jamais l'en-tête). Il ne survit pas au `COMMIT` : une connexion rendue au pool, même derrière pgbouncer en mode transaction, ne garde rien (testé sur une même connexion, `pg_backend_pid` à l'appui). Sans lui, la politique ne laisse **aucune** ligne : échec fermé (un service sans la liaison lit zéro ligne et ne peut rien écrire).
- **Le plugin refuse de démarrer** quand la protection ne protégerait rien : base qui n'est pas PostgreSQL, rôle de connexion superutilisateur ou `BYPASSRLS` (PostgreSQL ne leur applique aucune politique, même forcée), rôle qui n'est pas propriétaire des tables. Chaque cas a son message.
- **Prouvé avec un vrai rôle ordinaire** (la base de test appartient à un superutilisateur, qui ne serait pas contraint : la fixture `rls_app_url` crée un rôle `NOSUPERUSER NOBYPASSRLS`). Sans tenant : 0 ligne dans les trois tables, même pour `SELECT *` sans filtre. Avec le tenant : seulement les siennes. Écrire pour un autre tenant est refusé par la base (SQLSTATE 42501). `UPDATE` et `DELETE` des lignes d'un autre tenant ne touchent rien. Un `Query._scope` volontairement cassé (sans filtre de tenant) ne fuit pas. Les **13 scénarios de fuite** de `test_query_leak.py` (un par méthode publique de `Query`) repassent sous RLS, ainsi que le cycle complet du service (série, liens verrouillés `FOR KEY SHARE` / `FOR UPDATE`, historique) et 20 créations concurrentes sans trou.
- Un détail : la politique déclare `WITH CHECK`, mais PostgreSQL le déduit de `USING` quand il est absent ; le retirer ne change rien (c'est la seule mutation qui survit, et elle est équivalente).
- **Coûts et limites.** Un aller-retour de plus par transaction (`SELECT set_config`). Le rôle de connexion doit être propriétaire des tables `xdt_*` et ordinaire : un déploiement qui migre le schéma avec un autre rôle doit le faire avant, ou rendre les tables à l'application. Les DDL ne sont pas soumis à la RLS. Rien ne protège d'un administrateur de base. Non mesuré : l'effet de la politique sur de gros volumes (les index `(tenant_id, …)` existants s'y prêtent, pas de benchmark).

## 5. CI (GitHub Actions)

`.github/workflows/ci.yml`, deux jobs :

- **Tests** : Python 3.14, Poetry 2.4.1, service `postgres:18` (superutilisateur : les tests de RLS créent eux-mêmes leur rôle ordinaire), `poetry check --lock`, `make build-ui` (types + build), `make test-ui`, Chromium (`playwright-core install --with-deps`), `make test-pg` (SQLite **et** PostgreSQL, scénario navigateur compris).
- **Wheel** : `XCORE_DOCTYPE_INSTALL_DEPS=1`, build du Desk puis `tests/delivery` : un vrai `pip install` du wheel dans un venv vierge et une instance `xcli init`. Le wheel est publié comme artefact.

**Non exécutée ici.** Je l'ai vérifiée comme YAML et rejoué chaque commande en local ; restent à confirmer sur GitHub : la disponibilité de Python 3.14 et de Node 24 sur les runners, de l'image `postgres:18`, `poetry check --lock`, `pnpm/action-setup` avec `package_json_file`, et la résolution des dépendances d'xcore sur l'index (c'est le but du job *Wheel*). La CI aurait **déjà attrapé** l'erreur de `pyproject.toml` du §6.1 (`poetry check`).

## 6. Défauts trouvés pendant la phase

1. **`pyproject.toml` est invalide pour Poetry, et ce n'est pas de mon fait.** Les `authors` ont été réécrits en trois entrées (`{name}`, `{email}`, `{homepage}`) ; `homepage` n'est pas une clé permise (PEP 621 : `name` et `email`), et un auteur qui n'a ni l'un ni l'autre est rejeté (`project.authors[2] cannot be validated`). Conséquence : `poetry run`, `poetry install` et `poetry build` échouent dans le dépôt, donc aussi `make` (qui passe par Poetry). **Je n'y ai pas touché.** Correctif d'une ligne : `authors = [{name = "traoreera", email = "traoreera@gmail.com"}]` et `[project.urls] Homepage = "https://github.com/traoreera"`. J'ai vérifié le test de livraison sur une copie où seul ce bloc est corrigé (8 réussis).
2. **Le wheel d'un dépôt git perdrait tout le Desk sans `include`** : mesuré (0 fichier / 24). Je croyais la ligne utile ; elle est nécessaire, et maintenant gardée par un test.
3. **Un DocType enfant ne pouvait pas être apporté par un plugin** : la règle « `module == plugin` » refusait `InvoiceLine`, qui n'a pas de `module` par construction. Trouvé par le test d'intégration du plugin `sales` ; corrigé (un enfant appartient au plugin qui l'apporte).
4. **Une instance `xcli init` ne fonctionne pas avec un plugin asynchrone** sans deux corrections à la main (§2.3) : ce n'est pas un défaut d'xcore-doctype, mais c'est la première chose qu'un utilisateur rencontre ; la commande `install` la rend explicite. `process/xcore-issues.md` §4.
5. **Un test dépendait du répertoire courant** : le repli `./integration.yaml` de `install` trouvait celui du dépôt pendant `pytest` ; le test isole maintenant le répertoire courant.
6. **L'ordre des tests de la RLS m'a appris une chose sur PostgreSQL** : les tables créées par un autre test appartiennent au superutilisateur ; sans les supprimer d'abord, le rôle ordinaire ne peut pas les modifier. D'où le message « propriétaire des tables » (§4) et la fixture qui repart de zéro.

## 7. Non fait et non vérifié

- **Rien n'a été publié** : ni PyPI, ni GitHub. Le `pip install xcore-doctype` du critère est celui d'un wheel local (`make build`).
- **L'entry point `xcore.plugins` ne sert à rien tant qu'xcore ne le lit pas** ; `process/xcore-issues.md` rédige les demandes (entry points, confiance, manifeste invalide, gabarit d'`xcli init`, API de tenant, `source` des événements). Aucun patch n'a été préparé : vous avez choisi de laisser xcore intact.
- **La signature** (`strict_trusted: true`) n'est pas exercée : `install` la signale et donne la commande, mais le test ne signe rien.
- **La CI n'a pas tourné** (§5). Le job *Wheel* avec les vraies dépendances non plus.
- **RLS** : pas de benchmark ; pas d'essai derrière pgbouncer (le comportement transactionnel du `set_config` local est testé, pas le pooler) ; migrations de schéma avec un autre rôle non couvertes.
- **Le premier DocType réel reste un exemple** (`app/sales`) : aucun plugin métier du dépôt `orchestra-backend` n'a été migré (hors périmètre : vous avez demandé de n'y rien toucher).
- Le canal des DocTypes ne gère pas le rechargement à chaud de xcore-doctype (les plugins doivent se réenregistrer), ni un ordre de chargement circulaire.
- Hors périmètre, inchangé : documents soumettables, permissions par champ, hooks de suppression, mode sombre, rafraîchissement du jeton et double authentification dans le Desk.

## 8. Suites

Dernière exécution complète (17 octobre 2026, fin de phase) :

| Suite | Résultat |
| --- | --- |
| pytest, SQLite + PostgreSQL 18 | **1542 réussis**, 12 ignorés (les 8 tests de livraison, désactivés par `XCORE_DOCTYPE_SKIP_DELIVERY` dans cette exécution ; 4 cas qui exigent PostgreSQL, ignorés quand le fixture `db` joue sa variante SQLite : deux entrelacements de verrous de ligne et deux tests de RLS) |
| pytest, SQLite seul | 1161 réussis, 393 ignorés (les cas PostgreSQL, dont toute la suite de RLS, et les 8 tests de livraison désactivés par la variable) |
| livraison (`tests/delivery`) | **8 réussis** sur une copie du dépôt dont seul le bloc `authors` est corrigé (§6.1) ; dans le dépôt tel quel, `poetry build` échoue |
| vitest | 346 réussis, inchangé depuis la phase 3 |

Pour comparaison, la fin de la phase 3 comptait 1438 tests pytest.
