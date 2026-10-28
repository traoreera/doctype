# xcore-doctype

Un moteur de **DocTypes** pour [xcore](https://pypi.org/project/xcoreruntime/), inspiré de Frappe : on décrit un document une fois (YAML),
on obtient le stockage, l'API, les permissions, les hooks, l'historique et une interface (le **Desk**, Vue 3 + PrimeVue). Des documents
**soumettables** (brouillon → soumis → annulé → amendé) et des quantités **exactes** (`Decimal`) permettent d'écrire un vrai métier :
le plugin `tests/support/plugins/stock` en est la démonstration (réceptions, sorties, transferts, journal immuable, stock jamais négatif). Le Desk
est utilisable au téléphone, a un mode sombre, renouvelle la session d'xauth tout seul et adapte ses formulaires au document (colonnes
de lignes selon la nature, unités, date du jour) : voir `process/desk-revue.md`.

```yaml
name: Invoice
module: sales
naming: { series: "FA-{YYYY}-{#####}" }       # FA-2026-00001, sans doublon ni trou
fields:
  - { fieldname: customer, fieldtype: Link, options: Customer, required: true }
  - { fieldname: items, fieldtype: Table, options: InvoiceLine, required: true }
  - { fieldname: total, fieldtype: Number, read_only_depends_on: "true" }
hooks: { before_save: [sales.invoice_totals] }
```

## Installer

```bash
pip install xcore-doctype            # le wheel embarque le Desk ; extra [postgres] pour asyncpg
pip install "xcore-doctype[pdf]"     # + l'impression PDF (plutoprint, pypdf) : optionnelle, voir plus bas
xcore-doctype install ./app          # pose le plugin dans l'instance xcore et la vérifie
```

**L'impression PDF est une dépendance optionnelle** (`process/impression.md`). Sans l'extra `[pdf]`, tout
fonctionne sauf la génération du fichier : l'aperçu HTML d'un document (`GET /print/{type}/{nom}`) répond
normalement, et seule la route PDF répond `503 pdf_engine_missing` en nommant le paquet à installer. C'est
délibéré : le moteur embarqué pèse une vingtaine de méga-octets, et une instance qui n'imprime pas ne les porte
pas.

xcore ne découvre aujourd'hui que des **dossiers** de plugins : `install` dépose `app/xcore-doctype/` (manifeste + shim) et lit votre
`integration.yaml` pour signaler ce qui empêcherait le plugin de répondre. Sur une instance neuve (`xcli init`), il relève :

- `services.databases.default.type: sqlite` → adaptateur **synchrone** : mettez `sqlite+aio` ;
- `main.py` sans `xcore.setup(app)` → toute requête répond 500 `tenant_middleware_missing` ;
- `plugins.strict_trusted` actif → signez le plugin : `xcli plugin security sign app/xcore-doctype`.

`xcore-doctype doctor` refait la vérification à tout moment. Les réglages du plugin (fuseau des numéros de série, Desk, RLS…) sont le
bloc `doctype:` de `app/xcore-doctype/plugin.yaml` : c'est **votre** copie, `install` ne l'écrase pas (sauf `--force`).

Le paquet déclare aussi l'entry point `xcore.plugins` ; xcore ne le lit pas encore (voir `process/xcore-issues.md`).
Le Desk est servi sous `<plugin_prefix>/xcore-doctype/desk/`.

## Apporter vos DocTypes (module métier)

Un plugin métier déclare `requires: [xcore-doctype]` et apporte ses DocTypes ; leur `module` doit être **le nom du plugin** (c'est
le premier segment de leurs grants, `sales:invoice:write`) :

```python
class Plugin(TrustedBase):
    async def on_load(self):
        self.get_service("doctype_hooks").register("sales.invoice_totals", self.invoice_totals, owner=self.ctx.name)
        self.get_service("doctype_registry").load_dir(self.ctx.name, DOCTYPES_DIR)
        grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)         # source unique : le YAML
        await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": grants}, source=self.ctx.name)
```

`xcore-doctype check tests/support/plugins/sales/doctypes --owner sales --with tests/support/plugins/stock/doctypes` valide le dossier (types, liens, module, grants, hooks à
enregistrer) : à mettre dans la CI du plugin (`--with` : les DocTypes des plugins dont il dépend, cibles de ses liens). Deux exemples
complets sont livrés : `tests/support/plugins/sales/` (clients, factures à lignes) et `tests/support/plugins/stock/` (articles, entrepôts, mouvements soumettables) ; ce sont des jeux d'essai du moteur, pas des produits.

### Étendre un autre plugin, étendre le Desk

