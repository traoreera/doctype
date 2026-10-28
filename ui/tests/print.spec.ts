/**
 * Impression côté Desk (process/impression.md §7, étape 5) : les appels, le nom du fichier, et le dialogue.
 *
 * Ce qui se vérifie ici et nulle part ailleurs : qu'un PDF voyage en OCTETS et non en JSON, que le nom proposé
 * vient du serveur (accents compris), que le bouton PDF n'existe pas quand le moteur n'est pas installé — et
 * que l'aperçu reste cantonné dans une iframe en bac à sable.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import PrintDialog from '../src/components/PrintDialog.vue'
import { ApiClient, filenameFromDisposition, type ClientContext } from '../src/lib/api'
import type { HttpRequest, HttpResponse, Transport } from '../src/lib/transport'
import { FetchTransport } from '../src/lib/transport'
import { mountWith, unmountAll } from './views-fixtures'

function setup(responses: HttpResponse[]) {
  const requests: HttpRequest[] = []
  const transport: Transport = {
    async request(req) {
      requests.push(req)
      return responses.shift() ?? { status: 200, json: {} }
    },
  }
  const context: ClientContext = { baseUrl: '/app/xcore-doctype', token: 'tok', tenant: 'acme' }
  return { api: new ApiClient(transport, () => context), requests }
}

const TEMPLATES = {
  doctype: 'Facture',
  default: 'standard',
  pdf: true,
  templates: [
    { name: 'standard', label: 'Facture', source: 'plugin' as const },
    { name: 'duplicata', label: 'Duplicata', source: 'organisation' as const },
  ],
}
const RENDERED = { doctype: 'Facture', name: 'FA-1', template: 'standard', html: '<html><body><h1>Facture FA-1</h1></body></html>', background: false }

describe('les appels d’impression', () => {
  it('demande les gabarits du type', async () => {
    const { api, requests } = setup([{ status: 200, json: TEMPLATES }])
    expect(await api.printTemplates('Sales Invoice')).toEqual(TEMPLATES)
    expect(requests[0]!.url).toBe('/app/xcore-doctype/print-templates/Sales%20Invoice')
  })

  it('passe le gabarit et le papier à en-tête en paramètres, et rien quand il n’y en a pas', async () => {
    const { api, requests } = setup([{ status: 200, json: RENDERED }, { status: 200, json: RENDERED }])
    await api.print('Facture', 'FA-1')
    expect(requests[0]!.url).toBe('/app/xcore-doctype/print/Facture/FA-1')
    await api.print('Facture', 'FA-1', { template: 'duplicata', letterhead: 'none' })
    const url = new URL(requests[1]!.url, 'http://x')
    expect(Object.fromEntries(url.searchParams)).toEqual({ template: 'duplicata', letterhead: 'none' })
  })

  it('demande le PDF en OCTETS, pas en JSON', async () => {
    const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46])
    const { api, requests } = setup([{ status: 200, json: null, bytes, contentType: 'application/pdf', disposition: 'attachment; filename="Facture FA-1.pdf"' }])
    const file = await api.printPdf('Facture', 'FA-1')
    expect(requests[0]).toMatchObject({ method: 'GET', url: '/app/xcore-doctype/print/Facture/FA-1/pdf', binary: true })
    expect(requests[0]!.headers.Accept).toBe('*/*')
    expect(file.bytes).toEqual(bytes)
    expect(file.filename).toBe('Facture FA-1.pdf')
  })

  it('un 503 sans le moteur reste une erreur JSON ordinaire', async () => {
    const { api } = setup([{ status: 503, json: { detail: { code: 'pdf_engine_missing', msg: 'il manque plutoprint' } } }])
    await expect(api.printPdf('Facture', 'FA-1')).rejects.toMatchObject({ code: 'pdf_engine_missing', status: 503 })
  })

  it('se rabat sur un nom fabriqué quand le serveur n’en propose pas', async () => {
    const { api } = setup([{ status: 200, json: null, bytes: new Uint8Array([1]), contentType: 'application/pdf', disposition: '' }])
    expect((await api.printPdf('Facture', 'FA-1')).filename).toBe('Facture FA-1.pdf')
  })
})

