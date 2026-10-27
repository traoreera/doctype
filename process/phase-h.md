# Phase H : types de champ, fichiers, documents uniques, module RH

Palier 70 % (`process/palier70.md`). Réponse aux manques relevés : pas de champs image ni fichier, pas d'onglet Paramètres, des liens qui ne se cliquent pas, et aucun module « profil / ressources humaines » pour prouver que le moteur tient.

## 1. Livré

**Types de champ** (`fieldtypes.py`, `schema.FieldType`) : `Datetime` (UTC, `AAAA-MM-JJThh:mm:ssZ`, défaut `now`, le Desk convertit depuis et vers l'heure locale), `Currency` (un Decimal exact, 2 décimales par défaut, `options: EUR` pour le symbole), `Rating` (entier de 0 à `stars`), `Color` (`#rrggbb`), `LongText`, et `format: email | phone | url` sur un `Text` (http et https seulement : jamais `javascript:`). Écriture stricte, rien n'est corrigé en silence. Filtres, tris et agrégats identiques sur SQLite et PostgreSQL (une devise se somme exactement, un Datetime se regroupe par jour, mois, année).

**Fichiers** (`files.py`, `file_service.py`, `api/files.py`, table `xdt_file`) : champs `Image` et `Attach`. Le document ne garde que l'identifiant du fichier.
- Stockage : l'extension `ext.storage` d'`orchestra-backend` (local, s3, r2, supabase) si l'hôte la fournit, sinon un dossier local (`doctype.files.dir`).
- À l'entrée : taille bornée (`max_mb`, 10 par défaut, 50 au plus) ; une image est reconnue à son CONTENU (PNG, JPEG, GIF, WebP ; SVG refusé), pas à son nom ni à l'en-tête déclaré ; nom nettoyé.
- À la lecture : qui peut lire un document qui cite le fichier (le périmètre `if_owner` s'applique), ou celui qui l'a déposé tant qu'aucun document ne le cite. Le contenu se sert par une URL SIGNÉE de 5 minutes (HMAC du tenant, du fichier et de l'échéance) : une balise `<img>` n'envoie pas de jeton. `nosniff`, CSP vide, pièces jointes servies en téléchargement.
- Un document ne peut citer qu'un fichier téléversé pour CE champ de CE type dans CE tenant. Un fichier remplacé, retiré ou dont le document est supprimé est libéré, sauf s'il est cité ailleurs.
- `image_field` d'un DocType : la photo (avatar de la liste, des cartes, de la fiche).

**Documents uniques** (`single: true`) : un seul enregistrement par organisation, nommé comme son type, ouvert avec ses valeurs par défaut (`version: 0`) tant qu'il n'est pas enregistré ; le premier enregistrement le crée (deux enregistrements simultanés : un seul passe, l'autre est un conflit de version). Ni création, ni suppression.

**Desk** : saisie Image/Attach (glisser-déposer, vignette, remplacer, retirer), étoiles, couleur, devise, date-heure, texte long, clavier adapté à l'e-mail et au téléphone ; **valeurs cliquables partout** (un lien ouvre le document cité sous son titre, un e-mail ouvre le courrier, un téléphone compose, une adresse web s'ouvre dans un nouvel onglet) et une flèche « ouvrir » sur un champ lien ; page et entrée **Paramètres** (réglages groupés par module, affichage, compte).

**Module RH** (`app/hr`, AUCUN code : un `main.py` de quelques lignes et du YAML) : employés (photo, contrat, coordonnées, salaire, évaluation, couleur), services en arborescence, demandes de congé soumettables et numérotées, paramètres RH, espace de travail avec tableau de bord.

## 2. Preuves

`test_fieldtypes.py`, `test_files.py` (contrôles de contenu, signatures, droits, tenants étanches, libération), `test_single.py` (dont la course de deux premiers enregistrements), `test_files_api.py` (multipart de bout en bout), scénario de fuite entre tenants pour les quatre nouvelles méthodes de `Query`. Mutations vérifiées : tenant et périmètre d'un fichier, citation d'un autre champ, signature, expiration, SVG accepté, pièce jointe servie en ligne. Côté Desk : `fieldtypes-ui`, `files-ui`, `settings`. `ui/e2e/look-hr.mjs` sème le module RH avec de vraies photos et le photographie.

## 3. Limites

- Pas de fichiers dans les lignes d'une table ; pas de rognage ni de redimensionnement d'image ; pas de nettoyage planifié des fichiers déposés mais jamais cités (ils restent privés à leur dépositaire).
- Sans `doctype.files.secret`, un secret par processus : les liens signés ne valent que pour ce processus (à fixer pour plusieurs workers).
- Permissions PAR CHAMP absentes : le salaire d'un employé se lit comme le reste de sa fiche (phase F).
- `Datetime` : pas de fuseau d'affichage choisi par l'utilisateur (celui du navigateur) ; `Datetime` ne sert pas encore de début à un calendrier ou un Gantt.
- Pas de texte riche (HTML) : un texte long est du texte brut.

## Personnaliser un tableau de bord (visuel, sans JSON)

Avant : « Habiller » ouvrait un éditeur JSON, et le tableau de bord généré d'un type n'était pas modifiable. Maintenant, sur un espace
ET sur le tableau de bord d'un type, **Personnaliser** met la page en édition (`BlockEditor.vue`) :

- chaque bloc est une **tuile** : glisser-déposer (ou flèches, seule voie au tactile) pour déplacer, largeur 1/4 · 1/3 · 1/2 · 2/3 · 1/1,
  crayon pour modifier, corbeille pour retirer ; les cartes restent **vivantes** (chiffres réels) mais ne se cliquent pas ;
- **Ajouter** : chiffre, graphique, derniers documents, raccourci, carte de liens, titre, texte, espace ; la fenêtre d'un bloc
  (`BlockDialog.vue`) ne propose que ce que le serveur accepte (champs du type choisi, calculs compatibles avec le graphique, période si
  le regroupement est une date, filtres par le même composant que les listes) ;
- rien ne part avant **Enregistrer** ; quitter la page avec un brouillon demande confirmation ; **Revenir à la disposition d'origine**
  retire l'habillage (le YAML du plugin, ou le tableau généré, reprend la main). La disposition vaut pour toute l'organisation.

Côté serveur, un nouveau genre d'habillage `dashboard` (`overrides.py`, `PUT|DELETE /override/dashboard/{doctype}`, actions
`doc.override.dashboard.save|delete`) : `{blocks: [...]}` validé comme `views.dashboard` d'une définition (`doctype` y est implicite et
refusé s'il est écrit, chaque carte se mesure contre le type), droit requis = **écrire** le type, par tenant, appliqué à `/meta`.

