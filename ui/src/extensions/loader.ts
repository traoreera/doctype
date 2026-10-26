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
/** Lit une URL pour le diagnostic (remplaçable dans les tests). */
export type Prober = (url: string) => Promise<{ ok: boolean; status: number; contentType: string }>

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

const browserProbe: Prober = async (url) => {
  const r = await fetch(url, { cache: 'no-store', credentials: 'same-origin' })
  return { ok: r.ok, status: r.status, contentType: r.headers.get('content-type') ?? '' }
}

/** Les modules que l'import map du Desk désigne (`vue`, `@xcore-doctype/desk`), résolus comme le navigateur les résout. */
function importMapTargets(): [string, string][] {
  const tag = typeof document === 'undefined' ? null : document.querySelector('script[type="importmap"]')
  try {
    const imports = (JSON.parse(tag?.textContent ?? '{}') as { imports?: Record<string, string> }).imports ?? {}
    return Object.entries(imports).map(([name, target]) => [name, new URL(target, document.baseURI).href])
  } catch {
    return []
  }
}

/**
 * « Failed to fetch dynamically imported module » ne dit pas QUEL module manque : le point d'entrée de l'extension, ou un des modules
 * partagés qu'elle importe par l'import map. On les relit pour le dire. Un serveur de développement répond souvent 200 avec une page HTML
 * à une adresse inconnue : ce n'est pas un module, et c'est ce que le navigateur refuse.
 */
export async function diagnoseImport(url: string, probe: Prober = browserProbe): Promise<string | null> {
  const isModule = (contentType: string) => /javascript|ecmascript/i.test(contentType)
  const check = async (label: string, target: string): Promise<string | null> => {
    try {
      const r = await probe(target)
      if (!r.ok) return `${label} : HTTP ${r.status} (${target})`
      if (!isModule(r.contentType)) return `${label} : ${r.contentType || 'type inconnu'} au lieu d'un module JavaScript (${target})`
      return null
    } catch {
      return `${label} : injoignable (${target})`
    }
  }
  const entry = await check("point d'entrée", url)
  if (entry) return entry
  for (const [name, target] of importMapTargets()) {
    const found = await check(`import « ${name} »`, target)
    if (found) return found
  }
  return null
}

function within<T>(work: Promise<T>, ms: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`pas prête après ${ms / 1000} s`)), ms)
  })
  return Promise.race([work, late]).finally(() => clearTimeout(timer))
}

export async function loadExtensions(
  host: ExtensionHost,
  entries: readonly ExtensionEntry[],
  opts: { importer?: Importer; timeoutMs?: number; probe?: Prober } = {},
): Promise<void> {
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
      let reason = e instanceof Error ? e.message : String(e)
      // Un module introuvable : on cherche lequel (entrée ou import partagé), sans jamais faire échouer le démarrage pour autant.
      if (/dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(reason)) {
        const found = await diagnoseImport(entry.url, opts.probe).catch(() => null)
        if (found) reason = found
      }
      host.failures.push({ name: entry.name, reason })
      console.error(`[extension ${entry.name}] écartée`, e)
    }
  }
}
