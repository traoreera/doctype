# Le jeu de DocTypes d'ERP (`doctypes/`)

**État au 27 oct. 2026.** 704 DocTypes répartis en 22 modules, migrés d'ERPNext (commit `b39a542`), puis réadaptés
pour être réellement chargeables par le moteur. Les 5 espaces de travail vivent à côté, dans `workspaces/`.

Ce fichier dit **comment ce jeu se charge**, **ce qui a été changé et pourquoi**, et **ce qui reste à faire**.
Il ne remplace pas `process/doctype.md` (le contrat de conception) : il décrit un jeu de définitions, pas le moteur.

## 1. Ce que vaut la migration

Mesuré en passant les fichiers dans le vrai registre, pas à l'œil :

| Contrôle du moteur | Résultat |
|---|---|
| `parse_definition` (Pydantic, `extra="forbid"`) | 721/721 |
| `load_dir` + `validate_links()` | passe |
| `Currency`/`Decimal` sans `decimals` | 0 — les 854 montants sont exacts, jamais des flottants |
| Champs `Table` cassés | 0 |
| Clés interdites sur les 310 enfants | 0 |
| Préfixes de série en collision | 0 |

Le piège le plus coûteux d'un ERP — l'argent en virgule flottante — avait été évité dès la migration.

## 2. Comment il se charge

**Un seul appel récursif.** `load_dir` descend dans les sous-dossiers, et son tri « enfants d'abord » porte sur
tout l'arbre. Ce tri suffit toujours : le schéma refuse les tables imbriquées, un enfant est donc une feuille du
graphe parent→enfant, qui ne peut pas avoir de cycle.

```yaml
# dans le plugin.yaml de l'opérateur
doctype:
  doctypes_dirs: ["./doctypes"]
```

C'est ce que fait `plugin.py` : `load_dir` par dossier listé, puis **un** `validate_links()` quand tout est là.
Deux conséquences qui décident de toute la forme :

- **`module` est libre** (`owner=None` : la règle « `module` == nom du plugin » ne s'applique qu'à un plugin
  propriétaire). Les 22 namespaces de grants cohabitent donc dans un seul chargement.
- **Les Link peuvent être mutuels.** `validate_links()` n'est appelé qu'une fois, tout est enregistré. C'est
  indispensable ici : les modules forment un **cycle de 15** (`accounts ↔ setup ↔ stock ↔ selling ↔ buying…`),
  et les relations Table en forment un de 5. Aucun ordre de chargement n'existe — `requires` ordonne, il ne
  dénoue pas un cycle.

**Mise à jour du 27 octobre : « un plugin par module » est possible** (`DocTypeChannel.load_dir(…, peers=True)`,
`tests/unit/test_peer_modules.py`). Chaque module devient un vrai plugin, propriétaire de ses DocTypes (`module` ==
nom du plugin, ses grants, son espace, ses hooks), chargé dans N'IMPORTE QUEL ordre :

- un Link vers un module frère pas encore chargé n'est pas une faute : le type est **incomplet** — il se lit, mais
  toute écriture est refusée (503 `doctype_incomplete`, qui nomme le frère attendu) — jusqu'à l'arrivée du frère ;
- un parent dont la table enfant vient d'un frère pas encore chargé **attend**, invisible, et s'enregistre à son
  arrivée ; ses grants sont déclarés dès le chargement de son plugin (`GrantCatalog` compte les types en attente) ;
- à l'arrivée d'un frère, les contrôles qui dépendent de lui (`fetch_from`, `link_filters`, `unit_from`,
  `target_status`, `created_from`) se refont chez ceux qui le citent ; une faute révélée ainsi **brise** le type qui
  cite (écritures refusées, journalisé), pas le chargement du frère ;
- un frère qui part remet en attente ce qui dépendait de ses tables ; il revient, tout revient.

Mesuré : les 23 dossiers de `doctypes/`, chargés comme 23 plugins en ordre alphabétique, inverse et mélangé, donnent
exactement le registre d'un chargement unique, chaque plugin ne déclarant que les grants de son namespace. Sans
`peers`, une cible inconnue reste une erreur de chargement (une faute de frappe se voit tout de suite) ; avec, elle
reste « incomplète » et le dit.

**Grants et espaces.** `rbac.declare` doit être émis par un plugin portant le nom du namespace, et
`load_workspaces` exige `name == nom du plugin`. Un module se dote donc d'un plugin minuscule, qui n'apporte
aucun DocType :

```python
# app/accounts/src/main.py  — ~10 lignes, même forme pour les 22 modules
async def on_load(self):
    grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)   # lus dans le YAML des DocTypes
    await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants})
    self.get_service("doctype_registry").load_workspaces(self.ctx.name, Path("workspaces"))
```

