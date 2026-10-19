# Revue du Desk (après la phase A)

Demande : « on va revoir le frontend, il y a trop de manquements ». Ce document dit ce qui a été constaté, ce qui a été corrigé, et ce qui reste.

## 1. Comment on a constaté

Hôte réel, données réelles (clients, articles, entrepôts, factures, mouvements de stock brouillon, soumis et annulé), Chromium en 1280 × 800, en 390 × 844 (téléphone) et en préférence « sombre ». Les captures de la revue ne sont pas livrées (elles étaient dans un dossier temporaire) : le scénario de bout en bout en reprend les points (`ui/e2e/desk.e2e.mjs`). La console du navigateur était propre : les manques étaient d'usage, pas de bogues JavaScript.

## 2. Constats

| # | Constat | Gravité |
|---|---|---|
| 1 | **Téléphone inutilisable** : la barre latérale disparaissait sans bouton pour la rouvrir (plus aucune navigation), l'en-tête et les boutons d'action débordaient de l'écran, le numéro de document se coupait en trois lignes, les lignes d'une table se lisaient par défilement latéral. | bloquant |
| 2 | **Listes sans identifiant** : trois mouvements de stock s'affichaient « Transfert / Sortie / Réception », sans leur numéro (`MS-2026-00003`) ; les factures sans `FA-…`. Impossible de désigner un document. | bloquant |
| 3 | **Pas de mode sombre**, même pour qui le demande à son système. | important |
| 4 | **Session de 30 minutes** avec xauth : le Desk ignorait le jeton de renouvellement et renvoyait à la connexion à chaque expiration. | important |
| 5 | **Accueil** : une copie de la barre latérale, des modules affichés par leur identifiant technique (« SALES », « STOCK »), ni récents ni raccourci de création. | important |
| 6 | **Saisie d'un mouvement** : date vide à chaque fois, « Entrepôt source » et « Entrepôt cible » toujours affichés quelle que soit la nature (une réception n'a pas de source), aucune unité à côté des quantités, la souris obligatoire pour ajouter une ligne. | important |
| 7 | **Nombres** (plafond, total, quantité) alignés à gauche comme du texte, sans unité. | moyen |
| 8 | **Messages avec des identifiants** : « Article '2a120aa438f9' est utilisé par Facture ». | moyen |
| 9 | **Notifications empilées** : cinq « Document créé » à la suite masquaient la page. | moyen |
| 10 | Pastilles de statut petites et peu contrastées ; recherche de l'en-tête tronquée (« Chercher un type de docume… »). | mineur |
| 11 | Titres de liens en lecture seule (document figé) qui restaient sur l'identifiant : un défaut RÉEL de réactivité, trouvé pendant la phase A et corrigé alors. | corrigé avant |

## 3. Corrigé

1. **Mobile et navigation** : sous 900 px la barre latérale est un tiroir (bouton dans l'en-tête, il se ferme au choix d'une page, sur Échap ou en touchant le fond) ; les en-têtes passent à la ligne ; les tableaux défilent dans leur cadre ; une ligne de table devient une fiche (« Ligne 1 », un libellé par cellule).
2. **Listes** : première colonne = numéro du document pour un DocType numéroté ; nombres et quantités à droite (chiffres de même chasse), avec leur unité (« 120,000 pcs ») ; pastilles plus lisibles ; les messages de refus de suppression nomment le document par son titre.
3. **Formulaires** : date du jour par défaut (`default: today`, dans le fuseau de l'ORGANISATION, pas celui du navigateur) ; **conditions dans les lignes** : une colonne que la nature du document exclut disparaît, une cellule rendue obligatoire par le parent est signalée et réévaluée par le serveur ; unités (`unit_from`) ; `Tab` dans la dernière cellule ajoute une ligne.
4. **Accueil** : libellés de module lisibles, 8 derniers documents ouverts (par utilisateur et organisation), « + » de création là où l'on peut créer.
5. **Thème sombre** : choix Clair / Sombre / Automatique dans le menu de l'utilisateur, mémorisé, « automatique » suit le système même en cours de route. Tous les composants passent par des jetons sémantiques ou des variables définies par schéma.
6. **Session** : renouvellement du jeton une minute avant son expiration (jeton de renouvellement à usage unique : un seul échange à la fois), rejeu UNE fois d'une requête qui reçoit un 401, déconnexion qui révoque la session côté serveur, avertissement deux minutes avant l'expiration d'un jeton de développement (non renouvelable).
7. **Notifications** : une seule à l'écran, la dernière.

Côté serveur, pour ces écrans : `/meta` envoie `create` par type, `timezone`, les conditions de ligne en arbres et `unit_from` ; le défaut `today` se résout à la création.

## 4. Preuves

Dernière exécution complète : **1893 tests pytest réussis, 6 ignorés** (SQLite et PostgreSQL, navigateur et installation depuis le wheel compris), **494 tests vitest**, `vue-tsc` sans erreur (avant la revue : 1851 et 415).

Tests vitest (`tests/*.spec.ts`) : `rows`, `home`, `appshell`, `theme`, `recents`, `session`, `api`, `formview`. Chaque comportement ci-dessus a été vérifié par mutation (on casse la ligne qui le porte, un test doit échouer), y compris : le renouvellement jamais rejoué en boucle, un seul échange simultané, la valeur d'une cellule écartée qui ne part pas, la colonne masquée, le fuseau de la date du jour, le menu qui se referme. Côté serveur : `test_row_conditions.py`, `test_today_and_units.py`. Scénario Chromium : accueil, colonnes selon la nature, unité, `Tab`, thème sombre mémorisé après rechargement, téléphone (menu en tiroir, aucun débordement horizontal).

## 5. Reste à faire (non traité)

- **Accessibilité** : pas d'audit au lecteur d'écran ni de piège de focus dans le tiroir ; le contraste des pastilles n'est pas mesuré.
- **Listes** : pas de colonnes configurables, pas de filtres enregistrés, pas d'export (phase C du palier 40 %).
- **Saisie** : pas de duplication d'un document, pas de réordonnancement des lignes, pas de valeurs par défaut calculées autres que `today`.
- **Renouvellement** : deux onglets qui partagent un jeton de renouvellement (onglet dupliqué) se le disputent (il est à usage unique) ; le second est déconnecté. Pas de MFA.
- **Accueil** : pas de compteurs ni d'alertes (seuil de réapprovisionnement : phase C).
- **Tauri** : la couche de transport reste remplaçable, mais rien n'a été essayé hors navigateur.
