# Phase 2 : le Desk

17 octobre 2026. Contre xcore 2.9.1, SQLite et PostgreSQL 18, Chromium piloté par playwright-core 1.64. Suite de `process/phase1.md` ; `process/doctype.md` n'est pas modifié, les écarts sont proposés ici pour la v4.

## 1. Résultat

Critère de sortie de la feuille de route (§15) : **« le DocType Client se gère entièrement depuis l'UI »**.

| Critère | Résultat | Preuve |
| --- | --- | --- |
| Le DocType Client se gère entièrement depuis l'UI | **OK** | `tests/e2e/test_desk_browser.py` : vrai Chromium, vrai hôte uvicorn, base vierge et tenant neuf à chaque exécution (≈ 10 à 20 s). Connexion, création d'un pays puis d'un client, champ conditionnel, champ obligatoire, autocomplétion Link, enregistrement, liste, filtre, recherche rapide, conflit de version, suppression, déconnexion |
| ListView, FormView, barre latérale | **OK** | `ui/src/views/`, `ui/src/components/AppShell.vue` |
| `depends_on`, `mandatory_depends_on`, `read_only_depends_on` | **OK** | mêmes vecteurs exécutés par pytest et par vitest (177 cas + 60 erreurs) ; revérifiés côté serveur à l'écriture |
| Toast et ConfirmDialog | **OK** | suppression, conflit de version, sortie avec modifications non enregistrées (`FormView.vue`, `ListView.vue`) |

Suites : pytest **1215 réussis** sur SQLite et PostgreSQL 18 (940 réussis et 275 ignorés sur SQLite seul : ce sont les cas PostgreSQL), vitest **293 réussis** (9 fichiers). Aucun test ignoré quand Node, `pnpm install`, `pnpm build` et un Chromium sont présents ; sinon le test navigateur s'ignore en disant pourquoi.

Éprouvé par mutation, chacune fait échouer au moins un test :
- parité des deux évaluateurs : ordre des chaînes en unités UTF-16 au lieu de points de code, égalité lâche (`1 == true`), `'0'` tenu pour faux ;
- service du Desk : traversée de chemin, lien symbolique qui sort du build, cache immuable hors de `assets/` ;
- logique du formulaire : lecture seule évaluée sur les valeurs saisies au lieu de l'état enregistré, `required` sans tenir compte de la visibilité, `0` pris pour vide, filtre incomplet envoyé, opérateur interdit envoyé, date construite en UTC (celle-ci n'était **pas** détectée tant que les tests tournaient en UTC : voir §6) ;
- session et routeur : redirection ouverte (`//hôte`, `\hôte`), contrôle d'authentification retiré, double authentification ignorée, jeton conservé après déconnexion, mot de passe envoyé sans `login_url`.

## 2. Conditions `depends_on` (D22)

Choix de l'utilisateur : une **chaîne** dans le YAML, **compilée côté serveur** en arbre JSON ; le navigateur ne parse jamais rien.

```yaml
- { fieldname: company_id, fieldtype: Text, required: true, depends_on: "type == 'Entreprise'" }
- { fieldname: approver, fieldtype: Text, depends_on: "credit_limit > 0", mandatory_depends_on: "credit_limit > 10000" }
- { fieldname: credit_limit, fieldtype: Number, read_only_depends_on: "blocked" }
```

