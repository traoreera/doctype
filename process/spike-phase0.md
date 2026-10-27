# Spike de phase 0 : résultats

17 octobre 2026. Réalisé contre xcore 2.9.1 (installé dans le venv) et PostgreSQL 18.6 (`fr_FR.UTF-8`), Python 3.14.7.

Ce document consigne ce que le spike a vérifié, ce qu'il a infirmé ou précisé, et ce qu'il laisse ouvert. `process/doctype.md` n'est pas modifié : les écarts sont proposés ici, à reporter dans la v4.

## 1. Ce qui a été construit

Le strict nécessaire pour exécuter les critères de 14 bis.8, dans `src/xcore_doctype/` : `schema.py`, `builder.py`, `registry.py`, `context.py` (annexe A), `query.py`, `document.py`, `db/`, `api/http.py`, `plugin.py`, le dossier plugin embarqué (`plugin_dir/`) et `devauth.py` (backend d'auth de développement, non enregistré par le plugin).

**Hors périmètre, volontairement.** Aucun contrôle de rôle (`permissions`), `hooks`, `layout`, `depends_on`, numérotation par série, Link vérifié, tables enfants ni UI. *(Mise à jour : les permissions et les hooks sont faits en phase 1, voir `process/phase1.md`. Le reste est toujours ouvert, et cette section décrit l'état à la fin de la phase 0.)* Un identifiant authentifié lit et écrit tous les DocTypes de son tenant. Le schéma **refuse** les blocs non appliqués (`permissions`, `hooks`, `layout`, `depends_on`, `naming` en série) au lieu de les ignorer : une définition ne doit pas donner l'impression d'être protégée alors qu'elle ne l'est pas.

## 2. Critères d'acceptation (14 bis.8)

| # | Critère | Résultat | Preuve |
| --- | --- | --- | --- |
| 1 | Chargement, `strict_trusted` actif, liste blanche | **Partiel** | Le plugin se charge depuis un dossier avec `strict_trusted: false`. Avec `strict_trusted: true` il ne se charge pas : D19 est confirmée nécessaire. La liste blanche et l'entry point n'existent pas dans xcore 2.9.1 (changement amont) |
| 2 | `doc.get` abouti ; sans `permissions` : `permission_denied` | **OK** | `test_c2_*` |
| 3 | Route HTTP et action : même résultat | **OK** | `test_c3_*` (succès et erreurs identiques) |
| 4 | `X-Tenant-ID` ≠ claim : 403 ; avec `cross_tenant_grant` : autorisé | **OK** | `test_c4_*`, HTTP et IPC |
| 5 | Sans principal : refus ; contexte système autorisé et journalisé | **OK** | `test_c5_*`, `test_document.py::TestSystemContext` |
| 6 | `validate` qui lève annule ; `after_save` qui lève ne défait rien | Phase 1 | — |
| 7 | Reload : routes remontées, aucun état résiduel | **OK** | `test_c7_*` : routes identiques après 3 reloads, registre de l'ancienne instance vidé, ancienne instance libérée (`weakref`), `unload` retire les routes |
| 8 | Document > 64 KiB vers un hook sandboxé | Phase 1 | — |
| 9 | Test de fuite entre tenants sur chaque fonction de `query.py`, SQLite et PostgreSQL | **OK** | `test_query_leak.py`, vérifié par mutation : sans le filtre de tenant, les 5 scénarios échouent |
| 10 | Adaptateur synchrone : échec clair au `on_load` | **OK** | `test_db_check.py`, `test_c10_*` |

Suite complète : **299 tests passent** sur SQLite et PostgreSQL (211 sur SQLite seul, les 88 cas PostgreSQL sont SKIPPED sans `XCORE_DOCTYPE_TEST_PG_URL`).

## 3. Confirmé par l'exécution

- Découverte par scan de dossier uniquement ; le wheel embarque bien `plugin_dir/plugin.yaml` et le shim, mais aucun entry point n'est lu par xcore (D1, D19).
- Sans bloc `permissions`, toute action est refusée ; avec `allowed_callers: ["*"]` l'appel passe (D18).
- Les routes du plugin **contournent** `PermissionEngine` : même en `deny_all`, l'API HTTP répond (§10). Toute restriction HTTP doit vivre dans le service `Document`.
- `session()` commite à la sortie du bloc (D2, 14 bis.6).
- D15 : 8 mises à jour concurrentes avec la même `version`, exactement une réussit, sur SQLite et PostgreSQL.
- D3 : avec `isolate_db: true` et sans schéma de tenant, le `search_path` retombe sur `public` et tout fonctionne.
- Filtres d'égalité (texte, nombre, booléen, date, absence), tri avec `NULLS LAST`, pagination et total : mêmes résultats sur les deux bases.

## 4. Écarts avec la doc, à reporter dans la v4

1. **`Xcore.setup(app)` est obligatoire pour le tenant.** Seul `setup()` installe `TenantMiddleware`. `main.py` ne l'appelait pas (ni la docstring d'`Xcore`, ni `xcore-know.md`) : `request.state.tenant_id` n'existait jamais. `main.py` est corrigé ; le plugin échoue fermé (`500 tenant_middleware_missing`) au lieu de retomber sur `default`.
2. **`tenancy.enabled` n'est pas lisible depuis un plugin.** L'annexe A suppose `ContextSettings.from_plugin_config(config, tenancy_enabled)`. Je n'ai trouvé aucune API publique qui le donne (`PluginContext` ne l'expose pas, le registre ne le garde que dans un attribut privé). Remplacé par `doctype.enforce_tenant_binding`, vrai par défaut (échec fermé), à passer à `false` seulement si la tenancy d'xcore est désactivée. À ajouter au §14 : exposer `tenancy` aux plugins.
3. **`isolate_db: true` n'est pas une option de confort, c'est une condition de cohérence (D3).** Mesuré sur PostgreSQL :
   - les routes HTTP ne posent jamais `_current_tenant_id` (seul `Supervisor._dispatch` le fait) : `TenantAwareDB` y isole toujours sur `default` ;
   - avec des schémas de tenant existants, le DDL du `on_load` crée la table dans le schéma `default` ;
   - résultat : `GET /list` répond 200 (par accident) et `doc.list` par IPC échoue avec `la relation « xdt_document » n'existe pas`.
   Le plugin émet maintenant un avertissement au `on_load` si `db` est un `TenantAwareDB` sur PostgreSQL.
