# Le jeu de DocTypes d'ERP (`doctypes/`)

**État au 27 oct. 2026.** 721 DocTypes répartis en 23 modules, migrés d'ERPNext (commit `b39a542`), puis réadaptés
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
  et les relations Table en forment un de 5. Aucun ordre de chargement n'existe, donc **« un plugin par module »
  est impossible** — `requires` ordonne, il ne dénoue pas un cycle.

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

1. **Les hooks.** Zéro `hooks:` sur 721 définitions : les 136 pièces passent brouillon → soumis **sans rien
   écrire**. Pas de comptabilisation au grand livre, pas de solde de stock. Le moteur a ce qu'il faut
   (`on_submit` + `event.tx` dans la transaction du document), tout est à écrire — module par module,
   `accounts` et `stock` d'abord, les autres s'appuient dessus. C'est l'essentiel du travail restant.
2. **Les 22 plugins déclarants** (§2), sans quoi aucun grant n'est déclaré à xauth.
3. **`target_status` sur les liens entre pièces.** Rien n'empêche aujourd'hui une facture de citer une commande
   annulée.
4. **Les workflows.** Zéro `workflow:` : aucun circuit d'approbation. Le constructeur existe
   (`process/programme.md` §3.6).
5. **17 espaces de travail manquants** (5 modules sur 22 en ont un ; le moteur en fabrique un à défaut).
6. **`depends_on` et `fetch_from`** : absents partout. ERPNext s'en sert beaucoup pour alléger les formulaires.
7. **17 enfants orphelins** (`istable` qu'aucun champ `Table` ne cite) : `Budget Account`,
   `Subscription Invoice`… Vestiges de parents non migrés.

## 5. Le garde-fou

`tests/unit/test_erp_doctypes.py` charge tout le jeu comme `plugin.py` le fait et vérifie huit invariants : le
chargement et la résolution de chaque Link, l'absence de toute référence aux jeux d'essai, une série sur chaque
soumettable, l'unicité des préfixes, aucun namespace réservé, la lecture ouverte du référentiel, aucun champ
doublant une colonne système, et une vue `tree` sur une vraie hiérarchie.

La base est faite pour être relue et modifiée à la main. Ce test dit tout de suite si une retouche la casse, au
lieu de le laisser découvrir au démarrage d'une instance.
