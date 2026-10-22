# Extensions entre plugins et extensions du Desk

Demande de l'utilisateur (21 oct. 2026) : permettre aux plugins d'**étendre le Desk** par du **vrai code JavaScript** (pas de nouveau
bloc déclaratif), et améliorer l'**intégration entre plugins** : réagir aux événements des DocTypes d'un autre plugin (B1), contribuer à
l'espace d'un autre module (B2), ajouter des champs au DocType d'un autre plugin (B3).

Trois décisions nouvelles, numérotées à la suite de `doc/doctype.md` : D26 (extensions de DocType : B1 + B3), D27 (contributions
d'espace : B2), D28 (extensions JavaScript du Desk).

---

## D26. Extension de DocType : un plugin ajoute des hooks et des champs au DocType d'un autre

Un plugin décrit, dans son propre dossier, ce qu'il ajoute à un DocType qui ne lui appartient pas (`<plugin>/extends/*.yaml`) :

```yaml
schema_version: 1
doctype: Stock Entry          # le DocType étendu (celui d'un plugin que ce plugin `requires`)
label: Ventes                 # titre de la section ajoutée à la fiche (défaut : le nom du plugin)
fields:                       # B3 : des champs, nommés `<plugin>_…`
  - { fieldname: sales_customer, fieldtype: Link, options: Customer, label: Client }
  - { fieldname: sales_note, fieldtype: Text }
hooks:                        # B1 : des hooks, aux mêmes événements et avec les mêmes garanties que ceux du YAML d'origine
  on_submit: [sales.stock_entry_submitted]
```

Chargé par `doctype_registry.load_extensions(self.ctx.name, dossier)`, **après** `load_dir` (les champs ajoutés peuvent viser les
DocTypes du plugin) et **avant** `load_workspaces` (un bloc d'espace peut mesurer un champ ajouté), retiré par `unregister_owner` (comme le
reste de ce qu'il apporte). Ordre d'un `on_load` complet : `load_dir` → `load_extensions` → `load_workspaces` → `load_desk`.

**Comment.** Le registre garde la définition d'ORIGINE de chaque DocType et, à part, les extensions par DocType visé. La définition
servie est la **fusion** (origine + extensions dans l'ordre de chargement) : elle repasse par `parse_definition` (tous les contrôles du
schéma s'appliquent à la fusion) puis par la compilation et les contrôles de liens, comme une définition ordinaire. Rien d'autre dans le
moteur ne sait qu'un champ vient d'une extension : écriture stricte, conditions, filtres, droits, historique, `/meta`, Desk, tout suit.

- **Hooks** : ajoutés APRÈS ceux du DocType, événement par événement. Mêmes règles : un hook cité mais non enregistré **refuse** la
  sauvegarde (503), `action:` seulement avant la transaction, `on_submit`/`on_cancel`… avec `event.tx`. Un DocType étendu par un hook
  a donc des hooks : `tx.create/update` d'un AUTRE hook sur ce DocType est refusé (`hook_not_supported`), comme pour un DocType qui
  déclare les siens (ses hooks ne tourneraient pas).
- **Champs** : leur `fieldname` commence par `<plugin>_` (un champ ajouté plus tard par le propriétaire ne peut pas entrer en collision ;
  on voit dans les données d'où vient une valeur). Refusé dans une extension tout ce qui changerait ce que fait le DocType de son
  propriétaire : `required`, `mandatory_depends_on` (un obligatoire conditionnel) et `unique` (les documents existants et le code du
  propriétaire, ses `tx.create` par exemple, ne les connaissent pas), `set_only_once`, `in_list_view` (il remplacerait les colonnes par
  défaut de sa liste), les champs `Table` (le DocType de lignes serait celui d'un autre propriétaire) et `Password` (une valeur chiffrée
  resterait dans les documents si le plugin qui étend part). Un DocType avec `layout` reçoit une section de plus (`label`) pour les champs
  affichés (un champ `hidden` est stocké, jamais placé) ; sans `layout`, les champs suivent ceux d'origine.
- **Droits** : un champ ajouté est une partie du document : le lire ou l'écrire, c'est lire ou écrire le DocType étendu (pas de grant
  nouveau, pas de droit par champ, comme partout ailleurs).
- **Visés** : un DocType chargé, d'un AUTRE propriétaire (on modifie son propre YAML, pas une extension de soi), pas un DocType de lignes
  (`istable`, compilé par parent), pas un DocType virtuel (`source` : ses documents vivent chez la source). Une extension par
  (plugin, DocType). Une extension sans champ ni hook est refusée.
- **Ordre et rechargements.** Les extensions survivent au retrait du DocType visé (le propriétaire se recharge) : elles se réappliquent
  quand il revient. Si elles ne s'appliquent plus (le propriétaire a ajouté un champ du même nom, retiré la cible d'un Link, ou le
  DocType est devenu un DocType de lignes, virtuel, ou celui du plugin qui l'étendait), le chargement du propriétaire **échoue** avec le
  nom de l'extension en cause (tout ou rien, comme ailleurs) : jamais une extension
  silencieusement ignorée (ses hooks font peut-être respecter une règle).
- **`/meta`** porte `extended_by: [{plugin, label, fields}]` (jamais les hooks, privés comme ceux du DocType).
- **Preuves.** `tests/unit/test_extensions.py` (règles, fusion, tout ou rien, ordre de plusieurs plugins, rechargement du propriétaire,
  champs stockés, typés et liés, hook qui tourne après ceux du propriétaire et refus 503 quand il manque) et
  `tests/integration/test_doctype_channel.py` (un plugin `crm` étend l'article de `stock` sur un vrai xcore, puis s'en va).
- **Limite connue.** Quand le plugin qui étend est retiré, ses valeurs restent dans les documents mais le Desk ne les renvoie plus :
  la prochaine modification d'un document les efface (`update` remplace `data`). Réactiver le plugin avant de modifier. Une valeur
  CHIFFRÉE (un secret) n'est jamais rendue, même sous un champ que la définition ne connaît plus : `DocumentService._present` et
  l'historique la masquent d'après son préfixe (`sealing.PREFIX`).

## D27. Contribution d'espace : des blocs dans l'espace d'un autre module

Un fichier de `<plugin>/workspaces/` qui porte `contributes_to` au lieu de `name` ajoute des blocs à l'espace d'un AUTRE module :

```yaml
schema_version: 1
contributes_to: stock          # le module (= le plugin) dont l'espace reçoit ces blocs
blocks:
  - { type: header, text: Ventes }
  - { type: shortcut, doctype: Invoice, new: true }
  - { type: number_card, label: Factures du mois, doctype: Invoice, function: count }
```

- Mêmes blocs, mêmes contrôles au chargement que dans un espace (les DocTypes visés sont chargés), même filtrage par les droits de qui
  regarde. Les blocs suivent ceux de l'espace, dans l'ordre de chargement des plugins. Ils s'ajoutent aussi à un espace FABRIQUÉ.
- Le module visé doit exister (un espace déclaré ou un DocType de ce module qui n'est pas un réglage `single` : un module de réglages
  n'a pas d'espace) ; une contribution par (plugin, module) ; pas à soi-même. Retirée avec le plugin. `load_workspaces` la rend sous la
  forme `+<module>` dans sa liste. Un FICHIER d'espace a au plus 100 blocs ; l'espace composé (fichier + contributions) et son habillage
  jusqu'à 1000 (`MAX_COMPOSED_BLOCKS`).
- **Habillage.** Une disposition enregistrée (Personnaliser) part de l'espace AVEC ses contributions : ce qu'elle montre lui appartient
  désormais. Une contribution arrivée après l'enregistrement n'apparaît pas tant que l'habillage existe (« Revenir à l'origine » la
  montre). Quand le plugin qui contribuait part, ce que l'habillage citait de lui devient invisible (jamais une erreur : un type
  déchargé ne se lit pas) et l'habillage reste enregistrable (on y retire ce qui cite un type déchargé).
- **Preuves.** `tests/unit/test_workspaces.py::TestContributions` (blocs à la suite, retrait avec le plugin, cibles refusées, contrôles au
  chargement, espace fabriqué, filtrage par les droits avec son titre, bloc `extension` servi et validé).

## D28. Extensions JavaScript du Desk

Un plugin livre un module ES (déjà construit) que le Desk charge au démarrage. C'est du **code de confiance** dans la page de
l'utilisateur : il a sa session, ses droits, tout ce que le Desk peut faire. D'où :

- **Liste blanche de l'opérateur** : `doctype.desk.extensions` dans le manifeste d'xcore-doctype, liste de noms de plugins (`["*"]` :
  tous). Absente ou vide : aucune extension chargée (fermé par défaut). Une extension non autorisée est journalisée, jamais servie.
- **Même origine** : les fichiers sont servis par le routeur PUBLIC du Desk sous `desk/ext/<plugin>/…` (même garde qu'un fichier du
  build : pas de traversée, pas de lien symbolique qui sort du dossier, types de média explicites, `no-cache`). La CSP reste
  `script-src 'self'` (plus l'empreinte de l'import map, ci-dessous) : un plugin ne peut pas faire charger un script d'un autre domaine.
- **Déclarée par le plugin** : `doctype_registry.load_desk(self.ctx.name, dossier)` ; le dossier contient `index.js` (le point d'entrée)
  et ce qu'il importe. Retirée par `unregister_owner`. Le Desk la découvre par `config.json` (`extensions: [{name, url}]`, `url` porte
  l'empreinte du contenu pour que le navigateur ne garde pas une version périmée).

**Le contrat (API d'extension, version 1).** `index.js` exporte par défaut `defineExtension({...})` :

```js
import { defineExtension } from '@xcore-doctype/desk'
import { h, ref } from 'vue'

export default defineExtension({
  name: 'sales',            // = le plugin ; une extension qui se nomme autrement est refusée
  apiVersion: 1,
  setup(desk) {
    desk.routes.add({ path: 'pipeline', title: 'Pipeline', component: Pipeline })             // #/x/sales/pipeline
    desk.sidebar.add({ workspace: 'sales', label: 'Pipeline', icon: 'chart-line', to: '/x/sales/pipeline' })
    desk.form.panel({ doctype: 'Customer', title: 'Encours', component: CustomerBalance })   // props : doctype, name, record, meta
    desk.form.action({ doctype: 'Invoice', label: 'Relancer', icon: 'send', when: ({ record }) => record.docstatus === 1,
                       run: async ({ record, api, notify, reload }) => { … } })
    desk.list.action({ doctype: 'Invoice', label: 'Exporter', run: async ({ names }) => { … } })
    desk.blocks.add('pipeline', Pipeline)       // bloc d'espace `{ type: extension, component: sales.pipeline }`
    desk.commands.add({ label: 'Pipeline des ventes', icon: 'chart-line', run: ({ router }) => router.push('/x/sales/pipeline') })
  },
})
```

`desk` donne aussi `api` (le client du Desk : `/doc`, `/meta`… avec la session), `plugin` (un client des routes HTTP du plugin lui-même,
`desk.plugin.get('/pipeline')` → `<préfixe>/<plugin>/pipeline`, avec le même jeton et le même renouvellement ; un chemin qui sortirait de
ces routes, `..` même encodé ou suivi de `?`, est refusé), `notify`, `router` et `styles.add(href)` (la feuille de style d'une extension
construite, même origine). Les composants réutilisables (`CellValue`, `FieldControl`, `LinkField`, une sélection de PrimeVue), `t` (la
langue) et `useDesk()` (dans un composant : les mêmes outils) s'importent du SDK. Tout le reste du Desk est privé.

- **Une seule copie de Vue.** L'extension importe `vue` et `@xcore-doctype/desk` comme des noms nus ; l'`index.html` du Desk porte une
  **import map** qui les fait pointer vers `shared/vue.js` et `shared/sdk.js`, deux points d'entrée du build aux noms FIXES (le reste du
  build garde ses noms par empreinte). L'auteur construit son extension avec ces deux modules en `external`. La CSP autorise l'import
  map par son empreinte SHA-256 (calculée par le serveur dans l'`index.html` qu'il sert), jamais par `'unsafe-inline'`.
- **Isolée des pannes.** Un `setup` qui lève, un module qui ne se charge pas, un composant qui plante : l'extension est écartée (ou son
  composant remplacé par un encart « extension en panne »), une seule notification, le Desk continue. Les appels de `setup` passent un
  par un ; une extension ne voit pas les enregistrements des autres.
- **Les droits restent au serveur.** Une action ou un panneau ne contourne rien : ce qu'il lit ou écrit passe par l'API, qui juge.
  `when` ne fait que montrer ou cacher.
- **Où cela apparaît.** Une action de fiche va dans le menu « ⋯ » (en tête, séparée de celles du Desk), ou dans la barre d'en-tête avec
  `primary: true` ; un panneau se range sous le formulaire d'un document ENREGISTRÉ (pas d'un brouillon jamais sauvé ni d'un réglage) ;
  une action de liste rejoint les actions groupées (et la sélection est offerte même à qui n'a que la lecture, si son `when`, évalué avec
  une sélection VIDE, ne la cache pas pour ce type) ; un lien de barre latérale
  se range sous l'espace qu'il nomme, sinon dans un groupe « Extensions » ; une commande entre dans la palette (Ctrl+K). Une page reçoit
  les paramètres de son adresse en props. Un lien direct vers `#/x/…` fonctionne (la première navigation est rejouée une fois les
  extensions chargées).
- **Blocs d'espace.** Nouveau type de bloc déclaratif `extension` (`component: <plugin>.<nom>`, `label`, `cols`, `props` : petit objet
  JSON aux clés `[a-z][a-z0-9_]*`). Le composant reçoit DEUX props : `config` (l'objet `props` du bloc, jamais étalé : une clé non
  déclarée comme `innerHTML` ou `style` ne tombe pas sur son élément racine) et `label`. Le serveur ne peut pas savoir ce que le
  composant lit : le bloc se sert avec sa section à qui voit l'espace, mais il n'est jamais du contenu À LUI SEUL (un espace où
  l'utilisateur ne lit rien reste caché) ; un habillage peut le déplacer, le retirer ou changer sa largeur, jamais en ajouter un ni
  changer son `component`, son `label` ou ses `props` (`extension_block_not_offered`). Le Desk montre un encart discret quand aucune
  extension chargée ne fournit ce composant.

**Écrire une extension.** `xcore-doctype new-desk-extension <dossier du plugin>` pose `desk/index.js` (une page, un lien, une commande, et en commentaire les autres points d'extension) et `desk/README.md` : il se charge tel quel. `xcore-doctype check <plugin>/doctypes --owner <plugin>` valide `desk/`, `extends/` et les contributions d'espace avec les DocTypes. Un module ES ; le plus simple est un fichier écrit à la main (`tests/support/plugins/sales/desk/index.js`,
sans build : `h()` plutôt que des templates). Avec des composants `.vue`, la construire en bibliothèque :

```js
// vite.config.js du plugin
export default {
  plugins: [vue()],
  build: {
    // `cssFileName` : le CSS des composants `.vue` sort dans desk/style.css (à charger par `desk.styles.add`, ci-dessous)
    lib: { entry: 'src/index.js', formats: ['es'], fileName: () => 'index.js', cssFileName: 'style' },
    outDir: 'desk',
    rollupOptions: { external: ['vue', '@xcore-doctype/desk'] },   // la copie du Desk, par l'import map
  },
}
// et dans setup : desk.styles.add(new URL('./style.css', import.meta.url).href)
```

Puis `registry.load_desk(self.ctx.name, Path(__file__).parent.parent / "desk")` dans `on_load`, et l'opérateur l'autorise. Les types
du contrat sont dans `ui/src/extensions/types.ts` ; le SDK exporte `defineExtension`, `useDesk`, `CellValue`, `FieldControl`,
`LinkField`, `describeError`, `t`, `piIcon` et une sélection de PrimeVue (`ui/src/shared/sdk.ts`). En développement (`make dev`),
Vite relaie les extensions que le backend sert.

**Preuves.** `tests/unit/test_desk.py::TestExtensions` (liste blanche, traversée, lien symbolique, types de média, empreinte de
version, CSP de l'import map), `ui/tests/extensions.spec.ts` (contrat, isolation des pannes et du délai, contributions mal formées,
routes, filtres `when`, client du plugin confiné à ses routes, `readExtensions` même origine, blocs, fiche), et le scénario Chromium
`ui/e2e/extensions.e2e.mjs` (`tests/e2e/test_desk_browser.py`) : l'extension de `sales` se charge sous la vraie CSP, lien direct,
rechargement, panneau, action, barre latérale, palette, sans aucune erreur de console. `ui/tests/extension-contract.spec.ts` charge pour de vrai l'extension de `sales` et le modèle de `new-desk-extension` ; `tests/unit/test_cli.py` couvre `check` (D26-D28) et `new-desk-extension`.

**Revue adversariale (22 oct. 2026).** Cinq relecteurs (sécurité, registre, Desk, régressions, outils), chaque constat soumis à deux
contradicteurs : 34 confirmés (une vingtaine de défauts distincts), tous corrigés avec un test qui échoue sans le correctif. Les plus
graves : les props d'un bloc d'extension étalées sur l'élément racine (HTML injectable par quiconque écrit un seul type de l'espace) ;
`desk/index.html` servi sans la CSP ; un espace rendu visible par un seul bloc d'extension ; `%2e%2e` qui faisait sortir le client du
plugin de ses routes ; un habillage citant le type d'un plugin parti qui cassait `/workspaces` pour toute l'organisation. Deux défauts
ANTÉRIEURS en sont sortis : le contrôle `unique` rétroactif des DocTypes de plugin n'était jamais attendu (il tourne maintenant en tâche
et retire les DocTypes du plugin en cas de double), et `integrity.unique_conflicts` lisait `DOCUMENT_TABLE.c`, un NOM de table (le
contrôle au chargement et l'action `doc.check_unique` n'avaient jamais pu tourner). Tests : `tests/unit/test_extensions_review.py`,
`ui/tests/extensions.spec.ts`, `tests/unit/test_cli.py::TestReviewOfTheTools`, `tests/unit/test_desk.py`.

**Écarts et limites.** Pas de bac à sable (iframe) : l'opérateur autorise du code qu'il a choisi d'installer, comme un plugin `trusted`
côté serveur. Pas de version d'API autre que 1 (une extension `apiVersion: 2` est refusée avec un message, pas devinée). Les
extensions ne sont chargées qu'au démarrage du Desk (un plugin rechargé exige de recharger la page).