Sans ce plugin, les grants du module ne sont jamais déclarés à xauth : personne ne peut les recevoir, et le
module reste illisible pour tous (les droits refusent par défaut). **1461 grants** sont cités, sur 22 namespaces.

## 3. Ce qui a été changé, et pourquoi

Chaque ligne a été appliquée par programme sur la mesure, jamais à la devinette, et vérifiée par le moteur.

| # | Changement | Volume | Pourquoi |
|---|---|---|---|
| T1 | `Currency`, `Country` créés dans `setup` ; `Gender` déplacé de `hr` vers `setup` | 3 types | La migration citait les DocTypes des **jeux d'essai** (`geo`, `iam`, `sales`), qui ne sont pas du produit |
| T2 | `Devise` → `Currency`, `Pays` → `Country` | 111 champs | Un référentiel bilingue pour une seule notion ; les 2548 Link sont en anglais, le référentiel l'est aussi |
| T3 | Les champs `Link → Role` retirés | 19 champs | Le moteur **refuse les droits par nom de rôle** (texte libre par tenant) : un grant l'exprime, pas un champ |
| T4 | `Link → Member` → `Text` + `format: email` | 50 champs | L'identité est un UUID d'xauth ; c'est la convention des règles de notification (`recipient_field`) |
| T5 | Série de numérotation sur les soumettables | 136 types | Une facture sans numéro est un défaut fonctionnel. Préfixe = initiales (`JE`, `WO`, `SI`), zéro collision |
| T6 | `naming: {field: …}` là où la clé métier est déclarée `unique` | 8 types | Le document porte son code (`EUR`, `CI`) plutôt qu'un hash |
| T7 | L'auto-lien de parenté n'est plus obligatoire | 2 champs | `Account.parent_account` requis rendait **le premier compte incréable** |
| T8 | `layout` : colonnes remises en liste de listes, champs retirés élagués | 11 fichiers | `hr/gender.yaml` était le seul fichier que le schéma refusait |
| T9 | Les champs `amended_from` retirés | 138 types | C'est une **colonne système** (`db/tables.py:77`), posée par `amend` : un champ du même nom en est une copie écrivable et jamais renseignée |
| T10 | `Link` vers un enfant → `Text` | 8 champs | Une ligne vit dans le JSON de son parent : elle n'a pas d'identité adressable |
| T11 | `link_filters` sur un Boolean : `1` → `true` | 2 filtres | Le moteur est strict ; `0/1` vient d'ERPNext |
| T12 | Options d'un `DynamicLink` limitées aux DocTypes chargés | 2 options | `Email Group`, `User` n'existent pas ici |
| T13 | Vues `tree` fallacieuses retirées, 15 vraies gardées | 81 vues | La migration prenait **tout auto-lien** pour une arborescence : 72 pointaient `amended_from`, d'autres `return_against` |
| T14 | Les quantités `Number` → `Decimal` à **3 décimales** (défauts canonisés : `1.0` → `"1.000"`) | 273 champs, 92 types, 27 défauts | Décision 1 de `programme.md` §3.7, et la **première marche des hooks** : `amount = qty × rate` ; multiplier un flottant par un décimal exact pour produire de l'argent exigerait une règle d'arrondi, qui est une question métier. Échelle UNIFORME parce que `mapping.same_number_kind` exige le même nombre de décimales des deux côtés d'un mapping, et dix mappings recopient une quantité (`scripts/qty_to_decimal.py`, idempotent) |

Bilan : **809 insertions, 1597 suppressions**. La base a maigri, ce qui est la bonne direction — l'essentiel du
retrait est de la redondance (T9) et des vues inventées (T13).

### Décisions à connaître

- **Le référentiel partagé s'ouvre en lecture à tout utilisateur identifié.** `Currency`, `Country` et `Gender`
  portent `permissions: {read: ["@authenticated"]}` ; l'écriture suit la convention (`setup:country:write`).
  Sans cela, choisir un pays dans un formulaire de `crm` exigerait un grant `setup` : `search`/`get`
  appliquent le périmètre de lecture du type **visé**. C'est le motif du plugin `geo`.
- **Les dérogations par rôle sont perdues, volontairement** (T3). « Qui peut facturer au-delà du seuil » est un
  grant vérifié dans un hook, pas un champ de réglage. Les 19 champs nommaient un concept que le moteur
  n'implémentera jamais tel quel.
- **Un maître reste en `naming: hash`** quand aucune clé métier n'est déclarée : c'est légitime, son
  `title_field` l'affiche (362 types en ont un). Ce n'est un défaut que sur une **pièce**, et les 136 sont traitées.

