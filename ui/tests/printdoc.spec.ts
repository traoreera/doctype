/**
 * Le document d'impression LIBRE, façon traitement de texte (`lib/printdoc.ts`, `process/impression.md` §12).
 *
 * Le pur d'abord : les styles que le serveur accepte (les MÊMES cas que `tests/unit/test_print_document.py`), la
 * conversion d'un gabarit à blocs en document, les dimensions de la page. Puis la page « Personnaliser
 * l'impression » montée avec son éditeur.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import vectors from '../../tests/vectors/print_style_cases.json'
import { blocksToContent, checkStyle, newEmbedded, normalizeHtml, pageMm, textToHtml, tokenHtml } from '@/lib/printdoc'
import PrintBuilderView from '@/views/PrintBuilderView.vue'
import { INVOICE, TEMPLATE } from './print-fixtures'
import { mountWith, unmountAll } from './views-fixtures'

describe('les styles : ce que le serveur accepte, ni plus ni moins', () => {
  it.each(vectors.cases)('$tag « $style »', (c) => {
    const got = checkStyle(c.tag, c.style)
    expect(got.dropped.length === 0).toBe(c.ok)
    expect(got.kept).toBe(c.kept)
  })

  it('un collage perd les styles que le serveur refuserait, et garde le reste', () => {
    const html = '<p style="text-align: center; font-family: Calibri">a<span style="font-size: 1.2em; color: #ff0000">b</span></p>'
    expect(normalizeHtml(html)).toBe('<p style="text-align:center">a<span style="color:#ff0000">b</span></p>')
  })
})

describe('un gabarit à blocs s’ouvre en document', () => {
  it('le titre et le texte deviennent du texte, leurs jetons des champs insérés', () => {
    const html = blocksToContent(TEMPLATE.blocks as Record<string, unknown>[], INVOICE)
    expect(html.startsWith('<h1>Facture <span data-value="name">N° du document</span></h1>')).toBe(true)
  })

  it('les champs, le tableau des lignes et les totaux sont incorporés tels quels', () => {
    const html = blocksToContent(TEMPLATE.blocks as Record<string, unknown>[], INVOICE)
    const blocks = [...new DOMParser().parseFromString(html, 'text/html').querySelectorAll('div[data-block]')].map((d) => JSON.parse(d.getAttribute('data-block')!))
    expect(blocks).toEqual(TEMPLATE.blocks.slice(1))
  })

  it('un saut de page reste un saut de page ; rien du tout donne une page vide, pas un document vide', () => {
    expect(blocksToContent([{ type: 'page_break' }], INVOICE)).toBe('<div data-page-break=""></div>')
    expect(blocksToContent([], INVOICE)).toBe('<p></p>')
  })

  it('le texte est échappé, et un champ inconnu du vocabulaire reste du texte', () => {
    expect(textToHtml('<b>{{ doc.client }}</b> {{ actor }}', INVOICE)).toBe('&lt;b&gt;<span data-value="doc.client">Client</span>&lt;/b&gt; {{ actor }}')
    expect(tokenHtml('doc.inconnu', INVOICE)).toBe('<span data-value="doc.inconnu">inconnu</span>')
  })
})

describe('les éléments neufs', () => {
  it('le tableau des lignes part des colonnes imprimables, nombres à droite', () => {
    const b = newEmbedded('table', INVOICE, 'items')
    expect(b.field).toBe('items')
    expect(b.columns).toEqual([{ field: 'article' }, { field: 'entrepot' }, { field: 'qty', align: 'right' }, { field: 'amount', align: 'right' }])
  })

  it('les totaux ne prennent que des nombres', () => {
    expect(newEmbedded('totals', INVOICE)).toEqual({ type: 'totals', fields: ['total'] })
  })
})

describe('la page', () => {
  it('A4 portrait, A4 paysage, marges', () => {
    expect(pageMm({ size: 'A4', orientation: 'portrait', top: 20, right: 15, bottom: 20, left: 15 })).toMatchObject({ width: 210, height: 297, top: 20 })
    expect(pageMm({ size: 'A4', orientation: 'landscape', top: 10, right: 10, bottom: 10, left: 10 })).toMatchObject({ width: 297, height: 210 })
  })
})

describe('la page « Personnaliser l’impression »', () => {
  afterEach(() => {
    unmountAll() // AVANT de vider le corps de la page (une fenêtre PrimeVue s'y téléporte)
    document.body.innerHTML = ''
  })

  const override = {
    doctype: 'Facture',
    template: TEMPLATE,
    source: 'plugin' as const,
    editable: true,
    templates: [{ name: 'standard', label: 'Facture', source: 'plugin' as const }],
  }

  async function open(extra: Record<string, unknown> = {}, apiExtra: Record<string, unknown> = {}) {
    const api = {
      meta: async () => INVOICE,
      printOverride: vi.fn(async () => ({ ...override, ...extra })),
      printExample: vi.fn(async () => ({ doctype: 'Facture', name: 'FA-1' })),
      get: vi.fn(async () => ({ doctype: 'Facture', name: 'FA-1', version: 1, data: { client: 'CL-1', date: '2026-10-29', total: '37.50', items: [{ article: 'VIS', qty: '3.000', amount: '37.50' }] } })),
      list: vi.fn(async () => ({ items: [], total: 0 })),
      fileLinks: vi.fn(async () => ({})),
      printPreview: vi.fn(async (_d: string, _n: string, tpl: Record<string, unknown>) => ({
        doctype: 'Facture', name: 'FA-1', html: `<html><body>APERÇU ${String(tpl.content).length}</body></html>`, background: false,
      })),
      savePrintOverride: vi.fn(async () => ({ doctype: 'Facture', template: {} })),
      removePrintOverride: vi.fn(async () => ({ doctype: 'Facture', template: 'standard' })),
      ...apiExtra,
    }
    const { w } = mountWith(PrintBuilderView, { doctype: 'Facture' }, api)
    await new Promise((r) => setTimeout(r, 0))
    await w.vm.$nextTick()
    await new Promise((r) => setTimeout(r, 0))
    return { w, api }
  }
  const find = (id: string) => document.querySelector(`[data-testid="${id}"]`)

  it('ouvre le gabarit à blocs CONVERTI en document, avec les valeurs du document d’exemple', async () => {
    await open()
    const content = find('pd-content')!
    expect(content.querySelector('h1')?.textContent).toContain('Facture')
    expect(content.querySelector('[data-token="name"]')?.textContent).toBe('FA-1') // la valeur, pas l'étiquette
    expect(content.querySelectorAll('[data-testid="pd-block"]').length).toBe(3) // champs, lignes, totaux
    expect(content.textContent).toContain('37,50') // un montant formaté comme une pièce
  })

  it('enregistre un DOCUMENT libre, jamais la pile d’origine', async () => {
    const { w, api } = await open()
    const view = w.findComponent(PrintBuilderView).vm as unknown as { content: string }
    view.content = view.content + '<p>Merci de votre confiance.</p>'
    await w.vm.$nextTick()
    ;(find('pb-save') as HTMLButtonElement).click()
    await new Promise((r) => setTimeout(r, 0))
    const sent = (api.savePrintOverride.mock.calls[0] as unknown[])[1] as Record<string, unknown>
    expect(sent.blocks).toBeUndefined()
    expect(String(sent.content)).toContain('Merci de votre confiance.')
    expect(String(sent.content)).toContain('data-block=')
  })

  it('un clic sur « Totaux » ajoute un bloc à la fin du document, sans rien remplacer', async () => {
    await open()
    const before = document.querySelectorAll('[data-testid="pd-block"]').length
    ;(find('pd-insert-totals') as HTMLElement).click()
    await new Promise((r) => setTimeout(r, 0))
    const kinds = [...document.querySelectorAll('[data-testid="pd-block"]')].map((x) => (x as HTMLElement).dataset.kind)
    expect(kinds).toEqual(['fields', 'table', 'totals', 'totals']) // à la FIN, et rien n'a été remplacé
    expect(before).toBe(3)
  })

  it('un champ du document se glisse : il porte le HTML d’un champ inséré', async () => {
    await open()
    const data = new Map<string, string>()
    const ev = new Event('dragstart', { bubbles: true }) as DragEvent
    Object.defineProperty(ev, 'dataTransfer', { value: { setData: (k: string, v: string) => data.set(k, v), effectAllowed: '' } })
    find('pd-value-doc.client')!.dispatchEvent(ev)
    expect(data.get('text/html')).toBe('<span data-value="doc.client">Client</span>')
  })

  it('un lecteur voit le document, sans barre d’outils ni insertion, et ne peut pas enregistrer', async () => {
    await open({ editable: false })
    expect(find('pb-readonly')).not.toBeNull()
    expect(find('pd-toolbar')).toBeNull()
    expect((find('pd-insert-totals') as HTMLButtonElement).disabled).toBe(true)
    expect((find('pb-save') as HTMLButtonElement).disabled).toBe(true)
    expect(find('pd-content')!.getAttribute('contenteditable')).toBe('false')
  })

  it('un document de l’organisation s’ouvre tel qu’il est, image comprise', async () => {
    const id = 'a'.repeat(32)
    const { api } = await open({
      source: 'organisation',
      template: { name: 'standard', page: TEMPLATE.page, content: `<p>Bonjour <img data-file="${id}" data-wrap="front" style="width: 30mm; left: 100mm; top: 10mm"></p>` },
    })
    expect(api.fileLinks).toHaveBeenCalledWith([id]) // l'image de l'éditeur vient d'un lien signé
    const img = find('pd-image') as HTMLElement
    expect(img.classList.contains('wrap-front')).toBe(true)
    expect(img.style.left).toBe('100mm')
    expect(img.style.top).toBe('10mm')
    expect(find('pb-reset')).not.toBeNull()
  })

  it('« Aperçu imprimé » montre le rendu du SERVEUR à la place de l’éditeur', async () => {
    const { w, api } = await open()
    expect(api.printPreview).toHaveBeenCalled()
    const editor = find('pd-stage')!.closest('.pd-editor') as HTMLElement
    const button = [...document.querySelectorAll('[data-testid="pb-mode"] *')].find((b) => b.textContent?.trim() === 'Aperçu imprimé') as HTMLElement
    button.click()
    await w.vm.$nextTick()
    expect(editor.style.display).toBe('none')
    expect((find('pb-frame') as HTMLIFrameElement).getAttribute('sandbox')?.split(/\s+/)).not.toContain('allow-scripts')
  })
})
