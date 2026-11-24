/**
 * Le COFFRE de session de l'application de bureau, et le verrou de renouvellement entre ses fenêtres.
 *
 * **Décision (traoreera/doctype#73).** Les jetons vivent dans la mémoire du processus Rust (`src-tauri/src/vault.rs`), jamais dans le
 * stockage de la WebView et jamais sur disque :
 *
 * - **durée de vie** : celle du processus. Fermer une fenêtre, la recharger ou en ouvrir une autre garde la session ; QUITTER
 *   l'application la termine (on se reconnecte au lancement suivant). C'est la règle du navigateur — « le jeton disparaît à la
 *   fermeture » — portée de l'onglet à l'application. Aucun jeton au repos : rien à voler dans un profil copié ou une sauvegarde.
 * - **plusieurs fenêtres** : UNE session pour l'application. Toutes les fenêtres lisent le même coffre ; une écriture (connexion,
 *   changement d'organisation, renouvellement, déconnexion) est annoncée aux autres, qui la prennent (`subscribe`, suivi par
 *   `stores/session.onStorage`). Il n'y a pas de « lignées » à réconcilier comme entre onglets : le coffre EST l'état partagé.
 * - **renouvellement** : le jeton de renouvellement d'xauth ne sert qu'une fois. Un verrou tenu par le processus (`lock`/`unlock`,
 *   avec une échéance : une fenêtre fermée en plein échange ne bloque personne) laisse UNE fenêtre échanger ; sous le verrou, le coffre
 *   est RELU avant tout (`reload`), si bien que la fenêtre suivante trouve le couple déjà renouvelé et n'envoie pas un jeton usé.
 *
 * Garder la session d'un lancement à l'autre (trousseau du système) est un autre compromis de sécurité, laissé à une demande
 * distincte : il suffirait de changer le dépôt derrière les mêmes commandes, sans toucher ce module.
 *
 * Ce module ne dépend pas de `@tauri-apps/*` : il reçoit son `VaultBackend` (`desktop/tauri.ts`, ou `memoryVaultHub()` dans les tests).
 */
import type { KeyValue } from '@/lib/host'
import type { TabSync } from '@/lib/tabsync'

export interface VaultBackend {
  /** Tout le coffre (il ne contient que la session : trois ou quatre clés). */
  all(): Promise<Record<string, string>>
  /** `null` efface. Le processus prévient les AUTRES fenêtres. */
  set(key: string, value: string | null): Promise<void>
  /** `handler` est appelé quand une AUTRE fenêtre a écrit. Rend de quoi se désabonner. */
  listen(handler: () => void): Promise<() => void>
  /** Prend le verrou `name` pour `owner` s'il est libre, déjà à lui ou échu ; `false` sinon. */
  lock(name: string, owner: string, ttlMs: number): Promise<boolean>
  unlock(name: string, owner: string): Promise<void>
}

/** Une fenêtre qui tient le verrou plus longtemps que cela est tenue pour morte (un échange de jeton dure une requête). */
export const LOCK_TTL_MS = 15_000
/** Le verrou du renouvellement : un seul, puisque l'application n'a qu'une session. */
export const REFRESH_LOCK = 'xdt.desk.refresh'
const LOCK_RETRY_MS = 40

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

export class Vault implements KeyValue {
  private readonly cache = new Map<string, string>()
  private readonly handlers = new Set<() => void>()
  /** Les écritures partent dans l'ordre, et une relecture attend qu'elles soient parties : jamais un état plus vieux que le nôtre. */
  private pending: Promise<void> = Promise.resolve()

  private constructor(
    private readonly backend: VaultBackend,
    private readonly owner: string,
  ) {}

  /** Ouvre le coffre : son contenu est lu AVANT que le Desk existe (le magasin de session lit ses jetons de façon synchrone). */
  static async open(backend: VaultBackend, owner: string): Promise<Vault> {
    const vault = new Vault(backend, owner)
    await vault.reload()
    await backend.listen(() => void vault.reload())
    return vault
  }

  get(key: string): string | null {
    return this.cache.get(key) ?? null
  }

