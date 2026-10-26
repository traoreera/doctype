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
| 2 | ~~**Onglets de formulaire**~~ **FAIT le 24 oct. (voir CLAUDE.md « Form tabs »)** | tranché : un `tab` par section, les mêmes règles pour le YAML et l'habillage, qui peut lui aussi ranger en onglets ; ce qu'une disposition ne place pas ferme le premier onglet | angle mort → faite |
| 3 | ~~**Étiquettes**~~ **FAIT le 26 oct. (voir §1.2)** | tranché : lire le document suffit pour voir ses étiquettes, il faut l'ÉCRIRE pour en poser ou en retirer ; filtre `_tag` dans `Query` | une des 20 → faite |

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

### 1.2 Les étiquettes (fait le 26 octobre 2026)

Ce qui a été décidé :

- **Autour du document, comme la collaboration.** Table `xdt_tag` (tenant, type, document, forme repliée, écriture, auteur, date), jamais une
  clé de `data` : poser une étiquette ne modifie pas le document (ni version, ni historique, ni règle de notification), un document **soumis
  ou annulé s'étiquette encore**. Elles **suivent un renommage** et **disparaissent avec le document** (un document qui reprend le nom
  n'hérite de rien).
- **Lire pour voir, écrire pour poser.** Voir les étiquettes d'un document demande de le LIRE (hors périmètre `if_owner` : « introuvable ») ;
  en poser ou en retirer demande le périmètre d'ÉCRITURE de CE document (`_can_write`, comme joindre ou assigner) : une étiquette change
  ce que les autres trouvent en filtrant. Aucun grant nouveau.