4. **`on_reload` s'exécute sur l'ancienne instance, avant le déchargement**, puis une nouvelle instance est créée et reçoit `on_load`. L'exemple de `xcore-know.md` (`on_reload` qui appelle `on_load`) est trompeur. D8 est tenue sans `on_reload` : tout l'état est dans `on_load`/`on_unload`.
5. **D11 : un `isinstance(db, AsyncSQLAdapter)` ne suffit pas.** Avec la tenancy, `get_service("db")` est un `TenantAwareDB`. La vérification ouvre une vraie session et attrape l'absence de `__aenter__`.
6. **Les erreurs inattendues fuyaient.** xcore renvoie `str(e)` à l'appelant IPC (`code: all_retries_failed`), soit sur PostgreSQL la requête SQL complète et ses paramètres liés (tenant compris). `Plugin._run` journalise côté serveur et renvoie `internal_error`. À signaler côté xcore.
7. **`NaN` et `Infinity` empoisonnaient un tenant.** `json.loads` les accepte ; SQLite les stockait, puis la sérialisation de toute réponse contenant le document échouait. Les nombres non finis sont refusés à l'écriture.
8. **Le tri de texte n'est pas portable.** `Banana, apple, cherry` sur SQLite (binaire), `apple, Banana, cherry` sur PostgreSQL en `fr_FR.UTF-8`. À trancher avant la phase 2 : forcer une collation commune (`COLLATE "C"` / `BINARY`, déterministe mais peu naturel en français) ou accepter l'écart et le documenter.
9. **FastAPI 0.14x** : `app.router.routes` contient des `_IncludedRouter` sans `.path`. Les tests lisent les routes via OpenAPI. Un premier jet les lisait par `.path` et ne vérifiait rien (liste toujours vide) ; il a été corrigé et les comparaisons exigent maintenant une liste non vide.
10. **Formes d'erreur hétérogènes.** Les erreurs d'authentification d'xcore sont `{"detail": "Token manquant"}` ; les nôtres `{"detail": {"code", "msg", "details"}}`. L'UI devra gérer les deux.

## 5. Choix faits pendant le spike, à valider

- **Corps des requêtes** : `POST {"data": {...}}`, `PUT {"version": n, "data": {...}}`, les documents sont renvoyés sous la forme `{doctype, name, owner, creation, modified, version, data}`. Les champs système restent séparés des champs du DocType, donc rien à « rejeter dans un payload » ; la mise à jour **remplace** `data` (pas de fusion) et exige `version`.
- **Écriture stricte** : pas de coercion (`"12"` n'est pas 12, `1` n'est pas `true`), dates ISO canoniques seulement (`20261017` refusé), champs inconnus et noms système refusés.
- **Nommage** : `naming: hash` uniquement (identifiant aléatoire de 12 hexadécimaux) tant que les séries (phase 3) n'existent pas.
- **Tri** : `NULLS LAST` dans les deux sens, `name` en départage pour une pagination stable.

## 6. Non vérifié

- Entry point `xcore.plugins`, liste blanche `trusted_entry_points`, commande `sign` : rien d'implémenté côté xcore.
- Configuration par déploiement : `doctypes_dirs` est lu dans le `plugin.yaml` embarqué (donc dans `site-packages`) et relatif au répertoire de lancement. Le mécanisme pour surcharger la config d'un plugin installé reste à trouver.
- SQLite : `WAL` et `busy_timeout` (§7.3) non posés (nécessiterait un listener sur l'engine partagé). Les tests de concurrence passent sans.
- `StaticFiles` au montage, démontage et reload : pas de SPA encore.
- RLS PostgreSQL (D14), compteur de numérotation sur PostgreSQL (D7), hooks sandboxés et limite de 64 KiB.
- Tests PostgreSQL contre un seul serveur, version 18, collation `fr_FR.UTF-8`.

## 7. Rejouer

```bash
poetry install
poetry run pytest                      # SQLite ; les cas PostgreSQL sont SKIPPED
XCORE_DOCTYPE_TEST_PG_URL='postgresql+asyncpg://user@host:port/db' poetry run pytest   # + PostgreSQL
```

Pour lancer l'application hôte avec le plugin et un backend d'auth de développement :

```bash
export XCORE_DEV_AUTH_SECRET='<secret de développement>'   # aucun secret par défaut
poetry run uvicorn main:app --port 8000
# jeton : xcore_doctype.devauth.mint_token(secret, {"sub": "alice", "tenant_id": "acme",
#         "permissions": ["sales:customer:read", "sales:customer:write"]})   # les grants cités par doctypes/customer.yaml
# routes : /app/xcore-doctype/{meta,list,doc}/...  (plugin_prefix = /app)
```
