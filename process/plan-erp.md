# Plan : de `doctypes/` à un ERP qui fonctionne

**Plan du 29 oct. 2026.** Il part d'un audit mesuré du même jour : `doctypes/` (704 DocTypes, 22 modules) se
charge sans erreur, mais **aucune pièce n'écrit rien** (0 hook sur 704), et quatre manques du MOTEUR empêchent
d'écrire proprement les hooks d'un ERP. Ce plan dit dans quel ordre on les comble, puis dans quel ordre on écrit
les modules. Il complète `process/erp-doctypes.md` (l'état du jeu) et `process/programme.md` §3.7 (l'ordre des
marches 1a/1b/1c, déjà tenu : 1a et 1b sont faites).

## 0. Le constat, en chiffres

| Mesuré le 29 oct. | Valeur |
|---|---|
| DocTypes qui se chargent (`test_erp_doctypes`, `test_peer_modules`) | 704/704, un plugin par module possible |
| `hooks:` dans les définitions | **0** — les 136 pièces soumises n'écrivent ni grand livre ni stock |
| `workflow:` | 0 |
| fichiers avec `depends_on` ou `fetch_from` | 4 |
| champs `Currency` sans devise | **911/911** (le moteur n'accepte qu'un code ISO fixe dans `options`) |
| ce qu'un hook peut faire dans sa transaction | `get` par NOM, `create`, `ensure`, `update` — aucune recherche, aucune somme |
| ce qu'un plugin peut ÉCRIRE hors d'un hook | **rien** (`doctype_documents` lit seulement ; une action IPC exige le principal de la requête xcore) |
| annuler une pièce encore citée par une pièce soumise | **permis** (une facture payée s'annule) |
| lecture hors transaction | 200 documents par page, pas d'agrégat dans le lecteur des plugins |
| lignes par table | 500 au plus |

Un détail qui change la forme du lot M2 : `GL Entry` et `Stock Ledger Entry` sont eux-mêmes **soumis** et citent
leur pièce (`voucher_type`/`voucher_no`, DynamicLink). Un refus naïf « une pièce citée ne s'annule pas » rendrait
toute pièce comptabilisée inannulable.

## 1. Lot M — le moteur (avant tout hook)

Quatre changements, dans cet ordre : M1 et M2 décident de la FORME des hooks d'`accounts` et de `stock` ; les
écrire avant obligerait à les réécrire. Chacun suit la règle du dépôt : une règle nouvelle a un test qui échoue
sans elle (mutation vérifiée), une méthode publique de `Query` a son scénario de fuite (`test_query_leak.py`).

### M1. Chercher et sommer dans la transaction d'un hook — taille M

`event.tx` gagne :

- `find(doctype, filters, *, order_by=None, limit=500, lock=False)` → la liste des documents qui répondent aux
  filtres, lus DANS la transaction (un hook voit ce qu'il vient d'écrire) ;
- `sum(doctype, field, filters)` → un `Decimal` exact (un Decimal ou Currency seulement ; un Number refusé).

Mêmes filtres typés que la liste (`coerce_filter`), même tenant (`Query._scope`), périmètre système (code de
confiance, comme le reste de `tx`). `lock=True` pose `FOR UPDATE` sur PostgreSQL ; SQLite n'a pas de verrou de
ligne et compte, comme aujourd'hui, sur la première écriture de la transaction.

Ce que ça débloque : annuler une pièce en retrouvant SES écritures (`find("GL Entry", {"voucher_no": …})`), le
reste à payer d'une facture, le solde d'un compte pour un contrôle de crédit, la file de valorisation du stock.
Le contournement actuel du plugin `stock` (des noms prévisibles `<pièce>.<ligne>.in`) reste valable là où il suffit.

**Preuves** : `find` égale `list` sur les mêmes filtres ; un hook voit sa propre écriture ; rien ne fuit d'un
autre tenant (scénario de fuite) ; `sum` exact sur 0,1 + 0,2 ; un refus du hook annule tout.

### M2. Refuser d'annuler une pièce encore citée — taille M

`cancel` refuse (409 `document_cited`, qui nomme les TYPES qui citent, jamais les documents) quand une pièce
**soumise** du tenant cite celle qu'on annule par un Link ou un DynamicLink — sauf les types que la pièce déclare
ignorer :

```yaml
# accounts/sales_invoice.yaml
cancel_ignores: [GL Entry, Payment Ledger Entry, Stock Ledger Entry]   # ses propres écritures, que ses hooks annulent
```

C'est le `ignore_linked_doctypes` d'ERPNext, mais vérifié AU CHARGEMENT (types connus, soumissibles). Les
références se cherchent sans le périmètre de l'appelant (comme `document_in_use` à la suppression), après un
`FOR UPDATE` sur la pièce (la même parade que la suppression contre la course). Le sens inverse — soumettre un
paiement pendant qu'on annule sa facture — est déjà tenu par `target_status: submitted` sur les liens entre pièces.

**Preuves** : facture payée → annulation refusée ; paiement annulé → la facture s'annule ; ses `GL Entry` ne la
bloquent pas ; un type inconnu dans `cancel_ignores` est une erreur de chargement ; course sur PostgreSQL.

### M3. La devise d'un montant vient d'un champ — taille S

`currency_from: currency` (un champ du même conteneur) ou `currency_from: company.default_currency` (un saut par
un Link), sur le modèle exact de `unit_from` : AFFICHAGE seulement, vérifié au chargement
(`registry._check_units` en est le patron), lu par le Desk (`formatCell`, `CellValue`) et par l'impression
(`printing._cell`). `options` garde son sens (une devise fixe) ; les deux ensemble sont refusés.

Puis la transformation **T16** dans `doctypes/` : poser `currency_from` sur les 911 champs, par TABLE vérifiée
pièce par pièce comme T15 (`currency` pour les montants de la pièce, `company.default_currency` pour les `base_*`),
jamais par une règle sur les noms. Invariant ajouté à `test_erp_doctypes.py` : aucun `Currency` sans devise.

### M4. Une porte d'écriture pour les plugins — taille M

Un service exporté `doctype_writer`, deux portes et pas une de plus :

- `as_request(request)` : depuis une route HTTP du plugin, le contexte de l'UTILISATEUR (même vérification que
  `http_context`) ; ses droits s'appliquent comme sur l'API. C'est la porte des BOUTONS serveur (« Créer le
  paiement », « Rapprocher », « Récupérer les articles ») ;
