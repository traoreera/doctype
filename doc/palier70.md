# xcore-doctype : palier 70 % de Frappe

Décision de l'utilisateur, 17 octobre 2026 : « on élève tout le système à 70 % de Frappe, et le Desk doit ressembler à celui de Frappe ». Ce document rend ce « 70 % » **mesurable** et ordonne le travail. Il prolonge `doc/palier40.md` (phases A à F), qui reste le contrat des 40 %.

## 1. Ce que veut dire « 70 % »

« 70 % de Frappe » n'est pas une unité. Le contrat de 40 % prévenait déjà : *« 40 % peut devenir 70 % sans que cela se voie »*. Ici la dérive est voulue, donc on la **mesure** : un inventaire de fonctionnalités du **framework** Frappe (pas d'ERPNext : comptabilité, vente, RH ne sont pas dans le périmètre), chacune avec un état. Le pourcentage est le rapport du nombre d'éléments faits ou planifiés au nombre d'éléments de l'inventaire.

Limites de la mesure, à lire avant de s'y fier :

- l'inventaire est **le mien**, établi de mémoire sur le framework Frappe (pas sur un décompte officiel) ; deux personnes en dresseraient deux listes différentes ;
- chaque ligne compte pour 1, qu'elle vaille deux jours ou trois mois ;
- « fait » veut dire « fait dans ce dépôt et prouvé par des tests », pas « équivalent à Frappe en détail ».

## 2. Inventaire et état

Légende : **✔** fait (palier 20 % ou phase A) · **A-F** planifié au palier 40 % (`palier40.md`) · **G-M** planifié ici · **✖** hors périmètre, même à 70 %.

### DocType : champs et propriétés

