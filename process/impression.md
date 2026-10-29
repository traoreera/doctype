# Impression et PDF : spécification

**Spécification, 27 oct. 2026.** Ce fichier est le contrat à tenir.

**Fait à ce jour :** le vocabulaire des blocs et le rendu HTML pur (`printing.py`, §3/§4), les refus au
chargement (§8), le gabarit FABRIQUÉ (§2), le chargement des gabarits livrés par un plugin
(`registry.load_print_templates`, exposé au canal `doctype_registry`), la validation de l'habillage
d'organisation (`overrides.validate_print_override`, `KIND_PRINT`), **les trois routes et les trois actions
IPC** (`print_service.py`, `api/print.py`, §7) avec la résolution des images en `data:`, le papier à en-tête
(`Letter Head` lu en contexte système, fond PDF superposé) et la couche mince du moteur (`pdf.py`). Tests :
`tests/unit/test_printing.py` (le pur), `tests/unit/test_print_service.py` (la base), et
`tests/integration/test_print_api.py` (un vrai hôte, un vrai plugin qui livre ses gabarits).

Et l'étape 4 : les extras `[pdf]` et le test fumigène (`tests/unit/test_pdf_engine.py`), que la CI installe
toujours pour qu'il tourne au moins là.

Et l'étape 5 : le Desk (`PrintDialog.vue`, entrée « Imprimer… » du menu « ⋯ » de la fiche), avec sa preuve en
Chromium (`ui/e2e/print.e2e.mjs`).

Et les étapes 6 et 7 : le DocType `Letter Head` livré avec le plugin `system`, et l'éditeur de gabarits
(`#/print/<Type>`).

**Pas encore fait :** le TÉLÉVERSEMENT du PDF d'en-tête depuis l'écran. Le serveur le superpose déjà
(`pdf.overlay`, testé) et le modèle de l'éditeur porte ses réglages, mais une question reste OUVERTE et je
préfère l'écrire que de l'inventer : **à quoi le fichier téléversé est-il rattaché ?** `api.upload` exige un
couple (DocType, champ) existant, or le fond est cité depuis un GABARIT, pas depuis un champ de document. Deux
voies : (a) donner à `Letter Head` un champ `Attach` « fond », ce qui unifierait les deux formes d'en-tête mais
contredirait §5, qui met `pdf`/`pages`/`offset` dans le gabarit à dessein ; (b) téléverser vers ce champ juste
pour obtenir un identifiant, que le gabarit cite ensuite — mécaniquement juste, conceptuellement tordu. La
réponse change le schéma de `Letter Head` : elle se prend avant d'écrire, pas pendant.

Une facture qu'on ne peut pas imprimer n'est pas une facture — elle ne sort pas de l'entreprise, et les mentions
obligatoires n'existent nulle part. Le besoin est réglementaire, pas cosmétique. Il ne figurait dans aucune de nos
listes (ni les 7 points d'`erp-doctypes.md`, ni le chemin d'`etat.md`), seulement à l'état de projet dans
`palier70.md` (« Formats d'impression », « Impression PDF », phases F/L). C'était un trou de cadrage.

## 0. Ce qui a été vérifié avant de concevoir

Mesuré, pas supposé — et deux de ces points corrigent ce que je croyais :

| Fait | Source |
|---|---|
| `frappe/frappe` est **MIT**, `frappe-ui` est **MIT** | API GitHub (`repos/…/license`) |
| `erpnext` est **GPL-3.0** : rien à en reprendre | idem |
| wkhtmltopdf **n'est plus une option** chez Frappe | `frappe/utils/pdf.py` |
| Chromium est le défaut (`pdf_generator="chrome"`), **PlutoPrint** l'alternative | idem + `print_settings.json` |
| **PlutoPrint** : MIT, wheels binaires précompilés, `pip install`, Python ≥3.10 | PyPI |
| PlutoPrint ne sait pas : **CSS grid**, **JavaScript**, codes-barres | `frappe/utils/pdf_generator/pluto.py` (`plutoprint_blockers`) |
| Notre schéma n'a **aucune** propriété d'impression par champ, ni papier à en-tête | le dépôt |
| `pypdf` : **BSD-3**, **Python pur**, superpose des pages | PyPI |

**Et PlutoPrint a été ÉPROUVÉ**, le 27 oct., sur une facture écrite comme notre générateur l'écrirait (flux et
tableaux, aucun CSS grid) — pas seulement lu dans la documentation de Frappe :

