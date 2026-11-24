# Le Desk en application de bureau (Tauri)

Le Desk est la même application qu'au navigateur : même code (`ui/src`), mêmes écrans, même serveur. Ce qui change est ce qui
l'héberge. Dans un navigateur, la page parle au serveur par `fetch` et garde sa session dans l'onglet. Dans l'application de
bureau, la page vit dans une WebView (Tauri 2) et demande trois choses à un petit processus Rust : le réseau, la session, et les
gestes qu'une WebView ne doit pas faire elle-même (ouvrir un lien, enregistrer un fichier).

Ce guide dit comment la construire, ce qui a été décidé, et — séparément — **ce qui a réellement été vérifié, et où**.

## 1. Vue d'ensemble

```
ui/src/main.ts ──────────► createDesk(FetchTransport, config.json, browserHost())      le Desk web, inchangé
ui/src/desktop/main.ts ──► createDesk(NativeTransport, config du serveur, desktopHost())   l'application de bureau

            page (WebView, tauri://localhost)                 processus Rust (ui/src-tauri)
  NativeTransport ── @tauri-apps/plugin-http ───────────────► requêtes HTTP, bornées à UN serveur
  Vault / desktopHost ── commandes de l'application ────────► coffre de session, ouvrir, enregistrer, fenêtre
  liens profonds ◄── @tauri-apps/plugin-deep-link ──────────  erp://oauth-callback?…
```

Deux contrats portent tout le portage, et le reste du Desk n'en sait rien :

- **`Transport`** (`ui/src/lib/transport.ts`) : la seule chose qui touche le réseau. `FetchTransport` au navigateur,
  `NativeTransport` (`ui/src/desktop/transport.ts`) en bureau.
- **`DeskHost`** (`ui/src/lib/host.ts`) : où vivent les jetons, comment les fenêtres se parlent, comment on sort vers une page
  externe, comment on enregistre un fichier. `browserHost()` est le comportement de toujours ; `desktopHost()`
  (`ui/src/desktop/host.ts`) celui du bureau.

`@tauri-apps/*` n'est importé que par `ui/src/desktop/tauri.ts`. Le build web n'en contient rien
(`ui/tests/desktop-isolation.spec.ts` le tient).

## 2. Prérequis

| | |
|---|---|
| Node et pnpm | ceux du Desk (`make install`) |
| Rust | ≥ 1.77.2 (`rustup`), `cargo` dans le `PATH` |
| Linux | `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libssl-dev build-essential pkg-config` (Debian/Ubuntu) ; pour le test de fumée : `webkit2gtk-driver`, et sans écran `xvfb dbus-x11` |
| macOS | Xcode Command Line Tools |
| Windows | Microsoft C++ Build Tools, WebView2 (présent sur Windows 10/11 à jour) |

La liste Linux est celle qui a servi à la vérification (§ 9). Pour les autres systèmes, la référence est la page
« Prerequisites » de Tauri 2.

## 3. Régler le serveur

L'application n'a qu'**un** réglage : l'adresse du Desk que le serveur sert.

```bash
cd ui
pnpm desktop:configure https://erp.exemple.fr/app/xcore-doctype/desk/     # [--extensions] [--no-realtime]
pnpm desktop:configure                                                    # affiche le réglage et vérifie sa cohérence
```

Cette commande écrit les deux fichiers qui doivent nommer le serveur :

- `ui/src-tauri/desk.json` — ce que la page lit au démarrage :
  `{ "server": "…/desk/", "extensions": false, "realtime": true }`. Vocabulaire fermé : une clé inconnue est refusée.
- `ui/src-tauri/capabilities/server.json` — la portée réseau du processus (`http:default`) : la seule origine que le transport
  peut joindre.

**Tout le reste est lu chez le serveur**, dans le même `config.json` que le Desk web (`…/desk/config.json`) : racine de l'API,
adresse de connexion, extensions autorisées, temps réel, automatisations, mode développeur. L'hôte ne redéclare rien, il ne peut
que **retirer** (`extensions`, `realtime` à `false`). Un Desk monté ailleurs (`mount_desk`) se règle de la même façon : on donne
l'adresse du montage, le renvoi `extensions_from` est suivi comme au navigateur.

Règles appliquées à `server`, puis à l'API et à la connexion que le serveur annonce (`ui/src/desktop/config.ts`) :