| Fonctionnalité | État |
|---|---|
| Texte, nombre, date, case à cocher, liste, lien, table (8 types) | ✔ |
| Décimal exact | ✔ (A) |
| Valeur par défaut, obligatoire, masqué, lecture seule, description | ✔ |
| Conditions `depends_on` / `mandatory_depends_on` / `read_only_depends_on`, dans les lignes | ✔ |
| Sections, colonnes, sections repliables | ✔ |
| Numérotation par série, `hash` | ✔ |
| Documents enfants (tables) | ✔ |
| Soumission, annulation, amendement | ✔ (A) |
| Historique des modifications | ✔ |
| Unité affichée, défaut « aujourd'hui » | ✔ |
| Date-heure, heure, durée | ◐ Datetime (H) ; heure et durée : non |
| Entier, monétaire, pourcentage | ◐ Currency (H) ; entier, pourcentage : non |
| Texte long, texte enrichi (éditeur), code, JSON | ◐ LongText (H) ; éditeur, code, JSON : non |
| Pièce jointe, image | ✔ (H, M) |
| Lien dynamique (la cible dépend d'un champ) | H |
| Sélection multiple (table de liens) | ◐ table de liens, sans saisie par étiquettes |
| Mot de passe, couleur, note (étoiles), téléphone | ◐ couleur, note, téléphone (H) ; mot de passe : non |
| Unicité (`unique`), index de recherche, `set_only_once`, `no_copy` | H |
| `fetch_from` (recopie d'un champ du document lié) | H |
| Champs modifiables après soumission (`allow_on_submit`) | H |
| Règles de nommage : par champ, par saisie, par format | ◐ série et `owner` ; par champ, par saisie : non |
| DocType unique (Single : réglages) | ✔ (H) |
| DocType arborescent (Tree) | ✔ (I) |
| Renommer un document (et suivre ses liens) | H |
| Dupliquer un document | H |
| Niveaux de permission par champ (permlevel), masquage de champs | F |
| Hooks : `before_insert`, `after_insert`, `on_trash`, `after_delete`, `before_cancel`, `on_update_after_submit` | ◐ validate, before_save, after_save, on_submit, on_cancel ; on_trash, after_delete… : non |
| DocType virtuel (données hors base) | ✔ **hors décompte** (c'était ✖ ; fait pour xauth, `doc/iam.md`) |

### Permissions et partage

| Fonctionnalité | État |
|---|---|
| Droits par grant, `if_owner`, refus par défaut | ✔ |
| Soumettre / annuler distincts de écrire | ✔ (A) |
| Permissions utilisateur (restreindre à certaines valeurs d'un lien) | K |
| Partage d'un document avec un utilisateur | K |
| Écran du gestionnaire de permissions | ✔ (plugin `iam` : rôles, permissions, membres) |
| Droits d'export, d'import, d'impression, d'e-mail par DocType | K |

### Vues et formulaires

| Fonctionnalité | État |
|---|---|
| Liste : recherche rapide, filtres typés, tri, pagination | ✔ |
| Formulaire dynamique, historique, garde des modifications, conflit de version | ✔ |
| Filtres enregistrés, colonnes configurables, export CSV | ✔ (C) |
| Vue Rapport (grille configurable, regroupements, totaux) | ✔ (C / I) |
| Barre latérale de la liste : regroupement par valeur, compteurs | I |
| Actions groupées (modifier plusieurs documents, assigner, étiqueter) | ◐ suppression groupée ; modifier, assigner, étiqueter : non |
| Vue Kanban | ✔ (I) |
| Vue Calendrier | ✔ (I) |
| Vue Gantt | ✔ (I) |
| Vue Arbre | ✔ (E) |
| Vue Image / Galerie | I |
| Vue Carte | ✖ |
| Saisie rapide (fenêtre de création depuis un lien) | L |
| Aperçu d'un lien au survol, « créer » depuis un champ lien | L |
| Liens du document (« connexions » : documents qui le citent) | I |
| Chronologie du document (commentaires, modifications, pièces) | ✔ (D) |
| Visite guidée du formulaire (« tour ») | ✖ |

### Espace de travail et tableaux de bord

| Fonctionnalité | État |
|---|---|
| Espaces de travail déclaratifs (raccourcis, sections, compteurs) | ✔ (G) |
| Barre de commande / recherche (Ctrl+G) | ✔ (G) |
| Barre latérale par espaces, repliable, icônes | ✔ (G) |
| Notifications dans l'en-tête | ◐ la cloche d'xauth ; aucune source d'événements |
| Cartes de nombres (comptage, somme, moyenne filtrés) | ✔ (J) |
| Graphiques (par période, par valeur ; barre, ligne, camembert) | ✔ (J) |
| Tableau de bord (ensemble de cartes et de graphiques) | ✔ (J) |
| Rapports requête fournis par un plugin | J |
| Constructeur de rapports (par l'utilisateur) | ◐ vue Rapport et tableau de bord éditable par l'utilisateur ; pas de requête saisie |
| Intégration (assistant de démarrage) | ✖ |

### Collaboration

| Fonctionnalité | État |
|---|---|
| Commentaires | ✔ (D) |
| Assignation (À faire) | ✔ (D) |
| Pièces jointes | ✔ (D) |
| Étiquettes (tags) | K |
| « J'aime » | ✖ |
| Mentions (@utilisateur) | K |
| Recherche globale | ✔ (E) |
| Journal d'activité | ✔ (chronologie du document ; journal d'audit d'xauth) |

### Automatisation et intégration

| Fonctionnalité | État |
|---|---|
| Workflow (états, transitions, droits par transition) | D |
| Tâches planifiées | E |
| Webhooks sortants | E |
| Règles d'assignation automatique | K |
| Documents récurrents (auto-répétition) | K |
| Notifications (système, e-mail) sur événement | K |
| Rapports envoyés par e-mail | K |
| Import CSV avec rapport d'erreurs | E |
| Export de données | ✔ (C : CSV) |
| Clés d'API (clé + secret par utilisateur) | M |
| API REST documentée | ◐ OpenAPI de l'hôte et page « Accès API » ; pas de documentation dédiée |

### Personnalisation

| Fonctionnalité | État |
|---|---|
| Champs personnalisés (ajout par site sur un DocType existant) | L |
| Propriétés modifiées par site (« property setter ») | ◐ habillage de formulaire par organisation (libellé, description, masqué, disposition) |
| Formulaire personnalisable (écran) | ✔ (« Habiller ») |
| Éditeur de DocType dans le Desk | L |
| Formats d'impression et papier à en-tête | F / L |
| Impression PDF | F |
| Traductions (écran, import/export) | M |
| Scripts serveur ou client saisis dans le Desk | ✖ (sécurité : le code vit dans les plugins) |

### Plateforme

| Fonctionnalité | État |
|---|---|
| Multi-tenant, isolation, RLS optionnelle | ✔ |
| Plugins métier qui apportent leurs DocTypes | ✔ |
| Corbeille (documents supprimés, restaurables) | M |
| Journal d'accès et d'audit | ✔ (xauth : Paramètres > Journal d'audit ; historique par document) |
| Gestionnaire de fichiers | ◐ stockage, liens signés, pièces jointes ; pas d'écran de gestion |
| E-mail entrant (IMAP) et file d'envoi | ✖ |
| Site web, blog, portail, formulaires web | ✖ |
| Connexions sociales, LDAP, SAML | ✖ (c'est le rôle d'xauth) |
| Application mobile, hors ligne | ✖ |
| Système d'applications (bench, patches) | ✖ |


### Décompte (au 19 octobre 2026, compté par script sur les tableaux ci-dessus)

Légende ajoutée : **◐** = fait en partie (la ligne regroupe plusieurs choses, seule une partie existe). Les lignes « ✖ » (10) sortent du dénominateur, ainsi que « DocType virtuel »
(c'était ✖, c'est fait pour xauth : un bonus). Dénominateur : **87**.

| État | éléments | part de 87 |
|---|---:|---:|
| ✔ fait en entier | 41 | 47 % |
| ◐ fait en partie | 13 | 15 % |
| non fait (planifié ou non) | 33 | 38 % |
| **Avancement, en comptant une ligne partielle pour une demi-ligne** | **47.5** | **55 %** |

Le 17 octobre, 19 lignes étaient faites (22 %) : **+22 lignes en entier depuis** (collaboration, champs et fichiers, vues, tableaux de bord, DocTypes uniques et arborescents, gestion des accès par le plugin `iam`). Pour **70 % (61 lignes sur 87)** il en manque
**20 en entier**, ou 13,5 si l'on compte les demi-lignes déjà posées. Les lignes partielles sont les plus économiques à finir : 13 lignes dont la moitié du travail existe.

Mêmes réserves qu'au §1 : l'inventaire est le mien, chaque ligne compte pour 1 quelle que soit sa taille, et « fait » veut dire « fait dans ce dépôt et prouvé par des tests ». **Les plugins `sales`, `stock` et `hr` ne sont PAS
des produits : ce sont des jeux d'essai du moteur** (décision du 19 octobre 2026) ; seuls xauth, `iam` (sa face DocType) et `system` (réglages de base) sont du produit. L'avancement mesuré ici est celui du **moteur et du Desk**, jamais d'un module métier.

## 3. Ce qu'on retient pour 70 %

*Écrit le 17 octobre : H, I et J sont en grande partie faits depuis (voir le décompte ci-dessus) ; le reste de ce paragraphe est conservé tel quel pour mémoire.*

Avec G fait, il reste **24 éléments** à choisir dans H-M. Proposition, par ordre de valeur pour quelqu'un qui construit un ERP interne :

| Phase | Retenu | éléments |
|---|---|---:|
| G | **fait** (espaces de travail, barre de commande, barre latérale à icônes) | 3 (faits) |
| H | tout : types de champs, propriétés, nommage, renommage, duplication, hooks | 14 |
| I | barre latérale de liste, actions groupées, Kanban, Calendrier, Image / Galerie, connexions ; **sans Gantt** | 6 |
| J | cartes de nombres, graphiques, tableaux de bord, rapports fournis par un plugin ; **sans le constructeur de rapports par l'utilisateur** | 4 |
| | **Total restant** | **24** |

Cela donne 61 sur 87, soit 70 %. **K, L et M tombent donc après les 70 %** : permissions utilisateur et partage, notifications, champs personnalisés par site, éditeur de DocType, corbeille, clés d'API. C'est un vrai choix : ce sont précisément ces éléments qui rendent un produit utilisable par quelqu'un qui n'est pas développeur. Si le produit vise des utilisateurs qui configurent eux-mêmes, il faut échanger une partie de H ou de I contre K et L (voir §6).

## 4. Phases

Les durées sont des ordres de grandeur pour un développeur, **à revoir à la fin de chaque phase**. Chaque phase a un critère de sortie observable et un cas d'usage qui la tire (règle du contrat de 40 %).

| Phase | Contenu | Cas d'usage | Critère de sortie | Estimation |
|---|---|---|---|---|
| **G. Desk « espace de travail »** — **FAIT** (`doc/phase-g.md`) | espaces déclaratifs (YAML par plugin), barre latérale par espaces avec icônes et sections repliables, barre de commande (Ctrl+G), page d'espace avec raccourcis et compteurs, avatar | le Desk de Frappe : une page par module | le module Stock et le module Ventes s'ouvrent chacun sur leur espace ; un utilisateur sans droit sur un type n'en voit ni raccourci ni compteur ; la barre de commande trouve un type, un nouveau document, un récent | 2 à 3 sem. |
| **H. Champs et propriétés** | les types et propriétés du §2 « DocType », renommage, duplication, hooks restants | un référentiel article complet (photo, description enrichie, code-barres, prix monétaire) | un DocType qui utilise chaque nouveau type se crée, se valide, se filtre et s'affiche sur les deux bases | 6 à 9 sem. |
| **I. Vues** | vue Rapport, Kanban, Calendrier, Gantt, Image ; barre latérale de liste ; actions groupées ; connexions | suivi des mouvements en Kanban par statut ; calendrier des échéances | chaque vue se déclare en YAML et respecte permissions et tenant | 6 à 9 sem. |
| **J. Analytique** | cartes de nombres, graphiques, tableaux de bord, rapports requête fournis par un plugin | tableau de bord Stock (valeur, ruptures, mouvements par semaine) | un tableau de bord se définit en YAML ; ses requêtes passent par `query.py` (parité des deux bases) | 5 à 8 sem. |
| **K. Collaboration et automatisation** | permissions utilisateur, partage, notifications, étiquettes, mentions, journal d'activité | alerte de seuil de réapprovisionnement ; partage d'un mouvement avec un collègue | un utilisateur ne voit que les entrepôts qui lui sont attribués ; une notification arrive dans l'en-tête | 6 à 10 sem. |
| **L. Personnalisation** | champs personnalisés, formulaire personnalisable, gestionnaire de permissions, saisie rapide | un client ajoute « n° de série fournisseur » à un article sans toucher au plugin | un champ ajouté par site apparaît partout (formulaire, liste, filtre, API) et survit à une mise à jour du plugin | 6 à 10 sem. |
| **M. Plateforme** | corbeille, journal d'accès, stockage de fichiers, clés d'API, documentation d'API | restaurer un article supprimé par erreur | un document supprimé se restaure avec son historique | 4 à 6 sem. |

Ordre de grandeur : **H + I + J (le retenu à 70 %, G étant fait) : 17 à 26 semaines** ; **l'ensemble G-M : 35 à 55 semaines** ; dans les deux cas EN PLUS des 21 à 32 semaines du palier 40 %. Ce n'est pas un engagement : c'est ce qui rend visible que « 70 % » est un programme de plus d'un an.

## 5. Risques

| Risque | Parade |
|---|---|
| La mesure du §1 est subjective | l'inventaire est dans ce fichier, versionné : on le corrige à la vue, pas à l'intuition |
| Dérive à 100 % | rien n'entre sans phase, critère et cas d'usage ; la liste « ✖ » est un engagement |
| Personnalisation par site (L) fragilise les mises à jour des plugins | champs personnalisés dans une table à part, jamais dans la définition du plugin ; test de mise à jour |
| Types de champs (H) × deux bases × le Desk : coût multiplicatif | un type = définition, validation, stockage, filtre, tri, affichage, tests de parité, dans la même phase ; pas de type à moitié |
| Vues et graphiques (I, J) alourdissent `query.py` | agrégats par dialecte cantonnés à cette couche, tests de parité, benchmarks avant promotion en tables dédiées (B) |
| Le Desk grossit | un seul thème, des jetons sémantiques, un test d'écran étroit et un test de thème sombre par page (acquis de `doc/desk-revue.md`) |
| Sécurité : ce que l'utilisateur configure ne doit jamais devenir du code | aucun script saisi dans le Desk (✖) ; expressions du langage D22 seulement |

## 6. Décisions à valider

1. **Le chiffre.** Garde-t-on « 70 % » (les 27 éléments du §3), ou « tout G-M » (100 % de l'inventaire) ? Et si la cible reste 70 %, K et L (permissions utilisateur, partage, champs personnalisés) passent-ils avant une partie de H et I ?
2. **L'ordre.** G d'abord (le Desk comme Frappe) est le choix de l'utilisateur ; H avant I avant J est ma proposition (chaque vue et chaque graphique supposent les types).
3. **Personnalisation par site (L).** Permise (c'est ce qui rend le produit « configurable sans développeur ») ou interdite (tout passe par le plugin propriétaire) ? C'est la décision 3 du contrat de 40 % (extensibilité), toujours ouverte.
4. **Éditeur de DocType dans le Desk.** Dans les 70 % ou après ?
5. **Premier utilisateur réel.** Les phases D à F du contrat de 40 % s'ajustaient « selon le premier utilisateur réel du module Stock » : la question reste posée.