## 4. Ce qui reste à faire

Dans l'ordre de valeur :

1. **Les hooks.** Zéro `hooks:` sur 704 définitions : les 136 pièces passent brouillon → soumis **sans rien
   écrire**. Pas de comptabilisation au grand livre, pas de solde de stock. Le moteur a ce qu'il faut
   (`on_submit` + `event.tx` dans la transaction du document), tout est à écrire — module par module,
   `accounts` et `stock` d'abord, les autres s'appuient dessus. C'est l'essentiel du travail restant.

   **Trois marches, dans cet ordre, et l'ordre n'est pas négociable** (`programme.md` §3.7 : sinon les 136
   pièces s'écrivent deux fois).

   - **1a. Les quantités en décimal exact — FAIT** (T14 ci-dessus). Mesuré avant : 273 champs de quantité
     étaient des flottants.
   - **1b. `compute:`** — pas fait, et c'est le vrai verrou. Mesuré le 28 oct. sur le jeu : **129 champs de
     montant clés sont `read_only`** (`grand_total` ×17, `amount` ×40, `total` ×16, `net_amount` ×13,
     `total_taxes_and_charges` ×13, `base_total` ×13, `base_grand_total` ×10, `outstanding_amount` ×7) et
     **aucun mécanisme de calcul n'existe**. Écrire un hook de comptabilisation aujourd'hui, c'est poster un
     `grand_total` que rien ne remplit — ou recalculer les totaux en Python dans chaque module, 136 fois.
     (29 montants restent modifiables, dont `amount` ×23 : ceux-là sont saisis, ils vont bien.)
   - **1c. Les hooks eux-mêmes**, `accounts` et `stock` d'abord.

   **Et un prérequis d'hébergement**, orthogonal aux trois : un hook est une coroutine enregistrée PAR NOM dans
   le registre d'un plugin (D16), et un hook cité mais non enregistré refuse l'écriture (503 `hook_unavailable`,
   jamais ignoré). Or `doctypes/` se charge aujourd'hui en DocTypes d'opérateur (`doctypes_dirs`, sans
   propriétaire) : personne n'enregistre rien. Les hooks exigent donc le point 2 ci-dessous, au moins pour le
   module qu'on traite.
2. **Les plugins de module** (§2) : un par module, propriétaire de ses DocTypes (`load_dir(…, peers=True)`), qui déclare ses
   grants, son espace et enregistre ses hooks — sans quoi aucun grant n'est déclaré à xauth.
3. ~~**`target_status` sur les liens entre pièces.**~~ **Fait** : les 10 champs `document` des mappings portent
   `target_status: submitted`, donc une facture ne peut plus citer une commande annulée — jugé à l'écriture ET à
   la soumission du document citant (D23). Invariant dans `tests/unit/test_erp_doctypes.py`.
4. **Les workflows.** Zéro `workflow:` : aucun circuit d'approbation. Le constructeur existe
   (`process/programme.md` §3.6).
5. ~~**17 espaces de travail manquants**~~ **Fait** : 9 espaces métier écrits (libellé français, icône, `order`
   métier, raccourcis CHOISIS sous des en-têtes — ce sont les en-têtes qui font les sous-groupes dépliables de la
   barre latérale) et 7 modules de PLOMBERIE rattachés par `contributes_to` (D27) là où leurs types servent :
   maintenance → support, telephony → crm, regional → accounts, communication/edi/utilities/bulk_transaction →
   setup. Sept groupes pour quinze types n'étaient pas des domaines métier. Les 21 fichiers chargent ensemble.
6. **`depends_on` et `fetch_from`** : absents partout. ERPNext s'en sert beaucoup pour alléger les formulaires.
7. ~~**17 enfants orphelins**~~ **Fait** : retirés (704 DocTypes au lieu de 721). C'étaient des vestiges de
   parents non migrés, dont cinq de portail web — un périmètre explicitement exclu (`palier70.md`). Aucun `Link`
   ne pouvait les citer (le schéma l'interdit), le retrait est donc sans effet sur le reste. Invariant posé.

## 5. Le garde-fou

`tests/unit/test_erp_doctypes.py` charge tout le jeu comme `plugin.py` le fait et vérifie huit invariants : le
chargement et la résolution de chaque Link, l'absence de toute référence aux jeux d'essai, une série sur chaque
soumettable, l'unicité des préfixes, aucun namespace réservé, la lecture ouverte du référentiel, aucun champ
doublant une colonne système, et une vue `tree` sur une vraie hiérarchie.

La base est faite pour être relue et modifiée à la main. Ce test dit tout de suite si une retouche la casse, au
lieu de le laisser découvrir au démarrage d'une instance.