- `https` obligatoire ; `http` n'est admis que vers `localhost`, `127.0.0.1` ou `[::1]` (développement) ;
- pas d'identifiants, pas de `?` ni de `#` dans l'adresse ;
- si le serveur ne répond pas, ou si l'adresse ne sert pas un Desk, **l'application s'arrête en le disant** (un écran
  « L'application ne peut pas démarrer » avec la cause et « Réessayer »). Contrairement au navigateur, elle n'a pas d'URL de page
  d'où deviner l'API.

`desk.json` est embarqué à la compilation. À l'exécution, il peut être remplacé sans reconstruire : la variable `XDESK_CONFIG`
(un chemin), sinon `desk.json` dans le dossier de configuration de l'application (Linux : `~/.config/io.github.traoreera.desk/`).
**La portée réseau, elle, est fixée à la construction** : un `desk.json` d'exécution ne peut désigner qu'un serveur de la même
origine (un autre préfixe, d'autres interrupteurs). Changer d'origine demande `pnpm desktop:configure` et une reconstruction — et,
si le serveur annonce une API ou une connexion sur une AUTRE origine, d'ajouter celle-ci à la main dans
`capabilities/server.json`.

## 4. Commandes

Depuis `ui/` (ou `make desktop-dev`, `make desktop-build`, `make desktop-test`, `make desktop-smoke` depuis la racine) :

```bash
pnpm tauri dev                      # l'application sur le Vite de bureau (port 5174), rechargée à chaud ; le serveur doit tourner
pnpm tauri build                    # le paquet du système (deb/rpm/AppImage, dmg, msi/nsis) dans src-tauri/target/release/bundle/
pnpm build:desktop                  # le Desk seul, pour la WebView (ui/dist-desktop/, ignoré par git)
cargo test --manifest-path src-tauri/Cargo.toml      # le coffre et les politiques du processus
pnpm test                           # vitest : contrats, hôte, session multi-fenêtres, liens, configuration
```

Le build web (`make build-ui`) et celui de bureau sont indépendants : même `index.html`, un autre point d'entrée
(`vite.desktop.config.ts` remplace `src/main.ts` par `src/desktop/main.ts`).

Le test de fumée pilote la **vraie** application par WebDriver, contre un serveur de contrat qu'il lance lui-même :

```bash
pnpm tauri build --debug --no-bundle --config src-tauri/tauri.smoke.conf.json
xvfb-run -a dbus-run-session -- pnpm desktop:smoke        # Linux sans écran ; avec un écran : pnpm desktop:smoke
```

Il a besoin du port 8000 (celui du `desk.json` par défaut) et est **ignoré avec sa raison** s'il manque l'application, un pilote
ou un écran (`SMOKE_STRICT=1` en fait un échec).

## 5. Décisions

### 5.1 Jetons : dans la mémoire du processus, jamais sur disque

Les jetons (accès, renouvellement) vivent dans un coffre tenu par le processus Rust (`src-tauri/src/vault.rs`,
`ui/src/desktop/vault.ts`). Ni `sessionStorage`, ni `localStorage`, ni fichier.

- **Durée de vie : celle du processus.** Fermer une fenêtre, la recharger ou en ouvrir une autre garde la session. **Quitter
  l'application la termine** : on se reconnecte au lancement suivant. C'est la règle du navigateur (« le jeton disparaît à la
  fermeture de l'onglet »), portée à l'application.
- **Pourquoi pas le trousseau du système** : garder une session d'un lancement à l'autre est un autre compromis (un poste partagé
  rouvre la session du précédent), qui mérite sa propre décision. Le point de changement est unique : le dépôt derrière les
  commandes `vault_*`. Rien d'autre ne bougerait.
- **Renouvellement** : inchangé (une minute avant l'échéance, rejeu une fois sur 401).
- **Pas de cookies** : xauth pose aussi un cookie `access_token` à la connexion. Le plugin HTTP est compilé **sans** sa fonction
  `cookies` (`Cargo.toml`) : sinon ce jeton serait écrit dans un fichier de l'application et renvoyé à des requêtes sans
  `Authorization`, y compris après une déconnexion. L'application ne s'authentifie que par l'en-tête.
- Le coffre n'accepte que les clés de session (`xdt.desk.…`), en nombre et en taille bornés.

### 5.2 Plusieurs fenêtres : une seule session

Toutes les fenêtres lisent le même coffre. Une écriture (connexion, changement d'organisation, renouvellement, déconnexion) est
annoncée aux autres, qui la prennent aussitôt. Conséquences, voulues :

- une fenêtre ouverte ensuite est déjà connectée ;
- se connecter, changer d'organisation ou se déconnecter dans une fenêtre vaut pour toutes ;
- le jeton de renouvellement (à usage unique chez xauth) n'est échangé que par **une** fenêtre à la fois : un verrou tenu par le
  processus, avec une échéance de 15 s (une fenêtre fermée en plein échange ne bloque personne) ; sous le verrou, le coffre est
  relu avant tout, si bien que la fenêtre suivante trouve le couple déjà renouvelé.

Une « autre fenêtre » s'ouvre là où le navigateur ouvrait un onglet (`target="_blank"` vers une route du Desk).
Le Desk web garde son mécanisme d'onglets (`lib/tabsync.ts`, lignées) tel quel.

### 5.3 Liens : la WebView ne navigue jamais hors de l'application

| Le lien | Ce qui se passe |
|---|---|
| une route du Desk (`#/…`) | la WebView la suit |
| une route du Desk en « nouvel onglet » | une autre fenêtre de l'application |
| un lien de fichier (`<a download>`) | lu par le transport, enregistré par le dialogue natif |
| une page web, `mailto:`, `tel:` | ouverts par le système (navigateur, messagerie) |
| tout autre schéma (`javascript:`, `file:`, `data:`…) | refusé, avec sa raison |

Deux verrous indépendants : la page fait le bon geste (`ui/src/desktop/links.ts`, et `host.leave` / `host.openExternal` pour
les écrans qui sortent d'eux-mêmes) ; le processus **refuse** toute navigation de la WebView hors de l'application et n'ouvre par
le système que http(s), `mailto:` et `tel:` (`src-tauri/src/policy.rs`).

**Paiement.** La caisse et le portail du prestataire s'ouvrent dans le navigateur du système. Leur retour est construit par le
serveur (`desk_url` d'xlicense) et mène donc au **Desk web**, où le paiement est confirmé — pas à l'application : le prestataire
n'accepte que des adresses http(s), et l'API du serveur n'est pas modifiée pour ce portage. Dans l'application, rouvrir
« Paramètres › Abonnement » relit les modules.

### 5.4 OAuth : retour par lien profond, nonce exigé

1. L'écran de connexion demande l'adresse d'autorisation à xauth avec `redirect=erp://oauth-callback` (la cible de bureau d'xauth,
   toujours admise : `vendor/auth/src/utils/deeplink.py`) et un nonce `app_state` gardé **dans la mémoire de cette fenêtre**.
2. Le fournisseur s'ouvre dans le navigateur du système.
3. xauth renvoie vers `erp://oauth-callback?…&app_state=…` ; le système remet le lien à l'application (une seule instance : une
   seconde est aussitôt refermée après avoir transmis le lien).
4. Le lien arrive à toutes les fenêtres ; seule celle qui attend un retour le traite. La session **refuse** un retour sans
   connexion en cours, sans nonce, ou au nonce différent. Un lien rejoué ne passe pas (le nonce sert une fois).

Le navigateur garde sa tolérance d'origine (un retour sans `app_state` y passe) ; le bureau n'en a aucune, parce que n'importe
quel programme de la machine peut invoquer `erp://…`.

- **Double authentification** : le retour porte un défi, pas une session ; l'écran de connexion demande le code, comme après un
  mot de passe.
- **Plusieurs organisations** : le retour propose le choix ; rien n'est adopté avant.
- **Changer d'organisation** (menu utilisateur) : échange du jeton de renouvellement courant, puis rechargement de la page — la
  session, dans le coffre, y survit, et les autres fenêtres suivent.

Liens profonds acceptés : `erp://oauth-callback` et `erp://invite?token=…` (mène à l'écran d'invitation). Tout autre est ignoré
(`erp://oauth-link`, la liaison d'un compte, n'a pas d'écran dans le Desk).

## 6. Capacités et limites

| Fonction | En bureau | État |
|---|---|---|
| Appels JSON, erreurs, en-têtes, annulation | transport natif | vérifié (§ 9) |
| PDF et réponses binaires | transport natif, puis dialogue d'enregistrement | transport vérifié ; **dialogue non vérifié** |
| Téléversement (multipart) | transport natif | vérifié |
| Temps réel (SSE d'xpulse) | transport natif ; `"realtime": false` le coupe | vérifié |
| Extensions JavaScript (D28) | **désactivées par défaut** ; chacune est écartée avec la raison, annoncée au démarrage | activation **non vérifiée** |
| Exports (CSV, YAML) | dialogue d'enregistrement | **non vérifié** (même commande que le PDF) |
| Imprimer l'aperçu (`window.print` de l'iframe) | laissé à la WebView | **non vérifié** ; le PDF est la voie sûre |
| Presse-papiers (copier une clé, un exemple) | laissé à la WebView | non vérifié |
| Images servies par le serveur | CSP `img-src … https:` (et `http://127.0.0.1`, `localhost` en développement) | non vérifié |
| Commodités de l'appareil (thème, récents, vue, colonnes) | `localStorage` de la WebView, comme au navigateur | — |

**Extensions.** Une extension est un module JavaScript que la page importe depuis le serveur. Depuis `tauri://localhost`, c'est
un import d'une autre origine : il faut `"extensions": true`, l'origine du serveur dans `script-src` (`tauri.conf.json`), et que
le serveur réponde en CORS pour l'origine de l'application (`tauri://localhost` ; `http://tauri.localhost` sous Windows). Rien de
cela n'a été essayé ; tant que ce n'est pas fait, les laisser désactivées.

**Ce que le serveur voit.** Les requêtes partent du processus, sans CORS ni cookies, avec `Origin: tauri://localhost` (constaté par le test de fumée). Un serveur
qui filtrerait sur l'en-tête `Origin` devrait l'admettre.

## 7. Sécurité de la WebView

- **CSP** (`tauri.conf.json`) : `default-src 'self'`, `script-src 'self'`, `connect-src ipc: http://ipc.localhost` — la page ne
  peut joindre **aucun** serveur par elle-même ; tout le réseau passe par le processus.
- **Portée réseau** : une origine (`capabilities/server.json`), doublée dans la page par `NativeTransport`, qui refuse avant
  d'envoyer toute adresse hors du serveur configuré.
- **Permissions** (`capabilities/default.json`) : écouter les annonces du processus et lire le lien de lancement. Les seuls autres
  gestes natifs sont les commandes de l'application — `desk_config`, `vault_*`, `open_external`, `save_file`, `open_window`.
  Aucun accès au système de fichiers, au shell ni à la création de fenêtres n'est accordé à la page ; `save_file` n'écrit que là où
  l'utilisateur le choisit dans le dialogue, et la page ne voit aucun chemin.

## 8. Inventaire des accès directs au navigateur

Tenu par `ui/tests/desktop-isolation.spec.ts` : en ajouter un ailleurs fait échouer le test.

| Accès | Où il était | Ce qu'il est devenu |
|---|---|---|
| `fetch(` | `lib/transport.ts` (`FetchTransport`) | inchangé : c'est l'implémentation web du contrat |
| `fetch(` | `extensions/loader.ts` (diagnostic d'un module introuvable) | passe par `Transport` (`transportProbe`) |
| `import(url)` | `extensions/loader.ts` | reste (un module ne se charge pas autrement) ; l'hôte peut le refuser (`unavailable.extensions`) |
| `EventSource` | nulle part | le flux passe déjà par `Transport.stream` |
| `sessionStorage` | `stores/session.ts` (jetons, nonce OAuth) | `host.session` / `host.flow` ; web : `sessionStorage`, inchangé |
| `localStorage` + `BroadcastChannel` + `navigator.locks` | `lib/tabsync.ts` | inchangé au web (`host.tabs()`) ; le bureau a son coffre et son verrou |
| `location.assign` | `LoginView` (OAuth), `FormView`, `ModulesView` ×2, `BillingReturnView` (paiement) | `host.leave` |
| `window.open` | `NotificationsMenu` (lien externe d'une notification) | `host.openExternal` |
| `<a download>` / `Blob` | `lib/download.ts`, appelé par `PrintDialog`, `PrintBuilderView`, `ReportView`, `ImportDialog`, `DoctypeBuilderView`, `FilesView` | `host.saveBytes` / `saveText` / `saveUrl` ; `lib/download.ts` reste l'implémentation web |
| `<a href download>` | `AttachmentsCard`, `FilesView` (lien signé encore valable) | inchangé au web ; en bureau, la garde des liens le télécharge par le transport |
| `<a target="_blank">` | `ApiSettings` (documentation), `LinkField` (créer dans un nouvel onglet) | inchangé au web ; en bureau : le système, ou une fenêtre |
| `location.reload` | `AppShell`, `OrganisationSettings`, `ModulesView` | laissé : la page rechargée est celle de l'application, la session est dans le coffre |
| `window.print` | `PrintDialog` (iframe d'aperçu) | laissé à la WebView (limite, § 6) |
| `navigator.clipboard` | `ApiSettings`, `ApiDocsView` | laissé à la WebView |
| `localStorage` (thème, langue, récents, navigation, vue, colonnes, positions d'un graphe) | `lib/theme`, `i18n`, `lib/recents`, `lib/navstate`, `lib/viewprefs`, `ListView`, `WorkflowBuilderView` | laissé : commodités de l'appareil, rien de secret |

`loadConfig()` (`window.__DESK_CONFIG__`) propageait déjà `realtime`, `extensions`, `automations` et `developer` depuis le
23 novembre (commit `660e974`, tests dans `ui/tests/config.spec.ts`) ; l'application de bureau ne s'en sert pas — elle passe sa
configuration directement à `createDesk`.

## 9. Ce qui a été vérifié, et où

**Une seule plateforme : Linux x86_64** (Ubuntu 24.04, WebKitGTK 2.52, Tauri 2.12, sous Xvfb), le 24 novembre 2026.

- `pnpm tauri build --debug --no-bundle` et `pnpm tauri build --bundles deb` construisent l'application (un `.deb` de 4,5 Mo, non installé) ;
- `pnpm tauri dev` démarre l'application sur le Vite de bureau : elle lit `config.json` chez le serveur et affiche la connexion ;
- `cargo test` : 8 tests (coffre, verrou, politiques) ;
- `pnpm desktop:smoke` : **24 points** sur l'application réelle — démarrage avec la configuration lue chez le serveur ; transport
  natif (le serveur de test n'envoie aucun en-tête CORS, et le `fetch` de la WebView y est refusé) ; JSON et erreurs ; PDF octet
  pour octet ; multipart ; SSE reçu au fil de l'eau ; annulation ; refus hors serveur ; connexion par le formulaire avec les jetons
  dans le coffre et rien dans la WebView ; cookie de session du serveur ni gardé ni renvoyé ; renouvellement ; flux d'xpulse demandé avec le jeton ; lien externe remis au système
  sans navigation ; `open_external` refusant `file:` ; navigation forcée refusée par le processus ; aucune permission générique ;
  seconde fenêtre héritant de la session ; renouvellement simultané de deux fenêtres en un seul échange ; déconnexion propagée ;
  OAuth par lien profond (bon nonce accepté, nonce forgé ou rejoué refusé, lien inconnu ignoré).

**Non vérifié — à faire sur un poste :**

| Quoi | Comment |
|---|---|
| **macOS et Windows** (construction, démarrage, WebView) | `pnpm tauri build` ; sous Windows, `pnpm desktop:smoke` avec `tauri-driver` et `msedgedriver` ; macOS n'a pas de pilote WebDriver : dérouler les points du § 9 à la main |
| **Un vrai serveur xcore/xauth** (le test utilise un serveur de contrat) | `make serve`, puis `pnpm tauri dev` ; se connecter, ouvrir une liste, un document, imprimer en PDF, joindre un fichier, laisser tourner 30 min (renouvellement) |
| L'en-tête `Origin: tauri://localhost` face au vrai serveur | même parcours : aucune requête ne doit être refusée pour son origine |
| Le dialogue d'enregistrement (PDF, CSV, YAML, pièce jointe) | télécharger un PDF : le dialogue du système propose le nom, le fichier écrit s'ouvre |
| L'inscription du schéma `erp://` auprès du système (dans le conteneur de vérification, sans outils `xdg`, elle échoue — l'application le journalise et démarre ; les liens profonds du test sont remis en relançant l'application avec le lien, ce que fait le système) | installer le paquet, puis `xdg-open 'erp://invite?token=x'` (Linux), `open` (macOS), `start` (Windows) : l'application s'ouvre sur l'invitation |
| OAuth avec un vrai fournisseur | bouton du fournisseur → navigateur → retour dans l'application, connecté |
| Extensions activées, impression de l'aperçu, presse-papiers, images du serveur | § 6 |
| Signature et notarisation des paquets, mise à jour automatique | hors périmètre de ce portage |
