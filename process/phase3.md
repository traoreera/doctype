# Phase 3 : documents riches

17 octobre 2026. Contre xcore 2.9.1, SQLite et PostgreSQL 18, Chromium piloté par playwright-core 1.64. Suite de `process/phase2.md` ; `process/doctype.md` n'est pas modifié, les écarts sont proposés ici pour la v4.

## 1. Résultat

Critère de sortie de la feuille de route (§15) : **« un DocType à lignes (facture sans comptabilité), numérotation sans doublon en concurrence sur les deux bases »**.

| Critère | Résultat | Preuve |
| --- | --- | --- |
| Un DocType à lignes | **OK** | `app/sales/doctypes/invoice.yaml` + `invoice_line.yaml` + `item.yaml` (déplacés dans le plugin `sales` en phase 4), saisis, enregistrés, relus et modifiés depuis le Desk par `tests/e2e/test_desk_browser.py` (vrai Chromium) ; le total est calculé par le hook `sales.invoice_totals` du plugin `app/sales` |
| Numérotation sans doublon en concurrence, sur les deux bases | **OK** | `tests/unit/test_naming.py` : 40 créations simultanées donnent exactement `FA-2026-00001`…`00040`, sur SQLite et sur PostgreSQL ; la mutation « lire puis écrire » du compteur est détectée sur les deux |
| Link complet : contrôle d'existence et de référence | **OK** | `tests/unit/test_links.py` : existence et lisibilité à l'écriture, refus de suppression d'un document cité (par un champ ou par une ligne), entrelacements PostgreSQL déterministes |
| Tables enfants en tableau JSON | **OK** | `tests/unit/test_tables.py`, `ui/tests/table.spec.ts` |
| Versions | **OK** | `tests/unit/test_versions.py`, `tests/integration/test_history.py`, `ui/tests/history.spec.ts` |

Suites : 1438 tests pytest sur SQLite et PostgreSQL, 346 tests vitest, scénario navigateur compris (§9).

Éprouvé par mutation, chacune fait échouer au moins un test (44 mutations : 23 côté serveur, 21 côté interface) :
- **numérotation** : compteur incrémenté en lisant puis en écrivant (détecté sur SQLite *et* PostgreSQL) ;
- **Link** : verrou `FOR KEY SHARE` retiré, verrou `FOR UPDATE` retiré, contrôle des références retiré ou limité au périmètre de l'appelant, contrôle d'existence retiré, lisibilité ignorée, valeur inchangée recontrôlée ;
- **tables** : lignes non validées, liste vide conservée, liens des lignes non contrôlés, références des lignes ignorées, enfant joignable par l'API, parent chargé avant son enfant, clé de la contenance JSONB faussée ;
- **historique** : entrée de mise à jour ou de suppression non écrite, mise à jour sans changement écrite, lecture sans contrôle du périmètre, document supprimé lisible par tous, historique sans filtre de tenant, différence qui garde l'inchangé, `track_changes` ignoré ;
- **interface** : logique des lignes (7), panneau d'historique (6), protections contre les réponses tardives de `FormView` (8).

## 2. Numérotation par série (D7, D13)

```yaml
naming: { series: "FA-{YYYY}-{#####}" }   # FA-2026-00001, FA-2026-00002…
```

Motif : texte (`[A-Za-z0-9_.-]`), parties de date (`{YYYY}` `{YY}` `{MM}` `{DD}`) et **un** compteur (`{#####}`, le nombre de `#` est la largeur minimale, complétée de zéros ; au-delà le nombre s'allonge). La **clé du compteur** est le motif dont les dates sont résolues (`FA-2026-{#}`) : une série contenant `{YYYY}` repart de 1 chaque année sans rien planifier ; deux motifs différents ne partagent jamais un compteur. Le fuseau des parties de date est `doctype.timezone` (nom IANA, défaut `UTC`) : une facture du 1er janvier à 00 h 30 ne doit pas porter l'année précédente.

**Écart avec D7 : même transaction, pas transaction séparée.** `doctype.md` recommandait par défaut une transaction séparée (des trous acceptés, pas de contention), « sauf exigence légale de séquence continue ». Une facture est exactement ce cas (en France, la numérotation doit être continue et sans rupture), et la transaction séparée a un second défaut : sur SQLite, une seconde connexion qui veut écrire pendant que la première tient le verrou d'écriture attend et peut échouer. Le compteur est donc incrémenté **dans la transaction du document, en dernière étape avant l'insertion** (après les hooks et le contrôle des Link) :

- un document refusé (validation, hook, Link inexistant) ou une erreur plus loin **ne consomme aucun numéro** (testés : refus de schéma, refus d'un hook, échec de l'insertion) ;
- il n'y a pas de doublon : une seule instruction `INSERT … ON CONFLICT DO UPDATE … RETURNING` crée le compteur et l'incrémente, sans « lire puis écrire » ;
- en contrepartie, **les créations d'une même série s'enchaînent** sur le verrou de la ligne du compteur jusqu'au commit. Le verrou n'est tenu que pour la fin de la transaction (pas pendant les hooks) ; non mesuré sous charge.

Conséquences à connaître : (1) les hooks `validate` et `before_save` d'une création voient `event.name is None` (le numéro n'existe pas encore), `after_save` reçoit le nom définitif ; (2) un numéro n'est jamais réattribué après une suppression : la continuité légale exige donc de ne pas supprimer une facture (ce que les documents « soumettables », après la phase 4, rendront impossible) ; (3) D13 est étendu : la clé du compteur est `(tenant, DocType, série résolue)`, pas `(tenant, préfixe)`, pour que deux DocTypes ne se couplent pas par hasard.

