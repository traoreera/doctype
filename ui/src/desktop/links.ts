/**
 * La WebView ne navigue JAMAIS hors de l'application : la politique des liens du bureau.
 *
 * Dans un navigateur, un lien externe ouvre un onglet et un `<a download>` enregistre un fichier. Dans une WebView, le même clic
 * remplacerait le Desk par la page visée — sans barre d'adresse ni retour, et avec les ponts de l'application à portée de cette page.
 * Chaque clic sur un lien que personne n'a traité est donc JUGÉ ici (`linkVerdict`), vocabulaire fermé :
 *
 * | Le lien | Ce qui se passe |
 * |---|---|
 * | une route du Desk (`#/…`) | la WebView la suit (`stay`) |
 * | une route du Desk en `target="_blank"` | une autre fenêtre de l'application (`window`) |
 * | http(s) ailleurs, avec `download` | téléchargé par le transport, enregistré par le dialogue natif (`download`) |
 * | http(s) ailleurs, `mailto:`, `tel:` | ouvert par le système : navigateur, messagerie (`external`) |
 * | tout autre schéma (`javascript:`, `file:`, `data:`…) | refusé, avec sa raison (`refused`) |
 *
 * Le processus Rust refuse de son côté toute navigation de la WebView hors de l'application (`src-tauri/src/lib.rs`) : ce module est
 * le confort (le bon geste à la place), l'autre est le verrou.
 */
import type { DeskHost } from '@/lib/host'

export type LinkVerdict =
  | { kind: 'stay' }
  | { kind: 'window'; route: string }
  | { kind: 'download'; url: string; filename: string }
  | { kind: 'external'; url: string }
  | { kind: 'refused'; reason: string }

export interface LinkFacts {
  href: string
  /** L'attribut `download` : `null` s'il est absent, `''` s'il est présent sans nom. */
  download: string | null
  target: string
}

const SYSTEM_SCHEMES = new Set(['http:', 'https:', 'mailto:', 'tel:'])

/** Ce que le système a le droit d'ouvrir : une page web, un courriel, un numéro. Jamais un fichier local ni un autre programme. */
export function isExternal(raw: string): boolean {
  try {
    return SYSTEM_SCHEMES.has(new URL(raw).protocol)
  } catch {
    return false
  }
}

function filenameOf(url: URL, download: string): string {
  const last = decodeURIComponent(url.pathname.split('/').pop() ?? '')
  return download || last || 'fichier'
}

/** `page` : l'adresse de la page de l'application (`location.href`), contre laquelle un lien relatif se résout. */
export function linkVerdict(link: LinkFacts, page: string): LinkVerdict {
  let url: URL
  const here = new URL(page)
  try {
    url = new URL(link.href, here)
  } catch {
    return { kind: 'refused', reason: 'adresse illisible' }
  }
  // La page de l'application elle-même : seule sa partie « # » change (le routeur est en mode hash).
  if (url.origin === here.origin && url.pathname === here.pathname && url.protocol === here.protocol) {
    if (link.target === '_blank') return { kind: 'window', route: url.hash.replace(/^#/, '') || '/' }
    return { kind: 'stay' }
  }
  // Un fichier fabriqué en mémoire par la page (jamais du réseau) : la WebView s'en charge, il ne mène nulle part.
  if (url.protocol === 'blob:' && link.download !== null) return { kind: 'stay' }
  if (url.protocol === 'http:' || url.protocol === 'https:') {
    // Une autre adresse http(s) sur l'origine de l'application (ses propres fichiers) n'est pas une page du Desk : dehors, comme le reste.
    if (link.download !== null) return { kind: 'download', url: url.toString(), filename: filenameOf(url, link.download) }
    return { kind: 'external', url: url.toString() }
  }
  if (SYSTEM_SCHEMES.has(url.protocol)) return { kind: 'external', url: url.toString() }
  return { kind: 'refused', reason: `schéma « ${url.protocol} » refusé` }
}

export interface LinkGuardOptions {
  host: DeskHost
  /** L'adresse courante de la page (relue à chaque clic). */
  page: () => string
  /** Un lien refusé ou un geste raté : dit à l'utilisateur, jamais avalé. */
  report: (message: string, cause?: unknown) => void
}

/**
 * Pose le garde sur un document. Il écoute en phase de REMONTÉE et laisse ce qu'un écran a déjà traité (`defaultPrevented`) : il ne
 * rattrape que ce qui, sans lui, ferait naviguer la WebView. `window.open` passe par le même jugement.
 */
export function installLinkGuard(doc: Document, view: Window, opts: LinkGuardOptions): () => void {
  const act = (verdict: LinkVerdict): void => {
    const failed = (e: unknown) => opts.report(e instanceof Error ? e.message : String(e), e)
    if (verdict.kind === 'window') void opts.host.openWindow(verdict.route).catch(failed)
    else if (verdict.kind === 'download') void opts.host.saveUrl(verdict.url, verdict.filename).catch(failed)
    else if (verdict.kind === 'external') void opts.host.leave(verdict.url).catch(failed)
    else if (verdict.kind === 'refused') opts.report(`Lien non suivi : ${verdict.reason}`)
  }
  const onClick = (e: Event): void => {
    if (e.defaultPrevented) return
    const path = typeof e.composedPath === 'function' ? e.composedPath() : [e.target]
    const anchor = path.find((n): n is HTMLAnchorElement => !!n && (n as Element).tagName === 'A' && (n as Element).hasAttribute?.('href'))
    if (!anchor) return
    const verdict = linkVerdict({ href: anchor.getAttribute('href') ?? '', download: anchor.getAttribute('download'), target: anchor.getAttribute('target') ?? '' }, opts.page())
    if (verdict.kind === 'stay') return
    e.preventDefault()
    act(verdict)
  }
  doc.addEventListener('click', onClick)
  doc.addEventListener('auxclick', onClick) // le clic du milieu ouvre « dans un nouvel onglet » : même jugement
  const nativeOpen = view.open
  view.open = ((target?: string | URL) => {
    const verdict = linkVerdict({ href: String(target ?? ''), download: null, target: '_blank' }, opts.page())
    act(verdict.kind === 'stay' ? { kind: 'window', route: '/' } : verdict)
    return null
  }) as typeof view.open
  return () => {
    doc.removeEventListener('click', onClick)
    doc.removeEventListener('auxclick', onClick)
    view.open = nativeOpen
  }
}
