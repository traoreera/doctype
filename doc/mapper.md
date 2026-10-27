# « Créer depuis » : le mapper et le pré-remplissage

**Conception, 27 oct. 2026.** Ce fichier est le contrat à tenir.

**Fait à ce jour :** le schéma (`schema.CreatedFrom`, `MappingLines`, `MappingQuantity`), ses refus au
chargement (`registry._check_mappings`, §6), la logique PURE du préremplissage et de la somme du déjà-fait
(`mapping.py`, §2/§5), la lecture en base (`Query.cited_rows`), le service complet
(`DocumentService.available_mappings` et `mapped_values`, §3/§5) et la déclaration réelle sur `Stock Entry` (§8).
Tests : `tests/unit/test_mapping.py` (logique pure), `tests/unit/test_mapper_service.py` (contre une vraie base :
reste à faire, brouillons, droits, états, cloisonnement des tenants), un scénario de fuite dans
`test_query_leak.py`, un invariant dans `test_erp_doctypes.py`.

Exposé : `GET /mappings/{doctype}/{name}` et `GET /map/{doctype}/{source}/{name}`, actions `doc.mappings` et
`doc.map` (le manifeste ouvre déjà `doc.*`). La provenance est vérifiée à l'écriture
(`DocumentService._check_mapping_rows`, §4).

Desk : bouton **« Créer »** sur la fiche (`FormView`, `createItems`, `data-testid="create-from"`), qui n'apparaît
que si le serveur offre au moins une cible ; route `#/app/<Cible>/new?from=<Type>/<nom>` dont les valeurs sont
demandées au SERVEUR (lui seul connaît le reste à faire), donc un rechargement les retrouve ; bandeau de
provenance ; relecture à chaque version du document, le reste à faire changeant dès qu'une cible est enregistrée.

Déclarations dans l'ERP : **10 couples** (`doctypes/`), de Devis → Commande → Livraison → Facture côté vente,
Demande de matériel → Commande d'achat → Réception → Facture côté achat, et Demande de matériel → Mouvement de
stock.

**Pas fait : les hooks.** Un `Stock Entry` soumis n'écrit toujours rien au grand livre et ne bouge aucun solde.
Et ce n'est pas qu'il reste à écrire du YAML : déclarer `hooks:` sans un plugin qui les ENREGISTRE
(`doctype_hooks`) fait échouer **toute sauvegarde** du type en 503 `hook_unavailable`
(`document.py`). Or les DocTypes de l'ERP arrivent par `doctypes_dirs`, sans plugin propriétaire. Il faut donc
d'abord un plugin `stock` (grants, espace, enregistrement des hooks), puis la comptabilisation elle-même —
registre immuable, soldes de `Bin`, refus de stock négatif sous concurrence, contre-écritures à l'annulation.
C'est un module entier : voir `tests/support/plugins/stock/src/ledger.py`, qui le fait pour le jeu d'essai.

**`read_only` dans une ligne : non réparable en l'état.** Une ligne ne porte que ses champs déclarés, sans
aucune identité (vérifié) : « la même ligne » avant et après une modification est indécidable, et une
vérification positionnelle refuserait à tort dès qu'une ligne est supprimée, puisque tout décale. Le correctif
est de donner une identité aux lignes — un changement de conception, pas un correctif. Le risque concret est
clos autrement : `_check_mapping_rows` compare l'ENSEMBLE des provenances, il est donc insensible au
réordonnancement et refuse toute provenance nouvelle ou modifiée.

**Correction à la conception (§4).** L'exemption `read_only` prévue est INUTILE, et pour une raison qu'il faut
connaître : `read_only` n'est pas appliqué à la création (rien de stocké à comparer) et **ne l'est jamais sur un
champ de LIGNE**, même en modification — `builder._row_errors` ne gère que `required` et
`mandatory_depends_on`. Dans une table, `read_only` est donc une indication d'interface, pas une garantie du
serveur. C'est exactement pourquoi la vérification de la provenance n'est pas une finition : sans elle,
n'importe quel client peut écrire ou réécrire une référence et faire baisser la quantité restante d'une source.
(Ce trou de `read_only` dans les lignes dépasse le mapper et reste à traiter.)

