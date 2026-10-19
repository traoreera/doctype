# Phase 1 : permissions et hooks

17 octobre 2026. Contre xcore 2.9.1 et PostgreSQL 18.6. Suite de `doc/spike-phase0.md` ; `doc/doctype.md` n'est pas modifié, les écarts sont proposés ici pour la v4.

## 1. Résultat

Critères de sortie de la feuille de route (§15) :

| Critère | Résultat | Preuve |
| --- | --- | --- |
| Matrice grant × action × `if_owner` × `match` verte | **OK** | `tests/unit/test_permissions_matrix.py` : 14 jeux de grants × 3 utilisateurs, comparés à un oracle indépendant, sur SQLite et PostgreSQL |
| Un `validate` annule | **OK** | critère 6 : `test_hooks.py::test_c6_*` (service) et `integration/test_hooks.py::test_c6_*` (xcore réel) |
| Un `after_save` qui lève ne défait rien | **OK** | idem |
| Critère 8 : document > 64 Kio vers un hook sandboxé : erreur immédiate | **OK** | vrai plugin sandboxé (sous-processus xcore) : 413 en moins d'une seconde, appelant jamais invoqué, worker intact |
| Conflit 409 testé | **OK** (depuis la phase 0) | `test_query_parity.py`, `test_xcore_spike.py::test_c3_http_conflict_is_409` |
| Actions sandboxées avant la transaction | **OK** | aucune session ouverte pendant l'appel (`TrackingDB`) |

Suite complète : **579 tests passent** sur SQLite et PostgreSQL 18 (382 sur SQLite seul, les 197 cas PostgreSQL sont SKIPPED sans `XCORE_DOCTYPE_TEST_PG_URL`). Les tests d'intégration des hooks, qui démarrent un vrai worker sandbox, ont été répétés 16 fois avec quatre processus pytest en parallèle : 16 réussites sur 16 après correction du harnais (1 échec sur 8 avant, voir §4).

Les garanties ont été éprouvées par mutation : 6 mutations sur les permissions et 8 sur les hooks (filtre `if_owner`/`match` retiré, valeur écrite non contrôlée, attribut manquant pris pour « tout », hook manquant ignoré, sortie sandboxée non revalidée, `after_save` propagé, hooks `action:` dans la transaction, borne de taille retirée, tenant ambiant…). Chacune fait échouer au moins un test.

## 2. Permissions (D6)

*Révisé deux fois après lecture du plugin d'auth d'orchestra-backend (§7) : les règles citent des **grants**, non des rôles ; puis la syntaxe est réduite à une **convention** car xauth résout déjà rôles et grants à chaque requête.*

Un `Scope` est compilé une fois par (utilisateur, action) et évalué par deux interprètes équivalents : SQL (`Query`) pour lire, modifier et supprimer ; Python pour la valeur **écrite**, qui n'est pas encore en base.

**Cas courant : aucun bloc `permissions`.** Les grants se déduisent de `module` (le nom du plugin propriétaire) et du nom du DocType :