Deux défauts trouvés en chemin : le Desk renvoyait au serveur les champs de LECTURE d'un raccourci (`create`, `icon`, `single`) que
`extra="forbid"` refuse (`blocksedit.declare` les retire, `create` redevient `new`), et une carte déplacée montrait un instant les chiffres
de celle qui occupait sa place (clés de tuile = contenu du bloc, pas rang). Le tableau généré d'un type suit désormais la date que le type
annonce pour son Gantt / calendrier, ses cartes se partagent la rangée, et les étiquettes longues d'un axe sont coupées.

Les quatre limites de la première version (une seule disposition pour toute l'organisation, espace généré non modifiable, blocs invisibles
effacés à l'enregistrement, palette de couleurs fermée) sont levées plus bas. `ui/e2e/look-edit.mjs` rejoue le parcours dans un vrai navigateur.


## Tous les réglages dans UN seul onglet « Paramètres »

Avant : la page Paramètres était une liste de liens, chaque réglage s'ouvrait dans sa propre page, et les réglages d'une application
apparaissaient AUSSI dans son espace (sous-menu, raccourci). Maintenant `/settings/:doctype?` est la seule adresse d'un réglage :

- une colonne de rubriques : **Général** (affichage, compte), puis, par application, chacun de ses réglages (un type `single: true`) ;
- le réglage choisi s'édite **sur place** avec le formulaire habituel (`FormView` en mode `embedded` : pas de flèche de retour, pas de
  suppression, rien dans les récents) ; changer de rubrique avec des modifications non enregistrées demande confirmation ;
- l'ancienne adresse `/app/<Type>/<Type>` (et la « liste » d'un réglage) redirige ici ; la palette de commandes y mène aussi ;
- le serveur n'envoie plus un réglage comme raccourci d'espace (écrit à la main ou généré), et un module qui n'a que des réglages n'a pas
  d'espace : le sous-menu d'une application ne montre que ses documents ; `app/hr/workspaces/hr.yaml` n'a plus de raccourci « HR Settings ».

Pour qu'un plugin ajoute ses réglages : un DocType `single: true` dans son dossier `doctypes/`, rien d'autre.
Étroit (< 900 px) : les rubriques passent au-dessus, en une rangée qui défile.


## Barre latérale : applications, sous-modules, icônes

