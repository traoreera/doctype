//! Le processus de l'application de bureau du Desk (traoreera/doctype#73, guide : `docs/desktop.md`).
//!
//! Il fait PEU, exprès. Le Desk reste l'application web (`ui/src`) ; ce processus lui fournit ce qu'une WebView ne doit
//! pas faire elle-même, en commandes étroites plutôt qu'en permissions larges :
//!
//! | Commande | Ce qu'elle donne à la page |
//! |---|---|
//! | `desk_config` | le texte de `desk.json` (l'adresse du serveur) |
//! | `vault_all` / `vault_set` / `vault_lock` / `vault_unlock` | la session, en mémoire du processus, partagée entre fenêtres |
//! | `open_external` | ouvrir http(s)/mailto/tel par le système — jamais dans la WebView |
//! | `save_file` | enregistrer des octets là où l'UTILISATEUR choisit (dialogue natif) ; la page ne voit aucun chemin |
//! | `open_window` | une autre fenêtre sur une route du Desk |
//!
//! Le réseau passe par `tauri-plugin-http`, borné par `capabilities/` ; les liens profonds `erp://` par
//! `tauri-plugin-deep-link`. Aucun accès au système de fichiers, au shell ou aux fenêtres n'est accordé à la page.

mod policy;
mod vault;

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, Runtime, State, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use vault::Vault;

/// `desk.json` embarqué à la compilation : la valeur par défaut de CETTE construction.
const BUNDLED_CONFIG: &str = include_str!("../desk.json");

/// Où lire `desk.json`, par ordre : la variable `XDESK_CONFIG` (un chemin), le dossier de configuration de l'utilisateur,
/// puis celui que l'application embarque. Un fichier DÉSIGNÉ mais illisible est une erreur dite, pas un repli silencieux
/// sur un autre serveur.
#[tauri::command]
fn desk_config(app: AppHandle) -> Result<String, String> {
    if let Some(path) = std::env::var_os("XDESK_CONFIG").filter(|p| !p.is_empty()) {
        return std::fs::read_to_string(&path)
            .map_err(|e| format!("XDESK_CONFIG : {} illisible ({e})", std::path::Path::new(&path).display()));
    }
    if let Ok(dir) = app.path().app_config_dir() {
        let path = dir.join("desk.json");
        if path.exists() {
            return std::fs::read_to_string(&path).map_err(|e| format!("{} illisible ({e})", path.display()));
        }
    }
    Ok(BUNDLED_CONFIG.to_string())
}

#[tauri::command]
fn vault_all(vault: State<'_, Vault>) -> HashMap<String, String> {
    vault.all()
}

/// Écrit, puis annonce à toutes les fenêtres QUI a écrit (chacune ignore sa propre annonce et relit sinon).
#[tauri::command]
fn vault_set(window: WebviewWindow, vault: State<'_, Vault>, key: String, value: Option<String>) -> Result<(), String> {
    if vault.set(&key, value)? {
        let _ = window.app_handle().emit("vault:changed", window.label());
    }
    Ok(())
}

/// Le propriétaire du verrou est l'étiquette de la fenêtre APPELANTE, connue du processus : la page ne la choisit pas.
#[tauri::command]
fn vault_lock(window: WebviewWindow, vault: State<'_, Vault>, name: String, ttl_ms: u64) -> bool {
    vault.lock(&name, window.label(), Duration::from_millis(ttl_ms), Instant::now())
}

#[tauri::command]
fn vault_unlock(window: WebviewWindow, vault: State<'_, Vault>, name: String) {
    vault.unlock(&name, window.label());
}

#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let url = policy::external(&url)?;
    app.opener().open_url(url.as_str(), None::<&str>).map_err(|e| format!("ouverture refusée par le système : {e}"))
}

/// `true` : enregistré ; `false` : dialogue refermé. Asynchrone : un dialogue bloquant ne doit pas tenir le fil principal.
#[tauri::command]
async fn save_file(app: AppHandle, filename: String, bytes: Vec<u8>) -> Result<bool, String> {
    use tauri_plugin_dialog::DialogExt;
    let Some(picked) = app.dialog().file().set_file_name(policy::filename(&filename)).blocking_save_file() else {
        return Ok(false);
    };
    let path = picked.into_path().map_err(|e| format!("emplacement inutilisable : {e}"))?;
    std::fs::write(&path, bytes).map_err(|e| format!("écriture impossible dans {} : {e}", path.display()))?;
    Ok(true)
}

static WINDOWS: AtomicUsize = AtomicUsize::new(2);

/// Une autre fenêtre du Desk (asynchrone : créer une fenêtre depuis une commande synchrone bloque sous Windows). Son
/// étiquette `desk-N` est couverte par `capabilities/default.json` : elle a les mêmes droits que la première, pas plus.
#[tauri::command]
async fn open_window(app: AppHandle, route: String) -> Result<(), String> {
    let route = policy::route(&route)?;
    let label = format!("desk-{}", WINDOWS.fetch_add(1, Ordering::Relaxed));
    WebviewWindowBuilder::new(&app, label, WebviewUrl::App(format!("index.html#{route}").into()))
        .title("Desk")
        .inner_size(1280.0, 800.0)
        .min_inner_size(380.0, 500.0)
        .build()
        .map(|_| ())
        .map_err(|e| format!("fenêtre non ouverte : {e}"))
}

/// Le verrou des navigations : quoi que fasse la page, la WebView ne charge que l'application (`policy::internal`).
fn navigation_guard<R: Runtime>() -> tauri::plugin::TauriPlugin<R> {
    tauri::plugin::Builder::new("desk-navigation")
        .on_navigation(|webview, url| {
            let dev = if tauri::is_dev() { webview.config().build.dev_url.clone() } else { None };
            let allowed = policy::internal(url, dev.as_ref());
            if !allowed {
                eprintln!("[desk] navigation refusée : {url}");
            }
            allowed
        })
        .build()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();
    // Une seule instance : un lien profond ouvert pendant que l'application tourne lui est REMIS (au lieu d'en lancer une
    // seconde, qui n'aurait ni la session ni la tentative de connexion en cours). Doit être le premier plugin.
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }));
    }
    builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(navigation_guard())
        .manage(Vault::default())
        .invoke_handler(tauri::generate_handler![
            desk_config,
            vault_all,
            vault_set,
            vault_lock,
            vault_unlock,
            open_external,
            save_file,
            open_window
        ])
        .setup(|app| {
            // macOS lit les schémas dans le paquet installé ; Linux et Windows (en développement) les inscrivent au lancement.
            // Un échec ne doit pas empêcher l'application de démarrer : seul le retour OAuth par lien profond manquera.
            #[cfg(any(target_os = "linux", all(debug_assertions, windows)))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                if let Err(e) = app.deep_link().register_all() {
                    eprintln!("[desk] schéma erp:// non inscrit auprès du système : {e}");
                }
            }
            let _ = app;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                window.state::<Vault>().release(window.label());
            }
        })
        .run(tauri::generate_context!())
        .expect("le Desk n'a pas pu démarrer");
}
