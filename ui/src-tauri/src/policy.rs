//! Ce que le processus accepte de la WebView, jugé ICI une seconde fois (la page fait le bon geste, `ui/src/desktop/links.ts` ;
//! le processus est le verrou). Fonctions pures, testées seules.

use url::Url;

/// Une adresse que le système a le droit d'ouvrir : une page web, un courriel, un numéro. Jamais `file:`, jamais un autre programme.
pub fn external(raw: &str) -> Result<Url, String> {
    let url = Url::parse(raw).map_err(|_| format!("adresse illisible : « {raw} »"))?;
    match url.scheme() {
        "https" | "http" if url.host_str().is_some() => Ok(url),
        "mailto" | "tel" => Ok(url),
        other => Err(format!("schéma « {other}: » refusé : seuls http(s), mailto et tel s'ouvrent hors de l'application")),
    }
}

/// La WebView ne navigue que DANS l'application : ses propres pages (`tauri://localhost`, `http://tauri.localhost` sous
/// Windows et Android, le serveur de développement), et les documents qu'elle fabrique elle-même (`about:srcdoc` d'un aperçu
/// d'impression en bac à sable, `blob:`). Tout le reste est refusé — un lien externe s'ouvre par le système, pas ici.
pub fn internal(url: &Url, dev: Option<&Url>) -> bool {
    match url.scheme() {
        "tauri" => url.host_str() == Some("localhost"),
        "http" | "https" => {
            url.host_str() == Some("tauri.localhost") || dev.is_some_and(|d| d.origin() == url.origin())
        }
        "about" | "blob" => true,
        _ => false,
    }
}

/// Une route du Desk pour une nouvelle fenêtre : un chemin du routeur (`/app/Client/new`), rien qui change de page.
pub fn route(raw: &str) -> Result<&str, String> {
    if raw.starts_with('/') && !raw.starts_with("//") && raw.len() <= 2048 && !raw.chars().any(char::is_control) {
        Ok(raw)
    } else {
        Err(format!("route refusée : « {raw} » n'est pas un chemin du Desk"))
    }
}

/// Le nom PROPOSÉ dans le dialogue d'enregistrement : jamais un chemin (l'utilisateur choisit le dossier), jamais de caractère de contrôle.
pub fn filename(raw: &str) -> String {
    let clean: String = raw
        .chars()
        .map(|c| if c.is_control() || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') { '_' } else { c })
        .collect();
    let clean = clean.trim().trim_start_matches('.').chars().take(180).collect::<String>();
    if clean.is_empty() { "fichier".to_string() } else { clean }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn le_systeme_n_ouvre_que_le_web_le_courriel_et_le_telephone() {
        for ok in ["https://checkout.stripe.com/c/pay/x", "http://127.0.0.1:8000/docs", "mailto:a@exemple.fr", "tel:+2250700000000"] {
            assert!(external(ok).is_ok(), "{ok}");
        }
        for bad in ["file:///etc/passwd", "javascript:alert(1)", "erp://oauth-callback", "smb://serveur/partage", "/chemin", "https:"] {
            assert!(external(bad).is_err(), "{bad}");
        }
        assert!(external("file:///etc/passwd").unwrap_err().contains("file:"));
    }

    #[test]
    fn la_webview_ne_quitte_pas_l_application() {
        let dev = Url::parse("http://localhost:5174/").unwrap();
        let p = |s: &str| Url::parse(s).unwrap();
        assert!(internal(&p("tauri://localhost/index.html#/app/Client"), None));
        assert!(internal(&p("http://tauri.localhost/"), None));
        assert!(internal(&p("about:srcdoc"), None));
        assert!(internal(&p("blob:tauri://localhost/2f1c"), None));
        assert!(internal(&p("http://localhost:5174/src/desktop/main.ts"), Some(&dev)));
        assert!(!internal(&p("http://localhost:5174/"), None), "le serveur de dev n'existe pas en version livrée");
        assert!(!internal(&p("http://localhost:8000/app/auth/login"), Some(&dev)), "le serveur de l'API n'est pas une page");
        assert!(!internal(&p("https://checkout.stripe.com/c/pay/x"), Some(&dev)));
        assert!(!internal(&p("tauri://ailleurs/"), None));
        assert!(!internal(&p("file:///etc/passwd"), None));
        assert!(!internal(&p("data:text/html,<script>1</script>"), None));
    }

    #[test]
    fn une_fenetre_s_ouvre_sur_une_route_du_desk() {
        assert_eq!(route("/app/Sales Invoice/new"), Ok("/app/Sales Invoice/new"));
        assert!(route("//evil.example/x").is_err());
        assert!(route("https://evil.example").is_err());
        assert!(route("").is_err());
        assert!(route("/a\nb").is_err());
    }

    #[test]
    fn le_nom_propose_n_est_jamais_un_chemin() {
        assert_eq!(filename("FA-2026-00001.pdf"), "FA-2026-00001.pdf");
        assert_eq!(filename("../../etc/passwd"), "_.._etc_passwd");
        assert_eq!(filename("C:\\Windows\\x.exe"), "C__Windows_x.exe");
        assert_eq!(filename("  \u{0007} "), "_");
        assert_eq!(filename(""), "fichier");
        assert_eq!(filename("...."), "fichier");
    }
}
