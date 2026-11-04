import type { Transport } from './transport'

export interface DeskConfig {
  /** Racine de l'API, sans barre finale. */
  baseUrl: string
  /** Où envoyer e-mail et mot de passe ; `null` : le Desk demande un jeton collé (développement). */
  loginUrl: string | null
  /** Les extensions JavaScript que l'opérateur autorise (D28), URL absolues sur la même origine que le Desk. */
  extensions?: { name: string; url: string }[]
  /** Le mode développeur (constructeur de DocTypes) est ouvert par l'opérateur (`doctype.desk.developer_mode`). */
  developer?: boolean
  /** Le flux temps réel d'xpulse existe sur cet hôte (`config.json` `realtime`) : sans cette annonce, le Desk ne le demande pas
   * (un hôte sans xpulse répondrait 404 à chaque page, une erreur de console permanente). */
  realtime?: boolean
}

declare global {
  interface Window {
    /** Posé par un hôte (application desktop) qui connaît déjà l'URL du serveur. */
    __DESK_CONFIG__?: Partial<DeskConfig>
  }
}

const trimSlash = (s: string) => s.replace(/\/+$/, '')

/**
 * Les extensions annoncées par un `config.json`, gardées seulement si elles sont bien formées ET servies par l'origine de ce
 * `config.json` (`href`) — celle du Desk, ou celle de l'API quand le Desk est monté ailleurs et lit la liste du plugin ; la CSP
 * n'autorise que ces deux-là, on n'essaie même pas de charger autre chose.
 */
export function readExtensions(raw: unknown, href: string): { name: string; url: string }[] {
  if (!Array.isArray(raw)) return []
  const here = new URL(href)
  const out: { name: string; url: string }[] = []
  for (const e of raw) {
    if (!e || typeof e !== 'object') continue
    const { name, url } = e as { name?: unknown; url?: unknown }
    if (typeof name !== 'string' || !/^[a-z0-9_]+$/.test(name) || typeof url !== 'string') continue
    let full: URL
    try {
      full = new URL(url, here)
    } catch {
      continue
    }
    if (full.origin === here.origin && !out.some((x) => x.name === name)) out.push({ name, url: full.toString() })
  }
  return out
}

/**
 * xauth sert `…/auth/login`, `…/auth/refresh`, `…/auth/logout`, `…/auth/select-tenant` et `…/auth/auth/verify-mfa` sous un même préfixe : l'adresse de
 * connexion, seule configurée, donne les autres (pas de second réglage à tenir d'accord). `null` si elle n'a pas cette forme : rien de deviné.
 */
export function authUrl(loginUrl: string | null, action: string): string | null {
  return loginUrl && /\/login$/.test(loginUrl) ? loginUrl.replace(/\/login$/, `/${action}`) : null
}

function fromLocation(pathname: string): string {
  // « …/xcore-doctype/desk/ » -> « …/xcore-doctype »
  const i = pathname.lastIndexOf('/desk')
  return trimSlash(i >= 0 ? pathname.slice(0, i) : pathname)
}

interface ConfigBody {
  api_base?: unknown
  login_url?: unknown
  extensions?: unknown
  developer?: unknown
  realtime?: unknown
  /** Un Desk MONTÉ hors du plugin (`mount_desk`) : le `config.json` du plugin, seul à savoir ses extensions et si xpulse est là. */
  extensions_from?: unknown
}

/**
 * Un Desk monté ailleurs que dans le plugin (à la racine, sous `/erp`…) n'a pas la liste des extensions : il la lit dans le
 * `config.json` du PLUGIN, avec l'annonce du temps réel. Seulement si cette adresse est sous l'API annoncée (on ne suit pas un renvoi
 * vers n'importe où) ; injoignable : aucune extension, comme avant, mais le Desk démarre.
 */
async function fromPlugin(transport: Transport, raw: string, baseUrl: string, href: string): Promise<Pick<DeskConfig, 'extensions' | 'realtime'>> {
  let url: URL
  try {
    url = new URL(raw, href)
  } catch {
    return {}
  }
  const api = new URL(`${baseUrl}/`, href)
  if (url.origin !== api.origin || !url.pathname.startsWith(api.pathname)) return {}
  try {
    const res = await transport.request({ method: 'GET', url: url.toString(), headers: { Accept: 'application/json' } })
    const body = res.json as ConfigBody | null
    if (res.status !== 200 || !body) return {}
    return { extensions: readExtensions(body.extensions, url.toString()), realtime: body.realtime === true }
  } catch {
    return {}
  }
}

/**
 * Où est l'API ? Par ordre : l'hôte (application desktop) ; `config.json` servi à côté de l'application par le plugin, qui
 * connaît son préfixe ; à défaut, ce qu'on déduit de l'URL de la page. En développement (Vite), le plugin ne tourne pas à
 * côté : on retombe sur le chemin habituel, relayé par le proxy de Vite.
 */
export async function loadConfig(
  transport: Transport,
  where: { pathname: string; href: string },
  opts: { dev?: boolean } = {},
): Promise<DeskConfig> {
  const host = typeof window !== 'undefined' ? window.__DESK_CONFIG__ : undefined
  if (host?.baseUrl) return { baseUrl: trimSlash(host.baseUrl), loginUrl: host.loginUrl ?? null }

  try {
    const res = await transport.request({ method: 'GET', url: new URL('config.json', where.href).toString(), headers: { Accept: 'application/json' } })
    const body = res.json as ConfigBody | null
    if (res.status === 200 && body && typeof body.api_base === 'string') {
      const config: DeskConfig = {
        baseUrl: trimSlash(body.api_base),
        loginUrl: typeof body.login_url === 'string' ? body.login_url : null,
        extensions: readExtensions(body.extensions, where.href),
        developer: body.developer === true,
        realtime: body.realtime === true,
      }
      if (typeof body.extensions_from === 'string') Object.assign(config, await fromPlugin(transport, body.extensions_from, config.baseUrl, where.href))
      return config
    }
  } catch {
    // injoignable ou pas du JSON : on retombe sur le défaut
  }
  return { baseUrl: opts.dev ? '/app/xcore-doctype' : fromLocation(where.pathname), loginUrl: null }
}