| Question | Résultat mesuré |
|---|---|
| Installation sur notre Python | wheel **`cp314`** manylinux, `pip install` sans build — **20,5 Mo** (moteur embarqué), `pypdf` 409 Ko |
| Répétition de l'en-tête de tableau | **oui** : 300 lignes, 11 pages, en-tête présent sur les 10 pages de tableau et sur elles seules |
| Saut de page explicite (`page-break-before`) | **oui** |
| Interdiction du réseau | **oui**, par `Book.custom_resource_fetcher` : notre `fetch_url` lève, PlutoPrint avertit et **continue le rendu sans la ressource** (et le 28 oct. : **sans** ce récupérateur le moteur fait vraiment la requête — voir §6) |
| Superposition d'un fond PDF (`pypdf`) | **oui** : fond sous le contenu, sur toutes les pages, pied du fond conservé |

**Deux mesures de plus, le 28 oct.**, faites en écrivant les routes (plutoprint 0.23.0, pypdf 6.21.3) — les deux
changent la conception, c'est pourquoi elles sont ici et pas seulement dans `pdf.py` :

| Question | Résultat mesuré | Conséquence |
|---|---|---|
| `@page{size:…;margin:…}` de NOTRE feuille de style est-il suivi ? | **oui**, format et marges (A5, Letter, `landscape`, et 5 pages contre 7 selon la marge) | `Book()` n'a aucun réglage de géométrie : `printing.py` reste la SEULE source de la mise en page. Piège : `Book.get_page_size()` rend le défaut du livre et non la taille résolue — seuls `get_page_size_at(0)` et la `mediabox` du PDF disent vrai |
| une URL `data:` passe-t-elle par le récupérateur de ressources ? | **non** : PlutoPrint la décode lui-même, `fetch_url` n'est jamais appelé | refuser TOUT dans `fetch_url` et incorporer les images **cohabitent**. Ce n'était pas acquis : le verrou de §6 aurait pu interdire le logo du papier à en-tête |

