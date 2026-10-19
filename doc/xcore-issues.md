# Issues à ouvrir côté xcore

Brouillons, issus des phases 0 à 4 (`doc/doctype.md` §14, `doc/phase4.md`). **Rien n'a été modifié dans xcore ni dans orchestra-backend** :
xcore-doctype contourne chacun de ces points de son côté, et dit comment. Versions vérifiées : xcore 2.9.1, xcorecli 2.3.0.

## 1. Découverte des plugins par entry point (`xcore.plugins`)

**Constat.** `PluginLoader` ne connaît que le scan d'un dossier (`plugins.directory`) : `load_all()` et `discover_names()` itèrent le
dossier, `load()` suppose `<dossier>/<nom>`. Un `pip install` ne peut donc pas suffire à charger un plugin distribué comme paquet.

**Contournement actuel.** Le paquet déclare déjà `[project.entry-points."xcore.plugins"] xcore-doctype = "xcore_doctype:plugin_dir"`
(une fonction qui rend le dossier du plugin, inerte aujourd'hui) et `xcore-doctype install ./app` copie ce dossier dans l'instance.

**Proposition.** Une abstraction `PluginSource` (dossier local ; entry point du groupe `xcore.plugins` qui renvoie un chemin, résolu par
`importlib.resources`), que `load_all`, `load`, `reload` et `discover_names` utilisent. Un dossier local de même nom l'emporte, avec un
avertissement. Le drapeau `enabled` de `PluginStateStore` s'applique aussi aux entry points.

## 2. Confiance d'un plugin livré par wheel (`strict_trusted`)

**Constat.** `strict_trusted` vaut `True` par défaut et `plugin.sig` est calculé avec la clé du déploiement : un wheel ne peut pas le
fournir, et `site-packages` peut être en lecture seule.

**Contournement actuel.** `xcore-doctype install` signale l'option et donne la commande `xcli plugin security sign <dossier>`.

**Proposition.** `plugins.trusted_entry_points: [xcore-doctype]` : les entry points listés sont exemptés de `verify_plugin` (installer
un paquet donne déjà l'exécution de code ; la liste blanche dit lesquels le déploiement accepte).

## 3. Un `plugin.yaml` invalide est ignoré avec un simple avertissement

**Constat.** Un manifeste qui n'est pas du YAML valide (par exemple `description: … : …` non guillemeté) fait sauter le plugin avec
`WARNING invalid manifest … skipped=1`, et le démarrage se termine par « xcore ready ». Les hooks du plugin ne s'enregistrent jamais ;
les DocTypes qui les citent refusent alors leurs sauvegardes (503 `hook_unavailable`).

**Proposition.** Niveau `ERROR`, et une option pour faire échouer le démarrage (`plugins.fail_on_invalid_manifest`).

## 4. `xcli init` génère une instance qu'un plugin asynchrone ne peut pas utiliser

**Constat.** Le projet généré a `services.databases.default.type: sqlite` (adaptateur synchrone, avec une URL `sqlite+aiosqlite://`) et un
`main.py` sans `xcore.setup(app)`, que seul `Xcore.setup` installe : `TenantMiddleware`. Sans lui, `request.state.tenant_id` n'existe pas.

**Proposition.** `type: sqlite+aio` quand l'URL est asynchrone, et `xcore.setup(app)` dans le gabarit de `main.py`.

## 5. API publique du contexte de tenant

`xcore.kernel.tenancy.services._validate_tenant` et `xcore.kernel.tenancy._current_tenant_id` sont importés depuis des modules privés
(`xcore_doctype/context.py`). Les exposer dans `xcore.kernel.tenancy`.

## 6. `_ScopedEvents.emit` ne force pas `source` (sécurité, chantier reporté n° 1)

**Constat.** `source` est auto-déclaré par l'émetteur d'un événement, et le gestionnaire de `rbac.declare` d'xauth prend `data["plugin"]` tel
quel : un plugin de confiance peut déclarer, ou désactiver, les grants d'un autre. Lu dans le code, jamais exécuté.

**Proposition (deux petits changements, dans deux dépôts).** xcore force `source` au nom du plugin émetteur dans `_ScopedEvents.emit` ;
xauth ignore un `rbac.declare` dont `event.source != data["plugin"]`. Comparer `source` seul, sans le changement d'xcore, ne protège rien.

## 7. Documentation

`EventBus.emit` n'annule rien et avale les erreurs de ses abonnés ; `call_plugin` est absent en sandbox ; les routes d'un plugin
contournent le pipeline (ni `PermissionEngine`, ni rate limit, ni ContextVar) ; `permissions` absent d'un manifeste signifie `deny_all` ;
la limite de 64 Kio d'une ligne IPC du worker sandbox doit répondre par une erreur plutôt que de rester muette.
