/**
 * L'hôte de BUREAU (`lib/host.ts`) : jetons dans le coffre du processus, sorties par le navigateur du système, fichiers par le
 * dialogue natif. Comme le transport, il ne dépend pas de `@tauri-apps/*` : il reçoit sa `DesktopShell` (`desktop/tauri.ts`, ou un
 * faux dans les tests).
 */
import { HostRefusal, memoryKeyValue, type DeskHost, type HostCapability, type SaveOutcome } from '@/lib/host'
import type { Transport } from '@/lib/transport'
import { DEEP_LINK_SCHEME } from './deeplink'
import { isExternal } from './links'
import { vaultTabSync, type Vault } from './vault'

/** Les gestes que seul le processus de l'application sait faire. */
export interface DesktopShell {
  /** Ouvrir une adresse http(s), `mailto:` ou `tel:` par le système. Le processus revérifie le schéma. */
  openExternal(url: string): Promise<void>
  /** Demander où enregistrer (dialogue natif) puis écrire ; `false` si l'utilisateur referme. La WebView ne reçoit jamais de chemin. */
  saveFile(filename: string, bytes: Uint8Array): Promise<boolean>
  /** Une autre fenêtre de l'application sur une route du Desk. */
  openWindow(route: string): Promise<void>
}

/** Le retour OAuth d'xauth vers une application de bureau (sa cible par défaut : `vendor/auth/src/services/oauth.py`). */
export const OAUTH_DEEP_LINK = `${DEEP_LINK_SCHEME}://oauth-callback`

export function desktopHost(o: { vault: Vault; shell: DesktopShell; transport: Transport; unavailable?: Partial<Record<HostCapability, string>> }): DeskHost {
  const save = async (filename: string, bytes: Uint8Array): Promise<SaveOutcome> => ((await o.shell.saveFile(filename, bytes)) ? 'saved' : 'cancelled')
  const outside = async (url: string): Promise<void> => {
    if (!isExternal(url)) throw new HostRefusal(`Adresse non ouverte : « ${url} » n'est ni une page web, ni un courriel, ni un numéro`)
    await o.shell.openExternal(url)
  }
  return {
    kind: 'desktop',
    // Quitter le Desk ou ouvrir à côté : en bureau c'est le même geste, la WebView ne navigue dans aucun des deux cas.
    leave: outside,
    openExternal: outside,
    session: o.vault,
    // Le nonce OAuth reste dans la mémoire de CETTE fenêtre : un lien profond arrive à toutes, seule celle qui a ouvert le fournisseur doit l'accepter.
    flow: memoryKeyValue(),
    tabs: () => vaultTabSync(o.vault),
    unavailable: o.unavailable ?? {},
    oauth: { redirect: OAUTH_DEEP_LINK, strict: true },
    async openWindow(route) {
      if (!route.startsWith('/')) throw new HostRefusal(`Fenêtre non ouverte : « ${route} » n'est pas une route du Desk`)
      await o.shell.openWindow(route)
    },
    saveBytes: (filename, bytes) => save(filename, bytes),
    saveText: (filename, text) => save(filename, new TextEncoder().encode(text)),
    /** Le fichier est lu par le transport (donc sur le seul serveur configuré), puis enregistré là où l'utilisateur le choisit. */
    async saveUrl(url, filename) {
      const res = await o.transport.request({ method: 'GET', url, headers: { Accept: '*/*' }, binary: true })
      if (!res.bytes) throw new HostRefusal(`Téléchargement refusé par le serveur (HTTP ${res.status})`)
      return save(filename, res.bytes)
    },
  }
}
