// L'app desktop: una finestra Tauri sul frontend buildato, con il backend
// Python (PyInstaller) avviato come processo interno — il "sidecar".
//
// Il ciclo di vita è tutto qui: il sidecar parte PRIMA che la finestra carichi
// la UI e viene terminato all'uscita, così non restano processi orfani che
// tengono occupata la porta. La UI parla col backend su 127.0.0.1:8765
// (VITE_API_BASE, iniettata alla build del frontend).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// La porta è fissa e non standard di proposito: 8000 è spesso occupata da
/// altri dev server, e la UI viene compilata con questo valore dentro.
const BACKEND_PORT: &str = "8765";

struct Backend(Mutex<Option<CommandChild>>);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let sidecar = app
                .shell()
                .sidecar("backend")
                .expect("sidecar 'backend' non trovato nel bundle")
                .env("IDEA_RADAR_PORT", BACKEND_PORT);
            let (_events, child) = sidecar.spawn()?;
            app.manage(Backend(Mutex::new(Some(child))));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("errore nell'avvio di Idea Radar")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(child) = app.state::<Backend>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
