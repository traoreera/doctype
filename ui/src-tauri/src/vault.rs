//! Le coffre de session : les jetons du Desk, dans la mémoire du PROCESSUS.
//!
//! Décision (traoreera/doctype#73, `ui/src/desktop/vault.ts`) : jamais sur disque, jamais dans le stockage de la
//! WebView. Toutes les fenêtres lisent le même coffre (une session pour l'application) ; quitter l'application la
//! termine. Le verrou sert au renouvellement : le jeton de renouvellement d'xauth ne sert qu'une fois, une seule
//! fenêtre l'échange à la fois. Il a une échéance — une fenêtre fermée en plein échange ne bloque pas les autres.
//!
//! Aucune dépendance à Tauri ici : ce module se teste seul (`cargo test`).

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// Le coffre ne tient que la session du Desk : un préfixe, peu de clés, des valeurs bornées. Une page compromise ne
/// peut pas s'en servir comme d'un stockage arbitraire dans le processus.
const PREFIX: &str = "xdt.desk.";
const MAX_KEYS: usize = 16;
const MAX_VALUE: usize = 16 * 1024;
const MAX_LOCK_TTL: Duration = Duration::from_secs(60);

#[derive(Default)]
pub struct Vault {
    values: Mutex<HashMap<String, String>>,
    locks: Mutex<HashMap<String, (String, Instant)>>,
}

impl Vault {
    pub fn all(&self) -> HashMap<String, String> {
        self.values.lock().expect("coffre").clone()
    }

    /// `None` (ou vide) efface. Rend `true` si le coffre a changé : seul un changement s'annonce aux fenêtres.
    pub fn set(&self, key: &str, value: Option<String>) -> Result<bool, String> {
        if !key.starts_with(PREFIX) || key.len() > 128 {
            return Err(format!("clé refusée : « {key} » n'est pas une clé de session ({PREFIX}…)"));
        }
        let mut values = self.values.lock().expect("coffre");
        match value.filter(|v| !v.is_empty()) {
            None => Ok(values.remove(key).is_some()),
            Some(v) if v.len() > MAX_VALUE => Err(format!("valeur refusée : plus de {MAX_VALUE} octets pour « {key} »")),
            Some(v) => {
                if !values.contains_key(key) && values.len() >= MAX_KEYS {
                    return Err(format!("coffre plein : {MAX_KEYS} clés au plus"));
                }
                Ok(values.insert(key.to_string(), v.clone()).as_deref() != Some(v.as_str()))
            }
        }
    }

    /// Prend le verrou pour `owner` s'il est libre, déjà à lui, ou échu.
    pub fn lock(&self, name: &str, owner: &str, ttl: Duration, now: Instant) -> bool {
        let mut locks = self.locks.lock().expect("verrous");
        if let Some((held_by, until)) = locks.get(name) {
            if held_by != owner && *until > now {
                return false;
            }
        }
        locks.insert(name.to_string(), (owner.to_string(), now + ttl.min(MAX_LOCK_TTL)));
        true
    }

    /// Seul son propriétaire libère un verrou.
    pub fn unlock(&self, name: &str, owner: &str) {
        let mut locks = self.locks.lock().expect("verrous");
        if locks.get(name).is_some_and(|(held_by, _)| held_by == owner) {
            locks.remove(name);
        }
    }

    /// Une fenêtre détruite ne tient plus rien.
    pub fn release(&self, owner: &str) {
        self.locks.lock().expect("verrous").retain(|_, (held_by, _)| held_by != owner);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn une_valeur_ecrite_se_relit_et_null_efface() {
        let v = Vault::default();
        assert_eq!(v.set("xdt.desk.token", Some("a".into())), Ok(true));
        assert_eq!(v.all().get("xdt.desk.token").map(String::as_str), Some("a"));
        assert_eq!(v.set("xdt.desk.token", Some("a".into())), Ok(false), "réécrire la même valeur n'est pas un changement");
        assert_eq!(v.set("xdt.desk.token", None), Ok(true));
        assert_eq!(v.set("xdt.desk.token", Some(String::new())), Ok(false), "vide = absent");
        assert!(v.all().is_empty());
    }

    #[test]
    fn seules_les_cles_de_session_entrent_et_le_coffre_est_borne() {
        let v = Vault::default();
        assert!(v.set("autre.cle", Some("x".into())).unwrap_err().contains("autre.cle"));
        assert!(v.set("xdt.desk.token", Some("x".repeat(MAX_VALUE + 1))).is_err());
        for i in 0..MAX_KEYS {
            v.set(&format!("xdt.desk.k{i}"), Some("x".into())).unwrap();
        }
        assert!(v.set("xdt.desk.de-trop", Some("x".into())).unwrap_err().contains("plein"));
        assert_eq!(v.set("xdt.desk.k0", Some("y".into())), Ok(true), "une clé existante se remplace, même plein");
    }

    #[test]
    fn un_verrou_tenu_refuse_les_autres_jusqu_a_sa_liberation() {
        let v = Vault::default();
        let t0 = Instant::now();
        let ttl = Duration::from_secs(15);
        assert!(v.lock("refresh", "main", ttl, t0));
        assert!(v.lock("refresh", "main", ttl, t0), "son propriétaire le reprend");
        assert!(!v.lock("refresh", "desk-2", ttl, t0));
        v.unlock("refresh", "desk-2");
        assert!(!v.lock("refresh", "desk-2", ttl, t0), "une autre fenêtre ne libère pas le verrou de la première");
        v.unlock("refresh", "main");
        assert!(v.lock("refresh", "desk-2", ttl, t0));
    }

    #[test]
    fn un_verrou_echu_ou_orphelin_ne_bloque_personne() {
        let v = Vault::default();
        let t0 = Instant::now();
        assert!(v.lock("refresh", "main", Duration::from_secs(15), t0));
        assert!(!v.lock("refresh", "desk-2", Duration::from_secs(15), t0 + Duration::from_secs(14)));
        assert!(v.lock("refresh", "desk-2", Duration::from_secs(15), t0 + Duration::from_secs(16)), "échu");
        v.release("desk-2");
        assert!(v.lock("refresh", "main", Duration::from_secs(15), t0 + Duration::from_secs(17)), "fenêtre détruite");
        // Une échéance démesurée est ramenée au plafond : une page ne verrouille pas « pour toujours ».
        assert!(v.lock("x", "main", Duration::from_secs(86_400), t0));
        assert!(v.lock("x", "desk-2", Duration::from_secs(1), t0 + MAX_LOCK_TTL + Duration::from_secs(1)));
    }
}