- **Un seul parseur** (`src/xcore_doctype/expr.py`, analyse descendante récursive, jamais d'`eval`). Grammaire : comparaisons `== != < <= > >=` non chaînables, `in` / `not in` sur une liste de littéraux, `and` / `or` / `not`, parenthèses, littéraux (nombre, chaîne, `true`, `false`, `null`), noms de champs du même DocType. Pas d'arithmétique, d'appel ni d'attribut. Bornes : 500 caractères, 16 niveaux, 100 éléments de liste, entiers ≤ 2^53. Un nom inconnu ou un mot réservé comme nom de champ est refusé au chargement du DocType.
- **Sémantique stricte et identique dans les deux langages** : `==` exige le même type (`1 == true` est faux, `"1" == 1` est faux) ; l'ordre ne compare que deux nombres ou deux chaînes (par point de code) ; un champ absent vaut `null` ; `null`, `false`, `0` et `""` sont faux. `ui/src/lib/expr.ts` et `expr.py` exécutent `tests/vectors/expr_cases.json` ; `tests/vectors/expr_asts.json` (les arbres attendus) est généré par `scripts/gen_expr_asts.py` et pytest échoue s'il est périmé.
- **Le serveur revérifie** (`CompiledDocType._condition_errors`) : un champ `required` portant un `depends_on` n'est obligatoire que s'il est visible ; `mandatory_depends_on` est évalué sur les valeurs soumises ; `read_only_depends_on` est évalué sur le document **enregistré** et seulement pour une entrée client (la sortie d'un hook peut poser un champ que l'interface verrouille). Une valeur que `depends_on` masque est conservée, pas effacée.
- **Conséquence pour le Desk** : sur un document existant, `read_only_depends_on` s'évalue sur les valeurs enregistrées, comme le serveur. Cocher « Compte bloqué » ne verrouille donc pas le plafond avant l'enregistrement ; sur un nouveau document, l'interface suit les valeurs saisies.

Écart avec `process/doctype.md` : D22 laissait ouverte « chaîne ou JSON structuré ». La chaîne l'emporte, avec l'arbre comme format de transport.

## 3. API : filtres typés, recherche, métadonnées

- **Filtres** (`GET /list/{doctype}?filters=…`) : une valeur (égalité) ou `[opérateur, valeur]`, opérateurs permis **par type** (`builder.FILTER_OPS`). Pas d'ordre sur Text, Select et Link : il suivrait la collation de chaque base (SQLite binaire, PostgreSQL localisée), alors que les dates ISO canoniques se trient pareil partout. `like` est « contient », insensible à la casse, jokers échappés ; `in` ≤ 100 valeurs ; `!=` retient aussi les documents sans valeur. La colonne système `name` se filtre comme un texte (le Desk s'en sert pour retrouver le titre d'un lien avec `name in [...]`).
- **`GET /search/{doctype}?q=&limit=`** (≤ 50) : nom ou `title_field` contient `q`, dans le périmètre de lecture de l'appelant. Sert l'autocomplétion des champs Link.
- **`/meta/{doctype}`** renvoie les conditions déjà compilées en arbres, la mise en page effective (les champs non placés vont dans un dernier bloc : ils ne disparaissent jamais), les colonnes de liste, `title_field` et les **droits** de l'appelant (`all` / `restricted` / `none` par action), sans jamais exposer les règles de permission ni les hooks.
- Chaque nouvelle méthode de `Query` a son scénario de fuite entre tenants (`tests/unit/test_query_leak.py`) ; la parité SQLite / PostgreSQL de chaque opérateur est testée (`test_query_parity.py`).

## 4. Servir le Desk (`api/desk.py`)

Un **routeur public distinct**, monté à côté du routeur protégé : le navigateur doit charger l'application avant d'avoir un jeton, et une route ajoutée à l'API ne peut pas hériter par accident d'une exemption. Il ne donne accès à aucune donnée :

- `GET <préfixe>/xcore-doctype/desk/` : `index.html` du build, embarqué dans le wheel (`src/xcore_doctype/ui/dist`, listé dans `pyproject.toml` car `.gitignore` ignore `dist/`) ;
- `GET desk/<fichier>` : un fichier du build et rien d'autre (`resolve()` puis `is_relative_to` : traversée et liens symboliques qui sortent sont refusés), types de média explicites, cache immuable pour `assets/` ;
- `GET desk/config.json` : `{"api_base", "login_url"}`, dérivé de l'URL de la requête, donc sans connaître `plugin_prefix` ;
- CSP stricte (`script-src 'self'`, `frame-ancestors 'none'`, `connect-src 'self'` plus l'origine de `login_url` si elle est absolue), `nosniff`, `no-referrer` ; 503 `desk_not_built` sans build.

Bloc `doctype.desk` du manifeste : `enabled`, `login_url` (xauth : `/app/auth/login` ; `null` : le Desk demande un jeton collé, pour le développement), `dist_dir`.

## 5. Le Desk (`ui/`)

**Pile.** Vue 3.5, Pinia 4, vue-router 5, **PrimeVue 4.5** et `@primeuix/themes` 2 (preset dérivé d'Aura), primeicons 7, Inter (`@fontsource-variable`), Vite 8, TypeScript 6, vitest 5 + happy-dom, pnpm 11. Build : `vue-tsc` puis `vite build` vers `src/xcore_doctype/ui/dist` (≈ 458 ko de JS principal, 105 ko gzip, plus 268 ko pour la liste : PrimeVue DataTable).

**Licence : piège évité.** PrimeVue **5**, `@primeuix/themes` **3** et `primeicons` **8** sont sous la « PrimeUI License » (clé de licence ; gratuite seulement sous 5 développeurs, 1 M$ de revenus et 10 employés ; pas de redistribution en bibliothèque). Les versions 4 / 2 / 7 sont MIT : `ui/package.json` les épingle par intervalle et CLAUDE.md interdit `pnpm update --latest`. Audit (`pnpm licenses list`) : 111 paquets MIT, 6 ISC, 4 Apache-2.0, 2 BSD, 5 BlueOak (`glob`, `minimatch`… permissive), 1 OFL-1.1 (Inter), et **`lightningcss` en MPL-2.0** (outil de build, rien n'en est embarqué). Frappe (MIT) a servi de modèle de comportement et d'ambiance ; **aucune ligne n'en est reprise**, donc pas de NOTICE à ajouter. Si du code de Frappe était un jour porté tel quel, sa notice de copyright devrait suivre.

**Structure.**
- `lib/` sans interface, testée seule : `expr.ts` (évaluateur), `form.ts` (valeurs initiales, états visible / obligatoire / lecture seule, validation miroir du serveur, charge utile, correspondance des erreurs 422), `filters.ts`, `format.ts`, `token.ts`, `api.ts` (normalise les **quatre** formes d'erreur que l'API ou xcore peuvent renvoyer), `config.ts`, `transport.ts`.
- `stores/` : `session` (jeton, connexion), `meta` (cache des définitions, une requête en vol par DocType), `links` (titres des liens).
- `views/` : Login, Home, ListView, FormView, NotFound ; `components/` : AppShell, FieldControl, FieldInput, LinkField, FilterBar. Textes dans `i18n.ts` (français).
- `desk.ts` assemble l'application à partir de ce que l'hôte fournit (`Transport` + `DeskConfig`). Rien n'est global.

**Décisions.**
- **Transport remplaçable** : toute requête réseau passe par l'interface `Transport` (`FetchTransport` dans le navigateur). Un portage Tauri fournira un transport adossé à des commandes Rust ; `window.__DESK_CONFIG__` permet à l'hôte d'imposer l'URL de l'API. Jetons Bearer, pas de cookies.
- **Routage par hash** (`#/app/Customer/abc`) : aucune règle de réécriture côté serveur, et le même bundle marchera sous le protocole d'une application.
- **Jeton dans `sessionStorage`** : il reste dans l'onglet et disparaît à sa fermeture. Le tenant annoncé dans `X-Tenant-ID` est lu dans les revendications du jeton (le serveur exige qu'il leur corresponde, D17) ; rien lu côté navigateur ne donne un droit. Un 401 déconnecte, vide les caches et revient à l'écran de connexion avec un message. La destination demandée est retenue (`?next=`), restreinte à un chemin interne du Desk.
- **Connexion xauth** : `POST login_url {email, password, tenant_id?}` ; si le compte a plusieurs organisations la réponse les liste et le Desk redemande avec `tenant_id`. La double authentification et le compte sans organisation sont refusés avec un message (non gérés). Le jeton de rafraîchissement n'est pas utilisé : à l'expiration, on se reconnecte.
- **Thème** `ui/src/theme/frappe.ts` : gris neutres, bouton principal sombre, champs en aplat gris sans bordure, coins de 8 px, 14 px, Inter. Un seul thème clair (`darkModeSelector: 'none'`). Les droits (`rights`) masquent « Nouveau », « Supprimer » et la sélection de lignes ; le serveur reste seul juge.
- **Formulaire** : sections et colonnes de `layout` ; une section dont tous les champs sont masqués disparaît ; champ en lecture seule affiché comme du texte ; Link affiché par son **titre** mais stocké par son **nom** ; erreurs 422 rattachées aux champs, refus de hook affiché tel quel ; Ctrl+S ; confirmation en quittant avec des modifications ; 409 : proposition de recharger.
- **Dates** : `AAAA-MM-JJ` canonique, converties par jour **local** (jamais `toISOString()`, qui recule d'un jour à l'est de Greenwich).

## 6. Défauts trouvés pendant la phase

1. **Deux tests creux.** `host.plugin_routes()` et `test_c5_every_route_requires_identity` énuméraient `app.router.routes` : avec FastAPI 0.14x ces routes n'ont pas de `.path` (ce sont des `_IncludedRouter`), la boucle ne vérifiait rien. Réécrits sur OpenAPI avec une garde « liste non vide » ; une mutation (route sans identité) est maintenant détectée.
2. **Le champ Nombre de PrimeVue n'émet `update:modelValue` qu'au blur, à Tab ou à Entrée.** Pendant la frappe il n'émet que `input`. Sans correction, une condition `depends_on` sur un nombre ne réagissait pas à la frappe et **Ctrl+S enregistrait l'ancienne valeur**. Trouvé par le test navigateur (le champ « Validateur » n'apparaissait jamais) ; reproduit par `tests/field-input.spec.ts` (qui échoue sans le correctif), corrigé dans `FieldInput.vue` et `FilterBar.vue`.
3. **`integration.yaml` déclarait `tenancy.isolate_db: true`**, contre CLAUDE.md et son propre commentaire : un hôte lancé à la main faisait diverger HTTP et IPC. Corrigé à `false` (les tests posaient déjà leur propre valeur, c'est pourquoi rien n'échouait).
4. **Une mutation sur les dates passait en UTC.** Les dates locales ne se distinguent des dates UTC que loin de Greenwich : vitest tourne maintenant en `Pacific/Auckland` (`vite.config.ts`), et la mutation est détectée.
5. **Le scénario navigateur était fragile**, pas l'application : une expression `/#\/app\/Customer\/[^/]+$/` reconnaissait aussi `/new` (le nom du client devenait « new ») ; une première exécution interrompue laissait des pays en double. Corrigés par une assertion négative et, surtout, un **tenant neuf par exécution** (base vierge dans un dossier temporaire).
6. **Date compacte** : `date.fromisoformat("20261017")` est valide pour Python ; l'écriture n'accepte que la forme canonique, défauts compris (le tri textuel d'une colonne Date doit rester chronologique).

## 7. Constats sur xcore, xauth et PrimeVue (pour la v4)

- **Contrat de connexion d'xauth** (lu dans `orchestra-backend/app/auth`, rien modifié) : `POST /app/auth/login {email, password, tenant_id?}` renvoie `{access_token, refresh_token, tenant_id, mfa_required, mfa_token, onboarding_required, tenants[]}`. Sans `tenant_id` et avec plusieurs appartenances, `tenant_id` est nul et `tenants` liste les choix. Le limiteur de débit répond 429 (5 essais par minute).
- Les routes d'un plugin montées par `get_router()` contournent le pipeline d'xcore : la dépendance d'identité doit être posée explicitement, et la partie publique (le Desk) dans un routeur séparé.
- PrimeVue 4 : le champ Nombre ne synchronise pas son modèle pendant la frappe (§6.2) ; la locale doit être fournie par l'application (`ui/src/theme/locale.ts`, français écrit à la main).
- Un test d'intégrité de l'interface a besoin d'un **vrai** navigateur : happy-dom ne joue ni le focus, ni les overlays, ni les frappes de PrimeVue ; le défaut §6.2 n'y apparaissait pas.

## 8. Non fait et non vérifié

- Pas de **mode sombre**, pas de rafraîchissement du jeton, pas de double authentification, pas de changement d'organisation en cours de session, pas de déconnexion côté serveur (le jeton n'est qu'oublié par l'onglet).
- Chromium seulement ; pas d'essai Firefox ni WebKit. Pas d'audit d'accessibilité (on s'appuie sur l'ARIA de PrimeVue ; navigation au clavier non vérifiée hors Ctrl+S, Entrée et Échap). Mobile : sous 800 px la barre latérale disparaît et rien ne la remplace.
- Liste : pas de choix des colonnes, pas de filtres enregistrés, tri sur une colonne, sélection limitée à la page ; la suppression groupée envoie une requête par document (échec partiel signalé). Pas de virtualisation (le serveur plafonne une page à 200).
- Les contrôles Link ne sont pas faits côté serveur (phase 3) : une valeur inexistante s'affiche par son nom. Le titre d'un lien vers un DocType illisible pour l'utilisateur retombe sur le nom.
- Pas de tests de composants pour ListView et FormView : ils sont couverts par le scénario navigateur (un seul DocType, SQLite). Non essayé dans le scénario : champ Date ou Link portant un `depends_on`, droits `restricted` / `none` (testés seulement sur la logique et l'API), plusieurs onglets.
- Le portage **Tauri** n'est pas tenté : l'interface `Transport` et la configuration injectable sont prêtes, rien de plus.
- Hors périmètre de la phase (phase 3 et au-delà) : séries de nommage, tables enfants, permissions par champ, hooks de suppression, historique, `match` (gelé).
