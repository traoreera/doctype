# xcore-doctype : palier 40 % de Frappe

Suite de la documentation de conception (v1 + v2, palier 20 %). Ce document définit le second palier, son ordre de réalisation et le module Stock qui sert de cas d'usage. 17 octobre 2026.

## 1. Objectif et principe

Le palier 20 % livre le moteur : définition déclarative, CRUD générique, permissions, hooks et interface de base. Le palier 40 % le transforme en base d'ERP utilisable : documents soumettables, rapports, workflows, import et export, tables dédiées.

Principes :

- Le deuxième palier coûte plus cher que le premier. Il touche à des sujets transversaux (migrations, rapports, workflows).
- Un cas d'usage réel pilote chaque ajout. Le module Stock est le premier. Une fonctionnalité sans cas d'usage qui la demande reste hors périmètre.
- Tout reste déclaratif. Rapports et workflows sont définis en YAML, validés par Pydantic, jamais écrits en code par DocType.
- Le tableau de périmètre est un contrat. Chaque fonctionnalité a une phase, un critère de sortie et un cas d'usage.

## 2. Périmètre du palier 40 %

| Bloc | Contenu | Effort | Pourquoi |
|---|---|---|---|
| Documents soumettables | Submit, Cancel, Amend, documents figés après soumission | Moyen | Donne la sensation ERP ; prérequis du stock et de la comptabilité |
| Tables dédiées et migrations | Promotion depuis la table JSON, migrations générées à l'exécution | Élevé | Performance des rapports ; point le plus risqué |
| Vues et rapports | Filtres enregistrés, colonnes configurables, export CSV, rapports avec groupement et totaux | Moyen | Ce que les utilisateurs voient en premier |
| Workflows simples | États, transitions, rôles autorisés par transition | Moyen | Validation de documents sans code |
| Vie du document | Commentaires, assignation, historique d'activité, pièces jointes, notifications | Moyen, en petits morceaux | Change beaucoup l'usage quotidien |
| Import et export | Import CSV avec validation et rapport d'erreurs, export | Moyen | Indispensable pour migrer des données réelles |
| DocTypes spéciaux | Single (paramètres uniques), hiérarchiques (arbres) | Faible à moyen | Beaucoup de modules en ont besoin |
| Tâches planifiées et jobs | Planificateur, files de travail, retries | Moyen | Relances, recalculs, envois ; s'appuie sur ce que fournit xcore |
| Recherche globale | Recherche transversale sur les DocTypes | Faible à moyen | Gros gain d'ergonomie |
| Webhooks et API tierces | Événements sortants, clés d'API | Faible | S'appuie sur l'EventBus |
| Permissions par champ | Niveaux de permission, masquage de champs | Moyen | Déjà prévu dans le schéma |
| Impression | Formats d'impression, PDF | Moyen à élevé | Factures et bons, souvent demandés tôt |

## 3. Hors périmètre

Éditeur de DocType dans l'UI, personnalisation par site (champs et propriétés custom), portail web, traduction complète, email entrant et sortant, applications mobiles, comptabilité complète. Ce sont des chantiers qui ressemblent à des produits à part entière.

## 4. Module Stock : premier cas d'usage

Le module Stock exerce exactement ce que le palier 40 % ajoute : tables enfants, documents soumettables, table dédiée et cohérence sous concurrence.

### 4.1 Principe : un journal de mouvements

Le stock d'un article n'est jamais un champ qu'on édite : c'est la somme de mouvements immuables. Un mouvement se corrige en annulant le document, ce qui crée un mouvement inverse. On ne supprime ni ne modifie jamais une ligne du journal.

### 4.2 DocTypes

| DocType | Rôle |
|---|---|
| Article (Item) | Référence, unité de mesure, seuil de réapprovisionnement |
| Entrepôt (Warehouse) | Lieu de stockage |
| Mouvement de stock (Stock Entry) | Document soumettable avec table enfant de lignes : réception, sortie ou transfert |
| Écriture du journal de stock (Stock Ledger Entry) | Générée à la soumission, jamais saisie à la main, immuable |
| Solde (Bin) | Quantité par article et entrepôt, dérivée du journal |

Plus tard : bon de réception et bon de livraison, qui génèrent des mouvements de stock.

### 4.3 Exigences pour le moteur

