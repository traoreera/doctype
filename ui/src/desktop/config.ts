/**
 * La configuration de l'application de bureau : UNE adresse, celle du Desk que le serveur sert, et deux interrupteurs.
 *
 * ```json
 * { "server": "https://erp.exemple.fr/app/xcore-doctype/desk/", "extensions": false, "realtime": true }
 * ```
 *
 * Tout le reste — racine de l'API, adresse de connexion, extensions autorisées, temps réel, automatisations, mode développeur — est LU
 * chez ce serveur, dans le même `config.json` que le Desk web (`api/desk.py`). L'hôte ne redéclare rien : il n'y a pas deux listes
 * à tenir d'accord, et l'opérateur du serveur reste seul à décider de ce que son Desk annonce. L'hôte ne peut que RETIRER (`extensions`,
 * `realtime` à `false`), jamais ajouter.
 *
 * Un fichier mal formé, une adresse refusée ou un serveur muet ARRÊTENT le démarrage avec leur cause (`ConfigRefusal`) : contrairement
 * au navigateur, il n'y a pas d'URL de page d'où deviner l'API.
 */
import { readServedConfig, type DeskConfig } from '@/lib/config'
import type { HostCapability } from '@/lib/host'
import { NetworkError, type Transport } from '@/lib/transport'

export interface DesktopConfig {
  /** L'adresse du Desk servi par le plugin, barre finale comprise : `config.json` est à côté. */
  server: string
  /** Charger les extensions JavaScript que le serveur annonce (D28). Faux par défaut : elles exigent CSP et CORS (docs/desktop.md). */
  extensions: boolean
  /** Tenir le flux temps réel d'xpulse quand le serveur l'annonce. Vrai par défaut. */
  realtime: boolean
}

export class ConfigRefusal extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConfigRefusal'
  }
}

const KEYS = ['server', 'extensions', 'realtime'] as const
const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]'])

/** http en clair seulement vers la machine elle-même (développement) : ailleurs, mot de passe et jetons voyageraient lisibles. */
export function serverAddress(raw: string, what = 'server'): URL {
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    throw new ConfigRefusal(`${what} : « ${raw} » n'est pas une adresse absolue`)
  }
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOOPBACK.has(url.hostname))) {
    throw new ConfigRefusal(`${what} : ${url.protocol}//${url.host} refusé — https obligatoire (http n'est admis que vers localhost, 127.0.0.1 ou [::1])`)
  }
  if (url.username || url.password) throw new ConfigRefusal(`${what} : une adresse ne porte pas d'identifiants`)
  return url
}

/** Lit `desk.json` tel que l'hôte le rend (un texte JSON, ou déjà un objet). Vocabulaire fermé : une clé inconnue est une faute de frappe, pas un réglage ignoré. */
export function readDesktopConfig(raw: unknown): DesktopConfig {
  let value: unknown = raw
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw)
    } catch {
      throw new ConfigRefusal("desk.json n'est pas du JSON")
    }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ConfigRefusal('desk.json doit être un objet { "server": … }')
  const body = value as Record<string, unknown>
  const unknown = Object.keys(body).filter((k) => !(KEYS as readonly string[]).includes(k))
  if (unknown.length) throw new ConfigRefusal(`desk.json : clé inconnue ${unknown.map((k) => `« ${k} »`).join(', ')} (attendues : ${KEYS.join(', ')})`)
  if (typeof body.server !== 'string' || !body.server.trim()) throw new ConfigRefusal('desk.json : « server » manque (l\'adresse du Desk du serveur, par exemple https://erp.exemple.fr/app/xcore-doctype/desk/)')
  const url = serverAddress(body.server.trim())
  if (url.search || url.hash) throw new ConfigRefusal('server : ni « ? » ni « # » dans l\'adresse du Desk')
  for (const key of ['extensions', 'realtime'] as const) {
    if (body[key] !== undefined && typeof body[key] !== 'boolean') throw new ConfigRefusal(`desk.json : « ${key} » vaut true ou false`)
  }
  if (!url.pathname.endsWith('/')) url.pathname += '/'
  return { server: url.toString(), extensions: body.extensions === true, realtime: body.realtime !== false }
}

/** Ce que la configuration de l'application retire à l'hôte, avec la raison que l'utilisateur lira. */
export function disabledByConfig(desktop: DesktopConfig): Partial<Record<HostCapability, string>> {
  const out: Partial<Record<HostCapability, string>> = {}
  if (!desktop.extensions) out.extensions = "désactivées dans l'application de bureau (desk.json : extensions)"
  if (!desktop.realtime) out.realtime = "désactivé dans l'application de bureau (desk.json : realtime)"
  return out
}

/**
 * La configuration du Desk, lue chez le serveur et rendue ABSOLUE (la page de l'application n'a pas d'origine http à laquelle rapporter
 * `/app/xcore-doctype`). L'API et la connexion annoncées passent la même règle que `server` : https, ou la machine elle-même.
 */
export async function loadServerConfig(transport: Transport, desktop: DesktopConfig): Promise<DeskConfig> {
  let served: DeskConfig | null
  try {
    served = await readServedConfig(transport, desktop.server)
  } catch (e) {
    if (e instanceof NetworkError) throw new ConfigRefusal(`Le serveur ne répond pas (${desktop.server}config.json) : ${e.message}`)
    throw e
  }
  if (!served) throw new ConfigRefusal(`${desktop.server}config.json ne décrit pas un Desk (réponse absente, illisible ou sans « api_base ») : vérifiez l'adresse du serveur`)
  const baseUrl = serverAddress(new URL(`${served.baseUrl}/`, desktop.server).toString(), 'api_base annoncé par le serveur')
  const loginUrl = served.loginUrl === null ? null : serverAddress(new URL(served.loginUrl, desktop.server).toString(), 'login_url annoncé par le serveur').toString()
  return { ...served, baseUrl: baseUrl.toString().replace(/\/+$/, ''), loginUrl }
}

/** Les origines que le transport natif a le droit de joindre : le serveur, son API, sa connexion. */
export function allowedOrigins(desktop: DesktopConfig, config?: DeskConfig): string[] {
  const out = new Set([new URL(desktop.server).origin])
  if (config) {
    out.add(new URL(config.baseUrl).origin)
    if (config.loginUrl) out.add(new URL(config.loginUrl).origin)
  }
  return [...out]
}