Et surtout, le 28 oct., **le HTML que NOTRE générateur produit** (plus une facsimilé écrite à la main comme le
27) a été imprimé pour de vrai : une facture de 139 lignes groupées en 3 dépôts avec sous-totaux, un bloc
`totals`, un `page_break` et un pied de page. Résultat : **6 pages A4**, l'en-tête du tableau répété sur les
**5** pages de tableau et **pas** sur la 6e (celle d'après le saut), les sous-totaux par groupe, le pied présent,
et **aucune ressource externe demandée** (`fetch_url` jamais appelé). La chaîne complète est donc vérifiée, pas
seulement ses morceaux.

Le code du framework étant MIT, en reprendre est permis en conservant la notice de copyright dans les portions
reprises. Ce n'est pourtant pas ce qui est proposé ici : voir §9.

## 1. Décision — le moteur est PlutoPrint, en dépendance OPTIONNELLE

`pip install plutoprint` : MIT, wheels précompilés, aucun binaire système, pas de Chromium dans l'image. C'est
ce qui rend l'arbitrage léger, alors qu'il paraissait lourd.

Ses limites tombent juste :

- **pas de JavaScript** — c'est déjà notre position partout (vocabulaire fermé, code confiné aux plugins) ;
- **pas de CSS grid** — contrainte d'écriture, et c'est NOUS qui produisons la feuille de style à partir des
  blocs : on écrit en flux et en tableaux, point ;
- **pas de codes-barres** — hors périmètre v1 (§8).

Optionnelle, et déclarée comme telle : absente, les routes d'impression répondent `503 pdf_engine_missing`
nommant le paquet, et l'APERÇU HTML continue de fonctionner. Un repli Chromium reste possible plus tard, comme
chez Frappe, mais **n'est pas livré** : une seule voie à tenir.

**Seconde dépendance optionnelle : `pypdf`** (BSD-3, **Python pur**, vérifié sur PyPI), pour le fond PDF de §5.
Elle ne sert qu'à superposer des pages ; absente, un gabarit à fond PDF répond `503 pdf_engine_missing` et les
autres impriment normalement.

Réserve assumée : PlutoPrint est en **0.23.0**, un projet jeune. La dépendance optionnelle et la séparation
HTML/PDF (§3) sont la précaution — pas un accident. D'où l'épinglage au MINEUR dans `pyproject.toml`
(`>=0.23.0,<0.24.0`) : chaque 0.x+1 doit être re-mesuré, pas adopté à l'aveugle, parce que trois faits dont la
conception dépend sont des comportements du moteur et non des garanties contractuelles.

**L'extra, et pourquoi la CI l'installe toujours** (étape 4). `pip install "xcore-doctype[pdf]"` ou
`make install-pdf` ; hors de `make install` à dessein (le moteur embarqué pèse une vingtaine de méga-octets, et
une instance qui n'imprime pas ne les porte pas). Mais la CI fait `poetry install --extras pdf` : un test
fumigène qui s'ignore PARTOUT ne vérifie rien, et celui-ci est précisément ce qui détectera qu'une version du
moteur a changé un de ces comportements.

**Ce que le test fumigène a attrapé en s'écrivant**, et qui justifie sa forme :

- un PNG tapé à la main était **invalide** (libpng le refusait) et l'image était perdue **silencieusement** :
  PlutoPrint avertit et continue, donc le PDF amputé sort quand même. La vérification « le PDF avec image est
  plus gros » l'avait laissé passer — à six octets près, le document sans image était même plus gros. Le test
  lit maintenant l'**XObject image** du PDF produit, par le dictionnaire `/Resources` et non par `page.images`
  (qui exigerait Pillow). Leçon générale : contre un moteur qui dégrade au lieu d'échouer, une mesure indirecte
  ne prouve rien.
- un `page_break` en **dernier** bloc n'ouvre **aucune** page — il n'y a rien à y mettre. C'est le bon
  comportement (pas de page blanche à la fin d'une facture), et il est désormais écrit comme tel.
- `pdf.overlay` **plantait** sur un fond illisible (vide ou tronqué), parce que le garde-fou venait après la
  lecture. Un fond abîmé rend maintenant le contenu inchangé : la règle du papier à en-tête absent, pour la
  même raison. Le contenu, lui, vient de notre moteur — son illisibilité serait un défaut et remonte.

## 2. Décision — un gabarit vit sur DEUX niveaux, comme un formulaire

Exactement la séparation déjà en place pour les formulaires et les espaces (§3.5) :

- **livré par un plugin** : `<plugin>/print/*.yaml`, chargé par
  `doctype_registry.load_print_templates(self.ctx.name, dir)` APRÈS `load_dir` (les blocs visent ses champs).
  Non modifiable à l'écran, versionné avec le plugin.
- **celui de l'organisation** : un habillage en base, nouveau `kind` `print` de `xdt_layout_override`, cible
  `<DocType>:<nom du gabarit>` (une seule orthographe : voir la précision 1 de §7). Il PRIME sur celui du
  plugin, à nom de gabarit égal ; sous un autre nom, les deux niveaux coexistent dans le menu.

Et **à défaut, un gabarit est FABRIQUÉ** à partir du `layout` du DocType, comme le moteur fabrique déjà un
espace de travail ou un tableau de bord : aucun document n'est jamais inimprimable.

Pourquoi en base pour l'organisation : un gabarit de facture est propre à l'entreprise (ses mentions, son
ordre, son en-tête), pas au plugin ; et le registre est global au processus, donc un fichier déposé par un
tenant servirait à tous (le raisonnement de §3.5).

## 3. Décision — la génération du HTML est PURE, le PDF est une étape mince

`printing.py` reçoit un DocType compilé, les données d'un document, un gabarit et un papier à en-tête ; il rend
une **chaîne HTML**. Il ne touche ni la base, ni les droits, ni PlutoPrint.

C'est le découpage de `mapping.py` (la logique pure) contre `document.py` (ce qui exige la base), et il se
justifie pareil : l'HTML se teste sur des dictionnaires, à la ligne près, sans binaire ni instance. Le PDF
devient une fonction d'une ligne utile, testée par un seul test fumigène qui s'ignore si le paquet est absent.

## 4. Le vocabulaire des blocs — FERMÉ, et sans code

Sept blocs, qui couvrent une facture, un reçu, un bon de livraison. Aucun n'exécute quoi que ce soit.

```yaml
# doctypes/accounts/print/sales_invoice.yaml   (ou habillage d'organisation)
schema_version: 1
doctype: Sales Invoice
name: standard                 # plusieurs gabarits par type : « standard », « duplicata »…
label: Facture
letterhead: default            # un `Letter Head`, ou `none`
page: {size: A4, orientation: portrait, margins: {top: 20, bottom: 20, left: 15, right: 15}}
blocks:
  - {type: title, text: "Facture {{ doc.name }}"}
  - {type: fields, columns: 2, fields: [customer, posting_date, due_date, company]}
  - type: table
    field: items
    style: bordered          # bordered | ruled | plain
    density: normal          # compact | normal
    repeat_header: true      # l'en-tête se répète à chaque page
    group_by: warehouse      # facultatif : regroupe les lignes
    subtotal: [qty, amount]  # facultatif : un sous-total par groupe, sur ces colonnes
    columns:
      - {field: item_code, width: 40}
      - {field: qty, align: right, width: 15}
      - {field: rate, align: right, width: 20}
      - {field: amount, align: right, width: 25, label: Montant}
  - {type: totals, fields: [total, total_taxes_and_charges, grand_total]}
  - {type: text, text: "{{ doc.printing_terms }}"}
  - {type: spacer}
  - {type: page_break}
```

**La substitution est celle qui existe déjà**, celle des règles de notification : vocabulaire clos
(`{{ doc.<champ> }}`, `{{ doctype }}`, `{{ name }}`), motif linéaire, valeurs rendues en **texte échappé**,
bornées, et **jamais** un champ Password, Table, JSON, TextEditor ni un fichier. On réutilise le code testé ;
on n'ouvre pas Jinja. C'est la même décision que pour `compute:` (§3.7) : le besoin est un gabarit, pas un
langage.

**Les sous-totaux sont un calcul FERMÉ, pas une expression.** `group_by` nomme un champ de la LIGNE et
`subtotal` des colonnes numériques : le rendu somme en `decimal.Decimal`, donc exactement, et rend la valeur à
l'échelle du champ (la règle de `compute:`, §3.7 du programme — on ne fait pas entrer un flottant dans un
montant). Aucune autre agrégation, aucune expression : ni moyenne libre, ni condition. `group_by` sur un champ
Decimal, Table ou fichier est refusé au chargement.

**La répétition de l'en-tête vient du CSS paginé** (`thead` en `table-header-group`), pas de notre code, et
elle est **vérifiée** (§0 : 11 pages, en-tête partout où le tableau passe). Le cumul reporté (« à reporter » en pied de page, « report »
en tête de la suivante) est un autre problème : il exige de savoir OÙ le moteur coupe, ce qu'un HTML généré
ignore. Il demanderait un rendu en deux passes ou un moteur qui rappelle à chaque saut — donc une étude, pas
une option (§9).

**Pas de nouvelles propriétés de champ.** Frappe a besoin de `print_hide` parce qu'il génère tout
automatiquement ; nos blocs **nomment** ce qu'ils impriment, donc la question ne se pose pas. Seul le gabarit
FABRIQUÉ (§2) doit choisir, et il le fait par TYPE : il saute les Password, Image, Attach, JSON, TextEditor et
les champs `hidden`. Aucun ajout au schéma.

## 5. Le papier à en-tête

Un DocType `Letter Head` du plugin `system` (comme `Notification Rule`) : `name`, `image` (un fichier
`xdt_file`), `footer_text`, `is_default`, `disabled`. C'est de la donnée par organisation, donc un DocType et
pas un fichier. Un gabarit le désigne par son nom ; `default` prend celui marqué par défaut.

**Et il peut être un PDF** — c'est le « template importé en PDF » : l'entreprise fournit le PDF de son papier à
en-tête ou d'un formulaire préimprimé, et nos blocs s'impriment PAR-DESSUS.

```yaml
letterhead:
  pdf: lettre_entete.pdf       # un fichier xdt_file du tenant, jamais une URL
  pages: first                 # first | all : le fond sur la 1re page, ou sur toutes
  offset: {top: 45, bottom: 25}  # les zones (en mm) que le fond occupe : le contenu s'y interdit
```

Mécanique : on rend notre HTML en PDF (PlutoPrint) avec des marges qui réservent `offset`, puis on superpose le
fond avec `pypdf` (page fusionnée). Le fond est LU comme un fichier du tenant par le service de fichiers — pas
une URL, pas un chemin (§6). Un PDF de fond à plusieurs pages avec `pages: all` se répète ou s'aligne page à
page : à préciser à l'implémentation (le cas d'usage réel est une page unique répétée).

Ce qui est écarté : **déduire un gabarit depuis un PDF** (lire sa mise en page et en reconstruire les blocs).
L'analyse de mise en page n'est pas fiable par nature, et rendrait un gabarit approximatif à refaire
entièrement à la main (§9).

## 6. Droits, et ce que l'impression ne doit pas devenir

**Imprimer, c'est lire.** La route exige le droit de LIRE le document, avec le périmètre de l'appelant — aucun
grant nouveau. Un droit d'impression par DocType est une autre affaire (`palier70.md`, ligne K) et reste dehors.

**Le rendu n'accède pas au réseau.** C'est le point de sécurité central : un moteur HTML qui suit les URL d'un
gabarit est une faille SSRF, et le gabarit vient de l'organisation.

Deux verrous — et **l'ordre d'importance que j'avais écrit était faux**, la mesure du 28 oct. l'a renversé :

1. toute URL externe est refusée au CHARGEMENT du gabarit (§8), et une image est résolue par NOUS — seulement
   un fichier `xdt_file` du même tenant, lu par le service de fichiers ;
2. et à l'exécution, `Book.custom_resource_fetcher` donne la main sur CHAQUE résolution : notre `fetch_url`
   refuse tout ce qui n'est pas déjà incorporé.

J'avais présenté le second comme « une ceinture en plus de la bretelle ». **C'est l'inverse : c'est lui la
barrière.** Mesuré en écrivant l'étape 4 — le binaire de PlutoPrint embarque **libcurl** et toute sa pile TLS
(openssl, krb5, nghttp2, libpsl, libssh…), et **sans** notre récupérateur il effectue réellement la requête :
un serveur d'essai sur `127.0.0.1` la reçoit. Avec lui, aucune. Le refus au chargement est un contrôle STATIQUE
sur ce qu'un gabarit déclare ; le récupérateur voit chaque URL que le HTML contient au moment du rendu. Les deux
restent, mais c'est le second qu'il ne faut jamais retirer, et il est désormais prouvé par un test qui COMPTE
les requêtes reçues par un serveur local (`test_pdf_engine.TestTheRendererHasNoNetwork`, rien ne sort de la
machine : on ne sonde pas un hôte distant pour vérifier une règle de sécurité).

**Conséquence de licence, et deuxième bénéfice imprévu de la dépendance optionnelle.** Ce binaire embarque des
bibliothèques sous copyleft faible (libidn2 et libunistring en LGPL-3+, libssh en LGPL-2.1) à côté des MIT/BSD.
Nous n'en redistribuons **rien** : notre wheel ne contient pas plutoprint, il déclare l'extra, et c'est
l'opérateur qui l'installe depuis PyPI (vérifié dans les métadonnées du wheel : `Provides-Extra: pdf`,
`Requires-Dist: plutoprint … ; extra == "pdf"`). Notre posture de licence est donc inchangée — ce qui n'était
pas la raison du choix optionnel, mais en est un effet heureux.

**Rien n'est écrit.** `GET` ne stocke pas. L'archivage du PDF (obligation légale sur une facture) est un geste
distinct, explicite, et reste dehors en v1 (§8).

## 7. Les routes

- `GET /print/{doctype}/{name}?template=&letterhead=` → `{html}`. L'aperçu : fonctionne sans PlutoPrint.
- `GET /print/{doctype}/{name}/pdf?template=&letterhead=` → le PDF (`application/pdf`, en pièce jointe).
  Sans le paquet : `503 pdf_engine_missing`.
- `GET /print-templates/{doctype}` → les gabarits disponibles pour ce type (livrés + organisation + fabriqué),
  filtrés par les droits, pour que le Desk peuple son menu « Imprimer ».
- Actions IPC jumelles : `doc.print`, `doc.print.pdf`, `doc.print.templates`.

**Trois précisions que l'écriture a imposées** (28 oct., `print_service.py`) :

1. **La cible d'un habillage est toujours `<DocType>:<nom du gabarit>`** (`print_service.target_of`), jamais
   `<DocType>` seul. §2 offrait les deux orthographes ; en garder deux voulait dire qu'une organisation ajoutant
   un « duplicata » à côté de son « standard » n'aurait plus la même cible pour les deux, donc une reprise de
   données le jour où un second gabarit apparaît. Une seule orthographe, dès maintenant.
2. **Le papier à en-tête se lit en contexte SYSTÈME, sur une liste FERMÉE de champs** (`LETTERHEAD_FIELDS`) —
   le précédent exact de `preferences.py`. Autrement, un commercial qui lit les factures mais n'a aucun grant
   sur `Letter Head` imprimerait sans en-tête : l'en-tête est de la présentation d'entreprise, pas un document
   métier. Et un en-tête absent, désactivé, ou un plugin `system` pas encore installé **n'est jamais une
   erreur** : on imprime sans. Une facture ne devient pas inimprimable parce qu'une ligne d'en-tête a été
   supprimée — les mentions obligatoires sont dans les BLOCS.
3. **`name: default` est un sentinelle** (« celui que l'organisation a marqué par défaut »), avec repli sur un
   document nommé littéralement « default » s'il n'y en a aucun de marqué : le sentinelle ne crée donc pas
   d'impasse. C'est le choix déjà fait pour `login_url: auth` dans `api/desk.py`. La requête peut remplacer ce
   que le gabarit dit (`?letterhead=`), et `none` imprime sans en-tête.

## 7 bis. Le Desk (étape 5), et deux bugs que seul un vrai navigateur a montrés

`PrintDialog.vue`, ouvert par « Imprimer… » dans le menu « ⋯ » de la FICHE. Pas depuis la liste : imprimer
plusieurs documents d'un coup est un rendu MULTI-documents, donc une autre fonctionnalité (§9), pas une case à
cocher. Deux sorties, délibérément différentes :

- **« Imprimer »** passe par le navigateur (`contentWindow.print()` sur l'iframe). Il marche TOUJOURS, même sans
  le moteur serveur, parce que l'aperçu est du HTML ordinaire — c'est la promesse de §1 rendue utilisable.
- **« Télécharger le PDF »** demande le PDF du SERVEUR, et pas celui du navigateur qui saurait aussi le faire :
  une facture sort de l'entreprise, et deux utilisateurs sur deux navigateurs produiraient deux fichiers
  différents. Celui du serveur est la référence, le même qu'un traitement joindrait à un e-mail. Le bouton
  n'existe pas quand le moteur manque, et le dialogue le DIT plutôt que d'offrir un bouton qui échoue.

**Le PDF a demandé d'étendre le contrat de transport** (`HttpRequest.binary`, `HttpResponse.bytes`), parce que
ce contrat est la seule chose qui touche le réseau et qu'un portage Tauri doit servir ce cas aussi. L'autre voie
était une URL signée publique, comme pour les fichiers — écartée : une URL publique qui RESTITUE un document
rendu est une surface bien plus grande qu'un champ de plus (§6, rien n'est public).

**Deux bugs trouvés en lançant le scénario dans Chromium** (`ui/e2e/print.e2e.mjs`), invisibles au vitest parce
que jsdom n'applique pas l'isolation d'origine d'une iframe :

1. `sandbox="allow-modals"` seul donne au cadre une **origine opaque**, et `contentWindow.print` lève alors un
   `SecurityError` depuis la page parente : le bouton « Imprimer » aurait échoué sans rien dire. Le bac à sable
   est donc `allow-same-origin allow-modals`. Ce qui compte, et qui reste absent, c'est **`allow-scripts`** :
   rien ne s'exécute dans ce cadre, et le scénario le prouve en y plaçant un script.
2. Un `ReferenceError` **préexistant** dans `FormView.vue`, que l'impression n'a fait que révéler : son `watch`
   des « créer depuis » est `immediate`, donc `loadMappings` tourne pendant le `setup` et écrit `mappings.value`
   sans attendre — mais `mappings` était déclaré 460 lignes plus bas. Zone morte temporelle, à CHAQUE ouverture
   de fiche. Le `flow` juste au-dessus était, lui, déclaré correctement : la convention existait, une
   déclaration s'en était éloignée.

Leçon générale, la même que celle du test fumigène (§1) : une vérification indirecte ne prouve rien. Le premier
test de fuite de ce scénario cherchait le nom du client dans la page et trouvait le titre de la fiche ; il
mesure maintenant les classes de notre feuille d'impression, qui n'existent nulle part ailleurs.

## 8. Refus au CHARGEMENT

Comme partout : une faute de gabarit échoue au chargement, jamais à la première impression.

- `doctype` inconnu, enfant, virtuel ;
- un champ cité absent du type, ou de genre interdit (Password, Table hors bloc `table`, fichier dans une
  substitution) ;
- `table.field` qui n'est pas un champ Table ; une colonne absente du DocType de lignes ;
- deux gabarits de même `name` pour le même type et le même propriétaire ;
- une URL externe (image, feuille, police) ;
- `page.size` hors liste, marges négatives ;
- plus de `MAX_PRINT_BLOCKS` blocs (100), plus de `MAX_PRINT_COLUMNS` colonnes de tableau (12) ;
- `style`, `density` ou `pages` hors liste fermée ; une largeur de colonne hors 1-100, ou une somme de largeurs
  qui dépasse 100 ;
- `group_by` qui n'est pas un champ de la ligne, ou qui est Decimal, Table ou fichier ; `subtotal` sur une
  colonne non numérique, ou absente des `columns` ;
- `letterhead.pdf` qui n'est pas un fichier du tenant, ou un `offset` négatif.

## 9. Ce que la v1 ne fait PAS

- **Reprendre le constructeur de Frappe.** Son éditeur produit du JSON pour SON moteur (Jinja + Chromium) : pris
  seul, il rend un document que rien n'imprime ; pris avec son moteur, il importe Jinja côté serveur, ce que le
  projet a refusé deux fois. Et il attend l'API de Frappe, `frappe-ui` et Tailwind, dans un Desk PrimeVue : ce
  serait un portage, pas une intégration. On reprend la CONCEPTION — deux niveaux, blocs sans code, en-tête
  séparé — comme on l'a fait pour le Form Builder et le constructeur de workflow. La licence le permettrait ;
  l'architecture ne le conseille pas.
- **Le cumul reporté** d'un tableau sur plusieurs pages (« report » / « à reporter ») : il exige de connaître
  les coupures de pages, qu'un HTML généré ignore. Étude séparée (deux passes, ou un moteur qui rappelle).
- **Déduire un gabarit depuis un PDF** : analyse de mise en page, non fiable par nature.
- Codes-barres et QR (PlutoPrint ne les fait pas), impression brute ESC/POS pour tickets.
- Archivage du PDF dans `xdt_file`, signature électronique, filigrane.
- Droit d'impression par DocType, traduction du gabarit, plusieurs langues.
- Un gabarit PERSONNEL (par utilisateur) : imprimer est institutionnel, pas personnel. Seule la couche de
  l'organisation existe — contrairement aux espaces et aux tableaux de bord, qui ont les deux (§3.5).
- Repli Chromium.

## 10. L'éditeur de gabarits

Dans le périmètre, et pas en JSON : un éditeur visuel, de la même forme que le Form Builder — qui est le
précédent qui a marché (`FormBuilderView.vue`, modèle pur dans `lib/formbuilder.ts`).

**Quatre volets** : la palette des types de blocs | le gabarit comme pile de blocs | le panneau de propriétés du
bloc choisi | **l'aperçu**, rendu par le serveur. L'aperçu est ce qui distingue cet éditeur des autres : on
n'édite pas une disposition abstraite, on regarde la facture.

**Le modèle est PUR** (`ui/src/lib/printedit.ts`), comme `lib/formbuilder.ts` et `lib/blocksedit.ts` :
`addBlock`, `moveBlock`, `removeBlock`, `setProp`, `addColumn`, `moveColumn`, `templateProblems`. Aucune
requête, donc testable au vitest sans monter de composant. Et il tient les règles du serveur AVANT
l'enregistrement (§8) : un gabarit invalide est refusé par l'éditeur avec le même message, plutôt que par un 422
après coup.

**L'aperçu exige une route de plus** : `POST /print-preview/{doctype}/{name}` qui reçoit le gabarit en BROUILLON
(non enregistré) et rend l'HTML. Elle applique exactement les mêmes contrôles que §6 et §8 — y compris le
refus du réseau et la résolution des images par le service de fichiers. Un gabarit non enregistré ne doit pas
être un chemin d'écriture ni une porte : elle n'écrit rien, et exige le droit de LIRE le document d'exemple.

**Qui peut éditer : un périmètre d'écriture TOTAL** (`rights.write === 'all'`), comme les règles de formulaire
(§3.5) et non comme une simple disposition. **Tranché le 28 oct., et pour une raison plus solide que celle
écrite d'abord.** J'avais justifié par « un gabarit de facture engage l'entreprise » : c'est vrai et cela ne
prouve rien, puisqu'une disposition de formulaire engage aussi et ne demande qu'un droit ordinaire. Le vrai
argument : **un gabarit rend des documents que son éditeur n'a pas forcément le droit de LIRE.** Quelqu'un dont
le périmètre est `if_owner` déciderait de la présentation des factures des autres. C'est la forme exacte du
verrou des règles de notification, dont l'auteur doit pouvoir lire TOUS les documents du type — le précédent le
plus proche, bien plus que la disposition d'un formulaire. Le coût est faible : la CONVENTION donne un périmètre
total (`sales:facture:write` seul vaut « tout »), donc le verrou ne gêne pas le cas ordinaire ; il ne mord que
sur un type dont l'écriture est explicitement bornée par `if_owner`. Un lecteur voit l'éditeur en lecture seule
(`editable: false`), il n'est pas refusé.

**Le document d'exemple** : l'éditeur a besoin d'un document réel pour l'aperçu. Il prend le dernier que
l'utilisateur peut lire (un `list` à `limit=1`), et dit clairement quand il n'y en a aucun — un gabarit s'édite
alors sans aperçu, plutôt que pas du tout.

**Ce que l'éditeur ne fait pas** : placer des blocs librement en deux dimensions (le gabarit est une PILE, pas
une toile — c'est ce qui permet de garantir le rendu sans CSS grid), ni écrire du CSS, ni du HTML.

Page `#/print/<Type>` , entrée par le menu « ⋯ » de la fiche et de la liste, à côté de « Personnaliser le
formulaire ». Preuve en Chromium (`ui/e2e/`) comme pour le Form Builder et le constructeur de workflow : un
bloc déplacé, une colonne ajoutée, un aperçu qui suit, un retour à l'origine.

## 10 bis. L'éditeur en place, et le défaut que seul le navigateur a montré

`#/print/<Type>`, ouvert par « Personnaliser l'impression… » dans le menu « ⋯ » de la fiche ET de la liste.
Quatre volets : la palette (les sept blocs, le format, les marges) | la pile | les propriétés | l'aperçu.

**Ce que l'éditeur résout, et qui obligeait à écrire du YAML** : choisir les champs imprimés et leur ordre,
choisir le tableau, ses colonnes, leur titre, alignement, largeur, le regroupement et les sous-totaux.

Trois règles que le modèle pur tient pour que le serveur n'ait jamais à refuser :

- un bloc qu'on vient d'ajouter n'empêche pas d'enregistrer le reste (un tableau sans colonne n'est pas
  envoyé) — autrement l'éditeur obligerait à finir un bloc avant de pouvoir sauver le travail d'à côté ;
- retirer une colonne emporte son sous-total, et ne plus regrouper les emporte tous ;
- `columns` a DEUX sens dans le format du serveur — un nombre pour un bloc `fields`, une liste pour un
  `table`. Le modèle le dit (`tableColumns`, `fieldColumns`) au lieu de forcer la conversion, qui aurait marché
  jusqu'au jour où quelqu'un lit `.length` sur un nombre.

**Et le défaut trouvé dans Chromium, invisible partout ailleurs** : la palette DÉBORDAIT par-dessus la pile.
Un clic sur un bloc atteignait un bouton de palette — vitest ne le voit pas (jsdom ne calcule pas la mise en
page) et le typage encore moins. Cause : un enfant de grille CSS a `min-width: auto`, donc un contenu plus
large que sa piste sort de la colonne au lieu de la contraindre. Corrigé par `minmax(0, …)` sur les colonnes,
`max-content` pour la palette et `min-width: 0` sur les enfants — et le scénario CONTRÔLE désormais qu'aucun
élément cliquable n'est recouvert (`document.elementFromPoint` sur chacun), plutôt que de faire confiance.

Le diagnostic a aussi écarté une fausse piste, et c'est l'intérêt de mesurer : j'avais supposé une boucle de
rendu. Mesuré sur la page au repos : **1 aperçu demandé, 0 mutation du DOM**. La page était parfaitement stable ;
le clic était refusé pour une raison de géométrie, pas de réactivité.

## 11. Ordre de réalisation

1. ~~`printing.py` : les blocs, le rendu HTML pur, ses refus. Testable seul, sans base ni paquet.~~ **fait.**
2. ~~Le chargement des gabarits livrés + l'habillage d'organisation, et le gabarit fabriqué à défaut.~~ **fait.**
3. ~~Les routes et les actions, avec le droit de lecture et la résolution des images en `data:`.~~ **fait** —
   et le papier à en-tête est venu avec, parce que c'est lui qui porte les images à résoudre : l'étape 6 n'a plus
   qu'à livrer le YAML du DocType `Letter Head` dans le plugin `system`, le mécanisme est écrit et testé.
4. ~~`plutoprint` et `pypdf` en extras optionnels, et le test fumigène qui s'ignore sans eux.~~ **fait** —
   extra `[pdf]` (`pyproject.toml`), `make install-pdf`, `tests/unit/test_pdf_engine.py` (21 tests). La CI
   l'installe TOUJOURS (`poetry install --extras pdf`) : un test fumigène qui s'ignore partout ne vérifie rien.
5. ~~Desk : bouton « Imprimer », aperçu, choix du gabarit.~~ **fait** — `PrintDialog.vue`, entrée « Imprimer… »
   dans le menu « ⋯ » de la FICHE seulement (imprimer plusieurs documents d'un coup est une autre affaire, qui
   demanderait un rendu multi-documents : hors périmètre, §9). Preuve en Chromium : `ui/e2e/print.e2e.mjs`.
6. ~~`Letter Head` dans le plugin `system`.~~ **fait** — le DocType livré
   (`system_plugin_dir/doctypes/letter_head.yaml`) et son raccourci dans l'espace « Administration ». Deux
   choix que l'écriture a tranchés : le nom est SAISI (`naming: prompt`) et sert d'intitulé — un champ
   « intitulé » à côté serait un second nom, alors qu'un gabarit n'en désigne qu'un ; et **pas de
   `allow_rename`**, parce qu'un gabarit cite son papier par une simple CHAÎNE de son habillage, que `rename`
   ne peut pas suivre (il réécrit les Link, pas les chaînes) — renommer casserait silencieusement les gabarits.
7. ~~L'éditeur (§10) : le modèle pur d'abord et ses tests, puis la page, puis la route d'aperçu.~~ **fait** —
   modèle pur (`ui/src/lib/printedit.ts`), serveur (ouvrir, enregistrer, retirer, l'aperçu d'un BROUILLON par
   `POST /print-preview/{dt}/{name}`, le document d'exemple, et quatre actions IPC), et la page
   `#/print/<Type>` avec sa preuve en Chromium (`ui/e2e/printbuilder.e2e.mjs`, 8 étapes). Reste le
   TÉLÉVERSEMENT du PDF d'en-tête à l'écran : le serveur le superpose déjà et le modèle porte ses réglages
   (`letterhead.pdf`, `pages`, zones réservées), il manque le champ de fichier et ses deux curseurs.

**L'ordre 1-3 avant 7 n'est pas négociable** : un éditeur édite un format qui doit exister, et il n'est
utilisable qu'avec l'aperçu. Le construire d'abord donnerait un éditeur produisant un gabarit que rien ne valide
et que personne ne peut voir.

Les étapes 1 à 3 ne dépendent d'aucune décision ouverte. L'étape 4 est le seul engagement de paquet, et il est
réversible : rien d'autre ne dépend de PlutoPrint.
