# Programme après le recompte du 22 octobre 2026

Décision de l'utilisateur, 22 octobre 2026 : « on part sur le 1 et le 2, puis on termine avec le 3, mais avec une étude approfondie ».
Les trois groupes sont ceux proposés le même jour, après le recompte vérifié de `palier70.md` §2 (71 % des 87 lignes, 59 % des 119
lignes de l'inventaire élargi). Ce document ordonne le travail et dit, pour chaque élément, **la décision de conception à prendre avant
d'écrire une ligne de code** — c'est elle qui coûte, pas le code.

## 0. Deux corrections à mon propre classement

Je les sors du groupe où je les avais mises le 22 octobre, après avoir regardé ce qu'elles touchent :

- **La corbeille n'est pas une petite ligne.** Une suppression douce change **toutes les lectures** du moteur (`Query` filtre partout),
  les scénarios de fuite obligatoires (`test_query_leak.py`), `document_in_use` (un document à la corbeille retient-il encore ses
  liens ?), l'historique et l'unicité (`xdt_unique` : une valeur à la corbeille est-elle libre ?). Elle passe en fin de groupe 2.
- **L'éditeur de DocType n'est pas du groupe 2.** Il pose une question que rien dans le dépôt ne tranche : *où vivent les définitions
  créées à l'écran ?* Aujourd'hui une définition est un YAML **appartenant à un plugin**, chargé à `on_load`, qui porte le `module` d'où
  les grants sont déduits. Une définition créée dans le Desk n'a pas de plugin propriétaire. Il passe en tête du groupe 3, avec l'étude.

## 1. Groupe 1 — petites, visibles, sans risque pour le cœur

Dans cet ordre : chacune est indépendante des autres, la première est la plus isolée.

| # | Élément | Décision à prendre | Ligne d'inventaire |
|---|---|---|---|
| 1 | ~~**Filtres sur les choix d'un Lien**~~ **FAIT le 22 oct. (D29, voir §1.1)** | tranché : dynamiques ET vérifiés à l'écriture | angle mort → faite |
| 2 | **Onglets de formulaire** (Tab Break) | Un `layout` à deux niveaux (onglet > section > colonnes) sans casser les `layout` existants (à un niveau), ni l'habillage de fiche (`overrides` kind `form`, `BlockEditor`). Les onglets sont-ils habillables comme le reste ? | angle mort (non faite) |
| 3 | **Étiquettes** | Table dédiée jugée sur le document, comme la collaboration (`xdt_comment`…) : lire le document suffit-il pour voir ses étiquettes, et faut-il l'ÉCRIRE pour en poser une ? Filtrables dans la liste (donc dans `Query`, pas après coup). | une des 20 |

### 1.1 D29 — les filtres d'un champ Link (fait le 22 octobre 2026)

```yaml
- { fieldname: entrepot, fieldtype: Link, options: Entrepot,
    link_filters: { pays: "$pays", actif: true } }
```

Ce qui a été décidé, et qui vaut pour la suite :

- **Une valeur, ou `$champ`** : la valeur d'un autre champ du **même conteneur** — le document, ou la LIGNE pour un champ de table
  (`{genre: "$genre"}` dans une ligne lit le genre de cette ligne). `parent_…` n'est pas pris en charge : une ligne ne lit pas son
  parent ici, contrairement aux conditions.
- **Le serveur l'exige à l'écriture** (`document._check_link_filters`), pas seulement le Desk : un client d'API se fait refuser la
  même valeur, avec le type d'erreur `link_filtered` (422), distinct de `link_not_found`. C'est l'écart assumé avec Frappe, où
  `link_filters` n'est qu'une aide d'interface.
- **Un champ source vide ne restreint rien.** On ne refuse pas ce qu'on n'a pas restreint ; le Desk fait pareil (`resolveLinkFilters`).
- **Un lien INCHANGÉ n'est pas rejugé**, comme pour le contrôle des liens : fermer un entrepôt n'empêche pas de corriger la référence
  d'une livraison qui le citait déjà.
- **Les fautes échouent au chargement** (`registry._check_link_filters`) : champ absent chez la cible, littéral du mauvais type (il
  passe par le `coerce_filter` de la cible, soit exactement le contrôle d'un filtre de liste), ou champ source d'un autre type.
- **Un seul interprète** : les filtres sont typés par `coerce_filter` puis appliqués par `memory.select`, la version Python des
  filtres SQL, déjà tenue en parité par les tests. L'autocomplétion (`/search?filters=…`) applique les mêmes.
- `DynamicLink` en est exclu : sa cible change d'un document à l'autre, rien ne serait contrôlable au chargement.

Preuves : `tests/unit/test_link_filters.py` (18 cas), `ui/tests/form.spec.ts` et `ui/tests/lists-links-api.spec.ts`.

## 2. Groupe 2 — ce qui rend le produit utilisable par quelqu'un qui n'est pas développeur

| # | Élément | Décision à prendre | Ligne |
|---|---|---|---|
| 4 | **Permissions utilisateur + Partage d'un document** | Le cœur, et les deux vont ensemble : ce sont **deux clauses de plus dans `Scope`** (`permissions.py`), donc dans les DEUX interprètes qui doivent rester équivalents (SQL dans `Query`, Python dans `Scope.allows`) et dans l'oracle indépendant de `test_permissions_matrix.py`. À décider : où vivent les restrictions (table par utilisateur × DocType × champ × valeur), comment elles se composent avec `if_owner`, et le coût SQL (une jointure de plus par requête, ou un `IN` ?). | 2 des 20 |
| 5 | **Import CSV** | Le même validateur que l'API (jamais un chemin d'écriture parallèle : droits, hooks, liens, unicité), par lots bornés, avec un rapport ligne à ligne. Transaction par ligne ou par lot ? | 1 des 20 (reste de E) |
| 6 | **Clés d'API** | **Dépend d'xauth**, qui est hors du dépôt : la clé est-elle un jeton xauth à longue vie (c'est xauth qui la crée et la révoque) ou une clé propre au moteur ? Je ne touche pas à `app/auth` sans accord. | 1 des 20 |
| 7 | **Corbeille** (déplacée ici, voir §0) | Suppression douce : colonne d'état, exclusion dans `Query._scope()` (le seul goulet), restauration, purge, et les réponses aux questions du §0 (liens, unicité, historique). | 1 des 20 |

## 3. Groupe 3 — l'étude avant le code

Rien ne commence ici avant une étude écrite qui réponde, pour chaque élément, à sa question structurante :

- **Éditeur de DocType dans le Desk** (déplacé ici) : où vivent les définitions créées à l'écran, qui les possède, quel `module` porte
  leurs grants, que devient une définition quand le plugin qui la citait s'en va, et comment elle survit à une livraison.
- **Workflow** : des états × des rôles × des transitions, et surtout son articulation avec `docstatus` (brouillon/soumis/annulé), qui
  est déjà un automate. Deux automates sur le même document, ou un seul ?
- **Tâches planifiées et webhooks** : il faut un ordonnanceur. `xcdk` (optionnel) l'offre (`cron`, `interval`) ; sinon il faut le bâtir.
  Qui garantit qu'une tâche ne tourne qu'une fois quand plusieurs processus servent l'application ? Reprise et idempotence des webhooks.
- **Temps réel** : canal (SSE ou WebSocket), autorisation par tenant, et ce qu'on pousse (jamais le contenu d'un document, comme pour
  les événements du bus).
- **Formats d'impression et PDF** : moteur de rendu, polices, et la surface d'attaque d'un gabarit écrit par un utilisateur.

## 4. Ce qu'on vise

Les sept éléments des groupes 1 et 2 valent **7 lignes** de l'inventaire élargi (4 des 87, 3 des angles morts) : de 59 % à ~65 %. Le
pourcentage n'est pas le but (`palier70.md` §1 : l'inventaire est subjectif, chaque ligne vaut 1) ; le but est que les éléments 4 et 7
existent, parce que sans eux un administrateur ne peut ni restreindre ce qu'un collègue voit, ni défaire une suppression.
