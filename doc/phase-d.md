# Phase D : la collaboration autour d'un document

Ce que les gens ajoutent AUTOUR d'un document sans le modifier : **commentaires**, **assignations** (« À faire ») et **pièces jointes**, réunis
avec les modifications dans un seul fil d'**activité** (comme la chronologie de Frappe), plus une page **Mes tâches**.

## La règle qui gouverne tout : on juge sur le document

On ne lit les commentaires, assignations et pièces jointes d'un document que si on peut LE lire (périmètre `if_owner` compris : un document hors
périmètre est « introuvable », ses commentaires aussi). Aucun droit nouveau à déclarer :

| Action | Droit exigé |
|---|---|
| Lire le fil, commenter | lire le document (un relecteur doit pouvoir répondre ; un document soumis ou annulé reste commentable) |
| Modifier un commentaire | en être l'auteur |
| Retirer un commentaire | en être l'auteur, ou écrire le document (modérer) |
| Assigner, joindre un fichier | écrire le document |
| Terminer / annuler / rouvrir une assignation | être l'assigné (terminer ou annuler la sienne), ou écrire le document (tout) |
| Changer la consigne ou l'échéance | l'avoir assignée, ou écrire le document |
| Retirer une pièce jointe | l'avoir jointe, ou écrire le document |
| Proposer des personnes à assigner | écrire le type |

Tout disparaît avec le document (suppression en cascade, fichiers libérés). Un document unique (réglage) n'a pas de fil.

## Côté serveur

- `collaboration.py` (`CollaborationService`) + trois tables système `xdt_comment`, `xdt_assignment`, `xdt_attachment` (RLS PostgreSQL
  automatique : elles portent `tenant_id`). Les méthodes de `Query` n'imposent que le tenant ; le service lit d'abord le document avec le
  périmètre de l'appelant. `tests/unit/test_query_leak.py` couvre chaque méthode (scénarios de fuite entre tenants).
- Routes (`api/collab.py`) : `GET /collab/{type}/{nom}`, `POST|PUT|DELETE /comments…`, `POST /assignments/{type}/{nom}`, `PUT /assignments/{id}`,
  `GET /my-assignments?status=open|done|cancelled|all`, `GET /people/{type}?q=`, `POST /attachments/{type}/{nom}`, `DELETE /attachments/{id}` ;
  actions `doc.collab.get`, `doc.comment.add`, `doc.assign`, `doc.assignment.update`, `doc.assignments.mine`.
- Bornes : commentaire 4000 caractères sans caractère de contrôle, 1000 commentaires, 50 assignations ouvertes, 100 pièces jointes par document ;
  une personne n'a qu'une assignation OUVERTE par document (409 `already_assigned`) ; échéance `AAAA-MM-JJ` valide.
- **Pièces jointes** : le fichier passe par `FileService` (champ réservé `_attachment` dans `xdt_file`, même stockage `ext.storage`, mêmes
  règles : type vérifié, nom nettoyé, 10 Mo). Il se lit comme le document auquel il est joint (lien signé de 5 minutes via `/file-links`) ; il est
  toujours servi EN TÉLÉCHARGEMENT (`attachment`, `nosniff`, CSP `sandbox`).
- **Annuaire des utilisateurs : xauth par défaut** (`directory.py`). Avec xauth l'identité d'un utilisateur est un UUID (le `sub` du jeton) : illisible,
  et seul xauth connaît les membres d'une organisation. xcore-doctype appelle donc `xauth.users.list.tenant` (appel de plugin d'xcore, mis en cache
  60 s par organisation) pour : **proposer** à l'assignation les membres de l'organisation (`GET /people/{type}`, par e-mail), **vérifier** qu'un
  assigné est bien membre de CETTE organisation (422 `unknown_user` sinon ; un e-mail est converti en identifiant, c'est l'identifiant qu'on stocke) et
  **afficher** des noms (`GET /users?ids=…` : `{id: {id, email, name}}`, seuls les membres de l'organisation de l'appelant sont connus). Les droits
  passent d'abord : on ne sonde pas l'annuaire pour qui n'a pas le droit d'assigner.
  Sans xauth (jeton de développement, plugin d'authentification qui n'expose pas l'action, xauth arrêté ou qui refuse l'appel) : repli SANS erreur
  sur les identités que l'organisation a déjà fait apparaître, l'identifiant sert de nom et n'importe lequel peut être assigné ; l'absence est
  retentée après 15 s. Réglage : `doctype.directory` du manifeste (`provider: xauth|none`, `plugin`, `action`, `ttl_seconds`).
  **Avec le xauth du dépôt (`app/auth`, `doc/xauth.md`) c'est actif** : son `plugin.yaml` déclare `allowed_callers: ["xcore-doctype"]` (l'hôte active `enforce_ipc`),
  sans quoi xcore répond `ipc_denied`. Testé contre le vrai xauth (`tests/integration/test_xauth_real.py`) et contre un plugin de test qui imite l'action.

## Côté Desk

- Sur la fiche d'un document enregistré (`CollabPanel.vue`) : à droite **Assignations** et **Pièces jointes**, à gauche l'**Activité**
  (`ActivityPanel.vue`) : composer (Ctrl+Entrée envoie), filtres Tout / Commentaires / Modifications, fil du plus récent au plus ancien
  (`lib/feed.ts`). Un commentaire s'affiche en TEXTE (aucun HTML interprété).
- **Des personnes, pas des identifiants** (`stores/users.ts`) : partout où un document porte une identité (auteur d'un commentaire, assigné, propriétaire, fil
  d'activité, historique), le Desk affiche l'e-mail (un lot de requêtes `/users`, une fois), l'UUID abrégé en attendant ou si la personne n'est plus membre.
  Le sélecteur d'assigné liste les membres par e-mail.
- **Mes tâches** (`/tasks`, entrée de la barre latérale avec la pastille du nombre de tâches ouvertes, et la barre de commande) : l'échéance la plus
  proche d'abord, une échéance dépassée est signalée, on termine ou rouvre d'ici. Une tâche dont on ne peut plus lire le document n'y est pas.
- `ui/e2e/look-collab.mjs` rejoue le parcours avec deux utilisateurs (alice commente, assigne, joint ; bob voit la pastille, termine, répond ;
  suppression du document jetable, son fil disparaît).

## Pas encore

Notifications (e-mail ou dans l'en-tête) quand on vous assigne ou répond, mentions `@utilisateur`, étiquettes, réponses en fil, texte enrichi dans
un commentaire, assignation automatique, journal d'activité global. Qui a CLOS une assignation n'est pas conservé (le fil dit « terminée » sans
l'auteur). Les assignations ne se suppriment pas (on les annule) : elles disparaissent avec leur document.