describe('le nom de fichier du serveur', () => {
  it('préfère `filename*` (RFC 5987) au nom ASCII dégradé, pour garder les accents', () => {
    const header = `attachment; filename="Facture de re_glement FA-1.pdf"; filename*=UTF-8''Facture%20de%20r%C3%A8glement%20FA-1.pdf`
    expect(filenameFromDisposition(header)).toBe('Facture de règlement FA-1.pdf')
  })

  it('retombe sur le nom ASCII si l’encodage est abîmé', () => {
    expect(filenameFromDisposition(`attachment; filename="repli.pdf"; filename*=UTF-8''%E0%A4%A`)).toBe('repli.pdf')
  })

  it('lit un nom sans guillemets, et rend `null` quand il n’y a rien', () => {
    expect(filenameFromDisposition('attachment; filename=simple.pdf')).toBe('simple.pdf')
    expect(filenameFromDisposition('attachment')).toBeNull()
    expect(filenameFromDisposition('')).toBeNull()
  })
})

describe('le transport binaire', () => {
  it('rend les octets et les en-têtes sur un succès, du JSON sur un échec', async () => {
    const pdf = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d])
    const ok = new Response(pdf, { status: 200, headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="x.pdf"' } })
    const ko = new Response(JSON.stringify({ detail: { code: 'pdf_engine_missing' } }), { status: 503, headers: { 'Content-Type': 'application/json' } })
    const transport = new FetchTransport(vi.fn().mockResolvedValueOnce(ok).mockResolvedValueOnce(ko) as unknown as typeof fetch)

    const good = await transport.request({ method: 'GET', url: '/x', headers: {}, binary: true })
    expect(good.bytes).toEqual(pdf)
    expect(good.contentType).toBe('application/pdf')
    expect(good.disposition).toContain('x.pdf')

    const bad = await transport.request({ method: 'GET', url: '/x', headers: {}, binary: true })
    expect(bad.bytes).toBeUndefined()
    expect(bad.json).toEqual({ detail: { code: 'pdf_engine_missing' } })
  })

  it('ne lit jamais d’octets pour une requête ordinaire', async () => {
    const res = new Response(JSON.stringify({ a: 1 }), { status: 200 })
    const transport = new FetchTransport(vi.fn().mockResolvedValue(res) as unknown as typeof fetch)
    const out = await transport.request({ method: 'GET', url: '/x', headers: {} })
    expect(out.bytes).toBeUndefined()
    expect(out.json).toEqual({ a: 1 })
  })
})

