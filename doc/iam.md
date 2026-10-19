# Accès et profils : xauth, les DocTypes, la RH

Demande du 19 octobre 2026 : *« je pensais que tu allais faire des doctypes pour le paramètre ; en plus le système d'auth doit être lié au système RH et au système de gestion
de profil de l'utilisateur, de sorte que rôles et permissions soient affectables et gérables ; le catalogue de permissions doit se présenter comme la vue table »*.
Décisions prises avec l'utilisateur : **source virtuelle** (xauth reste l'unique vérité, rien n'est copié), **`Employee.user` + désactivation du compte quand l'employé part**,
**`User Profile` séparé**, livraison dans l'ordre (source + Permission, puis Role et Member, puis profil et RH).

## 1. Les DocTypes virtuels (`source: <nom>`)

Un DocType ordinaire stocke ses documents dans `xdt_document`. Un DocType **virtuel** les laisse chez une *source* que le plugin propriétaire enregistre (`doctype_sources`,
`src/xcore_doctype/sources.py`). Le moteur applique tout le reste : droits par grants, schéma strict, liens, filtres typés, tri, pagination, recherche, agrégats
(`src/xcore_doctype/memory.py`), tableaux de bord, rapport, export CSV, commentaires et assignations.

| Le moteur | La source |
|---|---|
| grants, périmètre de lecture/écriture (`Scope.allows`), schéma strict, `read_only`, liens (existence et lisibilité) | `fetch_all(ctx, doctype)` : TOUS les documents de l'organisation (`StoredDoc`) |
| filtres, tri, pagination, recherche, agrégats **en mémoire**, mêmes règles que le SQL | `insert`, `update(version)`, `remove` : applique à SA vérité et la **revérifie** |
| refuse un type sans source enregistrée (**503** `source_unavailable`) : jamais une liste vide | erreurs du moteur (`ValidationFailed`, `NotFound`, `PermissionDenied`, `VersionConflict`…) |
| une panne de la source est un **502** sans détail (`source_failed` ; le détail va au journal) | `version` : un entier qui CHANGE avec le contenu (`content_version`) |

Refusé au chargement (`schema._source_rules`) : `single`, `istable`, `submittable`, `naming` autre que `hash`, `hooks`, `track_changes: true`, champs `Image`/`Attach`, défaut
`today`. Pas d'historique (la source a le sien), pas de hook (une écriture depuis un `HookTransaction` d'un DocType virtuel est refusée : `hook_not_supported`).
Prévu pour **quelques milliers de lignes** (membres, rôles, permissions) : chaque lecture est un appel à la source.

**Parité SQL / mémoire** : `tests/unit/test_virtual_doctypes.py` pose les MÊMES sept ventes dans un DocType ordinaire (SQL) et dans un DocType virtuel (source en mémoire) et compare
`list` (25 filtres × 9 tris), les pages, la recherche, 17 agrégats et le périmètre `if_owner`, sur SQLite et PostgreSQL. Changer une interprétation sans l'autre fait échouer ces tests
(mutations vérifiées).

Aussi nouveau dans le schéma : `read_only: true` sur un champ (montré, jamais modifiable par un client ; une mise à jour qui le change est refusée `read_only`, un hook ou une source
l'écrit), `naming: owner` (un document par utilisateur, nommé comme lui ; le second est refusé en 409) et le grant spécial **`@authenticated`** (tout utilisateur identifié, sans rôle ;
jamais déclaré à auth).

## 2. Le plugin `iam` (`app/iam`)