Le geste central d'un ERP, celui qu'aucune de nos briques ne couvre : *Demande de matériel → Mouvement de
stock*, *Commande client → Bon de livraison → Facture*. Un bouton « Créer > » sur un document soumis ouvre un
**brouillon prérempli** du document suivant, aux quantités **restantes**.

Ce n'est ni du YAML (il n'y a pas de bloc pour ça) ni un hook (un hook s'exécute à l'écriture, il ne fabrique
pas un brouillon à l'écran). D'où une fonctionnalité du moteur, et non une extension JavaScript par plugin —
sinon vingt modules réimplémenteraient la même mécanique dans du code de confiance chargé chez chaque
utilisateur.

## 1. La déclaration vit sur la CIBLE

```yaml
# doctypes/stock/stock_entry.yaml
created_from:
  - source: Material Request
    label: Mouvement de stock      # le texte du bouton, affiché sur la SOURCE
    source_status: submitted       # défaut : on ne transfère pas un brouillon
    exclude: [posting_date]        # ce qu'on ne reprend pas malgré le même nom
    fields:                        # SEULEMENT les exceptions (champ cible: champ source)
      stock_entry_type: material_request_type
    lines:
      items:                       # champ Table de la CIBLE
        from: items                # champ Table de la SOURCE
        reference: material_request_item   # la ligne cible garde la trace de sa ligne source
        document: material_request         # ... et du document source
        quantity: {source: qty, target: qty}   # le couple qui fait le « reste à faire »
        fields: {item_code: item_code}         # exceptions, même règle
```

Pourquoi sur la cible : le plugin de la cible possède ses champs et sait les remplir ; un module nouveau
déclare « je me crée depuis X » **sans toucher au YAML de X** (la philosophie de D26) ; et le droit en jeu est
le `create` de la cible.

## 2. Recopie par convention, mapping pour les exceptions seulement

Tout champ de la cible qui porte le **même `fieldname` et un type compatible** sur la source est repris.
Écartés d'office, pour les mêmes raisons que « Dupliquer » (`ui/src/lib/form.duplicateValues`) :
`no_copy`, les secrets, les fichiers (un fichier appartient à UN document), les champs `fetch_from` (le serveur
les recalcule), les `unique`, le champ de nommage — et les `read_only`, **sauf** ceux que la déclaration
désigne (`reference`, `document`) : voir §4.

`fields:` ne sert qu'aux renommages, `exclude:` qu'aux retraits. Pas de second YAML qui redit le premier.

## 3. Ça PRÉ-REMPLIT, ça ne crée pas

- `GET /mappings/{source_doctype}/{name}` → ce que CE demandeur peut créer depuis CE document, maintenant
  (quantités restantes comprises). Même esprit que `/transitions` : le Desk ne peut pas en juger seul.
- `GET /map/{target_doctype}?from={source_doctype}/{name}` → les **valeurs** d'un brouillon. Rien n'est écrit.
- Desk : `#/app/Stock Entry/new?from=Material Request/MR-2026-00001`, et la fiche demande ses valeurs au
  serveur. Rien dans l'URL, donc un rechargement les retrouve. `FormView.init(meta, null, copy)` fait déjà
  exactement cela pour « Dupliquer ».

Contrairement à `amend`, qui crée immédiatement. Trois raisons de préférer le préremplissage : l'utilisateur
doit ajuster les quantités avant d'enregistrer, une lecture ne doit rien écrire, et la validation ordinaire
s'applique ensuite sans chemin d'écriture parallèle (la leçon de `csv_import.py`).

Droits : `read` sur la source **avec le périmètre du demandeur**, `create` sur la cible. Les deux jugés par le
serveur ; un bouton n'apparaît jamais pour qui ne peut pas s'en servir.

## 4. La référence de ligne : vérifiée, pas crue

Une ligne enfant vit dans le JSON de son parent : elle n'a **pas d'identité adressable**. La référence est donc
l'**indice de la ligne** dans la table source (`2`), qui est stable parce que **seule une source soumise est
mappable** et qu'un document soumis est figé.

`reference` et `document` nomment deux champs de la ligne CIBLE (Text et Link) que le mapper remplit. Ils sont
`read_only` — c'est voulu, l'utilisateur ne doit pas les retoucher — et le moteur les laisse passer en
**création seulement**, pour les champs que la déclaration désigne. En échange il les **vérifie** à l'écriture,
comme il vérifie un Link : le document source existe, est lisible par l'auteur, est dans l'état demandé, la
ligne d'indice visé existe, et la valeur du couple de quantité y est cohérente. Une référence forgée est
refusée (`mapping_reference`, 422), pas silencieusement crue.

## 5. Le reste à faire

Le calcul, pour une ligne source donnée :

> restant = quantité de la ligne source − somme des quantités des lignes cibles qui la citent

Les documents cibles qui citent UNE source se comptent sur les doigts : on les retrouve par un filtre de haut
niveau ordinaire sur le champ `document` (aucun SQL nouveau), puis **on somme en Python avec `decimal.Decimal`**.

C'est un choix mesuré. Sommer en SQL aurait demandé un nouveau dialecte : `_points_to` (`query.py`) sait
parcourir les lignes, mais par contenance JSONB sur PostgreSQL — un test d'existence, inapte à sommer — et par
`json_each` sur SQLite. Il aurait fallu un `jsonb_array_elements` côté PG, et sur un `Decimal` la somme serait
passée par des doubles côté SQLite. En Python la somme est **exacte** sur les deux bases.

Le prix : c'est borné par le nombre de documents cibles citant la source. On le plafonne
(`MAX_MAPPING_SOURCES`) et au-delà on refuse de prétendre connaître le reste plutôt que de mentir.

Comptent comme « déjà fait » les cibles en **brouillon ET soumises**, jamais les annulées. Un brouillon en
cours est du travail engagé : l'ignorer ferait préremplir deux fois la même quantité. Et comme le reste est
recalculé à chaque appel, supprimer ou annuler le brouillon le rend aussitôt.

Une ligne soldée n'est pas préremplie ; une source entièrement soldée **n'offre plus le bouton**
(`/mappings` ne la retourne pas) — c'est ce qui évite le double transfert, que la seule recopie ne peut pas
empêcher.

## 6. Refus au CHARGEMENT

Comme partout : une faute de déclaration est une erreur de chargement, jamais une surprise à la première
saisie (`registry.validate_links` est l'endroit). Sont refusés : une `source` inconnue, enfant ou virtuelle ;
une source non `submittable` avec `source_status: submitted` ; un champ `fields` absent d'un côté ou de l'autre,
ou de types incompatibles ; un `lines.<champ>` qui n'est pas une Table de la cible, ou dont le `from` n'est pas
une Table de la source ; un `reference` qui n'est pas un Text de la ligne cible, un `document` qui n'est pas un
Link vers la source ; un `quantity` dont les deux champs ne sont pas numériques du même genre (`Decimal` avec
`Decimal`, jamais `Number` avec `Decimal` : la somme doit rester exacte) ; deux déclarations pour la même
source.

## 7. Ce que la v1 ne fait pas

- Pas de création directe ni en lot (« faire les livraisons de ces 10 commandes ») : le préremplissage d'abord.
- Pas d'indicateur d'avancement sur la source (le `%` d'ERPNext) : `/mappings` sait le reste, mais aucun champ
  de la source n'est maintenu. Il faudra un hook, ou `allow_on_submit`.
- Pas de chaîne à plus de deux maillons en une fois (commande → facture **via** livraison).
- Pas de mapping d'une table vers une table d'un autre nom que par `from`.

## 8. Mise au point

Sur **Material Request → Stock Entry** (décidé le 27 oct. 2026) : tout entier dans le module `stock`, et
`Stock Entry Detail` porte déjà les champs qu'il faut — `material_request` (Link), `material_request_item`
(Text, issu de la réadaptation T10), `qty`, `transferred_qty`.