Le doc disait de la preuve sur SQLite (« 25 appels, 25 valeurs distinctes ») qu'elle était limitée car SQLite sérialise les écritures. Ici, la mutation « lire puis écrire » est détectée **aussi sur SQLite** : le test de concurrence démontre quelque chose.

## 3. Link complet

**À l'écriture** (`DocumentService._check_links`, dans la transaction) : chaque valeur de champ Link **modifiée** doit viser un document du tenant qui existe **et** que l'appelant peut lire, y compris dans les lignes d'une table (chemin d'erreur `items.0.item`). Un document illisible est refusé exactement comme un document absent (même `type: link_not_found`, même forme de message) : on ne révèle pas l'existence d'un document hors périmètre. Une valeur **inchangée** n'est pas recontrôlée, même si sa ligne a changé de place : un lien devenu orphelin par un import ne doit pas empêcher de corriger autre chose.

**À la suppression** : `DocumentInUse` (409 `document_in_use`) si un document du tenant, ou une ligne d'une de ses tables, cite encore la cible. La recherche des références se fait **sans** le périmètre de l'appelant (une référence qu'il ne peut pas lire bloque quand même), et la réponse nomme les types et les champs (« Facture (Lignes › Article) »), jamais les documents. Elle révèle donc tout de même qu'un type de document cite celui-ci, à quelqu'un qui n'a peut-être pas le droit de lire ce type : c'est inhérent à une contrainte d'intégrité (une clé étrangère en dirait autant).

**Sans clé étrangère, l'intégrité tient par des verrous** :

| Écriture | Suppression |
| --- | --- |
| `SELECT … FOR KEY SHARE` sur la cible, avant d'écrire | `SELECT … FOR UPDATE` sur la cible, **puis** recherche des références |

Deux entrelacements sont possibles, et les deux sont testés en suspendant une transaction à mi-chemin (`TestRace`) : si la suppression arrive pendant qu'un lien s'écrit, elle attend, puis voit la nouvelle référence et refuse ; si le lien arrive pendant une suppression, il attend, puis constate que la cible a disparu. Retirer l'un ou l'autre verrou fait échouer ces tests. Une modification ordinaire de la cible n'est pas bloquée (`KEY SHARE` n'entre en conflit qu'avec la suppression).

**Limite SQLite** : pas de verrou de ligne. Une fenêtre étroite subsiste entre le contrôle de la suppression et son exécution (mesurée : des liens orphelins apparaissent dans un test de course non orchestré). Ces tests s'ignorent donc sur SQLite, avec la raison. SQLite reste la base de développement ; en production, PostgreSQL.

**Au chargement** : `registry.validate_links()` fait échouer le plugin si la cible d'un Link n'est pas un DocType chargé, ou est un DocType enfant.

## 4. Tables enfants

```yaml
# invoice_line.yaml — un DocType « enfant »
name: InvoiceLine
istable: true
fields: [ {fieldname: item, fieldtype: Link, options: Item, required: true}, {fieldname: qty, fieldtype: Number, default: 1}, … ]

# invoice.yaml
fields:
  - { fieldname: items, fieldtype: Table, options: InvoiceLine, required: true }
```

- **Stockage** : un tableau JSON dans le parent (§17, point 7), jusqu'à **500 lignes** (le document voyage entier vers les hooks sandboxés, plafonnés à 64 Kio). Liste vide et champ absent sont le même état, un seul est stocké. `required` sur une table signifie au moins une ligne.
- **Validation** : chaque ligne passe par le modèle strict de son DocType enfant (pas de coercition, pas de NaN, pas de clé inconnue) ; les erreurs portent le chemin `items.0.qty`. Les hooks reçoivent et peuvent réécrire les lignes ; ce qu'ils renvoient est revalidé.
- **Un enfant n'a que des champs** : ni `module`, ni `permissions`, ni `hooks`, ni `layout`, ni `title_field`, ni série, ni table imbriquée, ni condition (`depends_on`…). Tout cela est **refusé au chargement** plutôt qu'ignoré. Ses lignes héritent des droits, des hooks et de la mise en page de leur parent.
- **Invisible de l'API** : `/meta`, `/doc`, `/list`, `/search` et `/history` répondent 404 `doctype_not_found` pour un enfant, comme pour un DocType inconnu ; il n'a pas de grants (`doctype_grants` ne le cite pas). `/meta` du parent embarque ses champs sous `children`, de quoi dessiner la grille.
- **Ordre de chargement** : les enfants passent d'abord (`load_dir` les trie ; `register` exige qu'ils soient déjà enregistrés), car un parent compile ses lignes avec la définition de leur DocType.
- **Interdits sur une table** : filtre, tri, présence dans une condition, `in_list_view`, valeur par défaut.
- **Références dans les lignes** : `Query.references_to` cherche un Link dans les lignes par `json_each` + `json_extract` sur SQLite et par contenance JSONB (`data -> 'items' @> '[{"item": "X"}]'`) sur PostgreSQL ; testé sur les deux, et chez deux tenants (`test_query_leak.py`).

