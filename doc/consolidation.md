# Consolidation (point 2) : compte rendu

*23 octobre 2026. Branche `consolidation-desk`, empilée sur `extensions-plugins` (D26-D28). Plan : `.plan/plan.md`, phases C2 à C4.*

## 1. Ce qui a été fait

| Chantier | Résultat | Où |
|---|---|---|
| C3.1 Session partagée entre onglets | Un onglet dupliqué n'use plus le jeton de renouvellement de l'autre : le dernier couple est partagé par lignée, les renouvellements passent un par un sous un verrou, une déconnexion vaut pour la lignée | `ui/src/lib/tabsync.ts`, `stores/session.ts` |
| C3.2 Tiroir mobile | Fenêtre modale au clavier (`inert`, piège du focus, retour au bouton) ; contraste des pastilles mesuré (6,3 à 10:1) | `AppShell.vue`, `ui/tests/pill-contrast.spec.ts` |
| C4.1 Règles de notification | DocType `Notification Rule` (plugin `system`) : événement, condition, destinataires, cloche et e-mail (`ext.email`) après le commit | `notification_rules.py` |
| C4.2 Écran « Fichiers » | Les fichiers que l'appelant peut lire, « Détacher » par l'écriture ordinaire | `file_library.py`, `FilesView.vue` |
| C4.3 Propriétés par organisation | `read_only`, `mandatory`, `default` appliqués par le serveur, réservés à qui écrit tout le type | `overrides.py`, `builder.FormRules`, `OverrideEditor.vue` |
| C2 Preuves | `target_status` (état de la cible d'un lien) et rétention d'historique prouvés ; deux défauts trouvés et corrigés | `tests/unit/test_link_status.py`, `test_versions.py` |

## 2. Méthode

Chaque chantier a été écrit, puis relu par un agent ADVERSARIAL qui ne modifiait rien : chaque constat devait être reproduit (script ou
test), puis corrigé avec un test qui échoue sans la correction. Les gardes importantes ont été vérifiées par mutation (la garde retirée,
le test échoue). Les reproductions des relecteurs ont été rejouées après correction : toutes échouent désormais.

## 3. Constats des relectures (tous corrigés)

**C4.1, règles de notification (7 constats)**
- Le motif des variables (`\{\{\s*([^{}]*?)\s*\}\}`) revenait en arrière en O(n³) : une règle dont l'objet contenait `{{` suivi de
  milliers de blancs figeait la boucle d'événements de TOUT le processus (27 s pour 4000 blancs). Motif linéaire, longueurs bornées
  avant toute expression régulière.
- Ce que les hooks écrivent par `event.tx` (le grand livre de stock, par exemple) et les documents qu'un renommage réécrit ne
  déclenchaient aucune règle ; une règle « Suppression » qui prévient les assignés ne prévenait personne (les assignations disparaissent
  avec le document avant le commit).
- Chaque écriture de chaque organisation lançait une lecture en contexte système et une ligne d'audit, même sans règle : les règles
  sont maintenant lues par organisation, au plus toutes les 30 s.
- `xcore-doctype install` supprimait en silence un YAML que l'opérateur avait posé dans `app/system/` : il ne retire plus que ce
  qu'une install précédente avait posé (`.shipped`) et le dit.
- Une garde de test avait glissé dans un commentaire.

**C4.2, écran « Fichiers » (6 constats)**
- Défaut du MOTEUR mis au jour : le masque `********` d'un secret, renvoyé tel quel par un formulaire, était comparé au secret chiffré
  par les contrôles `read_only`, `set_only_once` et « figé après soumission » : la modification était refusée. Le masque vaut maintenant
  « inchangé » partout (`sealing.kept`).
- Une valeur au préfixe scellé dans un champ non secret était masquée à la lecture puis écrasée en silence : refusée à l'écriture.
- Défaut de COLLABORATION préexistant : joindre, détacher, assigner ne testaient que le grant du type, pas le périmètre d'écriture du
  document : un rédacteur de ses seuls documents retirait les pièces jointes de ceux des autres.
- L'union SQL dépassait la limite de 500 termes de SQLite au-delà de 250 types lisibles.
- « Détacher » ignorait les conditions des champs ; cinq mutations survivaient à la suite.

**C4.3, propriétés par organisation (6 constats)**
- Le plus grave : la règle liait exactement ceux qui pouvaient la retirer. Un rédacteur refusé par un `read_only` supprimait l'habillage,
  enregistrait, le remettait ; un utilisateur sans grant (écriture `@authenticated` + `if_owner`) imposait des règles à toute
  l'organisation. Poser ou retirer une règle exige maintenant un périmètre d'écriture TOTAL du type.
- États bloqués (une règle contre une condition), défaut d'organisation qui rendait facultatif un champ obligatoire du YAML, « masqué »
  sur un champ obligatoire (création impossible dans le Desk), Kanban et Calendrier qui ignoraient `read_only`, trois mutants vivants.

**C2, liens et historique (5 constats)**
- La soumission figeait un lien vers une cible annulée depuis ; l'amendement devenait impossible ; une nouvelle ligne pouvait citer une
  cible annulée déjà citée par une autre ligne.
- Confidentialité, préexistant : un document qui reprenait le nom d'un document supprimé héritait de son historique (et du contenu
  supprimé), et la rétention de l'un purgeait les entrées de l'autre. L'historique est maintenant tenu par génération d'un nom.
