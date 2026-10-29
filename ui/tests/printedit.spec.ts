/**
 * Le modèle PUR de l'éditeur de gabarits (`lib/printedit.ts`, `process/impression.md` §10).
 *
 * Aucun composant, aucune requête : une pile de blocs qu'on manipule, et ce que le serveur recevra. Les règles
 * de `printing.py` §8 sont vérifiées ICI, avant tout enregistrement — un éditeur qui laisse enregistrer puis
 * montre un 422 fait deviner à l'utilisateur ce qu'un contrôle sait déjà.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_PAGE,
  MAX_COLUMNS,
  addBlock,
  addColumn,
  blockLabel,
  columnFields,
  draftProblems,
  fillText,
  fromTemplate,
  insertField,
  moveBlockToSlot,
  moveColumnToSlot,
  moveFieldTo,
  setColumnWidth,
  slotIndex,
  groupableColumns,
  isUseful,
  moveBlock,
  moveColumn,
  moveFieldInBlock,
  newBlock,
  nudgeBlock,
  printableFields,
  removeBlock,
  removeColumn,
  setGroupBy,
  toTemplate,
  toggleField,
  toggleSubtotal,
  totalFields,
  updateBlock,
  updateColumn,
  tableColumns,
  type Draft,
} from '@/lib/printedit'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'
import PrintBuilderView from '@/views/PrintBuilderView.vue'
import { mountWith, unmountAll } from './views-fixtures'

const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })

const LINE: DocTypeMeta = {
  name: 'Ligne', label: 'Ligne', module: 'sales', timezone: 'UTC',
  fields: [
    f({ fieldname: 'article', label: 'Article', fieldtype: 'Link', options: 'Article' }),
    f({ fieldname: 'entrepot', label: 'Dépôt', fieldtype: 'Select', options: ['A', 'B'] }),
    f({ fieldname: 'qty', label: 'Quantité', fieldtype: 'Decimal', decimals: 3 }),
    f({ fieldname: 'amount', label: 'Montant', fieldtype: 'Currency', decimals: 2 }),
    f({ fieldname: 'photo', label: 'Photo', fieldtype: 'Image' }),
    f({ fieldname: 'notes', label: 'Notes', fieldtype: 'TextEditor' }),
  ],
  layout: [], list_columns: [], rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
}
const INVOICE: DocTypeMeta = {
  name: 'Facture', label: 'Facture', module: 'sales', timezone: 'UTC',
  fields: [
    f({ fieldname: 'client', label: 'Client', fieldtype: 'Link', options: 'Client' }),
    f({ fieldname: 'date', label: 'Date', fieldtype: 'Date' }),
    f({ fieldname: 'items', label: 'Lignes', fieldtype: 'Table', options: 'Ligne' }),
    f({ fieldname: 'total', label: 'Total', fieldtype: 'Currency', decimals: 2 }),
    f({ fieldname: 'secret', label: 'Secret', fieldtype: 'Password' }),
    f({ fieldname: 'piece', label: 'Pièce', fieldtype: 'Attach' }),
  ],
  children: { Ligne: LINE },
  layout: [], list_columns: [], rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
}

const TEMPLATE = {
  schema_version: 1, doctype: 'Facture', name: 'standard', label: 'Facture',
  page: { size: 'A4', orientation: 'portrait', top: 20, bottom: 20, left: 15, right: 15 },
  blocks: [
    { type: 'title', text: 'Facture {{ name }}' },
    { type: 'fields', columns: 2, fields: ['client', 'date'] },
    { type: 'table', field: 'items', style: 'bordered', density: 'normal', repeat_header: true, columns: [{ field: 'article' }, { field: 'qty', align: 'right' }] },
    { type: 'totals', fields: ['total'] },
  ],
}

const draft = (): Draft => fromTemplate(TEMPLATE)

describe('aller et retour', () => {
  it('lit le gabarit du serveur sans rien y changer', () => {
    const d = draft()
    expect(d.name).toBe('standard')
    expect(d.label).toBe('Facture')
    expect(d.blocks.map((b) => b.type)).toEqual(['title', 'fields', 'table', 'totals'])
    expect(tableColumns(d.blocks[2]!).map((c) => c.field)).toEqual(['article', 'qty'])
  })

  it('rend au serveur exactement ce qu’il attend : les clés d’édition retirées', () => {
    const out = toTemplate(draft())
    expect(JSON.stringify(out)).not.toContain('"key"')
    expect(out.name).toBe('standard')
    expect((out.blocks as unknown[]).length).toBe(4)
    expect((out.blocks as Record<string, unknown>[])[2]!.columns).toEqual([{ field: 'article' }, { field: 'qty', align: 'right' }])
  })

  it('un gabarit vide se lit quand même : l’éditeur doit pouvoir partir de rien', () => {
    const d = fromTemplate(null)
    expect(d.blocks).toEqual([])
    expect(d.page).toEqual(DEFAULT_PAGE)
    expect(d.name).toBe('standard')
  })

  it('n’envoie pas les propriétés d’un autre genre de bloc', () => {
    let d = fromTemplate({ blocks: [{ type: 'spacer', size: 8 }] })
    d = updateBlock(d, d.blocks[0]!.key, { field: 'items', text: 'perdu', fields: ['client'] })
    const block = (toTemplate(d).blocks as Record<string, unknown>[])[0]!
    expect(block).toEqual({ type: 'spacer', size: 8 })
  })
})

describe('les blocs inutiles ne partent pas', () => {
  it('un bloc qu’on vient d’ajouter n’empêche pas d’enregistrer le reste', () => {
    // Le serveur refuse un tableau sans colonne (`min_length`) : l'éditeur ne doit pas obliger à le finir d'abord.
    const d = addBlock(draft(), newBlock('table', INVOICE))
    expect(d.blocks).toHaveLength(5)
    expect((toTemplate(d).blocks as unknown[]).length).toBe(4)
  })

  it('dit lesquels sont utiles', () => {
    expect(isUseful({ key: 'a', type: 'fields', fields: [] })).toBe(false)
    expect(isUseful({ key: 'a', type: 'fields', fields: ['client'] })).toBe(true)
    expect(isUseful({ key: 'a', type: 'table', field: 'items', columns: [] })).toBe(false)
    expect(isUseful({ key: 'a', type: 'title', text: '   ' })).toBe(false)
    expect(isUseful({ key: 'a', type: 'page_break' })).toBe(true)
    expect(isUseful({ key: 'a', type: 'spacer', size: 5 })).toBe(true)
  })
})

describe('déplacer un bloc', () => {
  it('par glisser-déposer, à un rang donné', () => {
    const d = draft()
    const moved = moveBlock(d, d.blocks[3]!.key, 0)
    expect(moved.blocks.map((b) => b.type)).toEqual(['totals', 'title', 'fields', 'table'])
  })

  it('au clavier, d’un cran, sans sortir de la pile', () => {
    const d = draft()
    expect(nudgeBlock(d, d.blocks[0]!.key, 'up').blocks.map((b) => b.type)).toEqual(['title', 'fields', 'table', 'totals'])
    expect(nudgeBlock(d, d.blocks[3]!.key, 'down').blocks.map((b) => b.type)).toEqual(['title', 'fields', 'table', 'totals'])
    expect(nudgeBlock(d, d.blocks[1]!.key, 'down').blocks.map((b) => b.type)).toEqual(['title', 'table', 'fields', 'totals'])
  })

  it('retire un bloc, et ne touche pas les autres', () => {
    const d = draft()
    const out = removeBlock(d, d.blocks[1]!.key)
    expect(out.blocks.map((b) => b.type)).toEqual(['title', 'table', 'totals'])
  })

  it('rend toujours une NOUVELLE pile (la page compare pour savoir si c’est modifié)', () => {
    const d = draft()
    const out = moveBlock(d, d.blocks[0]!.key, 1)
    expect(out).not.toBe(d)
    expect(d.blocks.map((b) => b.type)).toEqual(['title', 'fields', 'table', 'totals'])
  })
})

describe('choisir les champs à afficher', () => {
  it('ajoute et retire, en gardant l’ordre de choix', () => {
    const d = draft()
    const block = d.blocks[1]!.key
    let out = toggleField(d, block, 'total')
    expect(out.blocks[1]!.fields).toEqual(['client', 'date', 'total'])
    out = toggleField(out, block, 'date')
    expect(out.blocks[1]!.fields).toEqual(['client', 'total'])
  })

  it('réordonne un champ dans le bloc', () => {
    const d = draft()
    const block = d.blocks[1]!.key
    expect(moveFieldInBlock(d, block, 'date', 'up').blocks[1]!.fields).toEqual(['date', 'client'])
    expect(moveFieldInBlock(d, block, 'client', 'up').blocks[1]!.fields).toEqual(['client', 'date'])
  })

  it('n’offre que ce qui s’imprime : ni secret, ni fichier, ni table', () => {
    const names = printableFields(INVOICE).map((x) => x.fieldname)
    expect(names).toEqual(['client', 'date', 'total'])
    expect(totalFields(INVOICE).map((x) => x.fieldname)).toEqual(['total'])
  })
})

describe('choisir le tableau et ses colonnes', () => {
  it('n’offre en colonnes que les champs des LIGNES, et pas une image ni un texte riche', () => {
    expect(columnFields(INVOICE, 'items').map((x) => x.fieldname)).toEqual(['article', 'entrepot', 'qty', 'amount'])
    expect(columnFields(INVOICE, 'inconnu')).toEqual([])
  })

  it('ajoute une colonne, une seule fois, et pas plus que le serveur n’en accepte', () => {
    const d = draft()
    const block = d.blocks[2]!.key
    let out = addColumn(d, block, 'amount')
    expect(tableColumns(out.blocks[2]!).map((c) => c.field)).toEqual(['article', 'qty', 'amount'])
    out = addColumn(out, block, 'amount') // deux fois : refusé
    expect(tableColumns(out.blocks[2]!)).toHaveLength(3)
    for (let i = 0; i < MAX_COLUMNS + 5; i++) out = addColumn(out, block, `c${i}`)
    expect(tableColumns(out.blocks[2]!).length).toBeLessThanOrEqual(MAX_COLUMNS)
  })

  it('déplace et renomme une colonne', () => {
    const d = draft()
    const block = d.blocks[2]!.key
    const columns = tableColumns(d.blocks[2]!)
    expect(tableColumns(moveColumn(d, block, columns[1]!.key, 'left').blocks[2]!).map((c) => c.field)).toEqual(['qty', 'article'])
    const named = updateColumn(d, block, columns[1]!.key, { label: 'Qté', align: 'right', width: 20 })
    expect(tableColumns(named.blocks[2]!)[1]).toMatchObject({ field: 'qty', label: 'Qté', align: 'right', width: 20 })
  })

  it('retirer une colonne emporte son sous-total : le serveur le refuserait sinon', () => {
    let d = draft()
    const block = d.blocks[2]!.key
    d = setGroupBy(d, block, 'entrepot')
    d = toggleSubtotal(d, block, 'qty')
    expect(d.blocks[2]!.subtotal).toEqual(['qty'])
    const columns = tableColumns(d.blocks[2]!)
    d = removeColumn(d, block, columns.find((c) => c.field === 'qty')!.key)
    expect(d.blocks[2]!.subtotal).toEqual([])
  })

  it('ne plus regrouper emporte les sous-totaux', () => {
    let d = setGroupBy(draft(), draft().blocks[2]!.key, 'entrepot')
    const block = d.blocks[2]!.key
    d = setGroupBy(d, block, 'entrepot')
    d = toggleSubtotal(d, block, 'qty')
    d = setGroupBy(d, block, null)
    expect(d.blocks[2]!.group_by).toBeNull()
    expect(d.blocks[2]!.subtotal).toEqual([])
  })

  it('un sous-total sans regroupement ne se pose pas', () => {
    const d = draft()
    const out = toggleSubtotal(d, d.blocks[2]!.key, 'qty')
    expect(out.blocks[2]!.subtotal ?? []).toEqual([])
  })

  it('ne propose au regroupement que ce que le serveur regroupe', () => {
    const names = groupableColumns(INVOICE, 'items').map((x) => x.fieldname)
    expect(names).toContain('entrepot')
    expect(names).toContain('article')
    expect(names).not.toContain('qty') // un Decimal ne regroupe pas
    expect(names).not.toContain('amount')
  })
})

describe('ce que le serveur refuserait, dit avant', () => {
  it('un gabarit sans aucun bloc utile', () => {
    expect(draftProblems(fromTemplate({ blocks: [] }), INVOICE)[0]).toMatch(/au moins un bloc/)
  })

  it('un champ qui ne s’imprime pas', () => {
    const d = updateBlock(draft(), draft().blocks[1]!.key, {})
    const bad = toggleField(d, d.blocks[1]!.key, 'secret')
    expect(draftProblems(bad, INVOICE).join(' ')).toMatch(/secret/)
  })

  it('des largeurs dont la somme dépasse 100', () => {
    let d = draft()
    const block = d.blocks[2]!.key
    for (const c of tableColumns(d.blocks[2]!)) d = updateColumn(d, block, c.key, { width: 60 })
    expect(draftProblems(d, INVOICE).join(' ')).toMatch(/dépasse 100/)
  })

  it('un tableau sans tableau choisi', () => {
    const d = addBlock(draft(), { ...newBlock('table', INVOICE), field: undefined, columns: [{ key: 'c', field: 'qty' }] })
    expect(draftProblems(d, INVOICE).join(' ')).toMatch(/choisissez le tableau/)
  })

  it('un texte démesuré', () => {
    const d = addBlock(draft(), { ...newBlock('text'), text: 'x'.repeat(2001) })
    expect(draftProblems(d, INVOICE).join(' ')).toMatch(/caractères au plus/)
  })

  it('le gabarit du serveur, lui, ne pose aucun problème', () => {
    expect(draftProblems(draft(), INVOICE)).toEqual([])
  })
})

describe('un bloc neuf', () => {
  it('part des valeurs par défaut du serveur', () => {
    expect(newBlock('spacer').size).toBe(5)
    expect(newBlock('title').text).toContain('{{ name }}')
    const table = newBlock('table', INVOICE)
    expect(table).toMatchObject({ field: 'items', style: 'bordered', density: 'normal', repeat_header: true })
  })

  it('se nomme lisiblement dans la pile', () => {
    expect(blockLabel(newBlock('page_break'))).toBe('Saut de page')
    expect(blockLabel(newBlock('table', INVOICE), INVOICE)).toBe('Tableau Lignes')
    expect(blockLabel({ key: 'a', type: 'fields', fields: ['client', 'date'] })).toBe('Champs (2)')
  })
})

describe('le papier à en-tête', () => {
  it('un PDF importé voyage avec ses zones réservées', () => {
    const d: Draft = { ...draft(), letterhead: { pdf: 'a'.repeat(32), pages: 'all', offset_top: 45, offset_bottom: 25 } }
    expect(toTemplate(d).letterhead).toEqual({ pdf: 'a'.repeat(32), pages: 'all', offset_top: 45, offset_bottom: 25 })
  })

  it('un en-tête vide n’est pas envoyé', () => {
    expect(toTemplate({ ...draft(), letterhead: { name: null, pdf: null } }).letterhead).toBeUndefined()
    expect(toTemplate({ ...draft(), letterhead: null }).letterhead).toBeUndefined()
  })
})

describe('glisser sur la feuille', () => {
  const d = () => fromTemplate({
    name: 'standard',
    blocks: [
      { type: 'title', text: 'Facture {{ name }}' },
      { type: 'fields', columns: 3, fields: ['client', 'date'] },
      { type: 'table', field: 'items', columns: [{ field: 'article', width: 50 }, { field: 'qty', width: 20 }, { field: 'amount' }] },
      { type: 'totals', fields: ['total'] },
    ],
  })

  it('un bloc `fields` garde son NOMBRE de colonnes (il retombait à 2 à chaque ouverture)', () => {
    const draft = d()
    expect(draft.blocks[1]!.columns).toBe(3)
    expect((toTemplate(draft).blocks as Record<string, unknown>[])[1]!.columns).toBe(3)
  })

  it('un emplacement se lit APRÈS le retrait de l’élément déplacé', () => {
    expect(slotIndex(0, 3)).toBe(2) // le premier vers la fin d'une liste de 3
    expect(slotIndex(2, 0)).toBe(0)
    expect(slotIndex(1, 1)).toBe(1) // juste avant lui-même : il ne bouge pas
    expect(slotIndex(1, 2)).toBe(1) // juste après lui-même : non plus
  })

  it('un bloc lâché sous le dernier va à la fin, lâché en tête va en tête', () => {
    const draft = d()
    const title = draft.blocks[0]!.key
    expect(moveBlockToSlot(draft, title, 4).blocks.map((b) => b.type)).toEqual(['fields', 'table', 'totals', 'title'])
    const totals = draft.blocks[3]!.key
    expect(moveBlockToSlot(draft, totals, 0).blocks.map((b) => b.type)).toEqual(['totals', 'title', 'fields', 'table'])
    expect(moveBlockToSlot(draft, 'inconnu', 0)).toBe(draft)
  })

  it('un champ passe d’un bloc à l’autre, à l’emplacement choisi', () => {
    const draft = addBlock(d(), { key: 'bas', type: 'fields', fields: ['total'] })
    const head = draft.blocks[1]!.key
    const moved = moveFieldTo(draft, head, 'date', 'bas', 0)
    expect(moved.blocks[1]!.fields).toEqual(['client'])
    expect(moved.blocks.find((b) => b.key === 'bas')!.fields).toEqual(['date', 'total'])
    // Dans le même bloc : un simple réordonnancement.
    expect(moveFieldTo(draft, head, 'client', head, 2).blocks[1]!.fields).toEqual(['date', 'client'])
  })

  it('un champ n’entre pas dans un bloc qui l’imprime déjà, ni dans des totaux s’il n’est pas un nombre', () => {
    const draft = addBlock(d(), { key: 'bis', type: 'fields', fields: ['date'] })
    const head = draft.blocks[1]!.key
    expect(moveFieldTo(draft, head, 'date', 'bis', 0)).toBe(draft)
    const totals = draft.blocks[3]!.key
    expect(moveFieldTo(draft, head, 'client', totals, 0, new Set(['total']))).toBe(draft)
    // Ni vers un tableau ou un titre.
    expect(moveFieldTo(draft, head, 'client', draft.blocks[0]!.key, 0)).toBe(draft)
  })

  it('un champ ajouté depuis le panneau se pose une seule fois', () => {
    const draft = d()
    const head = draft.blocks[1]!.key
    expect(insertField(draft, head, 'total', 1).blocks[1]!.fields).toEqual(['client', 'total', 'date'])
    expect(insertField(draft, head, 'client', 0)).toBe(draft)
  })

  it('une colonne glissée change de rang dans SON tableau', () => {
    const draft = d()
    const table = draft.blocks[2]!
    const cols = tableColumns(table)
    const moved = moveColumnToSlot(draft, table.key, cols[2]!.key, 0)
    expect(tableColumns(moved.blocks[2]!).map((c) => c.field)).toEqual(['amount', 'article', 'qty'])
  })

  it('tirer une colonne ne fait jamais dépasser 100 % au total', () => {
    const draft = d()
    const table = draft.blocks[2]!
    const [article, qty, amount] = tableColumns(table)
    const widths = (x: Draft) => tableColumns(x.blocks[2]!).map((c) => c.width ?? null)
    expect(widths(setColumnWidth(draft, table.key, article!.key, 61.4))).toEqual([61, 20, null])
    expect(widths(setColumnWidth(draft, table.key, article!.key, 95))).toEqual([80, 20, null]) // 100 - 20
    expect(widths(setColumnWidth(draft, table.key, qty!.key, -4))).toEqual([50, 1, null])
    expect(widths(setColumnWidth(draft, table.key, amount!.key, 60))).toEqual([50, 20, 30])
    expect(draftProblems(setColumnWidth(draft, table.key, article!.key, 999), INVOICE)).toEqual([])
  })

  it('le texte d’un titre se remplit comme à l’impression', () => {
    const value = (f: string) => ({ client: 'Durand' })[f] ?? ''
    expect(fillText('Facture {{ name }} · {{ doc.client }} ({{ doctype }})', { name: 'FA-1', doctype: 'Facture', value })).toBe('Facture FA-1 · Durand (Facture)')
    expect(fillText('{{ name }}', { name: null, doctype: 'Facture', value })).toBe('{{ name }}')
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

  async function open(extra: Record<string, unknown> = {}) {
    const calls: string[] = []
    const api = {
      meta: async () => INVOICE,
      printOverride: vi.fn(async () => ({ ...override, ...extra })),
      printExample: vi.fn(async () => ({ doctype: 'Facture', name: 'FA-1' })),
      printPreview: vi.fn(async (_d: string, _n: string, tpl: Record<string, unknown>) => {
        calls.push(`preview:${(tpl.blocks as unknown[]).length}`)
        return { doctype: 'Facture', name: 'FA-1', html: '<html><body>APERÇU</body></html>', background: false }
      }),
      savePrintOverride: vi.fn(async () => ({ doctype: 'Facture', template: {} })),
      removePrintOverride: vi.fn(async () => ({ doctype: 'Facture', template: 'standard' })),
    }
    const { w } = mountWith(PrintBuilderView, { doctype: 'Facture' }, api)
    await new Promise((r) => setTimeout(r, 0))
    await w.vm.$nextTick()
    return { w, api, calls }
  }

  const find = (id: string) => document.querySelector(`[data-testid="${id}"]`)

  it('montre la pile du gabarit et son aperçu, rendu par le SERVEUR', async () => {
    const { api } = await open()
    expect(api.printOverride).toHaveBeenCalledWith('Facture')
    expect(api.printPreview).toHaveBeenCalled()
    expect(find('pb-block-0')).not.toBeNull()
    expect(find('pb-block-3')).not.toBeNull()
    const frame = find('pb-frame') as HTMLIFrameElement | null
    expect(frame?.getAttribute('srcdoc')).toContain('APERÇU')
    // Le même bac à sable que le dialogue d'impression : aucun script ne tourne dans l'aperçu.
    expect((frame?.getAttribute('sandbox') ?? '').split(/\s+/)).not.toContain('allow-scripts')
  })

  it('un lecteur voit tout, et ne peut rien enregistrer', async () => {
    await open({ editable: false })
    expect(find('pb-readonly')).not.toBeNull()
    expect((find('pb-save') as HTMLButtonElement | null)?.disabled).toBe(true)
    expect(find('pb-block-0')).not.toBeNull() // il regarde quand même
  })

  it('sans document du type, la page le dit et s’édite sans aperçu', async () => {
    const api = {
      meta: async () => INVOICE,
      printOverride: vi.fn(async () => override),
      printExample: vi.fn(async () => ({ doctype: 'Facture', name: null })),
      printPreview: vi.fn(),
    }
    mountWith(PrintBuilderView, { doctype: 'Facture' }, api)
    await new Promise((r) => setTimeout(r, 0))
    expect(find('pb-no-example')).not.toBeNull()
    expect(find('pb-frame')).toBeNull()
    expect(api.printPreview).not.toHaveBeenCalled() // on ne demande pas un aperçu sans document
  })

  it('ajouter un bloc l’ajoute à la pile et ne demande qu’UN aperçu', async () => {
    const { w, calls } = await open()
    calls.length = 0
    ;(find('pb-new-text') as HTMLElement).click()
    await w.vm.$nextTick()
    expect(find('pb-block-4')).not.toBeNull()
    // Le bloc texte est vide, donc inutile : il n'est pas envoyé, et l'aperçu ne bouge pas de longueur.
    await new Promise((r) => setTimeout(r, 600))
    expect(calls).toEqual(['preview:4'])
  })

  it('la feuille dessine les VALEURS du document d’exemple, liens par leur titre', async () => {
    const extra = {
      get: vi.fn(async () => ({ doctype: 'Facture', name: 'FA-1', version: 1, data: { client: 'CL-1', date: '2026-10-29', items: [{ article: 'VIS', qty: '3.000', amount: '37.50' }], total: '37.50' } })),
      search: vi.fn(),
      list: vi.fn(async () => ({ data: [{ name: 'CL-1', data: { nom: 'Durand' } }], total: 1 })),
    }
    const api = {
      meta: async () => INVOICE,
      printOverride: vi.fn(async () => override),
      printExample: vi.fn(async () => ({ doctype: 'Facture', name: 'FA-1' })),
      printPreview: vi.fn(async () => ({ doctype: 'Facture', name: 'FA-1', html: '<p>x</p>', background: false })),
      ...extra,
    }
    const { w } = mountWith(PrintBuilderView, { doctype: 'Facture' }, api)
    await new Promise((r) => setTimeout(r, 0))
    await w.vm.$nextTick()
    expect(extra.get).toHaveBeenCalledWith('Facture', 'FA-1')
    const paper = find('pc-paper')!.textContent ?? ''
    expect(paper).toContain('FA-1') // le titre `{{ name }}` rempli
    expect(paper).toContain('37,50') // un montant formaté comme une pièce, pas « 37.50 »
    expect(find('pc-col-article')).not.toBeNull() // les en-têtes du tableau, glissables
  })

  it('« Aperçu imprimé » montre le rendu du serveur à la place de la feuille', async () => {
    const { w } = await open()
    const paper = find('pc-desk') as HTMLElement
    expect(paper.style.display).not.toBe('none')
    const preview = [...document.querySelectorAll('[data-testid="pb-mode"] button, [data-testid="pb-mode"] [role="button"], [data-testid="pb-mode"] [data-pc-section="pctogglebutton"]')]
      .find((b) => b.textContent?.includes('Aperçu imprimé')) as HTMLElement | undefined
    expect(preview).toBeTruthy()
    preview!.click()
    await w.vm.$nextTick()
    expect(paper.style.display).toBe('none')
  })

  it('« Revenir à l’original » n’est offert que s’il y a un gabarit d’organisation à retirer', async () => {
    await open()
    expect(find('pb-reset')).toBeNull() // celui du plugin : rien à retirer
    unmountAll()
    document.body.innerHTML = ''
    await open({ source: 'organisation' })
    expect(find('pb-reset')).not.toBeNull()
  })
})