**Le total d'une facture est une affaire de hook**, pas du moteur : `app/sales` (plugin propriétaire du module) enregistre `sales.invoice_totals`, qui recalcule `total` à chaque sauvegarde ; le champ est `read_only_depends_on: "true"` dans le Desk. Un moteur de champs calculés n'est pas dans la feuille de route.

## 5. Historique des modifications (« Version »)

Table `xdt_version` : une entrée par création, modification utile et suppression, **écrite dans la transaction du document** (les deux réussissent ou échouent ensemble, testé dans les deux sens). Une entrée ne garde que la différence `{champ: {old, new}}` ; une création est une différence avec « rien », une suppression avec « plus rien » (elle **conserve donc le contenu supprimé** et son propriétaire). Une table de lignes est comparée en bloc. Une sauvegarde qui ne change rien n'écrit rien. `track_changes: false` désactive l'historique d'un DocType très bavard (défaut : actif).

`GET /history/{doctype}/{name}?limit=&offset=` (et l'action `doc.history`) : la plus récente d'abord. Il faut pouvoir **lire** le document, avec le même périmètre (403 sans droit, 404 hors périmètre ou chez un autre tenant). Pour un document supprimé, le périmètre est jugé sur le contenu et le propriétaire conservés dans l'entrée de suppression : l'historique d'une suppression reste lisible par ceux qui pouvaient lire le document.

## 6. Le Desk

- **`TableField`** : une grille (colonnes = champs visibles du DocType enfant, astérisque sur les obligatoires), ajout et suppression de ligne, clés de ligne stables, erreurs sous la cellule fautive (`items.0.rate`) et numéro de ligne en rouge ; en lecture seule, du texte. Corriger une cellule n'efface que son erreur.
- **`HistoryPanel`** : section « Historique » repliée par défaut, chargée à l'ouverture, rechargée à chaque sauvegarde si ouverte, « Voir plus » ; les liens s'affichent par titre, un tableau se résume (« 2 lignes → 3 lignes · 1 ajoutée, 1 modifiée »).
- **`FormView` ne se laisse plus écraser par une réponse tardive** (voir §7.1).
- Le scénario navigateur couvre maintenant : création d'articles, facture à deux lignes (autocomplétion dans les cellules), total et numéro `FA-2026-00001`, lignes incomplètes, garde « Modifications non enregistrées », article cité refusé à la suppression (toast), numéro suivant, historique d'une modification de ligne.

## 7. Défauts trouvés pendant la phase

1. **`FormView` : une réponse arrivant après un changement de page écrasait la page courante.** Le composant sert tous les documents (la route change, l'instance reste). Trois cas réels, observés : (a) un enregistrement encore en vol renvoyait l'utilisateur vers `Invoice/<nom d'un article>` (le `doctype` était relu *après* l'attente) ; (b) un chargement lent de A pouvait finir après celui de B et afficher A ; (c) l'échec d'un enregistrement d'une page quittée affichait ses erreurs sur la suivante, et un bouton « Enregistrer » restait en attente. Corrigé : cible figée avant d'attendre, numéro de chargement, `saving` remis à zéro à la navigation. Reproduit par `ui/tests/formview.spec.ts` (8 tests, 8 mutations détectées). Trouvé par le scénario navigateur, qui lançait `goto` avant que l'enregistrement précédent n'ait fini ; un toast resté à l'écran avait masqué l'attente (le scénario attend désormais la route du document).
2. **Un `plugin.yaml` dont une valeur contient `: ` n'est pas du YAML valide, et xcore saute alors le plugin avec un simple avertissement** (`invalid manifest`). Les hooks du plugin ne s'enregistrent jamais, et toute sauvegarde d'un DocType qui les cite répond 503 `hook_unavailable`. Trouvé en écrivant `app/sales/plugin.yaml` (description non guillemetée). C'est un point d'attention pour les auteurs de plugins (noté dans CLAUDE.md), pas un défaut du moteur : refuser une sauvegarde plutôt que d'ignorer un hook est le bon comportement.
3. **Un test de course non orchestré ne prouvait rien** : il n'attrapait pas la suppression du verrou `FOR KEY SHARE` (les transactions sont trop courtes pour se chevaucher) et échouait de temps en temps sur SQLite (la fenêtre réelle). Remplacé par deux entrelacements déterministes qui suspendent une transaction à mi-chemin, plus un test « sans orchestration » qui n'affirme que l'absence d'erreur inattendue. Une première version laissait en plus une transaction ouverte quand l'assertion échouait, ce qui bloquait le nettoyage de la base PostgreSQL : la reprise est maintenant dans un `finally`.
4. **Le harnais d'intégration ne chargeait pas `Country`**, cible du Link `Customer.country` : le chargement échouait (à raison : `validate_links`). Corrigé dans le harnais, et trois attentes qui énumèrent les grants ou les DocTypes ont été mises à jour.
5. **La réponse d'une suppression refusée nommait le DocType (`Item`) au lieu de son libellé** (`Article`) : corrigé.

## 8. Non fait et non vérifié

- **Numérotation** : pas de numérotation à partir d'un champ, pas de préfixe modifiable, pas de remise à zéro à la demande ; la contention du verrou du compteur n'est pas mesurée (40 créations concurrentes passent, pas de benchmark) ; la continuité légale complète exige les documents soumettables (après la phase 4).
- **Tables** : pas de condition dans les lignes (`depends_on` par ligne), pas de table imbriquée, pas de réordonnancement, pas de totaux ni de champs calculés par le moteur, pas de table dans la liste, les filtres ni la recherche, pas de droit par ligne. Les lignes n'ont pas d'identité propre : l'historique et la comparaison les prennent par position.
- **Historique** : pas de purge ni de durée de conservation (la table grossit sans limite), pas de restauration d'une version, pas de différence ligne à ligne (une table modifiée stocke ses deux tableaux entiers), rien pour les documents modifiés pendant que `track_changes` était désactivé.
- **Link** : pas de filtre sur la cible d'un lien (« seulement les clients actifs »), pas de renommage de document qui propage ses références, fenêtre de course sur SQLite (§3).
- **Plateformes** : un seul serveur PostgreSQL (18, locale `fr_FR.UTF-8`) ; Chromium seulement ; le scénario navigateur tourne sur SQLite (le Desk ne dépend pas de la base, la parité est testée côté serveur).
- Le Desk n'affiche toujours pas de mode sombre, ne rafraîchit pas son jeton et ne gère pas la double authentification (phase 2, §8).
- Hors périmètre (après la phase 3) : promotion en tables dédiées et migrations, documents soumettables, permissions par champ, hooks de suppression, rapports.

## 9. Suites

Dernière exécution complète (17 octobre 2026, fin de phase) :

| Suite | Résultat |
| --- | --- |
| pytest, SQLite + PostgreSQL 18 | **1438 réussis**, 2 ignorés (les deux entrelacements de verrous de ligne, qui exigent PostgreSQL, sont ignorés sur la base SQLite) |
| pytest, SQLite seul | 1087 réussis, 353 ignorés (les cas PostgreSQL) |
| vitest | **346 réussis** (12 fichiers) ; `vue-tsc` sans erreur |
| scénario navigateur (compris dans pytest) | réussi, ≈ 20 s, base vierge et tenant neuf à chaque exécution |

Pour comparaison, la fin de la phase 2 comptait 1215 tests pytest et 293 tests vitest.