- **Une étiquette, quelle que soit la casse.** La clé est la forme repliée (`str.casefold`, calculée en Python : le `lower()` de SQLite ne
  connaît que l'ASCII), l'écriture affichée est celle déjà en usage dans le type — mais prise **seulement sur un document que l'appelant lit**
  (sinon l'écriture trahirait l'étiquette d'un document invisible). Un nom : forme composée NFC (« é » composé ou décomposé : une seule
  étiquette), 1 à 50 caractères, blancs réduits (classe écrite un à un : `\s` n'a pas le même sens en Python et en JavaScript), ni virgule,
  ni contrôle, ni invisible (catégories Cc, Cf, Cs : « Urgent » suivi d'une espace sans chasse serait un sosie qui partagerait les filtres
  en deux). Vecteurs communs au serveur et au Desk : `tests/vectors/tag_cases.json`.
- **20 au plus par document**, comptées APRÈS la pose, sous le verrou du document (`FOR UPDATE` sur PostgreSQL, prouvé par un test de
  concurrence ; SQLite n'a qu'un écrivain) : une demande refusée ne garde rien, retraits compris.
- **Jamais d'étiquette orpheline** (constat de la relecture) : une pose qui croise une suppression ou un renommage relit le document
  APRÈS sa première écriture (sur SQLite, c'est elle qui prend le verrou de la base ; sur PostgreSQL, le verrou de ligne fait attendre la
  suppression, qui emporte alors l'étiquette) ; disparu, la pose est défaite (introuvable). Sans cela, le prochain document du même nom
  héritait de l'étiquette posée sur l'ancien.
- **Filtrables dans `Query`**, jamais après coup : `{"_tag": "Urgent"}`, `["!=", …]` (ne l'a pas, y compris sans aucune), `["in", […]]`,
  un `EXISTS` corrélé au document, donc valable partout où passent les filtres de la liste (liste, agrégats, recherche, vues enregistrées,
  blocs d'un espace) et toujours dans le périmètre de lecture. Le soulignement le sépare des champs. Refusé sur un document unique ou un
  type virtuel (ses documents ne sont pas dans la table), et dans `link_filters` (le contrôle à l'écriture se fait en mémoire).
- **Comptes** : `GET /tag-counts/{type}` donne les étiquettes en usage parmi les documents lisibles que les filtres retiennent (barre
  latérale, propositions de saisie) ; `GET /tags-used/{type}` (un `EXISTS`) dit seulement s'il y en a, ce que la liste demande à chaque
  ouverture.
- **Ce qu'elles ne font pas, exprès** : poser ou retirer une étiquette n'écrit ni historique ni événement (aucune règle de notification ne
  la voit, un retrait ne laisse pas de trace) ; `amend` ne recopie pas les étiquettes du document annulé, qui les garde (il compte encore
  dans les filtres et les comptes, comme tout document annulé que la liste montre).

Desk : barre d'étiquettes sous le titre d'une fiche (posée tout de suite, sans « Enregistrer » ; un changement à la fois, le focus passe à
l'étiquette suivante après un retrait), pastilles des lignes de liste (une requête par page) qui filtrent au clic, filtre tenu hors de la
barre de filtres (une pastille retirable qui dit « Urgent », « sauf Archivé » ou « A ou B » : un bloc d'espace ou une vue enregistrée peut
porter les trois formes, la liste qu'il ouvre les applique), groupe « Étiquettes » de la barre latérale, « Étiqueter… » en lot. Pas fait : couleurs d'étiquette, renommer ou fusionner une
étiquette dans tout un type, étiquettes dans les cartes de la liste sur téléphone.

## 2. Groupe 2 — ce qui rend le produit utilisable par quelqu'un qui n'est pas développeur

| # | Élément | Décision à prendre | Ligne |
|---|---|---|---|
| 4 | ~~**Permissions utilisateur + Partage d'un document**~~ **FAIT le 26 oct. (voir §2.1)** | tranché le 26 oct. : comme Frappe pour les restrictions, « qui écrit partage » pour le partage | 2 des 20 → faites |
| 5 | **Import CSV** | Le même validateur que l'API (jamais un chemin d'écriture parallèle : droits, hooks, liens, unicité), par lots bornés, avec un rapport ligne à ligne. Transaction par ligne ou par lot ? | 1 des 20 (reste de E) |
| 6 | **Clés d'API** | **Dépend d'xauth**, qui est hors du dépôt : la clé est-elle un jeton xauth à longue vie (c'est xauth qui la crée et la révoque) ou une clé propre au moteur ? Je ne touche pas à `app/auth` sans accord. | 1 des 20 |
| 7 | **Corbeille** (déplacée ici, voir §0) | Suppression douce : colonne d'état, exclusion dans `Query._scope()` (le seul goulet), restauration, purge, et les réponses aux questions du §0 (liens, unicité, historique). | 1 des 20 |

### 2.1 Permissions utilisateur et partage (fait le 26 octobre 2026)

Décisions de l'utilisateur, 26 octobre : « comme Frappe » pour les restrictions, « qui écrit partage » pour le partage, clés d'API par un
jeton xauth longue vie (à faire là où le code d'xauth est disponible), permissions et partage d'abord.

```
périmètre(utilisateur, action) = (rôles ET permissions utilisateur) OU documents partagés avec lui
```

- **Deux clauses de plus dans `Scope`** (`permissions.py`), et les deux interprètes qui restent équivalents : le SQL de `Query`
  (`_restriction_conds`, `_shared_cond`, sous-requêtes, aucun cache : une restriction ajoutée vaut à la requête suivante) et le Python de
  `Scope.allows`, qui juge une valeur pas encore en base avec les faits chargés par l'appelant (`Facts`, `Query.permission_facts`) et
  REFUSE de répondre sans eux. L'oracle indépendant de `test_permissions_matrix.py` les compare sur 4 variantes × 42 cas (rôles seuls,
  restrictions, partages, les deux), sur SQLite et PostgreSQL.
- **Une permission utilisateur** est un document `User Permission` (plugin `system`, espace Administration ; grants `system:user_permission:*`) :
  membre (e-mail, traduit en identifiant par l'annuaire : un hook), type, valeur permise, et `applies_to` facultatif. Elle RESSERRE ce que les
  rôles donnent (`if_owner` compris) sur le type lui-même (par le nom) et sur tout champ Lien de premier niveau vers lui ; un lien vide passe ;
  plusieurs lignes = plusieurs valeurs permises. Elle vaut à la lecture (liste, recherche, agrégats, contrôle des liens : on ne lie pas ce
  qu'on ne voit pas) comme à l'écriture (on ne crée ni ne déplace hors de ce qu'on voit). Un hook refuse un type inconnu ou non stocké, une
  valeur qui n'existe pas, un `applies_to` qui ne cite pas le type.
- **Un partage** (`xdt_share`) ouvre UN document à un membre, en lecture ou en lecture + écriture, **même sans rôle sur le type et malgré
  ses restrictions** ; jamais la suppression, la soumission ni l'annulation. Seul qui peut ÉCRIRE le document partage (et retire) ; le membre
  peut renoncer au sien ; il est prévenu (cloche). Il suit le renommage, disparaît avec le document.
- **Conséquence voulue sur les réponses** : lire, modifier, l'historique ou ce qui entoure UN document nommé ne répond plus 403 faute de
  rôle (un partage pourrait l'ouvrir) : sans partage, c'est « introuvable » (404), comme un nom qui n'existe pas. La liste, la création, la
  suppression gardent le 403. Sans rôle mais avec un partage, `/meta` donne de quoi ouvrir la fiche (`read`/`write` « restricted ») et la page
  « Partagés avec moi » en est l'entrée (la liste du type reste fermée).
- **Relecture adversariale** (cinq trous reproduits, tous corrigés avec leur test, chacun vérifié par mutation) : un partage écrit pendant
  une suppression survivait et ouvrait le PROCHAIN document de ce nom (verrou + relecture après l'écriture, comme les étiquettes) ; un
  utilisateur borné par une permission passait pour « tout lire » (`level == "all"`) et pouvait écrire une règle de notification diffusant
  ce qu'il ne voit pas, ou poser les règles de formulaire de l'organisation (`Query.restricted` dans les trois portes, et `/meta` dit
  « restricted ») ; il créait un document du type restreint sous un nom interdit et sondait les noms cachés par le 409 (le nom CHOISI est
  jugé avant l'insertion) ; un annuaire indisponible gardait une adresse comme identité, qui ne restreignait personne (refusée) ; un partage
  en écriture laissait renommer (ce qui réécrit les documents qui citent) et repartager de proche en proche (il faut un RÔLE d'écriture pour
  renommer, partager ou retirer le partage d'un autre). Les fichiers d'un document partagé s'ouvrent à son membre.
- **Pas couvert** (choix ou limite) : les liens dans les lignes de table enfant et les `DynamicLink` ne sont pas restreints ; un DocType
  virtuel, unique ou enfant n'a ni restrictions ni partages ; les faits d'une écriture sont lus juste avant elle (une restriction ajoutée
  entre les deux ne vaut que pour la suivante) ; le grant `system:user_permission:write` est un droit d'administrateur (il retire aussi ses
  propres restrictions, et la vérification de `for_value` dit si un nom existe) ; chaque lecture paie deux sous-requêtes par champ Lien,
  sans index sur `data->>'user'` (sans effet mesurable à l'échelle d'aujourd'hui).

## 3. Groupe 3 — l'étude avant le code

Rien ne commence ici avant une étude écrite qui réponde, pour chaque élément, à sa question structurante :

- **Éditeur de DocType dans le Desk** (déplacé ici) : où vivent les définitions créées à l'écran, qui les possède, quel `module` porte
  leurs grants, que devient une définition quand le plugin qui la citait s'en va, et comment elle survit à une livraison.
- **Workflow** : étude faite, voir §3.2 (et §3.3 : `xflow` ne le couvre pas, il est complémentaire).
- **Tâches planifiées et webhooks** : étude faite, voir §3.1 — **et largement répondue par `xflow`, voir §3.3**.
- **Temps réel** : canal (SSE ou WebSocket), autorisation par tenant, et ce qu'on pousse (jamais le contenu d'un document, comme pour
  les événements du bus).
- **Formats d'impression et PDF** : moteur de rendu, polices, et la surface d'attaque d'un gabarit écrit par un utilisateur.

### 3.1 Étude — l'ordonnanceur (22 octobre 2026)

Tout ce qui suit est **vérifié dans les paquets installés** (xcore 2.9.1, xcdk 0.2.0), pas supposé.

#### Ce qui existe déjà, et que nous croyions à bâtir

**1. xcore a un ordonnanceur**, `xcore/services/scheduler/service.py` : APScheduler en AsyncIO, `enabled: true` par défaut,
déclencheurs `cron`, `interval`, `date`, jobs déclarables en YAML (`services.scheduler.jobs`) ou par décorateur.

**2. Le multi-processus y est déjà traité — à une condition.** `SchedulerConfig.backend` vaut `memory` (défaut), `redis` ou
`database`. En `redis`, le service pose un **verrou distribué** `xcore:sched:lock:<job_id>` (jeton, TTL, prolongé tant que le job
tourne) : un seul processus exécute, les autres passent en silence. **En `memory` — le défaut — il n'y a aucun verrou** : chaque
worker uvicorn exécute le job. Une facture récurrente serait créée autant de fois qu'il y a de processus. C'est le piège, et il est
dans la configuration par défaut.

**3. xcdk expose ces jobs aux plugins** : `@cron("0 9 * * MON-FRI")`, `@interval(seconds=30)`, `ScheduledMixin`. Le module
s'importe **`sdk`** et non `xcdk` (la distribution s'appelle `xcdk`) : c'est pour ça qu'on le croyait absent. Ce n'est pas une
dépendance optionnelle, elle est dans `pyproject.toml`.

**4. xcore a DÉJÀ une intégration Celery** — la réponse à la question posée. `services.xworker` (et l'alias `services.celery` :
s'il est présent et que `xworker` n'est pas activé, il le remplace) construit l'app Celery depuis `integration.yaml` :
`broker_url` (Redis par défaut), `result_backend`, `queues`, `concurrency`, `task_soft_time_limit`/`task_time_limit`,
sérialisation JSON, `modules` (les modules de tâches importés au démarrage). Côté code : décorateur `@task(queue=…, bind=True,
max_retries=…)`, `task_registry`, `WorkerService.send(nom, …, queue=…)` et `get_result(task_id)`. L'app est construite **à
l'import** (`_bootstrap`) pour que `celery -A …` démarre un worker séparé : c'est le déploiement prévu.

**Mais Celery n'est pas installé ici** (`import celery` échoue) : le service journalise `celery not installed — uv add
'celery[redis]'` et passe en FAILED ; broker injoignable = DEGRADED, jamais fatal. Il faut donc **ajouter `celery[redis]` et un
Redis** pour s'en servir.

#### Les deux pièges que l'étude met au jour

**Les jobs tournent sur le event loop de l'application.** Le service le dit et le mesure : au-delà de 250 ms sans `await`, il
journalise « scheduler job blocked the event loop ». Un import CSV, un lot de PDF, un rapport sur dix mille documents n'ont rien à
y faire — ils y gèleraient les requêtes HTTP. L'ordonnanceur sert à *déclencher*, pas à *travailler longtemps*.

**Un worker Celery est un AUTRE processus, qui ne connaît pas le moteur.** `WorkerService` importe les `modules` de tâches, rien
de plus : pas de plugins chargés, donc pas de `doctype_registry`, pas de `doctype_hooks`, pas de `RequestContext`, pas de
périmètre de tenant. Or nos travaux (documents récurrents, règles de notification datées, import CSV) sont **des écritures du
moteur**. Trois façons d'en sortir, et il faut en choisir une :

| | Comment | Ce que ça coûte |
|---|---|---|
| **A. Le worker démarre xcore** | le worker boote l'hôte complet (plugins, base, registre) avant de traiter ses tâches | un second boot complet à maintenir, deux jeux de hooks vivants, un registre à garder en phase ; à vérifier : xcore sait-il démarrer hors uvicorn ? |
| **B. Le worker rappelle l'application** | la tâche Celery fait un appel IPC (`/ipc/…`, clé du plugin) ; l'application fait le travail | simple, un seul moteur, mais le travail revient sur le processus web — donc inutile pour ce qui est long |
| **C. Partage des rôles** | l'ordonnanceur (verrouillé) déclenche les écritures COURTES dans l'application ; Celery ne reçoit que l'EXTERNE : e-mail, webhook HTTP, génération de PDF | deux chemins à comprendre, mais chacun fait ce pour quoi il est bon |

#### Ce que je recommande

**C.** Celery est excellent là où il y a un réseau à affronter — réessais, backoff, file morte : exactement les webhooks et les
e-mails, qui sont justement les lignes non faites de l'inventaire. Il est mal placé pour écrire des documents, parce qu'écrire un
document ici veut dire *tout* le moteur (droits, hooks, liens, unicité, historique) et qu'un second boot de l'hôte est un coût
permanent pour un gain nul : créer une facture récurrente prend quelques millisecondes.

Donc : **`services.scheduler.backend: redis` dès qu'il y a plus d'un processus** (le verrou existe déjà, il ne manque que Redis),
les déclenchements et les écritures courtes dans l'application, et **`celery[redis]` + un worker séparé pour l'externe**. Redis
devient une dépendance d'exploitation des deux côtés : c'est le prix, il faut le dire à l'opérateur plutôt que de le découvrir sur
un doublon de facture.

#### Décisions prises (22 octobre 2026)

**D30 — Redis obligatoire, et refus de démarrer sans lui.** Les jobs s'activent par `doctype.jobs.enabled` (défaut **false** :
aujourd'hui le moteur n'en planifie aucun). Quand ils sont activés, `on_load` exige un service `scheduler` dont le backend n'est
PAS `memory` ; sinon il lève une `ConfigurationError` et **le plugin refuse de se charger**, comme `ensure_rls` refuse de démarrer
sur une base qui n'est pas PostgreSQL. Dans le doute aussi : xcore n'expose pas publiquement son backend (seul `_config.backend`
existe, attribut privé ; `WorkerService`, lui, a un `status()`), donc **si on ne peut pas le lire, on refuse**. Demande déposée à
xcore : `doc/xcore-issues.md` §7.

Ce que ça achète : il devient impossible de créer une facture récurrente en double parce qu'un opérateur a lancé deux workers
uvicorn avec la configuration par défaut. Ce que ça coûte : Redis devient obligatoire pour qui veut des tâches planifiées, et
l'opérateur l'apprend au démarrage, par un message qui dit quoi changer — pas six mois plus tard sur un doublon.

**D31 — Journal d'exécution `xdt_job_run`.** Une table système, pas un DocType (rien à valider, rien à historiser) :
`job_id`, `tenant_id` (nul pour un job global), `started` / `finished` (Datetime UTC), `status` (`ok` | `error` | `timeout`),
`error_code` et un message **borné** (jamais de SQL, jamais un payload, jamais le contenu d'un document), `written` (combien de
documents le job a écrits), `duration_ms`.

- **On ne journalise que ce qui s'exécute** : un processus qui trouve le verrou tenu passe en silence et n'écrit rien, sinon
  chaque tick produirait autant de lignes que de workers.
- **Lecture** : une action d'opérateur en contexte système, comme `balances.verify` — pas une route publique. Un DocType virtuel
  pourra l'afficher dans le Desk plus tard (`source:`, la mécanique existe), ce n'est pas le sujet maintenant.
- **Rétention** : `doctype.jobs.keep_runs` (défaut 30 jours), purgé à l'écriture d'une nouvelle ligne du même `job_id`, dans la
  même transaction — exactement ce que `purge_versions` fait déjà pour l'historique. Pas de job pour purger le journal des jobs.

#### Décisions proposées, non confirmées

- **Un job qui déborde sur son exécution suivante** : `max_instances=1` et `coalesce=True`. Une exécution qui dure plus longtemps
  que la période ne se dédouble pas, et les ticks manqués se fondent en un seul au retour.
- **Idempotence** : un nom déterministe plutôt qu'un verrou applicatif. Rejouer une tâche échoue alors en 409 `already_exists`,
  comme le journal de stock le fait déjà avec `<voucher>.<ligne>.in|out`. C'est la base qui tranche, donc c'est vrai même en
  concurrence.
- **L'auteur d'une écriture planifiée** : un contexte système nommé par sa raison, et `owner = "job:<job_id>"` — jamais
  l'identifiant d'un utilisateur. Conséquence voulue : les règles de notification, qui ne préviennent jamais l'auteur d'un
  changement, préviennent ici **tout le monde**, puisque l'auteur n'est personne.

### 3.2 Étude — le Workflow (22 octobre 2026)

Rien n'existe dans le dépôt (aucun code, aucun champ) : le mot n'apparaît que dans les contrats de palier. Mais trois choses déjà
construites contraignent entièrement sa forme, et c'est là qu'est le sujet — pas dans les états ni les transitions, qui sont faciles.

#### Ce que le Workflow devra épouser

**1. `docstatus` EST déjà un automate.** Brouillon → soumis → annulé, avec tout ce qui s'y accroche : `Query.transition` (UN
`UPDATE` conditionnel sur l'état ET la version, donc idempotent et sûr en concurrence, exécuté EN PREMIER dans la transaction),
des grants `submit`/`cancel` distincts de `write`, des hooks transactionnels (`on_submit`, `before_cancel`, `on_cancel`,
`on_update_after_submit`) qui écrivent d'autres documents, le gel du document soumis (`allow_on_submit` près), l'amendement,
l'historique (`submit`/`cancel`), les événements du bus et un `docstatus` filtrable et triable. Un Workflow qui ignorerait cet
automate en créerait un second sur le même document.

**2. Les rôles ne comptent pas, ici.** `PermissionEntry._not_a_role` refuse `role:…` avec un message explicite (« citez un
grant »), parce qu'un nom de rôle est du texte libre, propre à chaque organisation, chez xauth (`context.py` : « Noms de rôle :
informatifs. Aucune règle de DocType ne s'y fie »). Or **un Workflow de Frappe est défini par des rôles** : « l'approbateur est le
rôle Responsable ». C'est l'écart central à combler : ici une transition sera ouverte par un **grant**.

**3. Il y a un précédent, et il contredit le contrat de 40 %.** `palier40.md` §13 disait : « Rapports et workflows sont définis en
YAML, jamais écrits en code par DocType ». Depuis, la phase C4 a fait des **règles de notification des DOCUMENTS** éditables par
l'organisation dans le Desk, bornés par une garde (l'auteur d'une règle doit pouvoir lire TOUS les documents du type) et des
limites écrites noir sur blanc. Les deux voies existent donc dans le dépôt, et il faut choisir pour le Workflow.

#### Le vrai sujet : un automate, ou deux ?

| | Ce que ça donne | Ce que ça coûte |
|---|---|---|
| **A. Un seul** — chaque état du workflow déclare son `docstatus` ; une transition fait `Query.transition` ET écrit le nouvel état | une seule vérité sur « où en est ce document » ; les hooks, le gel, l'historique, l'amendement continuent de marcher sans rien changer | il faut décider ce que deviennent `/submit` et `/cancel` sur un type à workflow (je dis : refusés, 409, en nommant la transition à employer) |
| **B. Deux, superposés** — un champ d'état avec ses transitions, orthogonal à `docstatus`, certaines transitions appelant la soumission | le moins intrusif à écrire | deux vérités, donc des combinaisons absurdes : « Approuvé » sur un document annulé. C'est le genre d'incohérence qu'on découvre en production |
| **C. Le workflow pour les types NON soumettables seulement** | aucun recouvrement, rien à arbitrer | le besoin réel (faire approuver une facture) est précisément sur les types soumettables |

**Je recommande A.** Un document a un état, pas deux.

#### Les questions qui restent, et ce que j'en pense

1. **Qui a le droit de faire une transition ?** Pas un rôle (voir ci-dessus). Soit on réutilise les grants existants quand la
   transition change le `docstatus` (`…:facture:submit`), soit chaque transition ouvre son propre grant
   (`…:facture:workflow_approuver`), ce qui multiplie les grants mais dit exactement qui fait quoi. Je penche pour le second, avec
   le premier en secours pour les transitions qui soumettent ou annulent.
2. **Séparation des pouvoirs.** « Le demandeur ne peut pas approuver sa propre demande » est le besoin le plus courant d'un
   workflow. Le moteur a déjà `if_owner` dans `Scope` : une transition portera un drapeau du même genre (`not_owner: true`), et
   c'est le même interprète qui tranchera.
3. **Où vit l'état ?** Une vraie colonne `workflow_state` (comme `docstatus`, ajoutée au démarrage par la migration additive qui
   existe déjà), parce que la transition doit être un `UPDATE` conditionnel sur l'état : sans ça, deux approbations simultanées
   passeraient toutes les deux. Un champ de `data` serait filtrable mais ne donnerait pas cette garantie aussi proprement.
4. **Où vit la DÉFINITION ?** C'est le choix politique. En **YAML du plugin** : une seule source, validée au chargement, aucune
   surface d'attaque — mais un administrateur ne peut pas ajouter une étape d'approbation sans développeur, ce qui est exactement
   ce que le palier 70 % cherchait. En **document par organisation** (comme les règles de notification) : configurable sans code,
   mais une personne qui édite le workflow peut supprimer l'étape d'approbation qui la gêne, et les hooks du plugin s'accrochent à
   des transitions qu'elle renomme. Je recommande **YAML d'abord**, et une couche par organisation seulement quand les permissions
   par champ (groupe F) existeront pour la borner.
5. **Ce qui arrive aux documents en vol** quand le workflow change (un état disparaît) : le même problème qu'une règle de
   notification devenue invalide. Au minimum : ne jamais bloquer un document dans un état inconnu, le dire au chargement.

#### Ce que je ne ferai pas sans décision

Rien. Les points 1 et 4 changent la nature de ce qu'on écrit ; les autres sont des détails d'implémentation que je peux trancher
seul si tu me laisses faire.

### 3.3 xflow — ce qu'il couvre, ce qu'il ne couvre pas (23 octobre 2026)

L'utilisateur signale un moteur existant : `../orchestra-backend/app/xflows` (plugin xcore `xflow`, 4 700 lignes, documenté,
avec migrations et exemples). Lu avant toute conclusion.

#### Ce que c'est

Un **orchestrateur d'actions IPC**, pas un automate de documents. Son modèle : un *trigger* (bus d'événements, cron, webhook,
IPC, manuel) déclenche une suite de *steps* — `action` (appeler l'action d'un autre plugin), `condition`, `switch`, `foreach`,
`parallel`, `wait`, `transform`, `template`, `ai` — avec retry exponentiel, timeout, file morte et reprise après crash, le tout
journalisé par run. Son exemple `relance_facture.yaml` dit tout : cron à 9 h → `billing.overdue_list` → `foreach` →
`mail.send` avec 2 réessais.

Il n'a **aucune notion de document, d'état porté par un document, d'approbation, ni de bouton sur une fiche**. Ce n'est donc pas
le « Workflow » de l'inventaire Frappe, et ça ne remplace pas §3.2.

#### En revanche, il répond à §3.1 mieux que ce que je proposais

Déclencheur cron, réessais, file morte, webhooks entrants ET sortants, journal des exécutions, reprise après crash : c'est
l'ordonnanceur, les webhooks, une partie des documents récurrents et des rapports par e-mail — **quatre lignes de l'inventaire**,
déjà écrites, dans l'écosystème de l'utilisateur. D30 et D31 (le cadre de jobs et la table `xdt_job_run`) deviennent en grande
partie inutiles : le moteur n'aurait pas à planifier, mais à **exposer des actions** que xflow orchestre — ce qu'il fait déjà
(`doc.create`, `doc.submit`, `doc.list`, `doc.aggregate`… sont des actions xcore depuis le palier 20 %).

#### Le seul vrai obstacle : l'identité

`WorkflowEngine` appelle `self.plugin.call_plugin(plugin_name, action, payload)` — **sans principal et sans tenant**. Or :

- `RequestContext.from_ipc` lève `unauthenticated` (401) quand il n'y a pas de principal. Un step xflow qui appellerait
  `doc.create` échoue donc immédiatement — c'est le bon comportement (fermé par défaut), pas un bogue à contourner.
- Et une règle dure de ce moteur l'interdit de toute façon : **`tenant_id` et `owner` ne viennent JAMAIS d'une charge utile**
  (D3, D17, D20). Élargir `doc.*` pour accepter un tenant dans le payload reviendrait à démonter le cloisonnement.

Le précédent qui marche existe déjà dans le dépôt : **`balances.verify`** (plugin `stock`) est une action d'OPÉRATEUR qui prend
`{"tenant": …}`, s'exécute en **contexte système nommé par sa raison**, et n'est joignable que par la clé du plugin — une porte
étroite, explicite et traçable, pas un élargissement des actions ordinaires. C'est la forme que doit prendre la jonction.

Restent à trancher : **quelles écritures** un workflow peut faire (créer un document récurrent, oui ; soumettre à la place d'un
humain, c'est autre chose), **au nom de qui** (`owner = "xflow:<workflow>"`, jamais l'identifiant d'un utilisateur), et **par
tenant** (un run par organisation, ou un run qui reçoit l'organisation en entrée — xflow a déjà un `tenant_id` sur ses runs,
mais ne le propage pas aux appels).

#### Conclusion

Deux moteurs, deux métiers, une couture nette :

- **xflow orchestre** : cron, webhooks, réessais, enchaînements entre plugins — §3.1 est répondu par lui, pas par du code neuf ici.
- **xcore-doctype garde l'état du document** : états, qui a le droit de transiter, gel, boutons de la fiche — inséparables de
  `docstatus`, des grants et du périmètre de lecture (§3.2 reste entier).
- **La couture** : une transition du moteur émet déjà un événement sur le bus (`doctype.<type>.after_submit`), et xflow sait
  justement se déclencher sur un événement (`TriggerType.EVENT`). Le moteur dit *ce qui est arrivé* ; xflow décide *ce qu'on en
  fait*. Aucun des deux n'a besoin de connaître l'intérieur de l'autre.

### 3.4 Rendre `xflow` multi-tenant et identifié (23 octobre 2026)

Décision de l'utilisateur : « je veux un moteur générique et général au lieu de tout refaire ». L'examen donne raison à cette
direction — l'écart est beaucoup plus petit qu'il n'y paraissait.

#### Ce qui marche déjà, et que personne n'avait vu

**`call_plugin` d'xcore propage DÉJÀ le tenant et le principal** (`xcore/kernel/api/contract.py`) :

```python
tenant_id = _current_tenant_id.get(None) or self.ctx.tenant_id
return await self.ctx.caller(..., caller=self.ctx.name, tenant_id=tenant_id, principal=get_current_principal())
```

Il les lit dans les ContextVar de la requête en cours. Donc un step xflow exécuté **pendant** une requête (déclenchement manuel
par son API) transmet déjà l'organisation et l'utilisateur : `doc.create` marcherait tel quel, avec les droits du déclencheur.

**Et xflow est déjà multi-tenant dans son stockage** : `tenant_id` indexé sur les flows, les runs, les secrets de webhook et les
composites, avec des contraintes d'unicité par `(tenant_id, name)`. Le modèle est là.

**Le trou est donc étroit et nommable** : l'exécution **hors requête** — cron, file d'attente, reprise après crash, réessai
différé. Là, `_current_tenant_id` est vide (repli sur `ctx.tenant_id`, figé à « default » au chargement) et
`get_current_principal()` rend `None` : notre `from_ipc` lève `unauthenticated` (401). Ce n'est pas l'IPC qui manque d'identité,
c'est l'exécution différée qui l'a perdue.

#### Ce qu'il faut changer dans xflow

1. **Passer l'identité explicitement au lieu de la deviner.** `WorkflowEngine` appelle aujourd'hui
   `self.plugin.call_plugin(plugin, action, payload)` — la signature étroite. Il doit appeler
   `self.plugin.ctx.caller(plugin, action, payload, caller=…, tenant_id=run.tenant_id, principal=run.principal)` : le run connaît
   déjà son organisation, il ne la donne simplement à personne.
2. **Décider qui est « on » dans un run.** Deux cas, deux réponses :
   - déclenchement **manuel, webhook ou IPC** : l'identité du déclencheur, capturée au déclenchement ;
   - déclenchement **cron** : il n'y a pas d'utilisateur. Il faut une **identité de service** déclarée par le workflow, dont les
     droits viennent d'xauth — jamais fabriqués par xflow.
3. **Ne JAMAIS stocker de permissions dans un run.** Un principal stocké est une capacité stockée : un run rejoué trois jours plus
   tard agirait avec les droits qu'avait son déclencheur, même parti depuis. Stocker `sub` + `tenant_id`, et **re-résoudre les
   droits à l'exécution** (xauth expose déjà `xauth.get_user`, `xauth.has_permission`). Un run qui ne peut plus résoudre son
   identité échoue — il ne se rabat pas sur un contexte système.
4. **Des grants, pas des rôles.** Notre moteur refuse les noms de rôle par construction (`PermissionEntry._not_a_role`), parce
   qu'ils sont du texte libre par organisation. xauth compose déjà rôles → grants : l'identité portée par un run doit donc
   contenir des **permissions**, exactement ce que `from_ipc` attend. xflow n'a rien à savoir des rôles.
5. **Les droits sur le workflow lui-même existent déjà** : `xflow:workflows:read|write`, `xflow:runs:read|write`, `xflow:admin`,
   tous `tenant_grantable` sauf l'admin. C'est le même modèle que le nôtre, rien à inventer.

#### Ce que ça achète, au-delà de notre moteur

L'identité de service du point 2 est **le même besoin que les clés d'API** (groupe 2, élément 6, laissé en suspens parce qu'il
dépend d'xauth). Un compte de service xauth, avec ses grants, sert les deux : les workflows planifiés et les intégrations
externes. Une mécanique, deux lignes de l'inventaire.

#### Essayé pour de vrai (23 octobre 2026)

`xflow` copié dans `app/xflows`, chargé sur l'hôte de développement (`loaded=10`), un workflow d'essai déployé et déclenché avec
le jeton de l'administrateur. Trois résultats :

1. **Le tenant passe** : le run porte `tenant_id = 97fdc93a-…` (la vraie organisation), plus « default ». La propagation
   explicite fait son travail.
2. **L'identité ne passe pas — et pas seulement pour le cron.** Le step échoue sur `unauthenticated`. La raison est plus large
   que prévu : le déclenchement HTTP rend `{"status": "pending"}` **immédiatement** et exécute le run en tâche de fond. Il n'y a
   donc **jamais** de requête en cours quand un step tourne, même pour un déclenchement « manuel ». L'identité de service n'est
   pas un confort pour les tâches planifiées : elle est nécessaire à tout.
3. **Deux défauts de portabilité corrigés dans la copie** : le chemin `app.xflows` codé en dur pour Alembic (le dossier doit
   garder ce nom, même si le plugin s'appelle `xflow`), et `get_service("ext.websocket")` qui **lève** au lieu de rendre `None`
   sur un hôte sans WebSocket — ce qui faisait échouer tout le chargement.

Trois façons d'en sortir, par coût croissant :

| | Comment | Limite |
|---|---|---|
| **A. Le principal voyage avec la tâche** | capturé au déclenchement, passé à la file **en mémoire**, jamais persisté | un run repris après un crash n'a plus d'identité : il échoue (fermé par défaut), ce qui est acceptable |
| **B. Stocker `sub`, re-résoudre à l'exécution** | durable, survit au crash | `xauth.get_user` ne rend PAS les permissions : il faudrait une action xauth nouvelle (`xauth.principal`) — donc toucher à xauth |
| **C. Comptes de service** | la seule réponse pour le cron, où il n'y a aucun déclencheur humain | xauth encore, et c'est la même mécanique que les clés d'API (groupe 2, élément 6) |

**Les trois limites sont levées** (23 octobre), et prouvées sur l'hôte de développement.

Le principe retenu : **ne jamais stocker de permissions, stocker un identifiant, re-résoudre les droits à chaque exécution**.

- **xauth** gagne UNE action additive, `xauth.user.permissions` — elle enveloppe `PermissionService.get_permissions_for_user`,
  déjà servi en HTTP à qui détient `rbac:read`. Elle rend une liste de grants, rien d'autre : aucune décision, aucun effet de
  bord, et elle n'est joignable que par les plugins nommés dans `allowed_callers` (xflow y a été ajouté).
- **Un run retient `context._actor_sub`** (colonne JSON déjà existante : pas de migration) et ses droits sont redemandés à xauth
  une fois par run. Un compte désactivé ou un grant retiré arrête le travail, au lieu de le laisser courir avec les droits
  d'hier. Rien de capable ne reste en mémoire, donc un autre processus fait aussi bien.
- **Un cron n'a personne derrière lui** : le run s'exécute au nom de **celui qui a déployé le workflow** (`deployed_by`, retenu
  au déploiement, relu à chaque exécution).

Mesuré : quatre exécutions déclenchées par le planificateur, sans que personne n'y touche, toutes `success`, toutes dans la
bonne organisation, toutes avec l'identité du déployeur, chacune rendant le vrai agrégat du moteur.

**Quatre défauts de xflow trouvés en chemin, et corrigés dans la copie.** Le déclenchement planifié ne pouvait pas fonctionner
sur cette version d'xcore : `SchedulerService.add_job` s'appelle `trigger=` (pas `trigger_type=`), nomme son identifiant
`job_id` (pas `id`), pose lui-même `args` (xflow en passait d'autres, d'où « multiple values for keyword argument »), et il est
**synchrone** (xflow l'attendait). S'y ajoute un cinquième défaut, de cloisonnement : le run planifié partait en organisation
« default » et l'identifiant de job ignorait le tenant — deux organisations déployant le même workflow s'écrasaient.

Enfin, une règle de méthode, apprise deux fois : **l'identité ne passe jamais par la charge utile d'une action**. `@schema`
jette les clés non déclarées (c'est ainsi que `deployed_by` disparaissait en silence), et une charge utile est forgeable par
l'appelant. Les routes HTTP passent donc leur `AuthPayload` authentifié à une méthode interne ; les actions IPC lisent la
ContextVar du superviseur.

#### Ce qui reste vrai malgré tout

xflow identifié et multi-tenant **orchestre** ; il ne porte toujours pas l'état d'un document. Les approbations (§3.2) restent
dans le moteur : un état sur le document, un `UPDATE` conditionnel pour la concurrence, le gel, les boutons de la fiche. Ce que
xflow devient, c'est l'exécuteur des conséquences — et le déclencheur planifié dont nous avions besoin.

#### Avant d'y toucher

`orchestra-backend` est un dépôt voisin, en lecture seule pour moi jusqu'ici. Rien n'y sera modifié sans accord explicite.

## 4. Ce qu'on vise

Les sept éléments des groupes 1 et 2 valent **7 lignes** de l'inventaire élargi (4 des 87, 3 des angles morts) : de 59 % à ~65 %. Le
pourcentage n'est pas le but (`palier70.md` §1 : l'inventaire est subjectif, chaque ligne vaut 1) ; le but est que les éléments 4 et 7
existent, parce que sans eux un administrateur ne peut ni restreindre ce qu'un collègue voit, ni défaire une suppression.
