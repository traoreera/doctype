# Où en est le produit, et ce qui reste avant un ERP

**Synthèse au 27 oct. 2026.** Lue sur les 24 documents de ce dossier, le code et les mesures faites en séance.
Elle ne remplace rien : elle dit quel document fait autorité sur quoi, et redresse ce qui a dérivé.

## 0. Quel document croire

| Sujet | Document | État |
|---|---|---|
| Contrat de conception (D1-D29) | `doctype.md` | à jour |
| Feuille de route vivante | `programme.md` | **à jour** (dernière entrée : 27 oct., §3.6) |
| Inventaire chiffré « 70 % » | `palier70.md` §2 | **a dérivé** : voir §1 ci-dessous |
| Le jeu de DocTypes d'ERP | `erp-doctypes.md` | à jour |
| « Créer depuis » | `mapper.md` | à jour |
| Ce que chaque phase a confirmé | `phase*.md`, `spike-phase0.md` | figés, et ils gagnent sur `doctype.md` là où ils diffèrent |
| xcore et xauth | `xcore-know.md`, `xauth.md`, `iam.md` | à jour |

**`palier70.md` §2 est un instantané du 22 octobre.** Il marque encore « planifié » le **workflow** (D) et les
**champs personnalisés** (◐), faits depuis ; les étiquettes, les permissions utilisateur, le partage et l'import
CSV y sont absents alors qu'ils sont faits le 26. Ne pas citer son décompte sans le recompter.

## 1. Ce qui est fait

**Le moteur.** Définitions déclaratives (schéma strict, `extra="forbid"`), stockage en table générique
multi-tenant, CRUD par HTTP et par actions xcore, verrouillage optimiste, droits par grants avec périmètre par
ligne, hooks (onze événements, dont les hooks transactionnels), conditions compilées en AST partagé
serveur/client, filtres typés, recherche, liens vérifiés (existence, lisibilité, état, filtres), tables
enfants, numérotation par série, historique par génération de nom, documents soumettables
(brouillon → soumis → annulé → amendé), `Decimal` exact, documents uniques, DocTypes virtuels, fichiers,
étiquettes, permissions utilisateur et partage, import CSV, collaboration (commentaires, assignations, pièces
jointes), règles de notification, workflows, agrégats et tableaux de bord.

**Le Desk** (Vue 3 + PrimeVue) : listes et six vues, fiches à onglets, espaces de travail, tableau de bord,
mode sombre, responsive, palette de commandes, Form Builder, constructeur de workflow, éditeur de blocs,
écrans d'administration xauth, bibliothèque de fichiers, et le menu « Créer » du mapper.

**Le jeu d'ERP** (`erp-doctypes.md`) : 721 DocTypes en 23 modules qui chargent et dont chaque lien résout ;
136 pièces numérotées par série ; référentiel produit ; 10 couples « créer depuis » déclarés.

**Le mapper** (`mapper.md`) : préremplissage aux quantités restantes, provenance vérifiée, exposé et branché au
Desk.

Mesure de confiance : **3493 tests Python**, **1461 tests du Desk**, `typecheck` propre. Un seul échec, dans
`test_csv_import` (profondeur JSON), **préexistant**.

## 2. Ce qui manque pour que ce soit un ERP

Par ordre de blocage, pas par ordre de taille.

### 2.1 Personne ne peut rien lire (bloquant absolu)

Les DocTypes de l'ERP arrivent par `doctypes_dirs`, donc sans plugin propriétaire, donc **aucun grant n'est
déclaré à xauth**. Les droits refusant par défaut, une instance réelle n'affiche rien du tout.

**Levé côté moteur le 27 oct.** : un plugin par module est désormais possible malgré le cycle
(`load_dir(…, peers=True)`, `erp-doctypes.md` §2). Chaque module devient un vrai plugin, propriétaire de ses
DocTypes, qui déclare ses grants et porte sa logique. Reste à ÉCRIRE ces plugins (mécanique pour la déclaration,
le métier ensuite).

