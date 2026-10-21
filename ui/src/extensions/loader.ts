/**
 * Charger les extensions que `config.json` annonce (D28) : une par une, chacune isolée. Un module qui ne se charge pas, qui ne respecte
 * pas le contrat ou dont le `setup` lève (ou ne finit pas à temps) est ÉCARTÉ : ce qu'il avait enregistré est retiré, le Desk démarre.
 */
import type { ExtensionHost } from './host'
import { EXTENSION_API_VERSION, type ExtensionDefinition } from './types'

export interface ExtensionEntry {
  name: string
  /** L'URL absolue du point d'entrée (résolue par `loadConfig`). */
  url: string
}

export type Importer = (url: string) => Promise<{ default?: unknown }>

/** Le temps laissé à une extension pour se charger ET s'installer : au-delà, le Desk démarre sans elle. */
export const SETUP_TIMEOUT_MS = 8000

// `@vite-ignore` : l'URL n'est connue qu'à l'exécution, Vite ne doit pas tenter de la résoudre au build.
const browserImport: Importer = (url) => import(/* @vite-ignore */ url)

function contract(mod: { default?: unknown } | null | undefined, name: string): ExtensionDefinition {
  const def = mod?.default as Partial<ExtensionDefinition> | undefined
  if (!def || typeof def !== 'object') throw new TypeError("le module n'exporte pas d'extension par défaut (export default defineExtension({...}))")
  if (def.name !== name) throw new TypeError(`l'extension se nomme ${JSON.stringify(def.name)}, pas ${JSON.stringify(name)} (le plugin qui la livre)`)
  if (def.apiVersion !== EXTENSION_API_VERSION) throw new TypeError(`version d'API ${JSON.stringify(def.apiVersion)} non prise en charge (attendue : ${EXTENSION_API_VERSION})`)
  if (typeof def.setup !== 'function') throw new TypeError('setup(desk) manquant')
  return def as ExtensionDefinition
}

function within<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`pas prête après ${ms / 1000} s`)), ms)
  })
  return Promise.race([work, late]).finally(() => clearTimeout(timer))
}

export async function loadExtensions(host: ExtensionHost, entries: readonly ExtensionEntry[], opts: { importer?: Importer; timeoutMs?: number } = {}): Promise<void> {
  const importer = opts.importer ?? browserImport
  for (const entry of entries) {
    try {
      await within(
        (async () => {
          const def = contract(await importer(entry.url), entry.name)
          await def.setup(host.handle(entry.name))
        })(),
        opts.timeoutMs ?? SETUP_TIMEOUT_MS,
      )
      host.loaded.push(entry.name)
    } catch (e) {
      host.drop(entry.name)
      host.failures.push({ name: entry.name, reason: e instanceof Error ? e.message : String(e) })
      console.error(`[extension ${entry.name}] écartée`, e)
    }
  }
}
