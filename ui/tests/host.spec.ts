/**
 * Le contrat d'HÔTE (`lib/host.ts`, traoreera/doctype#73). Deux choses à tenir : (1) le navigateur garde EXACTEMENT son comportement
 * (jetons dans `sessionStorage`, sortie par navigation de l'onglet, fichiers par `<a download>`) ; (2) ce qu'un hôte dit ne pas savoir
 * faire est désactivé une fois, dans `createDesk`, et annoncé — jamais tenté puis raté.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createDesk } from '@/desk'
import { transportProbe } from '@/extensions/loader'
import { browserHost, HOST_CAPABILITIES, memoryKeyValue, type DeskHost } from '@/lib/host'
import type { HttpRequest, HttpResponse, Transport } from '@/lib/transport'

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})
afterEach(() => vi.unstubAllGlobals())

describe('le navigateur : le comportement d’avant, derrière le contrat', () => {
  it('les jetons et le nonce OAuth vivent dans le sessionStorage de l’onglet, sous leurs clés de toujours', () => {
    const host = browserHost()
    expect(host.kind).toBe('web')
    host.session.set('xdt.desk.token', 'abc')
    host.flow.set('xdt.desk.oauth', 'nonce')
    expect(sessionStorage.getItem('xdt.desk.token')).toBe('abc')
    expect(sessionStorage.getItem('xdt.desk.oauth')).toBe('nonce')
    expect(host.session.get('xdt.desk.token')).toBe('abc')
    host.session.set('xdt.desk.token', null)
    expect(sessionStorage.getItem('xdt.desk.token')).toBeNull()
    expect(localStorage.length).toBe(0)
    // Un onglet n'a pas d'autre fenêtre à suivre : pas d'abonnement, le magasin de session ne s'y branche pas.
    expect(host.session.subscribe).toBeUndefined()
  })

  it('un stockage refusé (navigation privée) ne casse rien : la session ne survit pas au rechargement, c’est tout', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => {
        throw new DOMException('refusé', 'SecurityError')
      },
      setItem: () => {
        throw new DOMException('refusé', 'SecurityError')
      },
      removeItem: () => {
        throw new DOMException('refusé', 'SecurityError')
      },
    })
    const host = browserHost()
    expect(() => host.session.set('k', 'v')).not.toThrow()
    expect(host.session.get('k')).toBeNull()
  })

  it('aucune capacité ne manque, le retour OAuth est la page `#/oauth` du Desk, tolérante comme avant', () => {
    const host = browserHost()
    expect(host.unavailable).toEqual({})
    expect(host.oauth).toEqual({ redirect: null, strict: false })
  })

  it('une sortie (paiement, fournisseur d’identité) est une navigation de l’onglet', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    await browserHost().leave('https://checkout.stripe.com/c/pay/cs_1')
    expect(assign).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay/cs_1')
  })

  it('enregistrer un fichier est un `<a download>` cliqué ; le navigateur ne dit jamais « annulé »', async () => {
    const clicked: { href: string; download: string }[] = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download })
    })
    try {
      const host = browserHost()
      expect(await host.saveText('export.csv', 'a;b')).toBe('saved')
      expect(await host.saveBytes('FA-1.pdf', new Uint8Array([1, 2]), 'application/pdf')).toBe('saved')
      expect(await host.saveUrl('https://erp.exemple.fr/files/f1?sig=x', 'note.txt')).toBe('saved')
      expect(clicked.map((c) => c.download)).toEqual(['export.csv', 'FA-1.pdf', 'note.txt'])
      expect(clicked[0]!.href).toMatch(/^blob:/)
      expect(clicked[2]!.href).toBe('https://erp.exemple.fr/files/f1?sig=x')
    } finally {
      click.mockRestore()
    }
  })

  it('`memoryKeyValue` : le stockage d’une fenêtre de bureau, vide = absent', () => {
    const kv = memoryKeyValue()
    kv.set('a', '1')
    expect(kv.get('a')).toBe('1')
    kv.set('a', null)
    expect(kv.get('a')).toBeNull()
  })
})

describe('createDesk désactive et annonce ce que l’hôte n’offre pas', () => {
  const sent: HttpRequest[] = []
  const transport: Transport = {
    async request(req): Promise<HttpResponse> {
      sent.push(req)
      return { status: 404, json: null }
    },
    async stream() {
      return { status: 404 }
    },
  }
  const config = {
    baseUrl: 'https://erp.exemple.fr/app/xcore-doctype',
    loginUrl: 'https://erp.exemple.fr/app/auth/login',
    realtime: true,
    extensions: [
      { name: 'sales', url: 'https://erp.exemple.fr/app/xcore-doctype/desk/ext/sales/index.js' },
      { name: 'crm', url: 'https://erp.exemple.fr/app/xcore-doctype/desk/ext/crm/index.js' },
    ],
  }
  const hostWith = (unavailable: DeskHost['unavailable']): DeskHost => ({ ...browserHost(), kind: 'desktop', session: memoryKeyValue(), flow: memoryKeyValue(), unavailable })

  it('sans rien dire, l’hôte est le navigateur : temps réel servi, extensions chargées', async () => {
    const desk = createDesk(transport, config)
    expect(desk.unavailable).toEqual([])
    expect(desk.services.host?.kind).toBe('web')
    expect(desk.services.realtime).not.toBeNull()
    const imported: string[] = []
    await desk.loadExtensions(async (url) => {
      imported.push(url)
      return { default: { name: url.includes('sales') ? 'sales' : 'crm', apiVersion: 1, setup() {} } }
    })
    expect(imported).toHaveLength(2)
    expect(desk.extensions.loaded).toEqual(['sales', 'crm'])
  })

  it('extensions indisponibles : AUCUN module n’est importé, chacune est écartée avec la raison de l’hôte', async () => {
    const desk = createDesk(transport, config, hostWith({ extensions: "désactivées dans l'application de bureau (desk.json : extensions)" }))
    let imported = 0
    await desk.loadExtensions(async () => {
      imported += 1
      return {}
    })
    expect(imported).toBe(0)
    expect(desk.extensions.loaded).toEqual([])
    // La même liste que pour un module qui ne se charge pas : `AppShell` la montre à l'utilisateur au démarrage.
    expect(desk.extensions.failures).toEqual([
      { name: 'sales', reason: "désactivées dans l'application de bureau (desk.json : extensions)" },
      { name: 'crm', reason: "désactivées dans l'application de bureau (desk.json : extensions)" },
    ])
    expect(desk.unavailable).toEqual([{ capability: 'extensions', reason: "désactivées dans l'application de bureau (desk.json : extensions)" }])
  })

  it('temps réel indisponible : aucun client, donc aucune requête vers le flux — même si le serveur l’annonce', () => {
    const desk = createDesk(transport, config, hostWith({ realtime: 'désactivé' }))
    expect(desk.services.realtime).toBeNull()
    expect(desk.unavailable).toEqual([{ capability: 'realtime', reason: 'désactivé' }])
  })

  it('le vocabulaire des capacités est fermé, et annoncé dans son ordre', () => {
    expect(HOST_CAPABILITIES).toEqual(['extensions', 'realtime', 'windows'])
    const desk = createDesk(transport, config, hostWith({ windows: 'w', extensions: 'e', realtime: 'r' }))
    expect(desk.unavailable.map((u) => u.capability)).toEqual(['extensions', 'realtime', 'windows'])
  })
})

describe('le diagnostic d’une extension lit par le transport, pas par un `fetch` direct', () => {
  it('un module servi : statut et type de contenu', async () => {
    const sent: HttpRequest[] = []
    const probe = transportProbe({
      async request(req) {
        sent.push(req)
        return { status: 200, json: null, bytes: new Uint8Array([1]), contentType: 'text/javascript; charset=utf-8' }
      },
    })
    expect(await probe('https://erp.exemple.fr/ext/sales/index.js')).toEqual({ ok: true, status: 200, contentType: 'text/javascript; charset=utf-8' })
    expect(sent[0]).toMatchObject({ method: 'GET', url: 'https://erp.exemple.fr/ext/sales/index.js', binary: true })
  })

  it('un module absent : son statut, sans type', async () => {
    const probe = transportProbe({ request: async () => ({ status: 404, json: { detail: 'not found' } }) })
    expect(await probe('https://erp.exemple.fr/ext/x.js')).toEqual({ ok: false, status: 404, contentType: '' })
  })
})
