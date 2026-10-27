# Consolidation avant le palier 80 % + suite (moteurs)

Décisions de l'utilisateur (20 oct. 2026) : le code de consolidation **ne dépend pas de `app/sales`, `app/stock`, `app/hr`** (fixtures, elles seront supprimées) ; les tests deviennent indépendantes de `app/` par des fixtures dans `tests/support/` ; le **moteur de stock** et le futur **moteur de comptabilité** vivent dans **leurs propres dépôts**, pas ici. Ce dépôt garde : le moteur DocType (`src/xcore_doctype/`), le Desk (`ui/`), et les plugins produit `auth` (xauth), `iam`, `system` (`app/auth`, `app/iam`, `app/system` + `system_plugin_dir` embarqué dans le wheel).

Règle transverse : **aucune ligne de consolidation dans `app/sales`, `app/stock`, `app/hr`**. Un besoin moteur de ces fixtures (ledger, règles de stock) se généralise dans `src/xcore_doctype/` ou se migre dans `system_plugin_dir/` s'il est produit.

---

## Phase C0 — Fondation (hygiène)

1. **Figer la base verte** : `make check` puis `make install-check` (et `make e2e` si Chromium dispo) ; noter les chiffres exacts.
2. **Rejets de promesse vitest** (`process/phase-h2.md` §3) : isoler `ui/tests/collab.spec.ts`, établir la cause (montage d'une carte de pièces jointe affichée après la fin du test), corriger. Une suite qui crie dans le vide masque les régressions.
3. **Remettre `CLAUDE.md` à jour** : la liste « Not implemented today » est fausse (C, D, Single/tree/search faits). Réécrire l'état exact ; ajouter la règle « `app/sales|stock|hr` sont des fixtures amenées à disparaître ».

**Critère** : suite verte sans bruit ; `CLAUDE.md` décrit le dépôt tel qu'il est.

✅ **Faite (20 oct. 2026).** Base verte notée : Python 2629 réussis / 1376 ignorés (SQLite), vitest 1232 réussis (55 fichiers), livraison 9/9 (`install-check`). Les rejets vitest de `phase-h2` §3 **ne se reproduisent plus** (deux suites complètes propres : le `unmountAll` + `flushPromises` de la revue UX les ont réglés) — rien à corriger. `CLAUDE.md` réécrit (état exact, règle « jamais exiger `app/` », chemins `tests/support/plugins/…`, sauts propres sans `app/auth`) ; `README.md` et le docstring de `cli.py` repointent aussi. Alerte connue confirmée : le démarrage du worker sandboxé (ping fixe de 5 s) échoue parfois sous charge — `_ensure_loaded` réessaie déjà 5 fois ; sur un essai complet avec `app/` masqué, 3 tests `test_hooks.py` ont flaké ainsi puis sont tous passés au rejeu (avec `app/` encore masqué).

## Phase C1 — Indépendance de `app/` (fixtures → `tests/support/`)

1. **Inventaire** de chaque référence à `app/sales`, `app/stock`, `app/hr` : `tests/integration/conftest.py` (`boot_host` lie `ROOT/app/sales/doctypes`), `tests/integration/test_stock_plugin.py`, `test_permissions.py`, `tests/unit/test_stock.py`, `test_cli.py`, `test_context.py`, les seeds des scénarios navigateur (`ui/e2e/*.mjs`), `makefile`, docs.
2. **Déplacer** les trois fixtures en `tests/support/plugins/{sales,stock,hr}/` (manifeste + doctypes, noms de plugin = noms de module, inchangés côté moteur) ; `boot_host` et les seeds e2e pointent vers ce dossier ; `integration.yaml` garde `directory: ./app` pour le dev (où `app/` ne contiendra plus QUE `auth`, `iam`, `system`).
3. **Vérifier** que la suppression mentale de `app/{sales,stock,hr}` ne casse rien : `make check`, `make e2e`, `make install-check`.

**Critère** : `app/` = produit seul (auth, iam, system) ; toute la suite et les scénarios navigateur passent sans les fixtures d'origine. `app/{sales,stock,hr}` peut être supprimé du dépôt dans le commit de fin (ou laissé un temps, mais plus référencé).

✅ **Faite (20 oct. 2026), plus large que prévu : `iam` démenagé aussi** (`tests/support/plugins/{sales,stock,hr,iam}`, copie fidèle sans `__pycache__`/`data`). `tests/support/__init__.py` exporte `FIXTURES` et `XAUTH_DIR` (`app/auth`, présent seulement sur le poste de dev) ; `boot_host` copie le vrai xauth depuis `XAUTH_DIR` et **saute avec raison** s'il est absent (`test_xauth_real`, `test_username`, `test_iam`, `test_hr_access`). Repointés : conftest, `test_doctype_channel`, `test_stock_plugin`, `test_iam`, `test_hr_access`, `test_permissions` (docstring), `test_system_plugin` (lien dev conditionnel), `test_dashboards`, `test_workspaces`, `test_cli`, `test_stock`, e2e (`test_desk_browser` écrit son `main.py` au lieu de copier le gitignoré `main.py` racine), livraison, `ui/e2e/README.md`. **Épreuve** : suite complète avec `app/{sales,stock,hr,iam,auth}` masqués → 2575 réussis / 1427 ignorés (+51 sauts d'xauth propres), seuls les 3 flakés sandbox ci-dessus ; navigateur e2e passé masqué ; rien n'ignore les dossiers dans le git.