| DocType | Stockage | Contenu |
|---|---|---|
| `Permission` | virtuel (xauth), **lecture seule** | le catalogue : nom technique, description, plugin, groupe, délégable, active. Vue **Rapport** par défaut |
| `Role` | virtuel (xauth) | nom, description, portée (Plateforme / Organisation), table de permissions. Un rôle de l'organisation se crée, se modifie, se supprime ; un rôle de la plateforme se règle (permissions, description) par l'administrateur de la plateforme seulement, sans renommage ni suppression |
| `Member` | virtuel (xauth) | compte (e-mail, nom d'utilisateur, propriétaire, depuis quand : lecture seule), actif, rôle principal, rôles supplémentaires, permissions directes. Pas de création (on rejoint par invitation) |
| `User Profile` | table générique, `naming: owner` | nom affiché, photo, fonction, e-mail pro, téléphone, langue, fuseau, thème, signature |

**Droits.** Grants `iam:permission:read`, `iam:role:read|write|delete`, `iam:member:read|write|delete`, `iam:profile:write|delete` (`User Profile` : lire et créer = `@authenticated`,
écrire = `iam:profile:write` ou son propre profil). Ils sont déclarés à auth par le plugin et vont au rôle `admin` **et** au rôle `tenant_admin` (le propriétaire d'une organisation) grâce à
la clé `default_roles` de `rbac.declare` (`app/auth/src/main.py`) : un propriétaire ouvre ces écrans sans délégation. Seuls `iam:member:read` (l'annuaire) et `iam:profile:*` sont **délégables** ;
gérer les accès ne l'est pas.

**xauth reste l'autorité** (`app/auth/src/services/iam.py`, actions IPC `xauth.iam.*` dans `ipc_iam.py`, réservées aux plugins nommés dans `allowed_callers` : `xcore-doctype`, `iam`, `hr`).
Chaque appel porte l'organisation ET l'acteur ; xauth juge : propriétaire de l'organisation ou administrateur de la plateforme pour tout ce qui écrit, et pour lire rôles, permissions
et accès des membres. Un simple membre lit l'annuaire (e-mail, nom, actif : de quoi lier une fiche), sans rôles ni permissions. Garde-fous : on ne retire ni ne change ses propres accès,
on ne désactive pas le propriétaire ni soi-même, on ne retire pas le propriétaire, un rôle attribué ne se supprime pas, un nom de rôle est unique (plateforme comprise). Chaque
changement est dans le journal d'audit (`role.saved`, `role.deleted`, `member.access_changed`, `tenant.member_removed`).

Désactiver un membre ne le ferme que dans **cette** organisation (`TenantMember.user_tenant_status`) ; son compte reste actif ailleurs.

## 3. La RH

`Employee.user` est un lien vers `Member` (la flèche l'ouvre : rôles et permissions se gèrent sur sa fiche). Le plugin `hr` **requiert** `iam`. Le hook `hr.employee_access`
(`app/hr/src/access.py`, `before_save`) désactive le compte lié quand le statut passe à **« Parti »**. Si l'acteur ne peut pas (responsable RH non propriétaire), l'enregistrement est
**refusé** avec un message clair plutôt que de laisser croire qu'un compte est fermé. Revenir à « Actif » ne rouvre **pas** l'accès tout seul (c'est une décision, sur la fiche du compte).

## 4. Le profil et le Desk

« Mon profil » (menu de l'utilisateur) ouvre MA fiche, ou sa création ; le nom affiché et la photo de la barre du haut la suivent (`stores/profile.ts`), sans recharger. Dans Paramètres,
Utilisateurs, Rôles et Catalogue des permissions ouvrent la **liste** du DocType quand il est lisible (sinon les pages d'administration d'xauth restent le repli). Une liste de DocType
virtuel se trie par titre et n'a pas de colonne « Modifié » (la source ne date rien).

## 5. Ce qui n'est pas fait

- inviter quelqu'un depuis le Desk par une action sur `Member` (l'invitation se fait toujours dans Paramètres > Utilisateurs) ;
- créer un rôle de la plateforme (rare, hors DocType) ;
- les noms affichés des commentaires et des tâches viennent toujours de l'annuaire d'xauth (e-mail), pas du profil ;
- la recherche globale interroge la source de chaque DocType virtuel lisible (3 appels pour `iam`) ;
- un changement de rôle de la plateforme met 5 minutes à atteindre les autres organisations (cache des permissions d'xauth) ;
- un plugin de confiance qui appelle `xauth.iam.*` fournit lui-même l'acteur : même confiance que les hooks (`allowed_callers` le borne) ;
- `iam` et `hr` sont des plugins du dépôt (comme `sales` et `stock`), pas livrés dans la roue.

## 6. Tests

`tests/unit/test_virtual_doctypes.py` (parité, droits, liens, pannes), `tests/unit/test_profile_rules.py` (`@authenticated`, `naming: owner`), `tests/integration/test_iam.py` (VRAI xauth :
catalogue, rôles, fiches de membres, autorité, annuaire, profils, commentaires), `tests/integration/test_hr_access.py` (lien et désactivation), `ui/tests/profile.spec.ts`,
`ui/e2e/look-iam.mjs` (vrai navigateur).
