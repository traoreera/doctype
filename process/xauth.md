# xauth : l'authentification de ce dépôt

xauth (« Orchauth ») est le plugin d'authentification d'orchestra-backend : utilisateurs, organisations (tenants), rôles et grants (RBAC), connexion par e-mail et
mot de passe, jetons JWT RS256 avec renouvellement tournant, MFA TOTP, invitations, OAuth (Google, GitHub, Discord, Microsoft), journal d'audit. Il est
**copié dans `app/auth`** et devient l'authentification du produit : plus de plugin d'authentification de développement (`app/dev-auth` a disparu ;
il reste, pour les seuls tests du moteur, `tests/support/dev-auth/`).

## Démarrer

```bash
make install     # installe aussi les dépendances d'xauth (groupe `host` de pyproject : python-jose, passlib[argon2], pyotp, qrcode)
make dev         # `make env` d'abord : .env (administrateur) et clés JWT conf/*.pem, créés s'ils n'existent pas
```

Connexion dans le Desk : `ADMIN_EMAIL` / `ADMIN_PASSWORD` (`.env` écrit par `make env` : `admin@example.com`, mot de passe aléatoire). Le bloc `env:` de
`app/auth/plugin.yaml` ne porte **aucun secret** : seulement l'indirection `${ADMIN_EMAIL}` / `${ADMIN_PASSWORD}`, qu'xcore résout depuis l'environnement (le `.env` chargé
par `app.dotenv`) ; `make env` et `make dev`/`serve` exportent bien ces noms. **Rien n'est commité en clair et il n'existe aucune valeur par défaut.** xauth crée ce compte, l'organisation
`default` et les rôles `admin`, `user`, `tenant_admin` au premier démarrage (`seed:` de `app/auth/plugin.yaml`). **Il refuse de se charger sans
`ADMIN_EMAIL` ni `ADMIN_PASSWORD`** : aucun mot de passe par défaut n'est commité. L'adresse `…@example.test` est refusée par la validation d'e-mail.

