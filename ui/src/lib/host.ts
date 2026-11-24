/**
 * L'HÔTE du Desk : tout ce que l'application demande à ce qui l'héberge, en dehors du réseau (`lib/transport.ts`).
 *
 * Le Desk tourne dans un navigateur (`browserHost()`, le comportement de toujours) ou dans une application de bureau Tauri
 * (`desktop/host.ts`). Ce contrat dit ce qui change de l'un à l'autre, et RIEN d'autre : où vivent les jetons, comment les fenêtres
 * d'une même session se parlent, comment on sort vers une page externe (paiement, fournisseur d'identité), comment on enregistre un
 * fichier. Un écran ne touche jamais `sessionStorage`, `location.assign` ou un `<a download>` fabriqué : il passe par ici, et le portage
 * consiste à fournir un autre hôte (`createDesk(transport, config, host)`).
 *
 * Ce qui reste volontairement HORS du contrat : les commodités de l'appareil gardées en `localStorage` (thème, récents, vue choisie,
 * colonnes, positions d'un graphe) — rien de secret, et une WebView les garde comme un navigateur ; le presse-papiers ; l'impression
 * d'un aperçu (`window.print` dans l'iframe de `PrintDialog`). Voir l'inventaire dans `docs/desktop.md`.
 */
import { inject } from 'vue'
import { downloadBytes, downloadText, downloadUrl } from './download'
import { SERVICES } from './services'
import { browserTabSync, type TabSync } from './tabsync'

/** Un petit stockage de chaînes, SYNCHRONE (le magasin de session lit ses jetons à sa création). `null` efface. */
export interface KeyValue {
  get(key: string): string | null
  set(key: string, value: string | null): void
  /**
   * Prévenu quand une AUTRE fenêtre a changé ce stockage (jamais pour ses propres écritures). Absent dans le navigateur : le
   * `sessionStorage` d'un onglet n'appartient qu'à lui. Présent en bureau, où toutes les fenêtres partagent le même coffre.
   */
  subscribe?(handler: () => void): () => void
}

/**
 * Ce qu'un hôte peut ne PAS savoir faire. Vocabulaire fermé : une capacité absente est nommée ici avec sa raison (`unavailable`),
 * le Desk la désactive et l'annonce — il ne tente pas, il n'échoue pas en silence.
 *
 * - `extensions` : charger les modules JavaScript des plugins (D28) — `import()` d'une autre origine, donc CSP et CORS.
 * - `realtime` : le flux SSE d'xpulse par `Transport.stream`.
 * - `windows` : ouvrir une page du Desk dans une autre fenêtre (le « nouvel onglet » du navigateur).
 */
export const HOST_CAPABILITIES = ['extensions', 'realtime', 'windows'] as const
export type HostCapability = (typeof HOST_CAPABILITIES)[number]

/** L'utilisateur a choisi où enregistrer, ou a refermé le dialogue. Un échec d'écriture lève. */
export type SaveOutcome = 'saved' | 'cancelled'

