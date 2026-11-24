/**
 * Le SEUL module (avec `desktop/main.ts`) qui importe `@tauri-apps/*` : il branche les contrats du bureau — `NativeFetch`,
 * `VaultBackend`, `DesktopShell`, liens profonds — sur les commandes du processus Rust (`src-tauri/src/lib.rs`). Rien d'ici n'entre
 * dans le build web : `main.ts` ne l'importe pas (vérifié par `tests/desktop-isolation.spec.ts`).
 *
 * La WebView n'a que cinq gestes natifs, tous des commandes de l'application ou des plugins bornés par `src-tauri/capabilities/` :
 * lire sa configuration, le coffre, ouvrir une adresse par le système, enregistrer un fichier par dialogue, ouvrir une fenêtre du Desk.
 * Aucun accès générique au système de fichiers, au shell ou à la création de fenêtres n'est accordé.
 */
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow'
import { getCurrent, onOpenUrl } from '@tauri-apps/plugin-deep-link'
import { fetch as pluginFetch } from '@tauri-apps/plugin-http'
import type { DesktopShell } from './host'
import type { NativeFetch } from './transport'
import type { VaultBackend } from './vault'

/** L'étiquette de cette fenêtre (`main`, `desk-2`…) : ce qui la distingue des autres auprès du coffre. */
export const windowLabel = (): string => getCurrentWebviewWindow().label

/** Le texte de `desk.json` (variable `XDESK_CONFIG`, dossier de configuration de l'utilisateur, ou celui que l'application embarque). */
export const readHostConfig = (): Promise<string> => invoke<string>('desk_config')

/** `fetch` du plugin HTTP : la requête part du processus Rust, bornée par la portée `http:default` des capacités. */
export const nativeFetch: NativeFetch = (url, init) => pluginFetch(url, init)

export function vaultBackend(): VaultBackend {
  const me = windowLabel()
  return {
    all: () => invoke<Record<string, string>>('vault_all'),
    set: (key, value) => invoke<void>('vault_set', { key, value }),
    // Le processus annonce l'écriture à TOUTES les fenêtres, en nommant celle qui a écrit : on ne relit pas pour soi-même.
    listen: (handler) => listen<string>('vault:changed', (e) => void (e.payload !== me && handler())),
    // Le propriétaire du verrou est l'étiquette que le PROCESSUS connaît de l'appelant : une fenêtre ne peut pas libérer celui d'une autre.
    lock: (name, _owner, ttlMs) => invoke<boolean>('vault_lock', { name, ttlMs }),
    unlock: (name) => invoke<void>('vault_unlock', { name }),
  }
}

export const shell: DesktopShell = {
  openExternal: (url) => invoke<void>('open_external', { url }),
  // Les octets partent en tableau JSON : lourd pour un très gros fichier, suffisant pour un PDF ou un export (quelques Mo).
  saveFile: (filename, bytes) => invoke<boolean>('save_file', { filename, bytes: Array.from(bytes) }),
  openWindow: (route) => invoke<void>('open_window', { route }),
}

/** Les liens profonds : celui qui a LANCÉ l'application (démarrage à froid), puis ceux qui arrivent pendant qu'elle tourne. */
export async function watchDeepLinks(handler: (url: string) => void): Promise<void> {
  // Le lien de lancement n'est rejoué que par la fenêtre principale : une seconde fenêtre ouverte plus tard ne doit pas le retraiter.
  if (windowLabel() === 'main') for (const url of (await getCurrent().catch(() => null)) ?? []) handler(url)
  await onOpenUrl((urls) => urls.forEach(handler))
}