## Phase C2 — Intégrité du moteur (données)

1. **Règles d'état sur les liens** : un Link (ou DynamicLink) vers un document annulé est aujourd'hui accepté. Property de champ `target_status` (absente = comportement actuel) : `not_cancelled` ou liste d'états exigés ; vérifiée dans `DocumentService._check_links` (existence + lisibilité + état) ; le Desk l'affiche (aperçu de lien, badge d'état). Nouvelle décision dans `process/doctype.md`, `schema.FieldDef`.
2. **`unique` rétroactif** (`process/phase-h2.md` §3) : contrôle au chargement (`GROUP BY … HAVING count(*) > 1` par champ unique, par type, sans détail de tenant dans l'erreur) ; conflit = erreur de chargement explicite. Plus une action opérateur `xcore-doctype check-unique`.
3. **Rétention d'historique** (`versions.py`) : `doctype.max_versions` (défaut 25), purge des versions au-delà dans LA transaction du document ; action opérateur `doc.history.purge` (contexte système nommé) pour l'âge. SQLite + PostgreSQL.

**Critère** : tests unitaires pour chaque point ; `tests/unit/test_query_leak.py` étendu si méthode publique ajoutée.

## Phase C3 — Bugs Desk connus (`process/desk-revue.md` §5)

1. **Deux onglets qui se disputent le jeton de renouvellement** : jeton de renouvellement partagé en `localStorage` (l'accès reste en mémoire/sessionStorage), `BroadcastChannel` pour que l'onglet perdant réadopte le jeton rafraîchi ; single-flight par onglet conservé ; si le serveur répond « déjà usé », relecture du token partagé puis une seule reprise. Tests dans `ui/tests/session.spec.ts`.
2. **Accessibilité du tiroir mobile** (`AppShell.vue`) : piège de focus, retour du focus au bouton à la fermeture, `aria` de base ; contraste des pastilles (`--pill-*`) mesuré et corrigé si < 4.5:1.

**Critère** : typecheck + vitest ; scénario e2e téléphone au clavier ; deux onglets : renouvellement sans déconnexion.

## Phase C4 — Lignes ◐ → ✔ (`process/palier70.md` §2)

1. **Notifications par DocType + e-mail** : règles = DocType du plugin `system` (embarqué, jamais dans `app/`), événements (`after_create`, `after_update`, `on_submit`…), cible (rôle ou membres), message ; évaluation dans `notifier.py`, politique fail-soft inchangée. E-mail : nouvel IPC `xauth.mail.send` dans `app/auth/src/ipc.py` (produit, reste ici) via l'extension `ext.email` en mode simulé, mêmes garde-fous que `xauth.notify`. `doctype.notifications.provider` inchangé.
2. **Écran de gestion des fichiers** : page Desk listant les `xdt_file` dont les documents citants sont lisibles par l'appelant ; suppression = détacher via l'écriture ordinaire (droits + hooks), jamais un `DELETE` sur `xdt_file`.
3. **Propriétés modifiées par site étendues** (`overrides.py`, `OverrideEditor.vue`) : `read_only`, `mandatory`, `default` en plus de libellé/description/masqué/disposition ; appliquées côté serveur à l'écriture (même porte que `builder`), pas seulement au Desk.

**Critère** : chaque ligne passe à ✔ honnête ou est **rabassée** dans l'inventaire (jamais gonflée) ; tests serveur + Desk pour chacune.

## Phase C5 — Preuves et décompte

`make check` complet, `make install-check`, `make e2e` ; recompter `process/palier70.md` §2 par script ; compte rendu `process/consolidation.md` (constats, décisions, limites, chiffres).

---

## Horizons suivants (dépôts distincts — pas dans ce plan détaillé)

1. **Moteur de stock, dépôt produit** : extraire `app/stock` (YAML + `ledger.py`) dans son dépôt ; il consomme le moteur DocType d'ici. L'**étape moteur préalable**, ICI, est la capacité « table dédiée + migration » (phase B de `palier40.md`) : le ledger stock cesse d'être logé dans `xdt_document` — c'est la seule pièce de ce chantier qui appartient à ce dépôt, à programmer **après** la consolidation.
2. **Moteur de comptabilité, dépôt produit — noyau d'abord** : plan comptable (DocTypes YAML sur le moteur), **journal d'écritures double entrée immuable** (une écriture = lignes débit/crédit équilibrées, jamais modifiée ni supprimée, corrections par écriture inverse — l'esprit du ledger stock), soldes par compte **vérifiables** (recomputation type `balances.verify`), pas de facturation, ni TVA, ni clôtures dans le noyau. Mêmes ingrédients que le stock : DocTypes déclaratifs + hooks de confiance + `tx.ensure`/`tx.get(lock=True)`.
3. **80 % du moteur** (K/L/M du `palier70.md` : étiquettes, partage, permissions utilisateur, champs personnalisés, corbeille, clés d'API…) reprend ensuite, dans ce dépôt, sur la base consolidée.

Ordre recommandé : C0 → C1 → C2 → C3 → C4 → C5, puis capacité « table dédiée » (ici) → extraction stock (dépôt dédié) → noyau compta (dépôt dédié) → phases K/L/M. Estimation consolidation : C0 2-3 j, C1 1-2 sem, C2 2-3 sem, C3 1 sem, C4 3-4 sem, C5 2-3 j.