| Action | Grant exigé (DocType `Customer`, `module: xsales`) |
| --- | --- |
| `read` | `xsales:customer:read` |
| `write` et `create` | `xsales:customer:write` (comme « Créer/modifier un client » dans le manifeste d'`xsales`) |
| `delete` | `xsales:customer:delete` |

**Cas particulier : un objet par action**, dont chaque clé liste les grants qui l'ouvrent. Une entrée est une chaîne, ou un objet pour un cas restreint :

```yaml
module: xsales
permissions:
  read:   [xsales:customer:read, { grant: xsales:customer:own, if_owner: true }]   # ses propres clients seulement
  delete: [{ grant: xsales:customer:delete, tenant_grantable: false }]             # métadonnée pour auth
  # write et create ne sont pas listés : convention. Une liste vide (`delete: []`) ferme l'action à tout le monde.
```

Décisions prises (à valider) :

1. **Refus par défaut.** Sans `module` ni `permissions`, un DocType est inaccessible aux utilisateurs ; seul le contexte système y accède. Un avertissement est journalisé au chargement.
2. **Des grants, jamais des rôles.** Vérifiés dans `principal["permissions"]`. Les noms de rôle sont libres et propres à chaque tenant chez auth ; la composition rôle -> grants est faite par auth. `role:admin` est refusé avec un message qui l'explique (sinon il serait lu comme un grant dont personne n'est titulaire). `permissions` doit être une liste de chaînes, sinon aucun grant.
3. **Une action non listée retombe sur la convention ; une action listée la remplace** (pas de fusion). Les entrées d'une action s'additionnent (OU). `if_owner` (et `match`, gelé) restreignent une entrée.
4. **403 ou 404.** 403 `permission_denied` si aucune entrée ne correspond aux grants de l'utilisateur pour cette action (indépendant du document). 404 si le document est hors de son périmètre de **lecture** : il est indiscernable d'un document inexistant. Modifier ou supprimer exige de pouvoir lire le document ; un utilisateur avec `write` mais sans `read` reçoit 403.
5. **La valeur écrite est contrôlée** (création, mise à jour, sortie d'un hook) par le même périmètre. Avec `if_owner` seul ce contrôle est trivial (le propriétaire ne change pas) : il ne prend son sens que quand un périmètre dépend de la valeur d'un champ (`match`, ou le futur scope, §7).
6. **`match` est gelé.** Il lit `principal["user"][attr]` ; or xauth ne met que `email` et `tenant_id` dans `user`. Le schéma le refuse (`schema.MATCH_ENABLED = False`) ; le code et ses tests restent en place. Voir §7 pour son remplaçant.
7. **`/meta/{doctype}`** renvoie les droits effectifs par action (`all` | `restricted` | `none`), **jamais** les règles, les noms de grants ni les hooks. `/meta` ne liste que les DocTypes sur lesquels l'utilisateur a au moins un droit.
8. **`Query` exige un `scope`** pour toute lecture, mise à jour ou suppression : l'oublier est une erreur, jamais un accès non restreint (`Scope.everything(...)` est explicite).
9. **`module` est un namespace de grants** : `[a-z0-9_]+`, et hors des namespaces réservés d'auth (`admin rbac auth tenants user license plugins`), dont auth refuserait la déclaration en silence.

## 3. Hooks (D2, D4, D16, D21)

```yaml
hooks:
  validate: [sales.check_amount]                          # de confiance, dans la transaction
  before_save: [sales.stamp, "action:sales-rules.check_credit"]
  after_save: [sales.notify]                              # de confiance, après le commit
```

**Hooks de confiance.** Ce sont des coroutines enregistrées **par nom** dans un registre (D16) : aucun import depuis une chaîne du YAML. Le schéma refuse les noms qui ne sont pas des identifiants séparés par des points (`../x`, `os.system('x')`). Un plugin qui déclare `requires: [xcore-doctype]` les enregistre via le service exporté :

```python
hooks = self.get_service("doctype_hooks")
hooks.register("sales.check_amount", self.check_amount, owner=self.ctx.name)   # on_load
hooks.unregister_owner(self.ctx.name)                                          # on_unload
```

Un hook reçoit un `HookEvent` (`event`, `ctx`, `doctype`, `name`, `data`, `old`, `get()`), lève `DocumentValidationError(message)` pour annuler (422). `data` est en lecture seule dans `validate`, modifiable dans `before_save` (le résultat est revalidé par le schéma et le périmètre). `get()` lit un document du même tenant **sans** filtre de permission : le code d'un hook de confiance en répond.

**Hooks `action:`.** L'action d'un autre plugin, typiquement sandboxé, appelée **avant** d'ouvrir la transaction (aucune connexion tenue). Elle reçoit `{event, doctype, name, doc, old, user, roles}` et répond `{"status": "ok", "doc": {...}}` (`doc` absent = inchangé) ou `{"status": "error", "code", "msg"}` pour refuser. Le tenant et le principal sont passés **explicitement** (`ctx.caller(..., tenant_id=, principal=)`), jamais les `ContextVar` d'`call_plugin`, qui ne sont pas posées pour une route HTTP (vérifié dans l'audit IPC : `tenant_id=globex`, `caller=xcore-doctype`). La réponse d'un plugin sandboxé est **revalidée comme une entrée client** : un `owner` injecté, un champ inconnu, un `NaN` ou un document sorti du périmètre sont refusés.

**Ordre d'une sauvegarde** : identité, permission de l'action, disponibilité des hooks, validation, contrôle de la valeur écrite, hooks `action:` (validate puis before_save), transaction (hooks de confiance validate puis before_save, revalidation, écriture avec contrôle de version), commit, `after_save` et événement. Les hooks `action:` passent donc **avant** les hooks de confiance, quel que soit l'événement qui les déclare (D21).

**Correspondance des erreurs** :

| Situation | Statut | Code |
| --- | --- | --- |
| Hook qui refuse (`DocumentValidationError`, ou `error` d'une action) | 422 | `hook_rejected` (message du hook, borné à 500 caractères) |
| Hook de confiance qui plante | 500 | `hook_error` (détail dans le journal seulement) |
| Hook référencé mais non enregistré, plugin absent, permission/`allowed_callers` refusés, crash du handler | 503 | `hook_unavailable` |
| Hook trop lent (notre budget ou timeout d'xcore) | 504 | `hook_timeout` |
| Réponse d'un plugin sandboxé invalide (forme, champ système, type) | 502 | `hook_invalid_result` |
| Document trop gros pour un hook `action:` | 413 | `hook_payload_too_large` |

**Échec fermé (D16).** Si un hook référencé n'est pas enregistré, la sauvegarde est **refusée** (503), jamais appliquée sans la validation que la définition promet. Après un reload d'xcore-doctype, le registre est vide : les DocTypes à hooks refusent les écritures (les lectures et les DocTypes sans hook ne sont pas touchés) jusqu'à ce que le plugin dépendant se réenregistre (`POST /ipc/<plugin>/reload`). Il n'y a pas de réenregistrement automatique. Cela vaut aussi pour `after_save`.

**`after_save` et événement.** Après la sortie du bloc de session. Une erreur ou un timeout est journalisé et ne défait rien. L'événement `doctype.<Nom>.after_save` part sur l'EventBus avec `{doctype, name, tenant_id, version, user, created}` et **sans le contenu du document** : le bus est global au processus et ses abonnés ne sont pas filtrés par tenant. `action:` est refusé dans `after_save` (une notification ne peut rien annuler).

**Taille (critère 8).** Le worker sandbox ignore sans répondre toute ligne IPC de plus de 64 Kio ; l'appelant attend alors le timeout du plugin et xcore recycle le sous-processus. Le plafond par défaut est donc **60 000 octets** mesurés comme le canal IPC (JSON en ASCII échappé : « é » pèse 6 octets), `old` compris, configurable jusqu'à 63 488 (`doctype.hook_max_payload_bytes`). Vérifié sur un vrai worker : un document à 59 000 passe, le maximum configurable (63 000) passe, 70 000 échoue en 413 immédiatement, et sans la borne le worker ne répond pas.

## 4. Défauts trouvés pendant la phase

1. **`self._log.error(..., msg=...)` plantait.** Le logger d'xcore a la signature `error(msg, *args, **fields)` : un champ nommé `msg` entre en collision avec le premier argument. Le chemin d'erreur d'infrastructure aurait levé un `TypeError` en production. Détecté parce que le double de journal des tests reproduit la signature.
2. **Un test instable cachait un défaut du harnais, pas du moteur.** Le démarrage d'un worker sandbox est borné par `min(startup_timeout = 5 s, resources.timeout_seconds)` ; mon plugin de test déclarait 3 s, et sur une machine chargée le plugin n'était pas activé (`Pas de réponse dans 3s`). Le moteur avait bien échoué fermé (503). Corrigé à la racine (10 s, défaut d'xcore) et le harnais vérifie le démarrage avec un message explicite.

## 5. Constats sur xcore (pour la v4 et le §14)

- **Démarrage d'un plugin sandboxé fragile sous charge.** Le worker doit répondre à un ping initial en `min(startup_timeout, resources.timeout_seconds)`. `startup_timeout` vaut 5 s et n'est pas configurable : l'activator crée `SandboxProcessManager(...)` sans `SandboxConfig`. Avec quatre pytest en parallèle (huit `import xcore` simultanés) l'activation a échoué une fois sur huit ; un plugin de hook qui déclare un petit `timeout_seconds` échoue plus souvent encore. Les DocTypes concernés refusent alors les écritures (503) jusqu'à ce que `POST /ipc/<plugin>/load` réussisse. Cette sensibilité est un point d'attention pour le déploiement (ne pas démarrer plusieurs workers sandbox à froid sur une machine saturée) et pour les auteurs de plugins de hook (laisser `timeout_seconds` à 10 s).
- **Le worker sandbox est séquentiel** (d'après `worker.py` : lecture d'une ligne, `handle`, réponse). Un hook lent retarde les suivants ; notre budget (`hook_timeout_seconds`, 5 s par défaut) annule l'attente côté xcore-doctype mais pas le traitement dans le worker.
- **Un service exporté par un plugin est repris au reload** : le dépendant doit être rechargé pour obtenir le nouveau registre (xcore n'a pas de notification dédiée aux dépendants).
- **Le logger d'xcore expurge par clé, pas par valeur** (`password`, `token`, `secret`, `auth`, `cookie`, `session`…). Une exception journalisée avec `error=repr(e)` peut contenir un secret dans sa valeur : les journaux serveur ne sont pas à exposer.

## 6. Non fait et non vérifié

- Pas de hooks ni d'événement sur la **suppression**, pas d'historique (`Version`), pas de contrôle des Link ni de recherche Link filtrée (phase 3), pas de permissions par champ.
- Pas de périmètre par ligne autre que `if_owner` tant que `match` est gelé (§7). Pas de droit par champ (`credit_override` d'`xsales` en est un exemple) : un hook `action:` reçoit `roles` mais pas `permissions`, il ne peut donc pas encore l'appliquer.
- Les hooks `action:` s'exécutent en série. Pas de parallélisme, pas de relance sur échec.
- Les hooks de confiance sont soumis au même budget de temps (`hook_timeout_seconds`) mais pas à une limite de taille ni de mémoire.
- Un hook de confiance peut lire n'importe quel document du tenant (`get()`), par construction.
- La matrice et les hooks sont testés sur SQLite et PostgreSQL 18 (un seul serveur, `fr_FR.UTF-8`).
- Le protocole de réenregistrement des hooks après reload est manuel.

## 7. Alignement sur auth (orchestra-backend)

Lu : `app/auth/src/{backend.py, models/rbac.py, services/rbac/{grants,permissions}.py, ipc.py}` et `app/xsales/{plugin.yaml, src/main.py, src/routes/customers.py}`. Orchestra-backend n'a pas été lancé.

**Le modèle d'auth.**

- Un plugin déclare ses **grants** dans `rbac.grants` (nom, description, `tenant_grantable`, `group`) et émet `rbac.declare` à chaque `on_load`. Auth les enregistre si le **premier segment est le nom du plugin** (`^[a-z0-9_]+:[a-z0-9_]+(:[a-z0-9_]+)*$`, namespaces réservés `admin rbac auth tenants user license plugins`) et **désactive** ceux que le plugin ne redéclare plus.
- Les rôles sont des données d'auth (globaux ou propres à un tenant). Droits effectifs = union des grants de tous les rôles du membre et de ses permissions directes, calculée par tenant.
- `decode_token` renvoie `AuthPayload(sub, roles=[noms], permissions=[grants du tenant], user={email, tenant_id})`. `has_permission` est une simple appartenance à `permissions`.
- Les plugins appliquent `require_permission("xsales:customer:read")` ; `credit_override` est testé à la main pour un champ.
- Auth a des **scopes** (`scope_type`/`scope_id`, par exemple un entrepôt) mais n'expose qu'un test ponctuel : `xauth.has_permission_scoped`. Une permission globale reste valable dans tout scope.

**Ce que cela change ici.**

| Sujet | Avant | Maintenant |
| --- | --- | --- |
| Clé d'une règle | `role: Sales Manager` | un grant, `sales:customer:read` |
| Syntaxe | liste de lignes `{grant, read, write, create, delete, if_owner}` | convention, ou un objet `{read, write, create, delete}` de listes de grants |
| Source de vérité | `principal["roles"]` | `principal["permissions"]` |
| Tenant | `user.tenant_id` (déjà lu) | inchangé : fonctionne tel quel avec xauth |
| `cross_tenant_grant` | cherché dans rôles **et** permissions | dans `permissions` seulement ; réglable (`admin:*` pour l'admin plateforme d'xauth), défaut inchangé |
| `match` | égalité sur un attribut du principal | gelé |

**Déclaration des grants : le YAML du DocType est la source unique.** Les mêmes noms n'ont plus à être tapés dans `rbac.grants` et dans le DocType. Le service `doctype_grants` (`GrantCatalog`) en donne la liste, au format de `rbac.declare` (`name`, `description`, `group`, `tenant_grantable`), et le plugin propriétaire la transmet :

```python
# on_load d'un plugin qui déclare `requires: [xcore-doctype]` et dont le nom est le `module` de ses DocTypes
grants = self.get_service("doctype_grants").for_plugin(self.ctx.name)
await self.ctx.events.emit("rbac.declare", {"plugin": self.ctx.name, "grants": [*grants_a_la_main, *grants]})
```

- **Un seul `rbac.declare` par plugin** : la réconciliation d'auth désactive tout grant du plugin absent de la liste reçue. Les grants écrits à la main (`settings:write`…) et ceux des DocTypes doivent partir ensemble.
- **xcore-doctype n'émet jamais l'événement lui-même.** `xcore-doctype` contient un tiret (pas un namespace valide), et deux émetteurs sous un même namespace se désactiveraient mutuellement.
- `for_plugin` ne renvoie que les grants dont le premier segment est le nom du plugin. Un DocType qui cite `stock:item:read` le vérifie, mais c'est `xstock` qui le déclare.
- Un grant cité par plusieurs DocTypes est listé une fois : premier libellé (ordre alphabétique) et, pour `tenant_grantable`, **le plus restrictif l'emporte**. Deux entrées d'un même DocType qui se contredisent sur `description` ou `tenant_grantable` sont refusées au chargement.
- Après un reload d'xcore-doctype, le propriétaire doit se recharger pour obtenir le nouveau catalogue (comme pour les hooks).
- `on_load` journalise les grants cités (`xcore-doctype loaded`, champ `grants`), utile pour contrôler ce qu'un déploiement doit déclarer.

**Proposition pour le périmètre par ligne (remplaçant de `match`).** Le bon mécanisme chez vous est le scope. Pour en tirer un filtre SQL il faut connaître les `scope_id` de l'utilisateur ; `has_permission_scoped` ne répond que pour un `scope_id` donné, ce qui obligerait à filtrer après coup (refusé, D6). Une action d'auth suffirait :

```
xauth.scopes_for_permission
  payload : {user_id, tenant_id, permission, scope_type}
  réponse : {status: "ok", global: bool, scope_ids: [str], truncated: bool}
```

- `global: true` : l'utilisateur détient la permission sans scope (valable partout) -> aucune restriction.
- sinon `scope_ids` : les identifiants (opaques) pour lesquels il la détient via un rôle ou une permission directe de ce `scope_type`.
- `truncated: true` au-delà d'une borne (par exemple 1000) : xcore-doctype refuse alors la requête plutôt que de lire un périmètre partiel.
- `allowed_callers` d'auth doit inclure `xcore-doctype` ; le tenant et le principal sont passés explicitement, comme pour les hooks.

Côté DocType, la règle deviendrait `{ grant: "stock:item:read", read: true, scope: { type: entrepot, field: warehouse_id } }`, et le filtre `doc[field] IN (scope_ids)`. Cela lève aussi la limite « une seule valeur » de `match` (plusieurs pays, plusieurs entrepôts) et réutilise `Clause`, `Scope.allows` et la matrice de tests. Coût : un appel IPC par requête (un cache de quelques secondes suffit, auth cache déjà ses permissions 300 s).

**Non traité.** Le droit par champ (`xsales:customer:credit_override`) n'existe pas dans le moteur. La valeur de `cross_tenant_grant` à retenir pour orchestra (`admin:*` ou un grant dédié) est à décider. Les DocTypes d'orchestra-backend (xsales, xstock…) sont écrits à la main, sans xcore-doctype : l'adoption de la convention par un plugin existant n'a pas été essayée.