| Besoin | Commande |
|---|---|
| un jeton d'accès (scripts, curl) | `make token` (`FIELD=tenant_id` : l'identifiant de l'organisation, à envoyer en `X-Tenant-ID`) |
| un utilisateur de plus | `make member EMAIL=bob@example.com PASSWORD='Bob-Passw0rd!1'` (inscription, invitation, acceptation, grants ; `GRANTS='a:b:c …'` pour limiter) |
| repartir de zéro | supprimer `xcore.db` (les tables d'xauth y vivent à côté de celles du moteur) |

L'identité d'un utilisateur est un **UUID** (`sub` du jeton, claim `tenant_id` pour son organisation) : c'est le « propriétaire » d'un document, l'auteur d'un
commentaire, l'assigné d'une tâche. Le Desk affiche l'e-mail à la place (`GET /users`, annuaire : `src/xcore_doctype/directory.py`).

## Se connecter : nom d'utilisateur ou e-mail

Le Desk demande un **identifiant** : le nom d'utilisateur OU l'adresse e-mail, avec le même mot de passe. Ce qui contient `@` est une adresse, le reste un nom (un nom ne peut jamais
contenir `@` : la connexion n'a donc aucune ambiguïté). Le Desk envoie `{"username": …}` ou `{"email": …}` selon ce qu'on a tapé ; le serveur accepte aussi un nom dans `email`
(anciens clients) et ne tient compte ni de la casse ni des espaces autour.

- **Règle d'un nom** (`app/auth/src/services/auth/username.py`) : 3 à 32 caractères, minuscules, chiffres, `.` `_` `-`, commençant par une lettre ou un chiffre ; rangé en minuscules ; unique.
- **Où on le choisit** : à l'inscription (champ facultatif, `POST /register {username}`), dans Mon compte (`PATCH /account/username`, avec le mot de passe, et le code OTP si la MFA est active).
  Un compte sans nom (anciens comptes, comptes créés par un fournisseur externe) se connecte par son adresse.
- **L'administrateur** reçoit au démarrage `ADMIN_USERNAME` s'il est défini, sinon la partie locale de son adresse (`admin@example.com` → `admin`, `traoreera@gmail.com` → `traoreera`).
  Un nom invalide ou déjà pris ne bloque jamais le démarrage. `ADMIN_USERNAME` est facultatif : ne l'ajoutez au bloc `env:` de `app/auth/plugin.yaml` qu'avec sa variable (xcore refuse un `${VAR}` absent).
- **Base existante** : `app/auth/src/utils/db_upgrade.py` ajoute au démarrage la colonne `xauth_users.username` et son index unique (additif seulement, idempotent) ; les NULL ne se heurtent pas.
- **Ce qui n'accepte QUE l'adresse** : « mot de passe oublié » et les invitations (elles se font par e-mail).
- **Limitation de débit** : 5 connexions et 3 inscriptions par minute et par adresse IP par défaut ; un script qui enchaîne les appels reçoit 429. Le bloc `rate_limit:` du manifeste la règle
  (`enabled: false` la coupe en développement et dans les tests ; `routes: {login: [10, 60]}` change les bornes d'une route : `utils/rate_limit.py`, `RateLimitPolicy`).
- **Gestion des accès par le Desk** : les actions `xauth.iam.*` (`services/iam.py`) servent les DocTypes du plugin `iam` (Permission, Role, Member) : voir `process/iam.md`.

## Comment les grants arrivent

Chaque plugin métier (`sales`, `stock`, `hr`, `system`) émet UN `rbac.declare` avec les grants que ses DocTypes exigent. xauth les enregistre
(`PluginGrantService.reconcile_plugin_grants`) et les accorde au rôle `admin` ; un autre rôle ou un membre les reçoit de l'administrateur de
l'organisation (`POST /app/auth/rbac/tenants/{id}/members/{user}/permissions`). Un jeton porte les grants du membre dans CETTE organisation. L'événement n'est écouté
que si xauth est déjà chargé : les plugins du dépôt déclarent `requires: [..., auth]`.

## Ce qui a changé par rapport à orchestra-backend

- **Courrier : l'extension `ext.email`** (`extensions/xmailler`, copiée de donna) est branchée dans `integration.yaml` (`services.extensions.email`) et xauth la
  trouve par `get_service("ext.email")` ; absente, `LogMailer` journalise. Sans serveur SMTP (`smtp_host` vide) l'extension est en **mode simulé** : elle reste
  PRÊTE, journalise le message (lien d'invitation ou de réinitialisation compris) et compte l'envoi, sans jamais ouvrir de connexion. Réglage : `XAUTH_SMTP_HOST`,
  `_PORT`, `_USER`, `_PASSWORD`, `_FROM`, `_FROM_NAME`, `_USE_TLS` dans `.env` (le YAML vide retombe dessus, d'où aucune `${VAR}` obligatoire ; `app.dotenv: ./.env`).
  `routes/mail.py` expose `GET /app/auth/mail/status` et `POST /app/auth/mail/test` à l'administrateur de la plateforme (`admin:*`) ; le mot de passe SMTP
  n'est jamais renvoyé.
- **Plus de migration au démarrage** : l'ancien code écrivait une nouvelle révision à chaque démarrage. Une base neuve reçoit le schéma courant (`create_all`) ;
  `migrations/` reste pour l'historique, faire évoluer une base existante est une opération explicite.
- **Secrets** : le dépôt ne livre aucun mot de passe par défaut (`make env` en tire un au hasard dans `.env`) ; le bloc `env:` ne liste que ce qui existe toujours (xcore rejette le plugin
  si une `${VAR}` listée n'est pas définie ; les variables optionnelles OAuth, durées JWT, noms de rôles sont listées en commentaire, à activer avec leur ligne).
- **`allowed_callers: ["xcore-doctype"]`** : l'hôte active `enforce_ipc` ; le moteur lit les membres d'une organisation par `xauth.users.list.tenant`. Sans cette
  ligne xcore répond `ipc_denied` et le moteur retombe sur les identités observées (sans e-mail).
- `ext.google` (Gmail, Calendar, Drive pour le compte de l'utilisateur) est facultatif : absent, `xauth.google.*` n'est pas disponible, la connexion OAuth marche.
- Dépendances ajoutées au groupe `host` (le wheel xcore-doctype n'en dépend pas) ; `app/auth/pyproject.toml`, `makefile` et `example.env` ne sont pas repris.
- **Notifications du moteur : `xauth.notify`** (IPC, même `allowed_callers`) : `{tenant_id, user_ids ≤ 50, title, message?, link?, type?}` crée une notification de la cloche pour chaque identifiant qui est MEMBRE de `tenant_id` (les autres sont ignorés en silence), avec un lien `#/…` ou `https://` seulement. Le moteur l'appelle à l'assignation d'un document et à une mention `@adresse` dans un commentaire (`process/phase-h2.md`) ; un échec ne fait jamais échouer l'opération qui l'a provoquée. La route `POST /notifications` (REST) accepte toujours n'importe quel `user_id` : à fermer, non traité.

## Desk : la connexion

Le Desk n'a qu'une seule adresse de connexion : `login_url`. Le manifeste livré porte la **sentinelle `auth`** : le Desk sert `<préfixe>/auth/login` en la déduisant du préfixe réel
de la requête (`api_base` moins `/xcore-doctype`, `src/xcore_doctype/api/desk.py`), donc il suit `app.plugin_prefix` quel qu'il soit. Un chemin (`/app/auth/login`) ou une URL absolue
force une autre adresse ; `null` demande un jeton collé (développement, et le groupe Paramètres > Système disparaît). `xcore-doctype doctor` **avertit** si `login_url` est défini mais
qu'aucun plugin d'authentification (`auth`) n'est installé sous `plugins.directory` : la connexion répondrait 404.

`ui/src/stores/session.ts` couvre tout le flux d'xauth, dans cet ordre : `POST /login` **avec `tenant_id`** quand `ADMIN_…` gère plusieurs organisations (la réponse liste alors les
organisations, le Desk redemande) ; sinon MFA (`mfa_required` → `POST /verify-mfa` avec le code, le dialogue reste à l'écran) ; sinon compte sans organisation → page **`/onboarding`**
(créer une organisation `POST /setup/create`, ou en rejoindre une `POST /setup/join`). Le jeton de rafraîchissement est utilisé (renouvellement tournant) ; l'URL de renouvellement et de
déconnexion se déduit de `login_url` (`…/login` → `…/refresh`), sans second réglage. L'e-mail affiché vient du claim `email` du jeton (`ui/src/lib/token.ts:displayName`, repli sur
`user.email` puis `sub`) ; côté serveur, `refresh`, `select-tenant` et `verify-mfa` le posent eux aussi dans le jeton (`app/auth/src/services/auth/`).

**Les liens des e-mails vont au Desk, jamais à un deep-link.** Les senders reçoivent `desk_url` (`app.desk_url`, ou `XAUTH_DESK_URL`, sinon `<base_url>/app/xcore-doctype/desk/`) et
l'inscription répond avec ce même `login_url` : le lien de réinitialisation est `<desk_url>#/reset?token=…`, celui d'invitation `<desk_url>#/invite?token=…`, la bienvenue
`<desk_url>#/login`. (`utils/wrap_bridge` ne sert plus ici ; `app/auth/src/main.py` lit la variable.) Le seul schéma `erp://` qu'xauth émet encore est la cible de retour **par défaut
de l'OAuth** : un opérateur qui veut le retour OAuth dans le navigateur déclare `OAUTH_WEB_REDIRECT_ORIGINS` (origine https du Desk, dans `.env`) ; `oauthAuthorizeUrl`
passe alors `redirect=<origine du Desk>/desk/#/oauth` et `app_state` (nonce gardé en `sessionStorage`, vérifié au retour).

**Les écrans** (`ui/src/views/`, routes publiques toutes marquées `meta: { public: true }`) :

| Route | Écran | Fait |
|---|---|---|
| `#/login` | `LoginView` | e-mail/mot de passe, MFA, choix d'organisation, `next`, éventuel « Compte créé », boutons fournisseurs OAuth (depuis `GET …/oauth/providers`, absents si aucun) |
| `#/register` | `RegisterView` | `POST …/register` (crée un compte SANS organisation), puis retour à la connexion avec `next` |
| `#/forgot` | `ForgotPasswordView` | `POST …/password/forgot`, réponse toujours identique (pas d'énumération d'adresses) |
| `#/reset` | `ResetPasswordView` | `POST …/password/reset` avec `?token=` du lien ; sans jeton, message d'erreur |
| `#/invite` | `InviteView` | `GET …/invites/token/{token}` SANS session (qui appelle, quelle organisation), acceptation `POST …/invites/accept` avec le jeton ; sans session, renvoie à la connexion avec `next` |
| `#/oauth` | `OAuthCallbackView` | retour du fournisseur : vérifie le nonce, adopte les jetons, puis avance (MFA → `#/login`, organisation à créer → `#/onboarding`) |

Le **changement d'organisation** est dans le menu utilisateur (`AppShell`) : `GET …/tenants/` liste les organisations du compte (aussi appelé au montage) et chaque autre
organisation appelle `POST …/select-tenant` avec le jeton de renouvellement courant, puis recharge la page (les caches du Desk sont par organisation). Aucun jeton de mot de
passe n'est relu à ce moment-là.

## Desk : Paramètres > Système

Connecté à xauth (le Desk déduit sa racine de `login_url`), le panneau Paramètres gagne le groupe **Système** (`/settings/~<rubrique>`, `ui/src/components/admin/`). Tout ce qu'un écran peut faire d'xauth y est couvert. Restent hors du Desk, volontairement : la création d'une permission et d'une notification (affaire des plugins), des lectures
redondantes (`/admin/users/{id}`, `/rbac/roles/{id}`, `/rbac/me/roles`, `/tenants/{id}/members`), la liaison d'un fournisseur (`/oauth/{p}/link` et `callback` : un flux de navigateur
que l'écran de connexion mène déjà) et `/bridge/redirect` (retour des liens `erp://`).

| Rubrique | Ce qu'elle fait | Réservé à |
|---|---|---|
| Mon compte | nom d'utilisateur (choisir, changer), mot de passe (ou en DÉFINIR un, pour un compte créé par un fournisseur externe), adresse (avec mot de passe), double authentification TOTP, sessions ouvertes, comptes externes liés (délier), invitations reçues | tout membre |
| Organisation | mes organisations (passer de l'une à l'autre), renommer, réglages libres (objet JSON), créer une organisation (3 au plus), supprimer | propriétaire (supprimer : plateforme) |
| Utilisateurs | membres, rôle principal, rôles supplémentaires, permissions directes, invitations, retrait | administrateur de l'organisation |
| Rôles et permissions | rôles de la plateforme (lecture seule), rôles propres à l'organisation (création, permissions, suppression) | administrateur de l'organisation |
| Catalogue des permissions | tout ce que les plugins ont déclaré (`rbac.declare`), rôles globaux (création, permissions PAR IDENTIFIANT) | administrateur de la plateforme |
| Comptes de la plateforme | tous les comptes : désactiver (sessions fermées), réactiver, supprimer, activité | administrateur de la plateforme |
| Courrier | serveur SMTP, envoi simulé ou réel, serveur joignable ou non, compteurs, message d'essai | administrateur de la plateforme |
| Journal d'audit | événements de l'organisation, par pages de 50 | administrateur de l'organisation |
| Accès API | adresses du moteur et d'xauth, organisation à envoyer en `X-Tenant-ID`, jeton (masqué, copiable), documentation OpenAPI | tout membre |

La **cloche** de la barre du haut (`NotificationsMenu`, `stores/notifications.ts`) affiche les notifications d'xauth (relues chaque minute, onglet visible) : lire, tout lire, supprimer.
Deux règles : le serveur échappe le HTML de `title`/`message`, le Desk le décode pour l'AFFICHER (jamais de `v-html`) ; le lien d'une notification ne mène que dans le Desk
(`#/…`) ou en https, tout le reste est ignoré (n'importe quel membre peut en écrire une). xauth ne crée aucune notification de lui-même : l'API `POST /notifications` est là pour les plugins.

**Qui est administrateur de la plateforme ?** Le jeton d'xauth ne porte PAS les permissions : le Desk les lit (`GET /rbac/me/permissions`, `session.loadPermissions`, appelé par la coquille) et
cache les rubriques réservées (Catalogue, Comptes, Courrier) à qui n'a pas `admin:*`. Un confort : xauth refuse de toute façon (403, que la page dit « réservé », jamais comme une panne).
Tant que les permissions ne sont pas lues, l'adresse d'une rubrique réservée n'est pas « inconnue » (pas de clignotement au rechargement).

Avec un jeton collé (sans `login_url`), le groupe et la cloche n'apparaissent pas. `ui/e2e/look-admin.mjs` parcourt les rubriques et la cloche dans un vrai navigateur (il choisit
l'organisation « Default » quand le compte en a plusieurs, et accepte le refus d'xauth au-delà de trois organisations).

## Tests

- Nom d'utilisateur : `tests/integration/test_xauth_real.py` (connexion par nom, casse, refus identique, inscription, changement), `tests/unit/test_username.py` (règle, évolution d'une base ancienne),
  `ui/tests/login-username.spec.ts` (écrans de connexion et d'inscription), `ui/e2e/look-login.mjs` avec `LOGIN=<nom>` (vrai navigateur).
- `tests/integration/test_xauth_real.py` démarre le VRAI xauth (`boot_host(xauth=True)`, clés JWT jetables, administrateur par l'environnement) : connexion,
  grants déclarés par un plugin, membre sans accès puis avec, annuaire par e-mail, assignation vérifiée, renouvellement tournant, déconnexion, `login_url` du Desk,
  courrier simulé, état et essai d'envoi réservés à l'administrateur de la plateforme. `tests/unit/test_mail_config.py` : repli de l'extension sur `XAUTH_SMTP_*`.
- Desk : `ui/tests/admin.spec.ts` (les six rubriques), `ui/tests/xauth-client.spec.ts` (chaque adresse et méthode ; elles ont été comparées au OpenAPI d'xauth),
  `ui/tests/session.spec.ts` (connexion simple, sélection d'organisation, MFA, onboarding, renouvellement, erreurs, puis inscription, réinitialisation, invitations,
  nonce OAuth, changement d'organisation) et `ui/tests/token.spec.ts` (l'identité affichée).
- Les autres tests du moteur fabriquent leurs principaux avec `tests/support/dev-auth` (jetons HMAC, plugin nommé `auth`) : rapides, sans clés ni base d'authentification.

## À savoir

- La limitation de débit d'xauth (connexion 10/min, inscription 5/min, par adresse) est dans son code et s'applique aussi en développement.
- Le cookie `access_token` posé à la connexion est `secure` : un navigateur ne le renvoie qu'en HTTPS ; le Desk, lui, envoie le jeton en en-tête.
- Le middleware de géolocalisation et les en-têtes de sécurité d'orchestra (`app.auth.src.middleware`) ne sont pas branchés dans `integration.yaml` ; le journal signale
  des avertissements de scan (« import non whitelisté », « accès interdit via attribut ») qui ne bloquent pas : xauth est un plugin de confiance.
- Production : clés JWT, administrateur, SMTP et OAuth viennent de votre gestion de secrets ; PostgreSQL via `services.databases` ; `strict_trusted: true` demande de signer
  les plugins (`plugin.sig` n'est pas repris).
