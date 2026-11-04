# Guide xcore-doctype : configurer, créer un plugin, utiliser le moteur

Ce guide explique, dans l'ordre où on en a besoin :

1. [Installer et configurer](#1-installer-et-configurer) une instance xcore avec xcore-doctype ;
2. [Créer un plugin métier](#2-créer-un-plugin-métier) (DocTypes, Python, espace, extension du Desk, gabarits d'impression) ;
3. [Utiliser xcore-doctype](#3-utiliser-xcore-doctype) depuis un plugin (services Python, hooks), par l'API HTTP et par le Desk.

Le plugin complet qui sert d'exemple de bout en bout est [`examples/biblio`](../examples/biblio) : des livres et leurs emprunts, en
YAML + Python + JavaScript. Chaque section y renvoie.

---

## 1. Installer et configurer

### 1.1 Ce qu'il faut

- Python **3.14** et une instance **xcore ≥ 2.9** (`xcoreruntime`) ;
- une base **asynchrone** : `sqlite+aio` (développement) ou `postgresql+aio` (production, avec l'extra `[postgres]`) ;
- un plugin d'authentification nommé **`auth`** (xauth) : c'est lui qui délivre les jetons et reçoit les grants.

```bash
pip install xcore-doctype            # le wheel embarque le Desk
pip install "xcore-doctype[pdf]"     # + l'impression PDF (optionnelle)
pip install "xcore-doctype[postgres]" # + asyncpg pour PostgreSQL
```

### 1.2 Poser le plugin dans l'instance

xcore découvre les plugins **par dossier** : `pip install` seul ne charge rien. La commande `install` dépose le plugin et vérifie
l'instance :

```bash
xcore-doctype install ./app          # ./app = plugins.directory de votre integration.yaml
```

Elle écrit :

| Dossier | Contenu | Réécrit à chaque `install` ? |
|---|---|---|
| `app/xcore-doctype/` | le moteur (`plugin.yaml` + un petit `src/main.py`) | le code oui, **`plugin.yaml` non** (c'est votre copie, gardée sauf `--force`) |
| `app/system/` | les réglages de base (`System Settings`), les règles de notification, les restrictions d'utilisateur | oui (un fichier que vous y ajoutez est gardé, avec un avertissement) |
| `app/xlicense/`, `app/xpulse/` | la plateforme : licences/modules, temps réel | le code oui, `plugin.yaml` non |

Puis elle affiche un diagnostic (`xcore-doctype doctor` le refait à tout moment) : base synchrone, `xcore.setup(app)` manquant,
tenancy incohérente, plugin `auth` absent, manifeste qui ferme encore les extensions…

### 1.3 `integration.yaml` (l'hôte xcore)

Les points qui comptent pour xcore-doctype :

```yaml
app:
  plugin_prefix: "/app"          # les routes du moteur seront sous /app/xcore-doctype

tenancy:
  enabled: true
  isolate_db: false              # OBLIGATOIRE : l'isolation est la colonne tenant_id (sinon HTTP et IPC divergent)

plugins:
  directory: ./app
  strict_trusted: false          # true en production : signez alors les plugins (xcli plugin security sign …)

services:
  databases:
    default:
      type: sqlite+aio           # ou postgresql+aio ; « sqlite » seul est l'adaptateur SYNCHRONE : refusé
      url: sqlite+aiosqlite:///./xcore.db
```

### 1.4 `main.py` (le point d'entrée)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from xcore import Xcore

xcore = Xcore(config_path="integration.yaml")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await xcore.boot(app)
    yield
    await xcore.shutdown()

app = FastAPI(lifespan=lifespan)
xcore.setup(app)   # OBLIGATOIRE : installe le TenantMiddleware (sinon 500 tenant_middleware_missing)
```

Lancement : `uvicorn main:app`. Le Desk est alors servi sous `http://hôte/app/xcore-doctype/desk/`.

**Servir le Desk ailleurs** (à la racine d'un domaine, sous `/erp`…) :

```python
from xcore_doctype import Config, mount_desk

mount_desk(app, Config(path="/erp", api_base="/app/xcore-doctype", login_url="/app/auth/login"))
```

Le Desk monté lit la liste des extensions et l'annonce du temps réel chez le plugin : laissez `doctype.desk.enabled` à `true`.

### 1.5 Le manifeste du moteur (`app/xcore-doctype/plugin.yaml`, bloc `doctype:`)

C'est **votre** copie : `install` ne la réécrit pas. Les réglages courants :

```yaml
doctype:
  enforce_tenant_binding: true   # false SEULEMENT si tenancy.enabled est false
  tenant_claim: tenant_id
  timezone: Africa/Abidjan       # fuseau des séries de numérotation ({YYYY}…) et de « aujourd'hui »
  rls: false                     # PostgreSQL : Row Level Security en plus (rôle de connexion ordinaire exigé)
  doctypes_dirs: []              # VOS DocTypes d'opérateur (les plugins apportent les leurs autrement, §2)
  directory:   { provider: xauth }           # annuaire des membres (assignations, mentions) ; none pour s'en passer
  notifications: { provider: xauth, email: true, desk_url: https://erp.example.com/app/xcore-doctype/desk/ }
  desk:
    enabled: true
    login_url: auth              # « auth » = <préfixe>/auth/login ; null = jeton collé (développement)
    # extensions: [sales]        # absent = toutes les extensions déclarées par les plugins ; [] = aucune
    # developer_mode: true       # le constructeur de DocTypes à l'écran (développement seulement)
```

Les actions d'**opérateur** (`ops.history.purge`, `ops.trash.purge`) sont fermées : décommentez leur ligne dans `permissions:` pour les
ouvrir. Elles exigent aussi un jeton qui porte le grant `cross_tenant_grant`.

### 1.6 Réglages de l'organisation (dans le Desk)

**Paramètres › Général** (DocType `System Settings` du plugin `system`, réservé aux administrateurs) : nom, logo, couleur, format de
date, langue, et la section **Impression** — le **fond PDF** (votre papier à en-tête : logo, coordonnées, pied de page, mentions),
les pages où il s'applique et les zones réservées en haut et en bas. Toutes les impressions s'impriment par-dessus.

### 1.7 Développement dans ce dépôt

```bash
make install   # poetry install + pnpm install (ui/)
make dev       # backend :8000 + Vite :5173, rechargement à chaud ; identifiants dans .env
make check     # build du Desk + tous les tests : à lancer avant de rendre un travail
```

---

## 2. Créer un plugin métier

Un plugin métier **apporte ses données** (DocTypes) au moteur ; il n'écrit ni SQL, ni route CRUD, ni écran de formulaire. Arborescence
type (celle de `examples/biblio`) :

```
mon_module/
├── plugin.yaml          # le manifeste xcore
├── src/main.py          # charge le tout, déclare les grants, fournit les hooks
├── doctypes/*.yaml      # les types de documents (OBLIGATOIRE)
├── workspaces/*.yaml    # la page d'accueil du module dans le Desk (facultatif)
├── print/*.yaml         # des gabarits d'impression livrés (facultatif)
├── extends/*.yaml       # des champs ajoutés aux DocTypes d'un AUTRE plugin (facultatif)
├── translations/*.yaml  # les libellés traduits (facultatif)
└── desk/index.js        # une extension JavaScript du Desk (facultatif)
```

> **Règle des noms.** Le nom du plugin est `[a-z0-9_]+` (pas de tiret) et chaque DocType déclare `module: <nom du plugin>` : c'est
> l'espace de noms de ses grants (`biblio:livre:read`). Les workspaces et traductions vivent **à côté** de `doctypes/`, jamais dedans
> (tout fichier de `doctypes/` est lu comme un DocType).

### 2.1 Le manifeste (`plugin.yaml`)

```yaml
name: biblio
version: 0.1.0
framework_version: ">=2.9"
execution_mode: trusted          # un plugin en bac à sable n'a pas accès aux services du moteur
entry_point: src/main.py
requires: [xcore-doctype, auth]  # le moteur fournit les services ; auth reçoit les grants
permissions:                     # sans ce bloc, xcore refuse toute action du plugin
  - { resource: "biblio.*", actions: ["execute"], effect: allow }
```

Citez entre guillemets toute `description:` qui contient « `: ` » : un manifeste YAML invalide est ignoré avec un simple avertissement,
et ses hooks n'existent alors jamais (chaque sauvegarde répond 503).

### 2.2 Un DocType (`doctypes/livre.yaml`)

```yaml
schema_version: 1
name: Livre
label: Livre
module: biblio                   # = le nom du plugin
icon: book                       # un nom PrimeIcons, sans « pi- »
title_field: titre               # ce qu'un lien affiche à la place de l'identifiant
fields:
  - { fieldname: titre,  label: Titre,  fieldtype: Text,   required: true, in_list_view: true }
  - { fieldname: auteur, label: Auteur, fieldtype: Text,   in_list_view: true }
  - { fieldname: genre,  label: Genre,  fieldtype: Select, options: [Roman, Essai, BD], default: Roman }
  - { fieldname: isbn,   label: ISBN,   fieldtype: Text,   unique: true }
layout:
  - { section: Le livre, columns: [[titre, auteur], [genre, isbn]] }
```

**Types de champ** : `Text`, `LongText`, `TextEditor`, `Code`, `Int`, `Number`, `Percent`, `Decimal`, `Currency`, `Rating`, `Date`,
`Datetime`, `Time`, `Duration`, `Boolean`, `Select`, `Link`, `DynamicLink`, `Table`, `Color`, `Image`, `Attach`, `JSON`, `Password`.
Un montant est un `Decimal`/`Currency` (avec `decimals:`), jamais un `Number` : il est exact.

**Ce qu'un DocType sait déclarer**, sans une ligne de Python :

| Besoin | Clé YAML |
|---|---|
| Numérotation sans trou | `naming: { series: "FA-{YYYY}-{#####}" }` (ou `prompt`, `{field: code}`, `hash`) |
| Champ visible/obligatoire/en lecture seule selon une condition | `depends_on`, `mandatory_depends_on`, `read_only_depends_on` (`"statut == 'Rendu'"`) |
| Lien vers un autre type | `fieldtype: Link, options: Client` (+ `link_filters`, `target_status`) |
| Lignes (un tableau dans le document) | `fieldtype: Table, options: Ligne Facture` + un DocType `istable: true` |
| Valeur copiée d'un document lié | `fetch_from: client.adresse` |
| Calcul | `compute: "qty * rate"`, `"total + taxes - remise"`, `"sum(items.amount)"` |
| Document validé puis figé | `submittable: true` (brouillon → soumis → annulé, `amend`) |
| Circuit d'approbation | `workflow: { states, transitions }` |
| Onglets dans le formulaire | `tab:` sur les sections du `layout` |
| Vues | `views: { kanban, calendar, gantt, dashboard }` |
| Droits autres que la convention | `permissions: { read: [...], write: [...], create: [...], delete: [...] }` |

**Droits par convention** (sans bloc `permissions`) : `<module>:<doctype>:read`, `…:write` (créer et modifier), `…:delete`, et pour un
type soumettable `…:submit` / `…:cancel`. Le nom du type est mis en minuscules, les espaces deviennent `_` (`Stock Entry` →
`stock:stock_entry:read`).

### 2.3 Le Python (`src/main.py`)

```python
from pathlib import Path
from xcore.sdk import AutoDispatchMixin, TrustedBase
from xcore_doctype import DocumentValidationError, HookEvent

ROOT = Path(__file__).resolve().parent.parent

class Plugin(AutoDispatchMixin, TrustedBase):
    async def on_load(self) -> None:
        # 1. Les hooks D'ABORD : un type dont un hook manque refuse ses sauvegardes (503).
        hooks = self.get_service("doctype_hooks")
        hooks.register("biblio.verifie_dates", self.verifie_dates, owner=self.ctx.name)

        # 2. Les définitions, dans cet ordre.
        registry = self.get_service("doctype_registry")
        registry.load_dir(self.ctx.name, ROOT / "doctypes")              # tout ou rien
        registry.load_extensions(self.ctx.name, ROOT / "extends")         # si le dossier existe
        registry.load_workspaces(self.ctx.name, ROOT / "workspaces")
        registry.load_print_templates(self.ctx.name, ROOT / "print")
        registry.load_translations(self.ctx.name, ROOT / "translations")
        registry.load_desk(self.ctx.name, ROOT / "desk")

        # 3. UN seul rbac.declare, tiré du YAML : rien n'est retapé à la main.
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)

    async def on_unload(self) -> None:
        self.get_service("doctype_hooks").unregister_owner(self.ctx.name)
        self.get_service("doctype_registry").unregister_owner(self.ctx.name)

    async def verifie_dates(self, event: HookEvent) -> None:
        if event.data.get("rendu_le") and event.data["rendu_le"] < event.data.get("sortie", ""):
            raise DocumentValidationError("Retour avant la sortie",
                details=[{"field": "rendu_le", "msg": "Ne peut pas précéder la sortie", "type": "hook_rejected"}])
```

Ne chargez que les dossiers qui existent (un plugin YAML seul n'a que `load_dir` + `rbac.declare`). Le DocType cite ses hooks **par
nom** :

```yaml
hooks:
  validate: [biblio.verifie_dates]
  before_save: [biblio.duree]
```

### 2.4 La page du module (`workspaces/biblio.yaml`)

```yaml
schema_version: 1
name: biblio                     # = le plugin
label: Bibliothèque
icon: book
order: 30
blocks:
  - { type: number_card, label: Emprunts en cours, doctype: Emprunt, filters: { statut: En cours } }
  - { type: chart, label: Emprunts par mois, doctype: Emprunt, kind: bar, group_by: sortie, bucket: month, periods: 6 }
  - { type: shortcut, doctype: Emprunt, new: true }
```

Blocs : `header`, `paragraph`, `spacer`, `shortcut`, `number_card`, `chart`, `quick_list`, `card`, `extension`. Chaque utilisateur ne
voit que ce qu'il a le droit de lire. Un fichier avec `contributes_to: <autre module>` à la place de `name` ajoute des blocs à
l'espace d'un autre plugin.

### 2.5 Un gabarit d'impression (`print/standard.yaml`)

```yaml
schema_version: 1
doctype: Facture
name: standard
label: Facture
page: { size: A4, orientation: portrait, top: 20, bottom: 20, left: 15, right: 15 }
blocks:
  - { type: title, text: "Facture {{ name }}" }
  - { type: fields, columns: 2, fields: [client, date, echeance] }
  - type: table
    field: lignes
    style: accent                # bordered | ruled | plain | striped | accent | boxed
    columns:
      - { field: article, width: 50 }
      - { field: qte, align: right, width: 15 }
      - { field: montant, align: right, width: 35 }
  - { type: totals, fields: [total_ht, tva, total_ttc] }
```

Blocs : `title`, `fields`, `table`, `totals`, `text`, `spacer`, `page_break`. Aucun code : `{{ name }}`, `{{ doctype }}` et
`{{ doc.<champ> }}` seulement. Sans gabarit, le moteur en fabrique un depuis le `layout` : aucun document n'est inimprimable. Chaque
organisation peut ensuite le refaire à l'écran (« Personnaliser l'impression ») ; le fond PDF vient de ses Paramètres.

### 2.6 Une extension du Desk (`desk/index.js`)

```bash
xcore-doctype new-desk-extension ./app/biblio   # pose desk/index.js + README, prêt à charger
```

```js
import { defineComponent, h } from 'vue'
import { defineExtension, useDesk } from '@xcore-doctype/desk'

export default defineExtension({
  name: 'biblio',              // = le nom du plugin
  apiVersion: 1,
  setup(desk) {
    desk.routes.add({ path: 'retards', title: 'Retards', component: Retards })   // #/x/biblio/retards
    desk.form.panel({ doctype: 'Livre', title: 'Emprunts', component: LivreEmprunts })
    desk.form.action({ doctype: 'Livre', label: 'Prêter', run: ({ record, router }) => router.push(/* … */) })
  },
})
```

Un module ES écrit à la main (aucun build) : `vue` et `@xcore-doctype/desk` sont fournis par le Desk. Tout passe par l'API avec la
session de l'utilisateur : ses droits s'appliquent. L'extension est servie dès que le plugin l'a déclarée par `load_desk` (sauf si
l'opérateur restreint la liste). Une extension qui plante affiche une notification et n'arrête jamais le Desk.

### 2.7 Vérifier avant de livrer

```bash
xcore-doctype check ./mon_module/doctypes --owner mon_module                 # DocTypes + espaces + extensions + desk/
xcore-doctype check ./sales/doctypes --owner sales --with ./stock/doctypes    # avec les types des plugins qu'il `requires`
```

Puis : lien symbolique dans `app/` (`ln -s ../mon_module app/mon_module`), relance, et attribuez les grants `mon_module:*` à un rôle
(Paramètres › Système › Rôles). L'organisation qui crée son espace les reçoit d'office.

---

## 3. Utiliser xcore-doctype

### 3.1 Depuis un plugin : les services

| Service | À quoi il sert |
|---|---|
| `doctype_registry` | charger ses définitions (`load_dir`, `load_workspaces`, `load_print_templates`, `load_extensions`, `load_translations`, `load_desk`) et les retirer (`unregister_owner`) |
| `doctype_hooks` | enregistrer ses hooks par nom (`register(nom, coroutine, owner=…)`) |
| `doctype_grants` | la liste des grants de ses DocTypes, prête pour `rbac.declare` (`for_plugin(nom)`) |
| `doctype_writer` | **écrire** : `await writer.as_request(request)` dans une route du plugin (droits de l'utilisateur), `writer.system(plugin, tenant, raison)` pour une tâche planifiée (seulement les types du plugin) |
| `doctype_documents` | **lire** en contexte système, nommé par sa raison : `system(tenant, raison).list(…) / .get(…)` |
| `doctype_sources` | brancher une source externe pour un DocType virtuel (`source: <nom>`) |

Méthodes du *writer* : `get`, `list`, `create`, `update`, `submit`, `cancel`, `amend`, `transition`, `delete`, et `ensure(doctype,
data, unique=<champ>)` pour une tâche rejouable (crée ou retrouve, sans doublon même en concurrence).

```python
@router.post("/reception-rapide")
async def reception(request: Request, body: dict):
    docs = await self.get_service("doctype_writer").as_request(request)   # 401/403 comme l'API
    entry = await docs.create("Stock Entry", {"purpose": "Réception", "items": body["items"]})
    return await docs.submit("Stock Entry", entry["name"], version=entry["version"])
```

### 3.2 Les hooks

| Moment | Ce que le hook peut faire |
|---|---|
| `validate` | refuser (`raise DocumentValidationError`, 422) ; `data` en lecture seule |
| `before_save` / `before_insert` | modifier `event.data` en place (revalidé ensuite) |
| `after_save` / `after_insert` / `after_delete` | réagir après le commit (une erreur est journalisée, rien n'est défait) |
| `on_submit` / `before_cancel` / `on_cancel` / `on_update_after_submit` / `on_trash` | écrire **d'autres** documents dans la MÊME transaction par `event.tx` |

`event.tx` : `get(doctype, nom, lock=True)`, `create`, `ensure`, `update`, `find(doctype, filtres, …)`, `sum(doctype, champ,
filtres)` (exact), `today`. Un refus annule tout, état du document compris. Un hook qui lit puis écrit **verrouille** (`lock=True`).

`HookEvent` porte `event`, `ctx`, `doctype`, `name` (`None` pendant la création d'un type numéroté), `data`, `old`.

### 3.3 Par l'API HTTP

Toutes les routes sont sous `<plugin_prefix>/xcore-doctype` et exigent `Authorization: Bearer <jeton>` et `X-Tenant-ID: <organisation>`.

| Route | Rôle |
|---|---|
| `GET /meta`, `GET /meta/{doctype}` | les types visibles, et la définition d'un type (champs, droits effectifs, vues) |
| `GET /list/{doctype}?filters=…&order_by=…&order=asc&limit=…&offset=…` | une page de documents |
| `GET /doc/{doctype}/{nom}` | un document |
| `POST /doc/{doctype}` `{data}` | créer |
| `PUT /doc/{doctype}/{nom}` `{data, version}` | modifier (verrou optimiste : `version` obligatoire) |
| `DELETE /doc/{doctype}/{nom}` | supprimer (vers la corbeille) |
| `POST /doc/{doctype}/{nom}/submit\|cancel` `{version}`, `…/amend`, `…/transition` `{action, version}` | cycle de vie |
| `GET /search/{doctype}?q=…` | autocomplétion |
| `GET /aggregate/{doctype}` | compter, sommer, regrouper |
| `GET /history/{doctype}/{nom}` | historique des modifications |
| `POST /bulk/{doctype}`, `POST /import/{doctype}` | actions en lot, import CSV |
| `GET /print/{doctype}/{nom}`, `…/pdf` | aperçu HTML, PDF |

Filtres : un objet JSON, `{"statut": "En cours"}` pour l'égalité, `{"date": [">=", "2026-01-01"]}` sinon (`=`, `!=`, `<`, `>`, `<=`,
`>=`, `like`, `in` selon le type du champ : pas d'ordre sur un texte, `like` insensible à la casse, `in` jusqu'à 100 valeurs). Les erreurs ont la forme `{"detail": {"code": "…", "msg": "…", "details": [...]}}` ;
un document hors de vos droits répond 404, comme s'il n'existait pas. Chaque route a sa jumelle en action IPC (`doc.save`, `doc.list`,
`doc.print`…).

### 3.4 Par le Desk

L'interface est générée depuis les définitions : listes (et vues kanban, calendrier, gantt, rapport, tableau de bord), formulaires
avec onglets et tables de lignes, recherche `Ctrl+K`, commentaires, assignations, pièces jointes, étiquettes, partages, corbeille,
historique, import CSV, impression. Pour chaque type, le menu « ⋯ » donne aussi accès à la personnalisation de l'organisation :

- **Personnaliser le formulaire** : disposition, libellés, champs ajoutés (`custom_…`), règles (obligatoire, lecture seule, défaut) ;
- **Personnaliser l'impression** : un éditeur façon traitement de texte, avec les styles de tableau et un fond PDF propre au gabarit ;
- **Circuit** : resserrer les transitions d'un workflow ;
- et sur chaque espace, **Personnaliser** ses blocs, pour l'organisation ou pour soi.

Rien de tout cela n'écrit de fichier : une personnalisation vit en base, par organisation ; une définition vit dans un plugin.

---

## Pour aller plus loin

| Sujet | Document |
|---|---|
| Le contrat de conception (décisions D1-D29) | [`process/doctype.md`](../process/doctype.md) |
| Extensions entre plugins et du Desk | [`process/extensions.md`](../process/extensions.md) |
| Impression et PDF | [`process/impression.md`](../process/impression.md) |
| L'authentification (xauth) | [`process/xauth.md`](../process/xauth.md) |
| Le plugin d'exemple, pas à pas | [`examples/biblio/README.md`](../examples/biblio/README.md) |