- Soumission et tables enfants : la soumission d'un Mouvement écrit dans le journal.
- Table dédiée pour le journal : les filtres, sommes et tris sur un journal volumineux seraient trop lents sur la table générique JSON. C'est le premier cas concret de promotion.
- Précision numérique : Decimal pour les quantités et les valeurs, jamais float (colonne NUMERIC sur PostgreSQL ; attention au stockage sur SQLite, qui n'a pas de vrai type décimal).
- Cohérence sous concurrence : vérifier le stock puis écrire le mouvement dans une seule transaction avec verrouillage (`SELECT … FOR UPDATE` sur PostgreSQL, stratégie différente sur SQLite, à isoler dans la couche de requêtes et à tester sur les deux bases).
- Idempotence : une soumission rejouée ne poste pas deux fois le mouvement.
- `tenant_id` sur le journal et les soldes, comme partout.

### 4.4 Ordre de réalisation du module

1. Quantités seulement : Article, Entrepôt, Mouvement soumettable, journal, solde, contrôle du stock négatif, annulation par mouvement inverse.
2. Rapports simples : solde par article et entrepôt, historique des mouvements d'un article, alertes de seuil.
3. Valorisation en coût moyen pondéré, à n'ouvrir qu'une fois la gestion des quantités solide : elle touche à la comptabilité.
4. Plus tard : lots et numéros de série, FIFO, inventaire physique avec ajustements, scan de code-barres.

### 4.5 Pièges à anticiper

- Ne pas recalculer un solde à partir de tout le journal à chaque lecture : garder un solde maintenu à chaque écriture, avec une commande de recalcul pour vérification.
- Un transfert entre entrepôts produit deux mouvements atomiques (sortie et entrée), jamais un seul qui puisse rester à moitié écrit.
- Un mouvement antidaté change les soldes de toutes les dates suivantes : décider dès le début s'il est autorisé.

## 5. Impacts sur l'architecture

- Rapports et workflows déclaratifs : définis dans le schéma, avec leur propre version.
- `query.py` grossit : groupements et agrégations par dialecte (SQLite et PostgreSQL) restent cantonnés à cette couche, avec des tests de parité.
- `schema_version` devient critique : avec des tables dédiées, chaque changement de définition est une migration à tester.
- Modèle d'extensibilité : des plugins voudront ajouter des champs à un DocType existant. Décider tôt si c'est permis (extensions déclarées par le plugin propriétaire) ou interdit.
- Concurrence : le contrôle optimiste sur `modified` (D15) devient indispensable avec des documents soumettables et des workflows.

## 6. Feuille de route

Les durées sont des ordres de grandeur pour un développeur, à confirmer à la fin de chaque phase. Elles ne couvrent pas le palier 20 %, qui doit être terminé avant.

| Phase | Contenu | Durée estimée | Critère de sortie |
|---|---|---|---|
| A. Soumission et Stock (quantités) | Documents soumettables, module Stock sans valorisation | 4 à 6 sem. | Réception, sortie et transfert soumis, annulés par mouvement inverse ; stock négatif refusé en concurrence sur les deux bases |
| B. Tables dédiées et migrations | Promotion du journal de stock, migrations générées à l'exécution | 3 à 5 sem. | Une définition qui change se migre sans perte sur SQLite et PostgreSQL ; benchmark documenté |
| C. Vues, rapports, export | Filtres enregistrés, colonnes, rapports groupés, export CSV | 3 à 4 sem. | Rapport de solde de stock généré depuis une définition YAML |
| D. Vie du document et workflows | Commentaires, assignation, pièces jointes, workflow simple | 4 à 6 sem. | Un mouvement de stock suit un workflow de validation avec rôles |
| E. Import et services transverses | Import CSV, DocTypes Single et hiérarchiques, recherche globale, jobs, webhooks | 4 à 6 sem. | Import d'un catalogue d'articles avec rapport d'erreurs ; recherche transversale fonctionnelle |
| F. Impression et permissions par champ | Formats PDF, niveaux de permission | 3 à 5 sem. | Bon de réception imprimable ; champ masqué selon le rôle |

Ordre de grandeur total : 21 à 32 semaines, soit plusieurs mois. Le rythme réel dépendra du nombre de développeurs et de la valorisation du stock, qui n'est pas incluse.

## 7. Risques

| Risque | Pourquoi | Parade |
|---|---|---|
| Dérive du périmètre | 40 % peut devenir 70 % sans que cela se voie | Contrat de périmètre : phase, critère de sortie et cas d'usage pour chaque ajout |
| Migrations de tables dédiées | Point le plus délicat, risque de perte de données | Définitions versionnées, migrations testées sur les deux bases, sauvegarde avant migration |
| Cohérence du stock | Concurrence, annulations, mouvements antidatés | Journal immuable, transactions avec verrouillage, idempotence, tests de concurrence |
| Parité SQLite et PostgreSQL | Agrégations, verrous et types décimaux diffèrent | Tout dans `query.py`, tests de parité en CI |
| Performance des rapports | Table générique JSON insuffisante | Promotion en tables dédiées guidée par des benchmarks |
| Complexité du workflow | Les workflows de Frappe sont très riches | Sous-ensemble minimal : états, transitions, rôles |
| Valorisation du stock | Touche à la comptabilité | Reportée après la gestion des quantités ; chantier distinct |

## 8. Décisions à valider

1. Valorisation du stock : dès le départ (coût moyen) ou quantités seulement dans un premier temps ?
2. Mouvements antidatés : autorisés ou interdits ?
3. Extensibilité : un plugin peut-il ajouter des champs à un DocType qu'il ne possède pas ?
4. Impression : moteur propre (modèles HTML vers PDF) ou délégué à un service externe ?
5. Ordre des phases D à F : à ajuster selon le premier utilisateur réel du module Stock.

> Les décisions 1 et 2 sont tranchées pour la phase A dans `doc/phase-a.md` ; les trois autres ne concernent pas cette phase.
