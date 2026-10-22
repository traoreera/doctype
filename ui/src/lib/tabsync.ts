/**
 * Les onglets d'une même session se passent le jeton de renouvellement (C3, `.plan/plan.md`).
 *
 * xauth fait TOURNER le jeton de renouvellement : il ne sert qu'une fois. Un onglet dupliqué copie la session de l'onglet d'origine
 * (sessionStorage), donc le même jeton : le premier qui renouvelle use le jeton de l'autre, qui était alors déconnecté à son tour. Ici, les
 * onglets d'une même LIGNÉE (une connexion ; l'onglet dupliqué en hérite) partagent le DERNIER couple de jetons, renouvellent un par un
 * sous un verrou commun, et se préviennent d'un renouvellement ou d'une déconnexion.
 *
 * Le couple partagé vit dans `localStorage` (même origine, comme `sessionStorage` : rien de plus exposé à un script de la page), sous la
 * clé de sa lignée ; il est retiré à la déconnexion et oublié au bout de `SHARED_TTL_MS`. Tout est injectable : les tests simulent deux
 * onglets avec `memoryTabHub()`.
 */

export interface SharedPair {
  access: string
  refresh: string
  /** Quand ce couple a été écrit (ms). */
  at: number
}

export type TabMessage = { type: 'rotated'; lineage: string } | { type: 'logout'; lineage: string }

export interface TabSync {
  /** Exécute `fn` seul parmi les onglets (même `name`) ; sans verrou disponible, l'exécute directement. */
  withLock<T>(name: string, fn: () => Promise<T>): Promise<T>
  read(lineage: string): SharedPair | null
  write(lineage: string, pair: SharedPair): void
  remove(lineage: string): void
  /** Prévient les AUTRES onglets (jamais soi-même). */
  post(message: TabMessage): void
  subscribe(handler: (message: TabMessage) => void): () => void
  /** Oublie les couples écrits il y a plus de `maxAgeMs` (une lignée dont tous les onglets sont fermés depuis longtemps). */
  prune(maxAgeMs: number, now: number): void
}

export const SHARED_TTL_MS = 24 * 3600 * 1000
const PREFIX = 'xdt.desk.session.'
const CHANNEL = 'xdt.desk.session'

function parsePair(raw: string | null): SharedPair | null {
  if (!raw) return null
  try {
    const v = JSON.parse(raw) as Partial<SharedPair>
    return typeof v.access === 'string' && typeof v.refresh === 'string' && typeof v.at === 'number' ? (v as SharedPair) : null
  } catch {
    return null
  }
}

const isMessage = (v: unknown): v is TabMessage =>
  !!v && typeof v === 'object' && ((v as TabMessage).type === 'rotated' || (v as TabMessage).type === 'logout') && typeof (v as TabMessage).lineage === 'string'

/** Le navigateur : `localStorage`, `BroadcastChannel`, `navigator.locks` ; chacun absent (navigation privée, vieux navigateur) dégrade sans casser. */
export function browserTabSync(): TabSync {
  const storage = (() => {
    try {
      return typeof localStorage !== 'undefined' ? localStorage : null
    } catch {
      return null
    }
  })()
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(CHANNEL) : null
  const locks = typeof navigator !== 'undefined' ? (navigator as Navigator & { locks?: LockManager }).locks : undefined
  return {
    withLock: (name, fn) => (locks ? (locks.request(name, fn) as Promise<Awaited<ReturnType<typeof fn>>>) : fn()),
    read: (lineage) => {
      try {
        return parsePair(storage?.getItem(PREFIX + lineage) ?? null)
      } catch {
        return null
      }
    },
    write: (lineage, pair) => {
      try {
        storage?.setItem(PREFIX + lineage, JSON.stringify(pair))
      } catch {
        // stockage plein ou refusé : les onglets ne se passeront pas le jeton, chacun garde le sien (comportement d'avant)
      }
    },
    remove: (lineage) => {
      try {
        storage?.removeItem(PREFIX + lineage)
      } catch {
        // idem
      }
    },
    post: (message) => channel?.postMessage(message),
    subscribe: (handler) => {
      if (!channel) return () => {}
      const listen = (e: MessageEvent) => isMessage(e.data) && handler(e.data)
      channel.addEventListener('message', listen)
      return () => channel.removeEventListener('message', listen)
    },
    prune: (maxAgeMs, now) => {
      try {
        if (!storage) return
        for (let i = storage.length - 1; i >= 0; i--) {
          const key = storage.key(i)
          if (!key?.startsWith(PREFIX)) continue
          const pair = parsePair(storage.getItem(key))
          if (!pair || now - pair.at > maxAgeMs) storage.removeItem(key)
        }
      } catch {
        // idem
      }
    },
  }
}

/** Plusieurs « onglets » dans un même test : un stockage, un verrou et une diffusion partagés (messages livrés aux AUTRES, en différé). */
export function memoryTabHub(): { connect(): TabSync; storage: Map<string, SharedPair> } {
  const storage = new Map<string, SharedPair>()
  const tabs = new Set<(m: TabMessage) => void>()
  const chains = new Map<string, Promise<unknown>>()
  return {
    storage,
    connect() {
      const mine = new Set<(m: TabMessage) => void>()
      return {
        withLock(name, fn) {
          const run = (chains.get(name) ?? Promise.resolve()).then(fn, fn)
          chains.set(name, run.catch(() => undefined))
          return run
        },
        read: (lineage) => storage.get(lineage) ?? null,
        write: (lineage, pair) => void storage.set(lineage, { ...pair }),
        remove: (lineage) => void storage.delete(lineage),
        post(message) {
          for (const deliver of tabs) if (!mine.has(deliver)) queueMicrotask(() => deliver(message))
        },
        subscribe(handler) {
          mine.add(handler)
          tabs.add(handler)
          return () => {
            mine.delete(handler)
            tabs.delete(handler)
          }
        },
        prune(maxAgeMs, now) {
          for (const [k, v] of storage) if (now - v.at > maxAgeMs) storage.delete(k)
        },
      }
    },
  }
}