  set(key: string, value: string | null): void {
    if ((this.cache.get(key) ?? null) === (value || null)) return
    if (value) this.cache.set(key, value)
    else this.cache.delete(key)
    this.pending = this.pending
      .then(() => this.backend.set(key, value || null))
      // Le processus ne répond pas : cette fenêtre garde sa session, les autres ne la verront pas. Rien à montrer à l'utilisateur.
      .catch((e) => console.error('[coffre] écriture refusée', key, e))
  }

  subscribe(handler: () => void): () => void {
    this.handlers.add(handler)
    return () => void this.handlers.delete(handler)
  }

  /** Relit le coffre ; prévient les abonnés seulement si quelque chose a changé (donc jamais pour nos propres écritures). */
  async reload(): Promise<void> {
    await this.pending
    const fresh = await this.backend.all()
    let changed = false
    for (const key of [...this.cache.keys()]) {
      if (!(key in fresh)) {
        this.cache.delete(key)
        changed = true
      }
    }
    for (const [key, value] of Object.entries(fresh)) {
      if (this.cache.get(key) !== value) {
        this.cache.set(key, value)
        changed = true
      }
    }
    if (changed) for (const handler of [...this.handlers]) handler()
  }

  /** Exécute `fn` seule parmi les fenêtres, sur un coffre relu. */
  async withLock<T>(name: string, fn: () => Promise<T>): Promise<T> {
    while (!(await this.backend.lock(name, this.owner, LOCK_TTL_MS))) await sleep(LOCK_RETRY_MS)
    try {
      await this.reload()
      return await fn()
    } finally {
      await this.pending // ce que `fn` a écrit est dans le coffre AVANT que la fenêtre suivante ne le relise
      await this.backend.unlock(name, this.owner).catch(() => {})
    }
  }
}

/**
 * Le `TabSync` des fenêtres d'une application de bureau : seul le VERROU a un sens. Le couple partagé, sa diffusion et son oubli
 * (`read`/`write`/`post`/`prune`) répondaient, dans le navigateur, à l'absence d'état commun entre onglets ; ici le coffre en tient
 * lieu, et le magasin de session suit ses changements directement.
 */
export function vaultTabSync(vault: Vault): TabSync {
  return {
    // UN verrou pour l'application, quel que soit le nom demandé : le magasin de session nomme le sien d'après sa lignée, qu'une fenêtre
    // en train de suivre une écriture (trois clés, trois annonces) peut ne pas encore connaître — deux noms, et plus de verrou.
    withLock: (_name, fn) => vault.withLock(REFRESH_LOCK, fn),
    read: () => null,
    write: () => {},
    remove: () => {},
    post: () => {},
    subscribe: () => () => {},
    prune: () => {},
  }
}

/**
 * Un processus en miniature pour les tests : un coffre, des verrous et des annonces partagés par autant de « fenêtres » qu'on en
 * connecte — avec la sémantique de `src-tauri/src/vault.rs` (annonce aux AUTRES, verrou à échéance).
 */
export function memoryVaultHub(now: () => number = Date.now): { backend(owner: string): VaultBackend; values: Map<string, string>; locks: Map<string, { owner: string; until: number }> } {
  const values = new Map<string, string>()
  const locks = new Map<string, { owner: string; until: number }>()
  const windows = new Map<string, Set<() => void>>()
  return {
    values,
    locks,
    backend(owner) {
      const mine = new Set<() => void>()
      windows.set(owner, mine)
      return {
        all: async () => Object.fromEntries(values),
        async set(key, value) {
          if (value) values.set(key, value)
          else values.delete(key)
          for (const [other, handlers] of windows) if (other !== owner) for (const h of [...handlers]) queueMicrotask(h)
        },
        async listen(handler) {
          mine.add(handler)
          return () => void mine.delete(handler)
        },
        async lock(name, who, ttlMs) {
          const held = locks.get(name)
          if (held && held.owner !== who && held.until > now()) return false
          locks.set(name, { owner: who, until: now() + ttlMs })
          return true
        },
        async unlock(name, who) {
          if (locks.get(name)?.owner === who) locks.delete(name)
        },
      }
    },
  }
}
