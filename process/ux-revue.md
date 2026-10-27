# Revue d'expérience utilisateur du Desk (20 octobre 2026)

Demande : « on va améliorer le UI pour l'expérience utilisateur au max de la max ». Ce document dit comment on a mesuré, ce qui a été constaté, ce qui a été corrigé, comment c'est vérifié, et ce qui reste. Il prolonge `process/desk-revue.md` (mobile, mode sombre, session) sans le répéter.

## 1. Comment on a constaté

Deux scripts Playwright, rejouables contre un hôte réel avec des données réelles (`ui/e2e/README.md`) :

- `ui/e2e/audit.mjs` **mesure** les écrans principaux (accueil, espace, liste, fiche, nouveau document, bon soumis, tâches, paramètres, documentation de l'API, barre de commande, liste avec sélection, liste filtrée, connexion) en clair et en sombre, sur ordinateur et sur téléphone : contraste de chaque texte visible contre son fond effectif (seuil WCAG AA, 4,5:1), taille des cibles (24 px au moins), nom accessible des boutons et des champs, débordement horizontal, erreurs de console. Un écran passe ou il liste ses manquements.
- `ui/e2e/look-ux.mjs` vérifie 22 **comportements** : titre d'onglet, focus après un changement de page, lien d'évitement, repère de focus, ligne cliquable, barre de sélection, Échap, liste vide, deux colonnes, en-tête qui reste visible, focus sur l'erreur, champ lien, salutation, cartes sur téléphone.

Chromium seulement (pas Firefox ni Safari), aucun lecteur d'écran réel : voir § 6.

## 2. Constats et corrections

| Constat | Correction |
|---|---|
| Texte discret (3,9:1), texte de substitution des champs (2,5:1 en clair, 4,1:1 en sombre), boutons « texte » et « contour » secondaires (2,7:1) et dangereux (3,5:1) sous le seuil AA. | Jetons du thème (`theme/frappe.ts`) : 5,6:1, 4,5:1 / 5,4:1, gris 700, rouge 700. Les couleurs `--desk-danger*` suivent. |
| Repère de focus gris 300 sur blanc : 1,3:1, invisible. | Anneau de 2 px sur `{text.color}` (19,8:1 mesuré), qui suit le schéma clair/sombre. |
| En-tête de tableau **blanc en mode sombre** (`--p-surface-50` écrit en dur). | `--desk-subtle`. Règle : aucune couleur ne s'écrit dans un composant. |
| Cases à cocher de 16 px, boutons de 20 px : cibles trop petites. | Case de 1,25 rem avec zone de toucher élargie ; minimums sur marque, sélecteur de vue, sections. |
| Aucun lien d'évitement ; le titre d'onglet ne changeait jamais ; après un changement de page le focus restait sur le menu. | `AppShell` : lien « Aller au contenu » (premier élément focalisable), `<main tabindex="-1">`, titre d'onglet « Page · Organisation » (`stores/preferences.ts`), focus posé sur le `h1` à chaque changement de page (la première page ne vole pas le focus). |
| Un clic sur une ligne de liste ne faisait rien : il fallait viser le lien. | La ligne ouvre le document, sauf un clic sur un lien, un bouton, un champ ou la case à cocher (ou pendant une sélection de texte). |
| Aucun retour quand on coche des lignes ; l'action en lot n'apparaissait qu'ensuite. | Barre de sélection (compteur, actions, « Tout désélectionner »), Échap la vide (sauf dans un champ de saisie ou une boîte ouverte). |
| Premier chargement : liste blanche puis saut. Liste vide : texte sec. | Lignes squelettes ; état vide avec un bouton « Créer le premier… » **seulement** si aucun filtre ne cache des documents. |
| **Défaut préexistant** : une liste d'un type sans champ Select/Lien/Booléen était écrasée dans la colonne étroite de la barre latérale. | `lib/sidebar.ts` : la colonne n'existe que si la barre a de quoi proposer (`hasSidebar`). |
| Fiche : tous les champs en une colonne étroite sur un grand écran. | Une section sans titre et à une colonne est rangée en deux colonnes quand la place le permet (`container query`) ; texte long, code, JSON, tableau gardent toute la largeur (`isAutoSection`, `isWide`). |
| Enregistrement refusé : un bandeau, et il fallait chercher le champ. | Focus et défilement vers le premier champ en cause, liste des erreurs en tête avec un lien par champ (« Lignes › Quantité (ligne 2) » pour une cellule). |
| Barre d'actions de la fiche encombrée (Renommer, Dupliquer, Habiller, Supprimer à côté d'Enregistrer). | Menu « ⋯ » (`more-actions`) pour ce qu'on fait rarement ; Supprimer y est en rouge, après un séparateur. Enregistrer et Soumettre restent visibles. |
| En-tête de page qui défile hors de vue : Enregistrer hors d'atteinte sur une longue fiche. | En-tête collant (statique sous 640 px, où il prendrait trop de place). |
| Champ lien : boutons « + » et « ouvrir » hors du champ (largeur différente des autres) ; le texte tapé était perdu au clic ailleurs. | Boutons dans le champ ; même largeur que les autres (475 px / 475 px mesurés) ; le texte tapé est gardé jusqu'au choix ; premiers choix dès l'entrée dans le champ. |
| Téléphone : le tableau de liste défile de côté. | Sous 700 px, une **carte** par document (`RecordCards` : titre, trois champs, état, heure relative) ; toucher la carte l'ouvre, la case ne l'ouvre pas. |
| Accueil sans accueil. | Salutation selon l'heure, rangée « Créer » (types déjà utilisés d'abord, six au plus, jamais un réglage ni un type virtuel), « À faire » (tâches les plus proches, en retard signalées), récents, espaces. |
| Une icône nommée dans un YAML (`icon: flask`) qui n'existe pas dans PrimeIcons laissait un trou. | `lib/icons.ts` `piIcon(nom, repli)` : on vérifie dans le navigateur que la classe dessine quelque chose, sinon l'icône de repli. |