- `system(tenant, reason)` : pour une tâche planifiée (amortissements, factures récurrentes, réapprovisionnement),
  périmètre système, historisé sous un acteur nommé par la raison (D20), **limité aux types du module du plugin**
  (son propre `module`) — une tâche d'`assets` qui doit comptabiliser le fait en SOUMETTANT sa pièce, dont les
  hooks écrivent le grand livre par `tx`.

Méthodes : `create`, `update`, `submit`, `cancel`, `delete`, exactement celles de `DocumentService` (aucun chemin
d'écriture parallèle). Les tâches elles-mêmes passent par l'ordonnanceur d'xcore (`xcdk`, importé `sdk`) :
attention, son backend par défaut `memory` n'a AUCUN verrou, chaque processus uvicorn lancerait la tâche — en
production il faut `backend: redis` ou un seul processus. Ce sera écrit dans la doc du service.

**Preuves** : un bouton respecte les droits de l'utilisateur (403/404 comme l'API) ; la porte système refuse un
type d'un autre module ; une tâche lancée deux fois dans la même transaction n'écrit pas deux fois (nom prévisible
ou `ensure`).

## 2. Lot P — les plugins de module

Après le lot M. Chaque module devient un plugin de confiance : `load_dir(nom, dossier, peers=True)`, un seul
`rbac.declare` de ses grants, son espace, ses hooks enregistrés par NOM. Rien de cela ne touche au moteur.