### 2.2 Rien ne calcule

**Mesuré :** aucun mécanisme de calcul déclaratif n'existe dans le schéma, et `fetch_from` ne sait que COPIER
la valeur d'un document lié. Or sur une facture, `amount`, `net_amount`, `total`, `grand_total`,
`total_taxes_and_charges`, `outstanding_amount` sont tous `read_only` : l'utilisateur ne peut pas les saisir, et
rien ne les remplit. **Une facture de vente ne peut donc pas avoir de total** — ce n'est pas une imprécision,
c'est structurel.

Deux voies, et le choix n'est pas fait : des hooks par type (répéter `amount = qty × rate` dans 136 types), ou
**un champ calculé déclaratif dans le moteur**. La seconde évite un cauchemar d'entretien ; c'est la décision à
prendre avant d'écrire les 135 autres.

### 2.3 Rien ne comptabilise

Zéro `hooks:` dans les 721 fichiers. Un `Stock Entry` soumis n'écrit rien au grand livre et ne bouge aucun
solde de `Bin` ; une facture n'écrit aucune écriture comptable. Et ce n'est pas qu'une affaire de YAML :
déclarer `hooks:` **sans un plugin qui les enregistre** fait échouer toute sauvegarde du type en 503
`hook_unavailable`. Deux limites du moteur sont levées (27 oct.) : un hook peut écrire un type qui a ses propres
hooks (ils tournent), et mettre à jour les champs `allow_on_submit` d'un document SOUMIS (le reste dû d'une facture
quand un paiement est soumis). Il faut donc §2.1 d'abord, puis la logique : registre immuable, soldes maintenus, refus de
négatif sous concurrence, contre-écritures à l'annulation. Le gabarit existe
(`tests/support/plugins/stock/src/ledger.py`), c'est un module par domaine.

### 2.4 Les états ne sont pas représentables

`aggregate` ne groupe que sur **un** champ. Une balance générale ou une balance de stock est une matrice
**compte × période** ou **article × entrepôt**. Aucun écran déclaratif ne l'exprime, et l'explosion d'une
nomenclature non plus (les tables imbriquées sont refusées).

Trois modules seulement en ont besoin — `accounts`, `stock`, `manufacturing` — et la bonne réponse n'est pas
trois extensions JavaScript (ce serait l'erreur qu'on vient d'éviter avec le mapper) mais **un `group_by` à
deux champs et un type de vue « rapport mesuré »** dans le moteur. Après quoi ces états sont du YAML. Resteraient
en JS trois écrans, pas trois plugins : le lettrage des paiements, l'explosion de nomenclature, la téléphonie.

### 2.5 Le reste, connu et chiffré

De `palier70.md` (recompté) : impression et PDF, permissions par champ (permlevel), tâches planifiées,
webhooks, corbeille, clés d'API, traductions, éditeur de DocType dans le Desk, rapports envoyés par e-mail,
documents récurrents, règles d'assignation automatique. Plus les points ouverts de ce dossier : identité des
lignes (sans laquelle `read_only` dans une table n'est qu'une indication d'interface), tables dédiées et
migrations (phase B), refus d'annuler un document encore cité.

## 3. Ce que je ferais ensuite

1. **Trancher le champ calculé** (§2.2). Cette décision conditionne la façon d'écrire tout le métier.
2. **Une verticale complète**, une seule : *Demande de matériel → Mouvement de stock*. Plugin `stock` avec ses
   grants, les calculs, le registre, les soldes — du clic à l'écriture vérifiable. Elle sert de gabarit.
3. **Les 21 autres plugins déclarants**, mécaniques, pour que le reste devienne visible.
4. **Le rapport mesuré à deux dimensions** (§2.4), qui débloque `accounts` et `stock` sans JavaScript.

Le piège serait de déclarer 136 pièces soumettables et d'espérer. Une verticale qui marche vaut mieux que vingt
modules qui affichent des zéros.
