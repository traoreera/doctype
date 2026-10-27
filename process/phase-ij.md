# Phases I et J : vues d'un type, tableaux de bord et graphiques

Palier 70 % (`process/palier70.md`). Demande de départ : un Desk qui RESSEMBLE à Frappe, avec la facilité d'xcore (un plugin déclare, rien à coder). La phase G n'avait livré que des raccourcis ; ici, ouvrir un module montre un vrai tableau de bord, et chaque type a ses vues.

## 1. Ce qui est livré

**Moteur (`aggregation.py`, `Query.aggregate`, `GET /aggregate/{doctype}`, action `doc.aggregate`)**
- Compter, additionner, moyenner, min/max, regrouper par Select, Link, Boolean, Date (jour, mois, année) ou colonne système (`docstatus`, `owner`, `creation`, `modified`), avec une fenêtre `periods` (les N derniers mois, périodes vides comblées à zéro, « aujourd'hui » dans le fuseau de l'organisation).
- Mêmes filtres typés que la liste, même périmètre de lecture (`if_owner` compris : un total ne révèle pas le nombre de documents des autres), tenant imposé par `Query._scope` (scénario de fuite ajouté).
- Un Decimal reste une chaîne exacte ; SQLite additionne en double (exact jusqu'à ~15 chiffres, documenté), PostgreSQL en `NUMERIC`.

**Déclaratif (`dashboards.py`)**
- Blocs d'un espace de travail : `header`, `paragraph`, `spacer`, `shortcut`, `number_card`, `chart` (`bar`, `line`, `pie`, `donut`, `percentage`), `quick_list`, `card` (liens). Largeur sur 12 colonnes (`cols`). Une faute (champ inconnu, somme d'un texte, filtre invalide, camembert d'une moyenne, filtre `docstatus` sur un type non soumettable) est une ERREUR DE CHARGEMENT.
- Tout est filtré par les droits de celui qui regarde : un bloc sur un type illisible disparaît, une section sans bloc visible aussi.
- Un module SANS fichier d'espace reçoit un espace généré : un compteur par type, les derniers documents, puis les raccourcis.
- `views:` d'un DocType : `kanban {field}`, `calendar {start, end, title, color}`, `gantt {start, end}`, `dashboard` (sinon un tableau de bord est GÉNÉRÉ : total, sommes, créés par mois, répartition par Select et par état). `/meta` envoie `views`.

**Desk**
- Sélecteur de vue sur chaque type : Liste, Rapport, Tableau de bord, et Kanban, Calendrier, Gantt s'ils sont déclarés ; la vue se retient par appareil, la recherche et les filtres sont COMMUNS à toutes les vues. Une carte de chiffres ouvre la liste avec exactement les filtres qu'elle a comptés (visibles dans la barre).
- Rapport : colonnes au choix (retenues), regroupement avec nombre et totaux, total général calculé par le serveur sur tous les documents, export CSV (séparateur « ; », BOM, virgule décimale, cellules texte neutralisées contre l'injection de formule, plafond 10 000 lignes).
- Kanban : une colonne par valeur, glisser une carte = modification ordinaire (mêmes droits, contrôles et verrou de version), affichée tout de suite et défaite si le serveur refuse ; menu « Déplacer vers » au clavier et au toucher.
- Calendrier (mois, semaine) : « + » d'un jour pré-remplit la date, glisser un événement déplace sa date (même durée). Gantt : barres sur une échelle jour / semaine / mois.
- Graphiques en SVG, sans bibliothèque, selon la méthode `dataviz` : palette catégorielle de 8 teintes validée (daltonisme, contraste, clair et sombre), barres fines à bout arrondi, infobulle (valeur d'abord) et repère sur une courbe, 6 parts au plus puis « Autres », vue tableau jumelle, rechargement qui garde le dessin.
- Espaces livrés : Stock et Ventes ouvrent sur un tableau de bord ; la facture déclare Kanban (paiement), Calendrier (échéances) et Gantt.

## 2. Preuves

Mutations vérifiées : périmètre et tenant d'un agrégat, fenêtre de temps, troncature, exactitude Decimal, filtrage des blocs par droits. Tests : `test_aggregate.py` (SQLite et PostgreSQL), `test_dashboards.py`, scénario de fuite, intégration HTTP/action ; côté Desk `charts`, `aggregate`, `blocks`, `kanban`, `calendar-view`, `gantt-view`, `report-view`, `listview`, `csv`, `views-lib`. `ui/e2e/look.mjs` sème un tenant de démonstration et photographie les écrans (c'est avec lui que l'allure a été jugée).

## 3. Limites

- Pas de découpage hebdomadaire ni trimestriel d'un graphique (jour, mois, année seulement) ; `creation` et `modified` se découpent en UTC.
- Les filtres d'un bloc sont une valeur par champ (comme la liste) ; pas de plages relatives (« 30 derniers jours ») hors `periods`.
- Kanban : pas d'ordre manuel des cartes dans une colonne (tri par modification) ; Gantt en lecture seule ; pas de vue Image, Carte, Arbre.
- Pas de graphique à plusieurs séries, pas de tableau de bord choisi par l'utilisateur (phase L), pas de filtres enregistrés.
- Les totaux et regroupements lisent la table générique : pas de benchmark sur de gros volumes (phase B).
- Le test de « stockage refusé » de `theme.spec.ts`, `recents.spec.ts` et `navstate.spec.ts` espionne `Storage.prototype`, ce qui n'a pas d'effet sous happy-dom : à vérifier (`vi.stubGlobal` fonctionne, voir `views-lib.spec.ts`).
