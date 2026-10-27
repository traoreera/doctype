# Phase G : le Desk « espace de travail »

Première phase du palier 70 % (`process/palier70.md`). Cible visuelle donnée par l'utilisateur : le Desk de Frappe (barre latérale d'espaces à icônes, barre de recherche et de commande, avatar, page d'espace). Critère de sortie du contrat :

> Le module Stock et le module Ventes s'ouvrent chacun sur leur espace ; un utilisateur sans droit sur un type n'en voit ni raccourci ni compteur ; la barre de commande trouve un type, un nouveau document, un récent.

## 1. Livré

**Serveur**

- **Espace de travail déclaratif** (`src/xcore_doctype/workspaces.py`) : un fichier YAML par module (`<plugin>/workspaces/*.yaml`) avec `label`, `icon`, `description`, `order` et des **blocs** d'une liste fermée (`header`, `shortcut`). Aucun bloc n'exécute quoi que ce soit.
- Chargé par le service `doctype_registry` (`load_workspaces(self.ctx.name, dossier)`), avec les mêmes règles que les DocTypes : le `name` de l'espace est le nom du plugin, un raccourci vise un DocType déjà chargé (pas une table de lignes), tout ou rien, retiré avec le plugin.
- `GET /workspaces` (et l'action `meta.workspaces`) : les espaces **filtrés par les droits** de l'appelant. Un raccourci vers un type illisible disparaît, une section sans raccourci aussi, un espace vide aussi ; `create` (le « + ») n'est offert qu'à qui peut créer. Un module sans fichier d'espace en reçoit un fabriqué (un raccourci par type lisible).
- `icon` sur un DocType (nom PrimeIcons sans préfixe, validé : une valeur libre ne porte pas de classe CSS) ; `xcore-doctype check` valide aussi l'espace du plugin ; espaces fournis pour `sales` et `stock`.

**Desk**

- **Barre latérale par espaces** : icône, libellé, flèche qui déplie les types de l'espace ; l'espace de la page courante est déplié d'office et en évidence ; les choix de l'utilisateur et le repli complet (bouton de l'en-tête) sont mémorisés sur l'appareil.
- **Barre de commande** (Ctrl+G comme Frappe, Ctrl+K) : aller à un type, à un espace, à un récent ; « Nouveau : … » là où l'on peut créer ; thème, déconnexion. Recherche sans accent ni majuscule, tous les mots doivent figurer, un mot qui commence le libellé passe devant, flèches, Entrée, Échap.
- **Page d'un espace** : titre, icône, description, sections, raccourcis avec **le nombre de documents** (lu par une requête d'un seul document, dans le périmètre de l'utilisateur) et « + ».
- **En-tête** : champ de recherche avec son raccourci, avatar à initiales avec le menu (thème, déconnexion). L'accueil montre les derniers documents et les espaces.
- Tout reste utilisable au téléphone (tiroir) et en thème sombre.

## 2. Preuves

Serveur : `test_workspaces.py` (définition, chargement, filtrage par droits, ordre, espaces fabriqués, icône), test d'intégration sur un vrai hôte (`GET /workspaces` filtré, action, 401 sans identité). Desk : `commands.spec.ts` (classement), `palette.spec.ts` (composant), `workspace.spec.ts` (page, chiffres, droits refusés), `appshell.spec.ts` (barre latérale, repli, tiroir, avatar, raccourcis clavier), `navstate.spec.ts`, `home.spec.ts`. Scénario Chromium : espaces dans la barre latérale, page d'espace avec chiffre exact, Ctrl+G puis saisie puis Entrée, repli mémorisé après rechargement. Chaque règle a été vérifiée par mutation côté serveur (filtre par droits, sections vides, « + » sans droit de créer, propriété du nom, retrait avec le plugin).

## 3. Écarts avec Frappe et limites

- **Pas de blocs riches** : la page d'un espace n'a que des titres et des raccourcis. Cartes de nombres, graphiques et listes rapides sont la phase J (elles s'ajouteront comme nouveaux types de bloc).
- **Pas de personnalisation par l'utilisateur** : pas de « Modifier » ni « Créer un espace » (phase L) ; un espace se décrit dans le plugin.
- **La barre de commande ne cherche pas dans les documents** (recherche globale : phase E du palier 40 %). Elle va aux types, espaces et récents.
- **Pas de cloche de notifications ni de menu d'aide** : les notifications sont en K ; une icône sans rien derrière serait un manque de plus.
- **Un compteur par raccourci** : une requête par raccourci (quatre à la fois). Sur un espace de beaucoup de raccourcis, un point d'agrégation unique serait préférable (phase J).
- Les types d'un module déclaré mais absents de son fichier d'espace ne sont atteignables que par la barre de commande et l'accueil.