describe('le dialogue d’impression', () => {
  afterEach(() => {
    unmountAll() // AVANT de vider le corps de la page : une fenêtre PrimeVue s'y téléporte (views-fixtures)
    document.body.innerHTML = ''
  })

  async function open(templates: unknown = TEMPLATES, rendered: unknown = RENDERED) {
    const calls: string[] = []
    const api = {
      printTemplates: vi.fn(async () => {
        calls.push('templates')
        return templates
      }),
      print: vi.fn(async (_dt: string, _n: string, opts: { template?: string; letterhead?: string } = {}) => {
        calls.push(`print:${opts.template ?? ''}:${opts.letterhead ?? ''}`)
        return rendered
      }),
      printPdf: vi.fn(async () => ({ bytes: new Uint8Array([0x25]), filename: 'Facture FA-1.pdf' })),
    }
    const { w } = mountWith(PrintDialog, { doctype: 'Facture', name: 'FA-1', label: 'Facture FA-1', visible: true }, api)
    await new Promise((r) => setTimeout(r, 0))
    await w.vm.$nextTick()
    return { wrapper: w, api, calls }
  }

  const find = (sel: string) => document.querySelector(`[data-testid="${sel}"]`)

  it('rend le document dans une iframe en BAC À SABLE, jamais dans la page', async () => {
    await open()
    const frame = find('print-frame') as HTMLIFrameElement | null
    expect(frame).not.toBeNull()
    // Ce qui compte : **aucun script** ne s'exécute dans ce cadre. `allow-same-origin` y est parce que sans lui
    // `contentWindow.print` lève un SecurityError depuis la page parente (mesuré dans un vrai navigateur), et il
    // n'ouvre rien tant que `allow-scripts` est absent — c'est leur COMBINAISON qui permettrait l'évasion.
    const sandbox = (frame!.getAttribute('sandbox') ?? '').split(/\s+/)
    expect(sandbox).toContain('allow-same-origin')
    expect(sandbox).not.toContain('allow-scripts')
    expect(sandbox).not.toContain('allow-top-navigation')
    expect(sandbox).not.toContain('allow-forms')
    expect(frame!.getAttribute('srcdoc')).toContain('Facture FA-1')
    // Le HTML du document reste une CHAÎNE dans l'attribut `srcdoc` : il n'est jamais analysé en nœuds vivants
    // de la page du Desk (c'est ce que `v-html` aurait fait, et c'est précisément ce qu'on refuse).
    expect([...document.querySelectorAll('h1')].some((h) => h.textContent === 'Facture FA-1')).toBe(false)
  })

  it('offre le PDF quand le serveur a le moteur, et explique son absence sinon', async () => {
    await open()
    expect(find('print-pdf')).not.toBeNull()
    expect(find('print-no-engine')).toBeNull()

    unmountAll()
    document.body.innerHTML = ''
    await open({ ...TEMPLATES, pdf: false })
    expect(find('print-pdf')).toBeNull()
    expect(find('print-no-engine')).not.toBeNull()
    // Et « Imprimer » reste là : l'aperçu s'imprime depuis le navigateur, sans le moteur.
    expect(find('print-now')).not.toBeNull()
  })

  it('demande l’aperçu une fois, pour le gabarit par défaut du serveur', async () => {
    const { calls } = await open()
    expect(calls).toEqual(['templates', 'print:standard:'])
  })

  it('redemande l’aperçu quand on change de gabarit ou d’en-tête', async () => {
    const { wrapper, calls } = await open()
    const vm = wrapper.vm as unknown as { chosen: string; letterhead: string }
    vm.chosen = 'duplicata'
    await new Promise((r) => setTimeout(r, 0))
    vm.letterhead = 'none'
    await new Promise((r) => setTimeout(r, 0))
    expect(calls).toEqual(['templates', 'print:standard:', 'print:duplicata:', 'print:duplicata:none'])
  })

  it('n’offre le choix du gabarit que s’il y en a plusieurs', async () => {
    await open()
    expect(find('print-template')).not.toBeNull()
    unmountAll()
    document.body.innerHTML = ''
    await open({ ...TEMPLATES, templates: [TEMPLATES.templates[0]!] })
    expect(find('print-template')).toBeNull()
  })

  it('dit qu’un fond préimprimé n’apparaît que dans le PDF', async () => {
    await open(TEMPLATES, { ...RENDERED, background: true })
    expect(find('print-background')).not.toBeNull()
  })

  it('montre l’erreur du serveur au lieu d’un aperçu vide', async () => {
    const api = {
      printTemplates: vi.fn(async () => TEMPLATES),
      print: vi.fn(async () => {
        throw new Error('gabarit introuvable')
      }),
      printPdf: vi.fn(),
    }
    mountWith(PrintDialog, { doctype: 'Facture', name: 'FA-1', label: 'Facture FA-1', visible: true }, api)
    await new Promise((r) => setTimeout(r, 0))
    expect(find('print-error')).not.toBeNull()
    expect(find('print-frame')).toBeNull()
  })
})
