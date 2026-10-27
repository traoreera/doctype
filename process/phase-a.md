# Phase A : documents soumettables et module Stock (quantités)

Première phase du palier 40 % (`process/palier40.md`). Critère de sortie du contrat :

> Réception, sortie et transfert soumis, annulés par mouvement inverse ; stock négatif refusé en concurrence sur les deux bases.

Il est atteint et prouvé par des tests sur SQLite **et** PostgreSQL (voir §5). Ce document dit ce qui a été construit, les décisions prises, ce qui diffère du contrat, et les limites.

## 1. Ce qui est livré

**Dans le moteur (`src/xcore_doctype/`)**

- **Documents soumettables** (`submittable: true`) : `docstatus` 0 brouillon → 1 soumis → 2 annulé, `submit`, `cancel`, `amend`. Un document soumis ou annulé est **figé**. Routes `POST /doc/{dt}/{name}/submit|cancel|amend` et actions `doc.submit|cancel|amend`.
- **Droits `submit` et `cancel`** : grants distincts (`<module>:<doctype>:submit|cancel`), déclarés à auth seulement pour un DocType soumettable. `write` ne les implique pas.
- **Hooks `on_submit` / `on_cancel`** dans la transaction de la transition, avec `event.tx` : la seule façon pour un hook d'écrire d'autres documents (créer, créer-si-absent, modifier avec contrôle de version, lire avec verrou).
- **Type `Decimal`** : quantités et montants exacts, stockés en chaîne canonique (`"12.500"`), jamais en flottant. Filtres et tris en `NUMERIC`.
- **Mise à niveau à chaud** des colonnes ajoutées (`docstatus`, `amended_from`) sur une base créée avant cette phase.
- **Service `doctype_documents`** : lecture seule, en contexte système, pour les contrôles d'un plugin de confiance.
- **`xcore-doctype check --with`** : valide les DocTypes d'un plugin qui cite ceux d'un plugin dont il dépend.

**Dans le Desk (`ui/`)** : pastille d'état, boutons Soumettre / Annuler le document / Amender (avec confirmation), formulaire figé après soumission, refus du métier signalé sur la ligne en cause, filtre par statut dans les listes, champ `Decimal` en texte de bout en bout, historique des soumissions et annulations.

**Le plugin `app/stock`** : `Item`, `Warehouse`, `Stock Entry` (+ `Stock Entry Line`), `Stock Ledger Entry`, `Bin`. `sales` en dépend maintenant (ses lignes de facture citent l'article du stock). L'action `balances.verify` recalcule les soldes depuis le journal.

## 2. Comment ça tient

### Une transition est une seule transaction

1. droit (`submit` / `cancel`) sur la valeur actuelle du document, état de départ, version, hooks disponibles ;
2. `UPDATE … WHERE version = ? AND docstatus = ?` **en premier** (`Query.transition`) ;
3. entrée d'historique, puis les hooks `on_submit` / `on_cancel` ;
4. commit, puis l'événement `doctype.<nom>.after_submit|after_cancel` (sans contenu).

Conséquences, toutes testées :

- **Idempotence.** Rejouée ou simultanée, une soumission n'en passe qu'une : les autres reçoivent 409 `invalid_docstatus` et leurs hooks ne tournent pas. Le journal ajoute une seconde barrière : le nom d'une écriture est déterministe (`<mouvement>.<ligne>.in|out`), le poster deux fois échoue sur l'unicité.
- **Atomicité.** Un refus d'un hook (stock insuffisant…) défait l'état, les écritures du hook et leur historique.
- **SQLite sans verrou de ligne.** Le changement d'état est la première écriture de la transaction : il prend le verrou de la base avant que les hooks lisent quoi que ce soit. Un test (hook qui lit un compteur puis l'écrit, 8 soumissions simultanées) échoue si l'état est écrit après les hooks.
- **PostgreSQL.** `tx.get(..., lock=True)` pose `FOR UPDATE`. Les verrous d'un mouvement sont pris dans un ordre fixe (article, entrepôt) : deux transferts qui se croisent ne se bloquent pas.

### Le stock

- Soumettre écrit une ligne de journal par entrée ou sortie, met à jour le solde (`Bin`) et **refuse un solde négatif**. Annuler écrit les lignes inverses (suffixe `.rev`), le journal n'est jamais modifié ni supprimé.
- Un transfert est une sortie et une entrée dans la même transaction : jamais la moitié.
- Journal et soldes sont fermés à tout écrivain humain (`write`, `create`, `delete` à `[]`) : seuls les hooks y écrivent. Un magasinier peut soumettre sans avoir le droit de lire les articles, le journal ou les soldes.
- Un article ou un entrepôt déjà utilisé ne peut plus être supprimé (les liens du journal et des soldes le protègent).
- Le solde est maintenu à chaque écriture, jamais recalculé à la lecture ; `balances.verify` le recalcule pour vérifier et **signale** l'écart (ne le corrige pas).

## 3. Décisions (process/palier40.md §8)

| Décision | Prise | Pourquoi |
|---|---|---|
| 1. Valorisation | **Non** : des quantités seulement | Le contrat le dit (§4.4) : elle touche à la comptabilité. |
| 2. Mouvements antidatés | **Interdits** : un mouvement n'est daté ni dans le futur ni avant le dernier mouvement déjà passé sur un de ses couples (article, entrepôt). L'annulation est une écriture du jour et n'est jamais refusée pour sa date. | Le solde à une date passée reste la somme du journal jusqu'à cette date. Plus simple et réversible : lever la règle ne change aucune donnée. |
| 3. Extensibilité | Non concernée | Aucun plugin n'ajoute de champ à un DocType qu'il ne possède pas. |
| 4. Impression, 5. Ordre D-F | Non concernées | Phases F et suivantes. |