| Étape | Contenu | Pourquoi dans cet ordre |
|---|---|---|
| **P0** | Le gabarit d'un plugin de module, et `xcore-doctype new-module <nom>` qui le pose | 22 plugins de la même forme : on l'écrit une fois |
| **P1** | `setup` + `accounts` : Société, exercice, plan comptable (arbre), centres de coût ; comptabilisation de l'Écriture de journal, des Factures de vente et d'achat, du Paiement ; extourne à l'annulation ; reste à payer mis à jour par `tx.update` (`allow_on_submit`) | tout le reste comptabilise par lui |
| **P2** | `stock` : grand livre du stock, `Bin`, valorisation, Bon de livraison, Réception, Mouvement de stock, Inventaire | s'appuie sur P1 pour la valeur comptable du stock |
| **P3** | `selling` + `buying` : Devis → Commande → Livraison → Facture, Commande fournisseur → Réception → Facture ; pourcentages livré/facturé tenus par les pièces aval | la chaîne commerciale, faite de mappings (`created_from`) qui existent |
| **P4** | `hr`, `payroll`, `assets`, `manufacturing`, `projects`, `crm`, `support`, `quality_management` | chacun comptabilise par P1/P2 |
| **P5** | Plomberie : `regional`, `communication`, `edi`, `utilities`, `bulk_transaction`, `telephony`, `maintenance`, `subcontracting`, `erpnext_integrations` | contribuent à l'espace d'un autre (D27), peu de logique |

Pour chaque module, la même preuve : un scénario métier de bout en bout sur la VRAIE définition (soumettre,
lire les écritures produites, annuler, vérifier l'extourne), et un invariant qui ne dépend d'aucun scénario —
pour `accounts` : la balance générale somme à zéro ; pour `stock` : le solde recalculé du grand livre égale le
`Bin` (le `balances.verify` du plugin d'essai en est le patron).

En parallèle des modules, sans ordre imposé : les **workflows** d'approbation là où un circuit existe (achats,
notes de frais, congés), et l'enrichissement des formulaires (`depends_on`, `fetch_from`) — du YAML seulement.

## 3. Lot B — plus tard, quand le volume le demande

Ne bloque pas le démarrage ; à mesurer sur un jeu de données réaliste avant de trancher.

- **Tables dédiées et index** pour `GL Entry`, `Stock Ledger Entry`, `Payment Ledger Entry` (la phase B de
  `palier40.md`) : aujourd'hui dans la table JSON générique, ce qui coûtera sur les états à des millions de lignes.
- **États** : agrégats dans le lecteur exporté aux plugins (`doctype_documents`), et un filtre « ce nœud et ses
  descendants » pour les arbres (un compte de regroupement et ses sous-comptes).
- **Le plafond de 500 lignes par table** : un inventaire ou un rapprochement le dépasse ; à rendre réglable par
  type plutôt qu'à relever pour tous.

## 4. Les décisions qu'il faut prendre avant P1

1. **Où vivent les plugins de module ?** Ce sont des PRODUITS, pas des jeux d'essai (la règle du 19 oct. interdit
   d'en faire dans `app/` ou `tests/support/plugins`). Proposition : un dossier `erp/<module>/` à la racine,
   `doctypes/<module>/` déplacé dedans.
2. **La méthode de valorisation du stock** : moyenne pondérée (simple, une valeur par `Bin`) ou FIFO (une file par
   article et dépôt). ERPNext offre les deux ; la décision 1 de `palier40.md` avait écarté toute valorisation.
3. **Le périmètre de la première version** : `setup` + `accounts` + `stock` + `selling` + `buying` (P1 à P3)
   suffit à une PME qui vend et achète ; le reste suit.
4. **La porte système de M4 limitée au module du plugin** : c'est la proposition (une tâche ne contourne pas les
   règles d'un autre module) ; à confirmer.

## 5. Ce qui n'est pas dans ce plan

La traduction, les clés d'API, la corbeille et l'authentification forte dans le Desk restent dans
`process/programme.md` : elles ne conditionnent pas l'ERP.
