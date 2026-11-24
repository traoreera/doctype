# Droits par champ (« champs réservés »)

Phase F de `process/palier40.md`, traoreera/doctype#74, 24 novembre 2026. Ce document est le contrat : le modèle, la politique de
chaque porte, les erreurs, ce que cela change pour un plugin, et ce qui n'est pas fait.

Le point de départ : le Desk sait masquer un champ (`hidden`, `depends_on`), mais ce n'est pas une protection — la valeur restait dans
la réponse de l'API, dans l'historique, dans un tri. Ici c'est le SERVEUR qui applique le droit, à chaque porte par laquelle une valeur
sort ou entre ; le Desk ne fait que refléter ce que `/meta` lui dit.

## 1. Le modèle

```yaml
fields:
  - fieldname: salary
    fieldtype: Currency
    permissions:
      read:  [hr:employee:salary_read]                      # il faut EN PLUS l'un de ces grants pour lire le champ
      write: [{grant: hr:employee:salary_write, description: "Fixer le salaire"}]   # … et l'un de ceux-ci pour l'écrire
  - { fieldname: grade, fieldtype: Text, permissions: { write: [hr:employee:grade_write] } }   # lu par tous, écrit par quelques-uns
  - { fieldname: audit_ref, fieldtype: Text, permissions: { read: [] } }                        # personne : le serveur seul
```

- **Par GRANT, jamais par niveau ni par rôle.** Frappe range les champs en `permlevel` 0-9 et donne des niveaux aux rôles ; ici un
  champ nomme les grants qui l'ouvrent, comme le fait `permissions` d'un DocType (D6). Les noms de rôle ne comptent toujours pas
  (`role:x` refusé), et `@authenticated` est refusé : il ne réserverait rien.
- **En plus du document, jamais à sa place.** Le droit sur un champ ne s'ajoute qu'à qui peut déjà lire (ou écrire) le DOCUMENT :
  périmètre `if_owner`, permissions utilisateur et partages jouent d'abord, inchangés. Un document partagé avec quelqu'un lui montre
  ses champs réservés seulement s'il en détient les grants.
- **Défaut : rien ne change.** Un champ sans `permissions` suit le document, comme avant ; un DocType sans champ réservé ne paie rien
  (`DocTypeDef.guarded_fields` est vide, `/meta` garde exactement sa forme).
- **Héritage.** `read` absent : qui lit le document lit le champ. `write` absent : qui écrit le document ET lit le champ l'écrit.
  **Écrire exige de lire** : le grant d'écriture seul n'ouvre rien. `[]` ne l'ouvre à personne. Une entrée est une chaîne ou
  `{grant, description, tenant_grantable}` — pas d'`if_owner` ni de `match` : le droit sur un champ ne dépend que de l'APPELANT.
- **Une table est réservée EN BLOC.** `permissions` se pose sur le champ `Table` du parent ; ses lignes le suivent. Un champ d'un
  DocType enfant ne porte pas de `permissions` (refusé au chargement) : une ligne n'a pas d'identité, le serveur ne saurait pas lui
  rendre une cellule que l'appelant n'a pas reçue (ni dire laquelle il a changée).
- **Le contexte système voit et écrit tout** : hooks de confiance, `event.tx`, `doctype_writer.system(...)`, lecteurs internes.
  C'est du code du serveur, pas une réponse faite à quelqu'un.
- **Les grants se déclarent comme les autres.** `DocTypeDef.grant_specs()` les rend, `GrantCatalog.for_plugin()` les passe au
  `rbac.declare` du plugin propriétaire (libellé par défaut « Employé : lire « Salaire » », `default_roles: [tenant_admin]` s'ils sont
  délégables). Aucun appel de plus dans le plugin.

L'état d'un champ pour un appelant est l'un de trois (`permissions.FieldAccess`, compilé par `permissions.field_access`) :
**caché** (ni lu ni écrit), **lu seulement**, ou libre.

### Pourquoi un seul interprète

