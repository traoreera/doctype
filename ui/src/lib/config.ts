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
  /** xflow est chargé (`config.json` `automations`) : Paramètres › Automatisations existe. */
  automations?: boolean
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
  automations?: unknown
  /** Un Desk MONTÉ hors du plugin (`mount_desk`) : le `config.json` du plugin, seul à savoir ses extensions et si xpulse est là. */
  extensions_from?: unknown
}

/**
 * Un Desk monté ailleurs que dans le plugin (à la racine, sous `/erp`…) n'a pas la liste des extensions : il la lit dans le
 * `config.json` du PLUGIN, avec l'annonce du temps réel. Seulement si cette adresse est sous l'API annoncée (on ne suit pas un renvoi
 * vers n'importe où) ; injoignable : aucune extension, comme avant, mais le Desk démarre.
 */
async function fromPlugin(transport: Transport, raw: string, baseUrl: string, href: string): Promise<Pick<DeskConfig, 'extensions' | 'realtime' | 'automations'>> {
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
    return { extensions: readExtensions(body.extensions, url.toString()), realtime: body.realtime === true, automations: body.automations === true }
  } catch {
    return {}
  }
}

/**
 * Les extensions déclarées par un hôte, relues sous l'origine de l'API qu'il annonce (absolue si l'hôte l'a donnée ainsi,
 * sinon résolue contre la page) : en bureau, la page n'est pas sur une origine http et c'est l'API qui les sert, comme pour
 * un Desk monté ailleurs (`fromPlugin`). Une adresse de page inutilisable ne doit pas empêcher le Desk de démarrer : aucune
 * extension alors, comme un plugin injoignable.
 */
function hostExtensions(raw: unknown, baseUrl: string, href: string): { name: string; url: string }[] {
  try {
    return readExtensions(raw, new URL(`${baseUrl}/`, href).toString())
  } catch {
    return []
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
  if (host?.baseUrl) {
    // L'hôte annonce ses CAPACITÉS comme un `config.json` le fait : sans elles, un Desk embarqué démarrait sans extension,
    // sans temps réel, sans automatisations et sans mode développeur, quoi que l'hôte ait déclaré.
    const baseUrl = trimSlash(host.baseUrl)
    return {
      baseUrl,
      loginUrl: host.loginUrl ?? null,
      extensions: hostExtensions(host.extensions, baseUrl, where.href),
      developer: host.developer === true,
      realtime: host.realtime === true,
      automations: host.automations === true,
    }
  }

  try {
    const served = await readServedConfig(transport, where.href)
    if (served) return served
  } catch {
    // injoignable ou pas du JSON : on retombe sur le défaut
  }
  return { baseUrl: opts.dev ? '/app/xcore-doctype' : fromLocation(where.pathname), loginUrl: null }
}

/**
 * Le `config.json` servi à côté du Desk dont `href` est l'adresse, tel que le plugin (ou un montage) l'annonce ; `null` s'il n'y en a
 * pas ou s'il ne décrit pas une API. Laisse passer une `NetworkError` : le navigateur retombe sur son défaut (`loadConfig`), une
 * application de bureau, qui n'a pas d'URL de page d'où deviner, s'arrête en disant pourquoi (`desktop/config.loadServerConfig`).
 */
export async function readServedConfig(transport: Transport, href: string): Promise<DeskConfig | null> {
  const res = await transport.request({ method: 'GET', url: new URL('config.json', href).toString(), headers: { Accept: 'application/json' } })
  const body = res.json as ConfigBody | null
  if (res.status !== 200 || !body || typeof body.api_base !== 'string') return null
  const config: DeskConfig = {
    baseUrl: trimSlash(body.api_base),
    loginUrl: typeof body.login_url === 'string' ? body.login_url : null,
    extensions: readExtensions(body.extensions, href),
    developer: body.developer === true,
    realtime: body.realtime === true,
    automations: body.automations === true,
  }
  if (typeof body.extensions_from === 'string') Object.assign(config, await fromPlugin(transport, body.extensions_from, config.baseUrl, href))
  return config
}
