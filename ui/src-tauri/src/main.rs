// Pas de console derrière la fenêtre sous Windows en version livrée.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    desk_lib::run()
}