- L'action de purge d'historique était ouverte à tout plugin de confiance, pour tous les tenants : renommée `ops.history.purge`, fermée
  par le manifeste livré et réservée à un jeton qui porte `cross_tenant_grant`. **Changement incompatible** pour les scripts
  d'opérateur qui appelaient `doc.history.purge`.

## 4. Décisions

- L'e-mail des règles passe directement par l'extension `ext.email` de l'hôte, pas par un nouvel IPC d'xauth (prévu par le plan) :
  l'appartenance à l'organisation vient de l'annuaire du moteur ; sans annuaire, pas d'e-mail.
- Une règle de notification n'est acceptée que si son auteur peut lire TOUS les documents du type (on ne diffuse pas ce qu'on ne lit pas
  soi-même). Ce droit est jugé à chaque enregistrement de la règle, pas à l'envoi.
- Les règles de formulaire d'une organisation sont réservées à qui écrit tout le type ; libellés, masquage et disposition gardent la
  règle d'avant (écrire le type).
- Sur un champ obligatoire du YAML, un défaut d'organisation n'est qu'un pré-remplissage du Desk : une création par l'API doit toujours
  l'envoyer.
- L'identité des commits de cette session est désormais `Claude <noreply@anthropic.com>` (exigence des hooks du dépôt).

## 5. Limites connues

- Notifications : pas de destinataires par rôle (l'annuaire ne connaît pas l'appartenance aux rôles), pas de règle par date (il faut des
  tâches planifiées) ; le droit de lecture des destinataires n'est jamais vérifié (le moteur ne connaît pas les grants d'un membre) ;
  une règle survit à la rétrogradation de son auteur.
- Fichiers : ceux d'un champ d'extension D26 retirée ne sont ni listés ni libérés ; les types virtuels qui ont des pièces jointes sont
  relus en entier à chaque liste.
- Propriétés : pas de règle sur les lignes de table, un défaut Link n'est pas vérifié à l'enregistrement, le premier enregistrement d'un
  document unique est une création (un `read_only` d'organisation ne le lie pas), deux enregistrements concurrents d'habillage se
  remplacent sans prévenir (comme tous les habillages).
- Liens : l'annulation d'un document encore cité n'est pas refusée ; une annulation concurrente n'est pas bloquée par le verrou du
  contrôle de lien (résultat équivalent à « lier puis annuler »).

## 6. Chiffres

- Python, `tests/` entier sur SQLite après les quatre fusions (unitaires, intégration, e2e Chromium et livraison compris) : 2926 réussis,
  1625 ignorés (variantes PostgreSQL et vrai xauth absent) ; e2e et livraison : 11 sur 11, aucun ignoré.
- Desk : 61 fichiers, 1319 tests vitest ; `vue-tsc -p tsconfig.app.json` sans erreur ; build Vite.
- PostgreSQL 16 jetable, fichiers concernés : C4.2 475 réussis, C2 `tests/unit` 4061 réussis.
- Inventaire Frappe (`doc/palier70.md`) : 59 ✔, 8 ◐ sur 87, soit 72 % avec les demi-lignes (71 % le 22 octobre) ; inventaire élargi
  (119) : 54 % en entier, 61 % avec les demi-lignes.