Un plugin peut aller au-delà de ses propres DocTypes (détails et règles : `process/extensions.md`) :

- **ajouter des champs et des hooks au DocType d'un plugin dont il dépend**, sans toucher à son YAML : `extends/*.yaml`
  (`doctype: Stock Entry`, champs `sales_…`, `hooks: {on_submit: [sales.on_stock_entry]}`), chargé par
  `doctype_registry.load_extensions(self.ctx.name, dossier)` ;
- **ajouter des blocs à l'espace d'un autre module** : un fichier de `workspaces/` avec `contributes_to: stock` au lieu de `name` ;
- **livrer une extension JavaScript du Desk** (pages, panneaux et actions de fiche, actions de liste, blocs d'espace, commandes) :
  un module ES `desk/index.js` déclaré par `doctype_registry.load_desk(self.ctx.name, dossier)`, que l'opérateur autorise dans
  `doctype.desk.extensions` (fermé par défaut). Il importe `vue` et `@xcore-doctype/desk` (la copie du Desk, par une import map), est
  servi depuis la même origine sous la CSP stricte, et une panne de l'extension n'arrête jamais le Desk.

```js
import { defineExtension } from '@xcore-doctype/desk'

export default defineExtension({
  name: 'sales',
  apiVersion: 1,
  setup(desk) {
    desk.form.action({ doctype: 'Customer', label: 'Nouvelle facture', run: ({ record, router }) =>
      router.push({ name: 'new', params: { doctype: 'Invoice' }, query: { customer: record.name } }) })
  },
})
```

### Un document soumettable

```yaml
name: Stock Entry
module: stock
submittable: true                      # brouillon -> soumis (figé) -> annulé ; un annulé s'amende
hooks: { on_submit: [stock.post_entry], on_cancel: [stock.reverse_entry] }
```

`on_submit` / `on_cancel` tournent **dans la transaction** de la soumission et écrivent par `event.tx` (créer, créer-si-absent, modifier
avec contrôle de version, lire avec verrou) : un refus défait l'état et tout ce que le hook avait écrit. Les droits `submit` et `cancel`
sont des grants à part (`stock:stock_entry:submit`). Voir `process/phase-a.md` et `tests/support/plugins/stock/src/ledger.py`.

## Développer

```bash
make install      # Poetry + pnpm
make dev          # backend :8000 et Desk :5173 avec rechargement ; connexion : ADMIN_EMAIL / ADMIN_PASSWORD de .env (créé par make env)
make check        # types, build du Desk, tests Python (SQLite), vitest, navigateur, installation du wheel
make test-pg PG_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/xdt   # + PostgreSQL
```

`make help` liste tout. **Authentification : xauth (`app/auth` sur le poste de développement, hors dépôt, voir `process/xauth.md`)** : utilisateurs, organisations, rôles et grants,
connexion par nom d'utilisateur ou e-mail et mot de passe (MFA, OAuth, invitations). `make env` crée `.env` (administrateur `admin@example.com`, mot de passe aléatoire) et les clés
JWT `conf/` ; xauth crée ce compte au premier démarrage. `make token` donne un jeton d'accès pour les scripts, `make member EMAIL=… PASSWORD=…` un utilisateur de plus. Dans le Desk, **Paramètres > Système** réunit tout xauth : compte (mot de passe, MFA, sessions, comptes liés, invitations reçues), organisations, utilisateurs, rôles et
permissions, catalogue et comptes de la plateforme (administrateur), courrier sortant (extension `ext.email`, simulée tant qu'aucun SMTP n'est configuré), journal d'audit,
accès API ; la cloche de la barre du haut montre les notifications. Le plugin `iam` (`tests/support/plugins/iam`, `process/iam.md`) présente les **permissions, rôles et membres d'xauth comme des DocTypes** (liste,
filtres, rapport, fiches ; xauth reste l'unique source), le profil de chaque utilisateur (« Mon profil »), et la fiche d'un employé (`tests/support/plugins/hr`) pointe vers son compte : un employé qui part ferme son accès.

## Documentation

`process/doctype.md` (le contrat de conception, v3), puis un rapport par phase : `process/spike-phase0.md`, `process/phase1.md` (permissions, hooks),
`process/phase2.md` (le Desk), `process/phase3.md` (liens, tables, numérotation, historique), `process/phase4.md` (livraison, RLS, CI). Le second palier a
son contrat, `process/palier40.md` (feuille de route A à F), et sa première phase, `process/phase-a.md` (documents soumettables, module Stock).
`CLAUDE.md` résume l'architecture et les pièges pour qui reprend le code.