La 2 est une décision **par défaut, à valider** : elle est surtout une règle de `app/stock/src/ledger.py`.

## 4. Écarts avec le contrat et choix de conception

- **Le journal vit dans la table générique JSON** (le contrat §4.3 prévoit une table dédiée : c'est la phase B). Conséquence mesurable : les agrégats se font page à page (`balances.verify` lit par pages de 200). Pas de benchmark dans cette phase.
- **`Decimal` en chaîne, pas en nombre JSON.** Un nombre JSON serait lu comme un flottant par tout client. Coût : le JSON de l'API porte `"12.500"`, et les filtres/tris convertissent en `NUMERIC` (exact sur PostgreSQL ; SQLite compare en double, exact jusqu'à ~15 chiffres).
- **`docstatus` est une colonne**, pas une clé de `data` : un `UPDATE` de modification porte `docstatus = 0` dans son `WHERE`.
- **`Item` a quitté `sales` pour `stock`** (un article appartient au stock). `sales` déclare `requires: [xcore-doctype, stock]`, ce qui montre un Link entre deux plugins. Les grants `sales:item:*` n'existent plus.
- **Un hook ne peut pas écrire un DocType qui déclare des hooks** (`hook_not_supported`) : ils ne tourneraient pas, et on ne veut pas écrire sans la validation promise.
- **`on_submit` / `on_cancel` n'acceptent pas `action:`** (un plugin sandboxé est appelé hors transaction, D21).
- **Un DocType soumettable garde `track_changes`** : qui a soumis et annulé est ce qui rend un document figé opposable.
- **Noms de DocType avec espaces** (`Stock Entry`) : grants `stock:stock_entry:submit`, URL encodées. Vérifié de bout en bout.
- **Quantités dans les messages en français** (`12,500`), stockées en `12.500`.

## 5. Preuves

Dernière exécution complète : **1851 tests pytest réussis, 6 ignorés** (SQLite et PostgreSQL 18, navigateur et installation depuis le wheel compris), **415 tests vitest**, `vue-tsc` sans erreur. Les 6 ignorés sont des cas sans objet sur une base (verrous de ligne sur SQLite, une valeur « sans filtre »). Avant la phase : 1542 pytest, 346 vitest. Chaque garantie ci-dessous a été vérifiée par mutation : on casse la ligne qui la porte, le test doit échouer.

| Garantie | Où | Mutation vérifiée |
|---|---|---|
| Figé après soumission | `test_submit.py`, `test_query_leak.py` (`transition`) | oui |
| Soumission unique (rejouée, simultanée) | `test_submit.py` | oui |
| Hooks dans la transaction (refus défait tout) | `test_submit.py` | oui |
| Pas de mise à jour perdue sur SQLite | `test_submit.py::…read_then_write…` | oui (état écrit après les hooks) |
| Droits `submit`/`cancel` distincts de `write` | `test_submit.py` | oui |
| Stock jamais négatif en concurrence (SQLite, PostgreSQL, HTTP + PostgreSQL) | `test_stock.py::TestConcurrency`, `test_stock_plugin.py` | oui (sans verrou) |
| Pas d'interblocage des transferts croisés | `test_stock.py` | oui (verrous non triés) |
| Premières réceptions simultanées | `test_stock.py` | oui (création non atomique) |
| Annulation par mouvement inverse, sans jamais passer sous zéro | `test_stock.py::TestCancellation` | oui |
| Pas d'antidatage | `test_stock.py::TestNoBackdating` | oui |
| Quantités exactes | `test_decimals.py`, vecteurs partagés Python/TypeScript | oui |
| Base d'avant la phase mise à niveau | `test_system_tables_upgrade.py` | oui |
| Desk : soumettre, figer, refus sur la ligne, annuler, amender | `formview.spec.ts`, `docstatus.spec.ts` | oui |
| Parcours complet dans Chromium | `tests/e2e` | n/a |
| Depuis le wheel | `tests/delivery` | n/a |

## 6. Limites connues

- **Table générique** : le journal grossit dans `xdt_document` ; filtres et tris sur de gros volumes ne sont pas mesurés (phase B).
- **SQLite sérialise** les soumissions (verrou de la base, délai de 5 s par défaut avant « database is locked ») ; PostgreSQL ne sérialise que par solde.
- **Pas de règle d'état entre DocTypes** : on peut lier un document à un document annulé ; annuler un document cité par un autre document soumis n'est pas refusé. Aucun cas d'usage ne l'a demandé dans cette phase.
- **Pas de champs modifiables après soumission** (`allow_on_submit`).
- **`balances.verify`** est un appel d'opérateur (clé du plugin), pas encore une page du Desk ; il lit par pages sur la table générique.
- **Une écriture de hook ne déclenche pas les hooks du DocType écrit** (voir §4).
- **Affichage du Desk** : un `Decimal` reste un texte de la frappe à l'envoi ; seul l'affichage formaté en français (« 1 234,500 ») passe par un nombre JavaScript, donc au-delà de 15 chiffres le Desk montre le texte exact, sans mise en forme.
- Non exécutés : la CI GitHub (toujours jamais lancée), PostgreSQL derrière pgbouncer, la RLS avec les nouvelles colonnes sur de gros volumes (les tests RLS passent).

## 7. Pour la phase B

- Promouvoir le journal et les soldes en tables dédiées (`NUMERIC`, index sur article/entrepôt/date) ; le contrat de `HookTransaction` ne change pas.
- Mesurer avant : benchmark du journal sur la table générique (c'est le critère de la phase).
- `schema_version` devient une migration : le mécanisme de rattrapage de colonnes de `db/access.py` n'est qu'un début (colonnes ajoutées, jamais modifiées ni retirées).
