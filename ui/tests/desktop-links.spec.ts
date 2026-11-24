/**
 * La WebView ne navigue jamais hors de l'application (`desktop/links.ts`), les liens profonds sont un vocabulaire fermé
 * (`desktop/deeplink.ts`), et l'hôte de bureau refuse ce qu'il n'ouvre pas (`desktop/host.ts`).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readDeepLink } from '@/desktop/deeplink'
import { desktopHost, type DesktopShell } from '@/desktop/host'
import { installLinkGuard, isExternal, linkVerdict } from '@/desktop/links'
import { memoryVaultHub, Vault } from '@/desktop/vault'
import { describeError } from '@/composables/useNotify'
import { HostRefusal } from '@/lib/host'
import type { HttpRequest, Transport } from '@/lib/transport'

const PAGE = 'tauri://localhost/#/app/Facture'
const link = (href: string, extra: { download?: string | null; target?: string } = {}) => linkVerdict({ href, download: extra.download ?? null, target: extra.target ?? '' }, PAGE)

describe('le jugement d’un lien', () => {
  it('une route du Desk reste dans la WebView', () => {
    expect(link('#/app/Client/C1')).toEqual({ kind: 'stay' })
    expect(link('tauri://localhost/#/settings')).toEqual({ kind: 'stay' })
  })

  it('une route du Desk en « nouvel onglet » ouvre une FENÊTRE de l’application', () => {
    expect(link('#/app/Client/new', { target: '_blank' })).toEqual({ kind: 'window', route: '/app/Client/new' })
    expect(link('#', { target: '_blank' })).toEqual({ kind: 'window', route: '/' })
  })

  it('une page web, un courriel, un numéro : le système les ouvre', () => {
    expect(link('https://checkout.stripe.com/c/pay/cs_1')).toEqual({ kind: 'external', url: 'https://checkout.stripe.com/c/pay/cs_1' })
    expect(link('https://erp.exemple.fr/docs', { target: '_blank' })).toEqual({ kind: 'external', url: 'https://erp.exemple.fr/docs' })
    expect(link('mailto:compta@exemple.fr')).toEqual({ kind: 'external', url: 'mailto:compta@exemple.fr' })
    expect(link('tel:+2250700000000')).toEqual({ kind: 'external', url: 'tel:+2250700000000' })
  })

  it('un lien de fichier (`download`) est téléchargé puis enregistré, sous le nom proposé', () => {
    expect(link('https://erp.exemple.fr/app/xcore-doctype/files/f1?sig=abc', { download: 'contrat.pdf' })).toEqual({
      kind: 'download',
      url: 'https://erp.exemple.fr/app/xcore-doctype/files/f1?sig=abc',
      filename: 'contrat.pdf',
    })
    expect(link('https://erp.exemple.fr/files/re%C3%A7u.pdf', { download: '' })).toMatchObject({ kind: 'download', filename: 'reçu.pdf' })
  })

  it('une autre page servie par l’origine de l’application n’est pas une page du Desk', () => {
    expect(link('tauri://localhost/autre.html')).toEqual({ kind: 'refused', reason: 'schéma « tauri: » refusé' })
  })

  it.each(['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,<b>x</b>', 'erp://oauth-callback?access_token=x', 'smb://serveur/partage'])('refuse %s, avec sa raison', (href) => {
    const verdict = link(href)
    expect(verdict.kind).toBe('refused')
    expect((verdict as { reason: string }).reason).toMatch(/schéma « .+ » refusé/)
  })

  it('`isExternal` : ce que le système a le droit d’ouvrir', () => {
    expect(['https://a.b/', 'http://a.b/', 'mailto:a@b.c', 'tel:1'].every(isExternal)).toBe(true)
    expect(['file:///x', 'javascript:1', 'erp://x', '/relatif', ''].some(isExternal)).toBe(false)
  })
})

describe('le garde posé sur la page', () => {
  function page() {
    const calls: string[] = []
    const reports: string[] = []
    const host = {
      leave: vi.fn(async (url: string) => void calls.push(`leave ${url}`)),
      openWindow: vi.fn(async (route: string) => void calls.push(`window ${route}`)),
      saveUrl: vi.fn(async (url: string, name: string) => (calls.push(`save ${url} ${name}`), 'saved' as const)),
    }
    const view = { open: () => null } as unknown as Window
    const stop = installLinkGuard(document, view, { host: host as never, page: () => PAGE, report: (m) => reports.push(m) })
    const click = (html: string, type = 'click') => {
      document.body.innerHTML = html
      const event = new MouseEvent(type, { bubbles: true, cancelable: true })
      document.body.querySelector('[data-click]')!.dispatchEvent(event)
      return event
    }
    return { calls, reports, click, view, stop, host }
  }
  let stop: (() => void) | undefined
  afterEach(() => {
    stop?.()
    document.body.innerHTML = ''
  })

  it('un lien externe ne navigue pas : le système l’ouvre', () => {
    const p = page()
    stop = p.stop
    const event = p.click('<a data-click href="https://docs.exemple.fr/api" target="_blank">doc</a>')
    expect(event.defaultPrevented).toBe(true)
    expect(p.calls).toEqual(['leave https://docs.exemple.fr/api'])
  })

  it('le clic sur un élément À L’INTÉRIEUR du lien est jugé comme le lien', () => {
    const p = page()
    stop = p.stop
    p.click('<a href="https://erp.exemple.fr/files/f1" download="note.txt"><span data-click>note</span></a>')
    expect(p.calls).toEqual(['save https://erp.exemple.fr/files/f1 note.txt'])
  })

  it('une route du Desk est laissée au routeur ; en « nouvel onglet », une fenêtre', () => {
    const p = page()
    stop = p.stop
    expect(p.click('<a data-click href="#/app/Client/C1">C1</a>').defaultPrevented).toBe(false)
    expect(p.click('<a data-click href="#/app/Client/new" target="_blank">+</a>').defaultPrevented).toBe(true)
    expect(p.click('<a data-click href="#/app/Client/C2" target="_blank">+</a>', 'auxclick').defaultPrevented).toBe(true)
    expect(p.calls).toEqual(['window /app/Client/new', 'window /app/Client/C2'])
  })

  it('ce qu’un écran a déjà traité n’est pas traité deux fois', () => {
    const p = page()
    stop = p.stop
    document.body.innerHTML = '<a data-click href="https://erp.exemple.fr/files/f1" download="a.pdf">a</a>'
    const anchor = document.body.querySelector('a')!
    anchor.addEventListener('click', (e) => e.preventDefault()) // FilesView redemande un lien signé frais et enregistre lui-même
    anchor.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    expect(p.calls).toEqual([])
  })

  it('un lien refusé est dit, et ne navigue pas', () => {
    const p = page()
    stop = p.stop
    expect(p.click('<a data-click href="file:///etc/passwd">x</a>').defaultPrevented).toBe(true)
    expect(p.calls).toEqual([])
    expect(p.reports).toEqual(['Lien non suivi : schéma « file: » refusé'])
  })

  it('`window.open` passe par le même jugement et ne rend jamais de fenêtre à piloter', () => {
    const p = page()
    stop = p.stop
    expect(p.view.open('https://portail.exemple/billing')).toBeNull()
    expect(p.view.open('javascript:alert(1)')).toBeNull()
    expect(p.calls).toEqual(['leave https://portail.exemple/billing'])
    expect(p.reports).toHaveLength(1)
  })

  it('un geste de l’hôte qui échoue est rapporté, pas avalé', async () => {
    const p = page()
    stop = p.stop
    p.host.saveUrl.mockRejectedValueOnce(new HostRefusal('Téléchargement refusé par le serveur (HTTP 403)'))
    p.click('<a data-click href="https://erp.exemple.fr/files/f1" download="a.pdf">a</a>')
    await new Promise((r) => setTimeout(r, 0))
    expect(p.reports).toEqual(['Téléchargement refusé par le serveur (HTTP 403)'])
  })
})

describe('les liens profonds', () => {
  it('`erp://oauth-callback?…` : un retour OAuth, ses paramètres tels quels (c’est la session qui juge le nonce)', () => {
    expect(readDeepLink('erp://oauth-callback?access_token=a.b&refresh_token=r1&user_id=u&app_state=n0nce')).toEqual({
      kind: 'oauth',
      query: { access_token: 'a.b', refresh_token: 'r1', user_id: 'u', app_state: 'n0nce' },
    })
    expect(readDeepLink('erp://oauth-callback/?mfa_required=true&mfa_token=d%C3%A9fi')).toEqual({ kind: 'oauth', query: { mfa_required: 'true', mfa_token: 'défi' } })
  })

  it('`erp://invite?token=…` mène à l’écran d’invitation, et n’emporte RIEN d’autre que le jeton', () => {
    expect(readDeepLink('erp://invite?token=inv-1&next=https://evil.example')).toEqual({ kind: 'route', path: '/invite', query: { token: 'inv-1' } })
    expect(readDeepLink('erp://invite')).toEqual({ kind: 'refused', reason: 'invitation sans jeton' })
  })

  it.each([
    ['erp://oauth-link?success=true', /non prise en charge/],
    ['erp://app/Facture/FA-1', /non prise en charge/],
    ['https://erp.exemple.fr/#/oauth?access_token=x', /schéma « https: » inconnu/],
    ['pas une adresse', /illisible/],
  ])('refuse %s', (raw, cause) => {
    const out = readDeepLink(raw)
    expect(out.kind).toBe('refused')
    expect((out as { reason: string }).reason).toMatch(cause)
  })
})

describe('l’hôte de bureau', () => {
  async function host(reply: (req: HttpRequest) => { status: number; json: unknown; bytes?: Uint8Array }, save = true) {
    const calls: unknown[][] = []
    const shell: DesktopShell = {
      openExternal: async (url) => void calls.push(['open', url]),
      saveFile: async (name, bytes) => (calls.push(['save', name, new TextDecoder().decode(bytes)]), save),
      openWindow: async (route) => void calls.push(['window', route]),
    }
    const sent: HttpRequest[] = []
    const transport: Transport = { request: async (req) => (sent.push(req), reply(req)) }
    const vault = await Vault.open(memoryVaultHub().backend('main'), 'main')
    return { host: desktopHost({ vault, shell, transport, unavailable: { extensions: 'fermées' } }), calls, sent }
  }

  it('une sortie (paiement, fournisseur d’identité) part vers le navigateur du système', async () => {
    const { host: h, calls } = await host(() => ({ status: 200, json: null }))
    expect(h.kind).toBe('desktop')
    await h.leave('https://checkout.stripe.com/c/pay/cs_1')
    expect(calls).toEqual([['open', 'https://checkout.stripe.com/c/pay/cs_1']])
  })

  it('refuse d’ouvrir ce qui n’est ni web, ni courriel, ni numéro — un refus écrit pour être lu', async () => {
    const { host: h, calls } = await host(() => ({ status: 200, json: null }))
    for (const bad of ['file:///etc/passwd', 'javascript:alert(1)', 'erp://oauth-callback', '/app/x']) {
      const failure = await h.leave(bad).catch((e: unknown) => e)
      expect(failure, bad).toBeInstanceOf(HostRefusal)
      expect(describeError(failure)).toContain(bad)
    }
    await expect(h.openWindow('https://evil.example')).rejects.toBeInstanceOf(HostRefusal)
    expect(calls).toEqual([])
  })

  it('enregistrer : les octets vont au dialogue natif ; le refermer n’est pas une erreur', async () => {
    const ok = await host(() => ({ status: 200, json: null }))
    expect(await ok.host.saveText('export.csv', 'a;b\n1;2\n')).toBe('saved')
    expect(await ok.host.saveBytes('FA-1.pdf', new TextEncoder().encode('%PDF'), 'application/pdf')).toBe('saved')
    expect(ok.calls).toEqual([['save', 'export.csv', 'a;b\n1;2\n'], ['save', 'FA-1.pdf', '%PDF']])
    const closed = await host(() => ({ status: 200, json: null }), false)
    expect(await closed.host.saveText('export.csv', 'x')).toBe('cancelled')
  })

  it('un lien de fichier est LU par le transport (donc sur le serveur configuré), puis enregistré', async () => {
    const { host: h, calls, sent } = await host(() => ({ status: 200, json: null, bytes: new TextEncoder().encode('contenu') }))
    expect(await h.saveUrl('https://erp.exemple.fr/app/xcore-doctype/files/f1?sig=abc', 'note.txt')).toBe('saved')
    expect(sent[0]).toMatchObject({ method: 'GET', url: 'https://erp.exemple.fr/app/xcore-doctype/files/f1?sig=abc', binary: true })
    expect(calls).toEqual([['save', 'note.txt', 'contenu']])
  })

  it('un lien de fichier expiré ou refusé est dit avec son statut, rien n’est enregistré', async () => {
    const { host: h, calls } = await host(() => ({ status: 403, json: { detail: 'expired' } }))
    const failure = await h.saveUrl('https://erp.exemple.fr/files/f1', 'note.txt').catch((e: unknown) => e)
    expect(failure).toBeInstanceOf(HostRefusal)
    expect(describeError(failure)).toContain('HTTP 403')
    expect(calls).toEqual([])
  })
})