Le menu avait deux défauts : les entrées d'une application étaient une liste plate sans icône (alors que chaque DocType déclare la sienne),
et les titres que l'espace écrit pour ses raccourcis (« Facturation », « Clients », « Opérations », « Référentiel ») n'y paraissaient pas.
Trois niveaux maintenant : **application** (l'espace, repliable) → **sous-module** (le titre qui précède des raccourcis dans le YAML de
l'espace, repliable, choix gardé sur l'appareil) → **document**, chacun avec son icône (`file` si le type n'en déclare pas). Un espace qui n'a
qu'un groupe n'affiche pas de titre (une liste suffit). Rien à écrire de plus pour un plugin : ses `header` et ses `icon` de DocType servent.
`lib/sections.ts` en est la règle (testée) ; un titre qui n'a que des chiffres et graphiques n'est pas un sous-module.


## Paramètres > Général : la gestion globale de l'application

Un plugin **`system`** (YAML seul, livré DANS le paquet xcore-doctype : `src/xcore_doctype/system_plugin_dir/`, lié en `app/system`, posé par
`xcore-doctype install` à côté du moteur) apporte le DocType unique **`System Settings`** (`doctypes/system_settings.yaml`) : nom de
l'organisation, logo, couleur, format de date. Pourquoi un plugin et pas le plugin `xcore-doctype` lui-même : son nom contient un tiret, il ne peut
ni être le `module` d'un DocType ni déclarer des grants à auth (voir `grants.py`) ; `system` est un namespace valide.

- **Où** : la rubrique « Général » de Paramètres montre le formulaire de chaque réglage global (`settings_group: general`, `System Settings`
  d'abord), puis le thème et le compte. Chaque réglage global a son propre « Enregistrer ».
- **Y ajouter un réglage (« à travers lui »)** : un plugin déclare un DocType `single: true` + `settings_group: general` dans ses `doctypes/` ;
  il apparaît dans « Général », sans rubrique à lui, sans toucher à `system`.
- **Qui lit quoi** : `System Settings` exige `system:system_settings:read|write` (un administrateur). Mais tout utilisateur identifié doit voir
  le nom, le logo et la couleur : `GET /preferences` (`preferences.py`) lit le document en contexte système, pour le tenant de l'appelant, et
  ne renvoie qu'une liste FERMÉE de champs (`organization_name`, `brand_color`, `date_format`, `logo`) ; un réglage sensible ajouté plus tard
  ne sort pas par là. Le logo est un lien signé de 5 minutes (redemandé une fois s'il expire).
- **Ce que le Desk en fait** (`stores/preferences.ts`) : nom + logo dans la barre du haut et titre de l'onglet ; couleur → variables de teinte
  de PrimeVue (`lib/brand.ts` : n'accepte que `#rrggbb`, texte noir/blanc selon la luminance, teinte éclaircie en thème sombre) ; format de
  date → `formatDate` et les sélecteurs de date. Appliqué dès l'enregistrement, effacé à la déconnexion.

Pas fait : jour de début de semaine (le calendrier commence le lundi), fuseau et devise par organisation (le fuseau est un réglage du
processus : `doctype.timezone`), langue. Aucun réglage n'est livré sans que le Desk ou le serveur l'applique réellement. `ui/e2e/look-general.mjs`
rejoue le parcours dans un navigateur et remet le tenant de démonstration à zéro.


## Dispositions : la mienne, celle de l'organisation, espaces fabriqués, blocs cachés, couleurs libres

- **Par utilisateur.** Deux couches par-dessus le YAML du plugin : la disposition de l'**organisation**, puis la **mienne** (elle passe avant).
  « Pour moi » est le choix par défaut de l'éditeur ; « Toute l'organisation » n'est offert qu'à qui peut ÉCRIRE ce que la page montre
  (`shareable`, calculé par le serveur). Enregistrer la mienne n'exige que de pouvoir LIRE ce qu'elle montre. `PUT|DELETE
  /override/workspace/{nom}?scope=user|org` et `/override/dashboard/{type}?scope=…` (sans `scope` : l'organisation, comme avant). Stockage :
  `xdt_layout_override`, genres `workspace_user` / `dashboard_user`, cible `<nom>@<empreinte de l'utilisateur>` (empreinte BLAKE2b, longueur
  fixe) ; seul `Query.overrides(kind, suffix=…)` lit ceux d'un utilisateur. `/workspaces` et `/meta` disent d'où vient la disposition (`layer`)
  et si on peut la partager ; le badge « Ma disposition » / « Disposition de l'organisation » et « Retirer ma disposition » en découlent.
- **Espace fabriqué.** Un module sans fichier d'espace est maintenant un `WorkspaceDef` fabriqué (`_generated_workspace`) qui passe par le
  même circuit que les autres : il s'habille, pour moi ou pour l'organisation, et « revenir à l'origine » rend la main à l'espace fabriqué.
- **Blocs cachés conservés.** Un utilisateur n'enregistre que ce qu'il voit ; les blocs que ses droits lui cachent (et que d'autres voient)
  ne sont plus effacés : `overrides.keep_hidden` les remet à côté de leur voisin visible (après celui qui les précédait, sinon avant celui qui
  les suivait, sinon à la fin), inchangés. Les droits exigés ne portent que sur ce que l'utilisateur envoie.
- **Couleurs libres.** `color` d'une carte de chiffres, et d'un graphique en barres ou en courbe, accepte un nom de la palette OU `#rrggbb`
  (ce format seulement : la valeur finit dans un attribut de style). Le Desk pose deux variables (clair, sombre éclairci si besoin, comme la
  couleur d'organisation : `customTone`). Un camembert garde la palette validée (plusieurs teintes ; le serveur refuse `color` pour lui).

Limites restantes : la fusion de blocs cachés est une heuristique de voisinage (si l'administrateur a modifié la disposition d'organisation
pendant qu'on éditait, elle replace au mieux) ; une couleur libre n'est pas contrôlée en contraste sur le fond de la carte ; les dispositions
de formulaire (`form`) restent par organisation. `ui/e2e/look-layouts.mjs` rejoue la mienne / l'organisation avec deux utilisateurs.