export interface DeskHost {
  kind: 'web' | 'desktop'
  /** Les jetons de la session (accès, renouvellement, lignée). Navigateur : `sessionStorage`, par onglet. Bureau : le coffre du processus. */
  session: KeyValue
  /** Ce qui ne vaut que pour CETTE fenêtre et ne doit jamais être partagé : le nonce d'une connexion OAuth en cours. */
  flow: KeyValue
  /** Ce que les fenêtres d'une même session se passent (`lib/tabsync`). Appelé une fois, par le magasin de session. */
  tabs(): TabSync
  /** Les capacités que cet hôte n'offre pas, chacune avec sa raison (affichable). */
  unavailable: Partial<Record<HostCapability, string>>
  oauth: {
    /**
     * Où le fournisseur d'identité renvoie. `null` : la page `#/oauth` du Desk web (calculée d'après l'API, comme avant).
     * Bureau : le lien profond d'xauth, `erp://oauth-callback`.
     */
    redirect: string | null
    /**
     * Refuser un retour sans nonce attendu OU sans nonce rendu. Le navigateur garde sa tolérance d'origine (un retour sans
     * `app_state` passe) ; un lien profond, que n'importe quel programme de la machine peut invoquer, n'en a aucune.
     */
    strict: boolean
  }
  /**
   * Quitter le Desk pour une page qui n'est pas la sienne : la caisse ou le portail du prestataire de paiement, la page
   * d'autorisation d'un fournisseur d'identité. Navigateur : l'onglet y navigue (et le prestataire le ramènera). Bureau : le
   * navigateur du système l'ouvre — la WebView ne navigue JAMAIS hors de l'application.
   */
  leave(url: string): Promise<void>
  /**
   * Ouvrir une page externe À CÔTÉ du Desk, qui reste où il est (le lien d'une notification). Navigateur : un nouvel onglet, sans
   * lien avec celui-ci (`noopener,noreferrer`). Bureau : le navigateur du système, comme `leave`.
   */
  openExternal(url: string): Promise<void>
  /** Ouvrir une route du Desk (`/app/Client/new`) dans une autre fenêtre. Sans la capacité `windows` : refusé avec sa raison. */
  openWindow(route: string): Promise<void>
  saveBytes(filename: string, bytes: Uint8Array, mime: string): Promise<SaveOutcome>
  saveText(filename: string, text: string, mime?: string): Promise<SaveOutcome>
  /** Enregistrer ce qu'une adresse sert (un lien signé de fichier, qu'on vient de redemander). */
  saveUrl(url: string, filename: string): Promise<SaveOutcome>
}

/** Une sortie qui n'est ni http(s) n'est pas suivie : `javascript:`, `file:`, `data:` ne sont jamais des pages de paiement. */
export class HostRefusal extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'HostRefusal'
  }
}

/** `sessionStorage`, enveloppé : indisponible (navigation privée…), la session ne survit pas à un rechargement, rien de plus. */
function tabStorage(): KeyValue {
  return {
    get(key) {
      try {
        return sessionStorage.getItem(key)
      } catch {
        return null
      }
    },
    set(key, value) {
      try {
        if (value) sessionStorage.setItem(key, value)
        else sessionStorage.removeItem(key)
      } catch {
        // stockage indisponible (navigation privée…) : la session ne survivra pas à un rechargement, rien de plus
      }
    },
  }
}

/** Un stockage en mémoire : la « fenêtre » d'un hôte de bureau (nonce OAuth), et les tests. */
export function memoryKeyValue(): KeyValue {
  const values = new Map<string, string>()
  return {
    get: (key) => values.get(key) ?? null,
    set: (key, value) => void (value ? values.set(key, value) : values.delete(key)),
  }
}

/**
 * Le navigateur : exactement ce que le Desk faisait avant ce contrat. Le jeton reste dans l'onglet (`sessionStorage` : il disparaît à
 * sa fermeture), les onglets d'une lignée se passent le jeton de renouvellement (`browserTabSync`), une sortie est une navigation de
 * l'onglet, un fichier est un `<a download>`.
 */
export function browserHost(): DeskHost {
  const storage = tabStorage()
  return {
    kind: 'web',
    session: storage,
    flow: storage,
    tabs: browserTabSync,
    unavailable: {},
    oauth: { redirect: null, strict: false },
    async leave(url) {
      globalThis.location.assign(url)
    },
    async openExternal(url) {
      globalThis.open(url, '_blank', 'noopener,noreferrer')
    },
    async openWindow(route) {
      globalThis.open(`#${route}`, '_blank', 'noopener')
    },
    async saveBytes(filename, bytes, mime) {
      downloadBytes(filename, bytes, mime)
      return 'saved'
    },
    async saveText(filename, text, mime) {
      if (mime === undefined) downloadText(filename, text)
      else downloadText(filename, text, mime)
      return 'saved'
    },
    async saveUrl(url, filename) {
      downloadUrl(url, filename)
      return 'saved'
    },
  }
}

/** L'hôte du Desk courant ; sans hôte fourni (les tests qui montent un écran seul), le navigateur. */
export function useHost(): DeskHost {
  return inject(SERVICES, null)?.host ?? browserHost()
}