## 3. Vérification

- `audit.mjs` : **tous les écrans audités passent** (contraste, cibles, noms, débordement) et la console est propre, en clair, en sombre et sur téléphone.
- `look-ux.mjs` : 22 comportements ✓ ; `look-h2.mjs` (qui passe désormais par le menu « ⋯ » pour renommer et dupliquer) ✓ ; `desk.e2e.mjs` via `tests/e2e` ✓.
- vitest : 55 fichiers, **1232 tests**, plus aucune erreur non gérée. Les nouveaux comportements sont couverts par `tests/ux.spec.ts` (icônes, barre latérale, colonnes, cartes), `tests/list-ux.spec.ts` (clic, sélection, Échap, liste vide), `tests/appshell.spec.ts` (titre, focus, lien d'évitement), `tests/formview.spec.ts` (focus sur l'erreur) et `tests/home.spec.ts` (salutation, création). Contrôle par mutation : défaire la garde du clic de carte, la garde d'Échap, la condition de la liste vide, le focus de page, le lien d'évitement ou le focus sur l'erreur fait échouer un test.
- `pnpm typecheck` (`vue-tsc -p tsconfig.app.json`) est la vraie vérification de types : `vue-tsc --noEmit` seul ne regarde rien (le tsconfig racine n'inclut aucun fichier) et avait laissé passer une clé de traduction supprimée et une chaîne `v-if` cassée qui masquait le fil d'activité.

## 4. Les « erreurs non gérées » de vitest : cause établie

Les deux rejets `insertBefore` / `nextSibling` d'un nœud nul, signalés depuis `process/phase-h2.md`, venaient des tests : `afterEach(() => document.body.innerHTML = '')` effaçait la page pendant que les composants vivaient encore ; une boîte PrimeVue téléportée dans `<body>` était ensuite corrigée sur un nœud sans parent. Les tests démontent maintenant leurs composants **avant** de vider la page (`unmountAll()` dans `tests/views-fixtures.ts`). Les autres fichiers qui vident `document.body` sans démonter ne produisent pas l'erreur aujourd'hui, mais le même piège les attend : un nouveau test qui monte une boîte doit démonter.

## 5. Conventions à garder

- Aucune couleur écrite dans un composant : jetons PrimeVue sémantiques ou variables `--desk-*` définies par schéma dans `desk.css`. Tout nouveau texte coloré se contrôle avec `audit.mjs` en clair ET en sombre.
- Icône venue d'un YAML : `piIcon(nom, repli)`, jamais `` `pi pi-${nom}` ``.
- Une action rare va dans le menu « ⋯ » de la fiche (`moreItems`), avec un `testid`. Les tests l'ouvrent avec `tests/menu.ts` (`openMore`, `menuItem`).
- Cibles ≥ 24 px, nom accessible sur tout bouton qui n'a qu'une icône.
- Un nouveau type de page garde son `h1` : le titre d'onglet et le focus en dépendent.

## 6. Ce qui reste

- Aucun essai avec un vrai lecteur d'écran (NVDA, VoiceOver, Orca), ni sous Firefox et Safari ; pas d'annonce `aria-live` du nombre de résultats d'une liste ; les notifications (PrimeVue) n'ont pas été écoutées avec un lecteur.
- Page de connexion : marque et message d'accueil, « Utiliser un jeton » à cacher pour l'utilisateur final.
- Listes : puces de filtres actifs, colonnes et densité réglables, filtres enregistrés par l'utilisateur (phase C).
- Fiche : squelette de chargement, barre d'enregistrement fixée en bas sur téléphone, brouillon récupéré après une coupure (enregistrement automatique).
- Menu des notifications et barre de commande : à passer à la même revue.
- Hors UI : `POST /notifications` d'xauth accepte un expéditeur falsifiable (non corrigé) ; l'historique git garde `app/` et `data/` des commits antérieurs (pas de réécriture d'historique faite).