Le périmètre d'un document (`Scope`) dépend du document : il lui faut un interprète SQL et un interprète Python, tenus équivalents par
un oracle. Le droit sur un champ ne dépend que des grants de l'appelant : il se juge AVANT la requête (un filtre, un tri ou un agrégat
sur un champ caché est refusé, il n'atteint ni le SQL de `Query` ni son jumeau `memory.py`) et APRÈS (la clé quitte `data`). Il n'y a
donc rien à garder équivalent entre deux langages — et c'est la raison de refuser `if_owner` sur un champ. Ce qui est testé à la
place : `field_access` contre un oracle écrit à part, sur tous les jeux de grants ; et un type stocké contre un type virtuel aux mêmes
champs, porte par porte (`test_the_sql_path_and_its_memory_twin_answer_alike`).

## 2. Les portes

Politique : **une valeur cachée est EXCLUE de ce qui sort ; une opération qui la ferait deviner est REFUSÉE**, jamais servie « sans
ce champ ». Une liste filtrée ou triée sur un champ caché le lirait bit par bit ; la refuser est la seule réponse qui ne dépend pas
des données.

| Porte | Ce que fait le serveur | Où |
|---|---|---|
| Fiche, liste, résultat d'une écriture (créer, modifier, soumettre, annuler, amender, renommer, transition) | la clé manque dans `data` — ni `null`, ni masque | `DocumentService._present(doc, ctx)` |
| Titres de liens, `unit_from` / `currency_from`, aperçu d'un lien, export CSV du Desk | passent par `list` / `get` : même sortie | idem |
| Recherche (`/search`, recherche globale) | ne lit que le nom et `title_field`, qui ne peut pas être réservé (chargement) | `DocTypeDef._field_permission_rules` |
| Historique | les entrées restent (qui, quand), sans les changements des champs cachés ; document supprimé compris | `DocumentService.history` |
| Corbeille | l'instantané sans les champs cachés ; restaurer les remet tels quels (le serveur recopie) | `DocumentService.trash`, `_create(restoring=)` |
| Impression, PDF, aperçu de l'éditeur | « imprimer, c'est lire » : rendu depuis `get(ctx)`, la valeur manque, rien n'échoue | `PrintService._html` |
| Fichiers (Image / Attach réservé) | pas de lien signé, absent de l'écran Fichiers et de ses comptes, dépôt et détachement refusés | `FileService._may_read`, `upload` ; `FileLibrary._citers`, `detach` |
| Connexions | une référence par un lien caché n'est ni listée ni comptée | `DocumentService.connections` |
| « Créer depuis » (`created_from`) | le brouillon rendu ne recopie que ce que l'appelant lit de la source | `mapped_values`, `available_mappings` |
| Filtres (liste, agrégat, recherche, compteur d'étiquettes, vue enregistrée), filtre de ligne `table.champ` | 403 `field_not_readable` | `aggregation.validate_filters(hidden=)` |
| Tri | 403 `field_not_readable` | `DocumentService.list`, `_view_config` |
| Agrégats : mesure (`sum`, `avg`, `min`, `max`), regroupement | 403 `field_not_readable` | `aggregation.resolve(hidden=)` |
| Blocs d'espace et de tableau de bord (carte, graphique, liste rapide) | un bloc qui mesure, regroupe ou filtre sur un champ caché n'est pas servi | `_visible_block`, `_restrict_meta` |
| `/meta` | les champs cachés n'y sont pas (ni disposition, ni colonnes, ni lignes de table) ; un champ réservé qui reste porte `access: read \| write` et `read_only` s'il n'est que lu ; les grants ne sortent jamais | `document._restrict_meta` |
| Form Builder (`GET /override/form`) | libellés et règles sans les champs cachés | `DocumentService.form_override` |
| Écriture par l'API (HTTP, IPC `doc.save`) | 403 `field_not_writable` ; la clé d'un champ caché est refusée QUELLE QUE SOIT sa valeur | `_guard_fields`, `_check_field_writes` |
| Lot (`/bulk`) | refusé une fois, avant tout document | `DocumentService.bulk` |
| Import CSV | une colonne non modifiable refuse l'import entier, avant toute ligne | `csv_import.import_csv` |
| Lignes d'une table réservée | la table est un champ : cachée, elle ne s'envoie pas ; lue seulement, elle doit revenir identique | `_guard_fields`, `_check_field_writes` |
| DocTypes virtuels | les mêmes contrôles avant d'appeler la source | `_create_virtual`, `_update_virtual` |
| Règles de formulaire de l'organisation (défaut, obligatoire, lecture seule) | refusées sur un champ que leur auteur ne peut pas écrire | `save_form_override` |
| Règles de notification | un champ réservé en lecture n'y entre par aucun bout (surveillé, condition, message, destinataire) ; une règle devenue fautive est écartée | `notification_rules.rule_problems` |
| Temps réel, événements du bus, flux xflow, canaux | ne portent aucun contenu de document : non concernés | — |
| Hooks (`validate`, `before_save`, `action:`), `event.tx`, `find` / `sum` d'un hook, `doctype_writer.system`, `doctype_documents` | code du serveur : voient et écrivent tout | `field_access` (contexte système), `_present(doc)` sans `ctx` |
| `doctype_writer.as_request(request)` | la requête d'un utilisateur : toutes les règles ci-dessus | les méthodes de `DocumentService` |

### Écrire : trois règles

1. **Un champ caché ne s'envoie pas**, même avec la bonne valeur : accepter « la même » et refuser « une autre » dirait laquelle est
   enregistrée (`salary = 5200 ?` se sonderait par 200/403). La réponse est la même dans les deux cas.
2. **Le serveur garde ce que l'appelant ne voit pas.** Une mise à jour REMPLACE `data` : sans cela, le premier enregistrement de la
   fiche par un commis viderait le salaire. Les valeurs cachées sont remises depuis le document enregistré avant la validation (un
   secret repart masqué, c'est-à-dire « inchangé »).
3. **Un champ lu seulement se compare comme `read_only`** (`builder.same_value`) : renvoyé tel quel, il passe — c'est ce que fait le
   Desk ; changé ou OMIS (omettre, c'est vider), il est refusé. À la création, seul ce que le client ENVOIE se juge, contre ce que
   le serveur aurait posé de lui-même (défauts du YAML et de l'organisation). Un champ que le serveur écrit (`compute`,
   `fetch_from`) n'est pas comparé, comme pour `read_only`.

Un amendement et une restauration recopient ce qui était enregistré : personne ne saisit, les champs réservés y reviennent tels quels.

## 3. Ce que la définition ne peut pas révéler

Une règle, vérifiée au CHARGEMENT : **rien de visible ne dérive d'un champ réservé en lecture.**

- Ce qui est montré à tout lecteur du document ne peut pas le citer : `title_field`, `image_field`, le nom (`naming: {field}` ou
  `{template}`, saut `{lien.champ}` compris), les champs des vues (`kanban`, `calendar`, `gantt`, `tree`), la condition d'une
  transition de workflow (YAML et habillage d'organisation), un `match`, la clé d'un `link_filters` chez la CIBLE.
- Ce qui en dérive pour un champ doit être **au moins aussi réservé** que lui (`FieldDef.covers` : ses grants de lecture sont parmi
  ceux de la source) : `compute`, `depends_on` / `mandatory_depends_on` / `read_only_depends_on`, `link_filters: $champ`,
  `fetch_from` (le lien du même document ET le champ copié chez la cible), et pour une condition de ligne `parent_x` c'est le champ
  Table qui doit couvrir `x`.

Les contrôles propres au document sont dans `schema.DocTypeDef._field_permission_rules`, ceux qui regardent un autre type dans le
registre (`_check_fetch`, `_check_link_filters`, `_check_naming_template`), la condition de ligne dans `CompiledDocType.compile`.
Un champ ajouté par une organisation (`custom_…`) ne porte pas de `permissions` (clé refusée : son grant ne serait déclaré nulle part).

## 4. Les erreurs

| Code | Statut | Quand | `details` |
|---|---|---|---|
| `field_not_writable` | 403 | une écriture touche un champ que l'appelant ne peut pas écrire (caché ou lu seulement) | `[{field, msg, type: field_not_writable}]` |
| `field_not_readable` | 403 | un filtre, un tri, une mesure ou un regroupement cite un champ qu'il ne lit pas | `[{field, msg, type: field_not_readable}]` (`field` = la clé demandée, `items.qty` pour un filtre de ligne) |

Les champs sont NOMMÉS : leur existence n'est pas un secret (elle est dans la définition du plugin), leur valeur si. Le refus ne
dépend jamais des données. Par IPC, le même code dans la réponse d'erreur de l'action.

## 5. Pour un plugin

- Réserver un champ, c'est écrire `permissions` dans le YAML : rien d'autre. Le grant doit être dans le namespace du plugin pour
  qu'il puisse le déclarer (`for_plugin`) ; un grant d'un autre namespace est cité, pas déclaré ici.
- Un **hook** reçoit le document entier et peut écrire un champ réservé (comme il écrit un champ `read_only`). C'est lui qui porte la
  règle métier (« seul le responsable augmente au-delà de 10 % ») : le moteur ne juge que le grant.
- Une **route HTTP de plugin** qui rend un document doit passer par `doctype_writer.as_request(request)` : c'est la porte qui
  applique les droits de l'utilisateur. Le lecteur système (`doctype_documents`) et `writer.system` voient tout : ne jamais renvoyer
  tel quel à un client ce qu'ils lisent.
- Une **extension D26** peut réserver les champs qu'elle ajoute (ils repassent par le schéma, qui ne refuse pas `permissions`) ; ses
  grants sont dans SON namespace. Non couvert par un test dédié.
- Une **extension du Desk** lit `meta.fields[].access` ; un champ absent du méta n'existe pas pour l'utilisateur.

## 6. Le Desk

Rien à masquer : un champ caché n'est pas dans `/meta`. Un champ lu seulement arrive `read_only: true`, que la fiche, le lot, l'import
et la saisie rapide appliquaient déjà. `FieldMeta.access` (`ui/src/lib/types.ts`) ; « Dupliquer » ne recopie pas un champ `access:
read` et une création ne l'envoie pas (`lib/form.creationPayload`) ; les deux refus ont leur message dans les deux langues.

## 7. Ce qui n'est pas fait, et les limites connues

- **Pas de droit par COLONNE dans une table** (voir §1) : on réserve la table entière.
- **Un document dont un champ caché est déjà invalide** (rendu obligatoire depuis, par exemple) ne s'enregistre pas par qui ne le
  voit pas, et l'erreur 422 nomme ce champ. Elle ne dit pas sa valeur, mais elle dit qu'une obligation porte dessus.
- **Un champ obligatoire, réservé en écriture et sans défaut** ferme la création à qui n'a pas le grant (« Champ obligatoire » sur un
  champ qu'il ne peut pas remplir). C'est peut-être voulu ; ce n'est pas refusé au chargement.
- **L'unicité** (`unique`) d'un champ réservé reste jugée sur toute l'organisation : qui peut l'écrire apprend par le refus qu'une
  valeur est prise ailleurs (déjà vrai d'un document hors de son périmètre `if_owner`).
- **Une disposition d'organisation enregistrée par quelqu'un qui ne voit pas un champ** ne le place pas : il rejoint le dernier bloc
  du premier onglet pour ceux qui le voient (la règle des champs non placés).
- **Le gabarit d'impression fabriqué** garde le LIBELLÉ d'un champ caché (cellule vide).
- **`_mapping_done`** (quantités déjà servies d'un « Créer depuis ») lit les documents cibles sans le droit par champ, comme il le
  fait sans le périmètre : il rend des quantités restantes, pas des valeurs.
- **Pas de cache** : `field_access` relit les grants du jeton à chaque requête. Un grant retiré vaut à la requête suivante.
- **PostgreSQL** : les tests de cette phase n'ont tourné que sur SQLite (aucun SQL nouveau : le droit se juge hors de la requête).
- **Pas de scénario Chromium** : le Desk est couvert par vitest (`ui/tests/field-access.spec.ts`), pas par un parcours réel.

## 8. Preuves

`tests/unit/test_field_permissions.py` (chaque porte, l'oracle, la parité stocké/virtuel, les refus de chargement, deux
organisations ; 36 mutations des lignes de contrôle, toutes détectées), `tests/integration/test_field_permissions_api.py` (un vrai
hôte : HTTP et IPC, codes d'erreur, organisations), `ui/tests/field-access.spec.ts`.
