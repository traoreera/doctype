import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import TableField from '@/components/TableField.vue'
import { ApiError } from '@/lib/api'
import { cellKey, childOf, fieldStates, isDirty, isEmpty, mapServerErrors, newRow, newValues, recordValues, toPayload, validate, type Row } from '@/lib/form'
import { SERVICES } from '@/lib/services'
import type { ChildMeta, DocRecord, FieldMeta } from '@/lib/types'
import { INVOICE } from './fixtures'

const items = INVOICE.fields.find((f) => f.fieldname === 'items')!
const child = childOf(INVOICE, items)!
const rec = (data: Record<string, unknown>): DocRecord => ({
  doctype: 'Invoice', name: 'FA-2026-00001', owner: 'u', creation: '', modified: '', version: 2, docstatus: 0, amended_from: null, data,
})
const check = (values: Record<string, unknown>) => validate(INVOICE, values, fieldStates(INVOICE, values, null))

describe('valeurs d’un champ Table', () => {
  it('un nouveau document commence sans ligne', () => {
    expect(newValues(INVOICE).items).toEqual([])
  })

  it('une ligne neuve porte les défauts de son DocType, les cases décochées, le reste vide', () => {
    expect(newRow(child)).toEqual({ item_name: null, qty: 1, rate: null, taxed: false, note: null })
  })

  it('les lignes d’un document sont normalisées : un champ absent reste vide, une case absente est décochée', () => {
    const v = recordValues(INVOICE, rec({ customer: 'c', items: [{ item_name: 'A', rate: 5 }] }))
    expect(v.items).toEqual([{ item_name: 'A', qty: null, rate: 5, taxed: false, note: null }])
  })

  it('un document sans ligne, ou des métadonnées sans enfant, donnent un tableau vide et non null', () => {
    expect(recordValues(INVOICE, rec({ customer: 'c' })).items).toEqual([])
    expect(recordValues({ ...INVOICE, children: {} }, rec({ items: [{ item_name: 'A' }] })).items).toEqual([])
  })

  it('un tableau sans ligne est vide, pas une valeur', () => {
    expect(isEmpty([])).toBe(true)
    expect(isEmpty([{}])).toBe(false)
  })
})

describe('validation des lignes', () => {
  const base = { customer: 'c', posting_date: null, total: null }

  it('une facture sans ligne : le champ obligatoire est signalé', () => {
    expect(check({ ...base, items: [] })).toEqual({ items: 'Champ obligatoire' })
  })

  it('chaque cellule obligatoire est signalée avec sa ligne (à partir de 0)', () => {
    const rows: Row[] = [
      { item_name: 'A', qty: 1, rate: 2, taxed: false },
      { item_name: null, qty: 1, rate: null, taxed: false },
    ]
    expect(check({ ...base, items: rows })).toEqual({
      [cellKey('items', 1, 'item_name')]: 'Champ obligatoire',
      [cellKey('items', 1, 'rate')]: 'Champ obligatoire',
    })
    expect(cellKey('items', 1, 'rate')).toBe('items.1.rate')
  })

  it('les types des cellules sont vérifiés comme ceux du document, sans coercition', () => {
    const rows: Row[] = [{ item_name: 'A', qty: '2', rate: NaN, taxed: 'oui' }]
    expect(Object.keys(check({ ...base, items: rows })).sort()).toEqual(['items.0.qty', 'items.0.rate', 'items.0.taxed'])
  })

  it('un champ caché de la ligne n’est pas contrôlé', () => {
    const rows: Row[] = [{ item_name: 'A', qty: 1, rate: 2, taxed: false, note: 12 }]
    expect(check({ ...base, items: rows })).toEqual({})
  })

  it('des lignes valides ne produisent aucune erreur', () => {
    expect(check({ ...base, items: [{ item_name: 'A', qty: 1, rate: 2, taxed: false }] })).toEqual({})
  })
})

describe('charge utile et modifications', () => {
  const rows: Row[] = [
    { item_name: 'A', qty: 2, rate: 5.5, taxed: false, note: null },
    { item_name: 'B', qty: 0, rate: null, taxed: true, note: '' },
  ]

  it('chaque ligne envoie ses champs renseignés ; 0 et false sont des valeurs', () => {
    expect(toPayload(INVOICE, { customer: 'c', items: rows, total: null }).items).toEqual([
      { item_name: 'A', qty: 2, rate: 5.5, taxed: false },
      { item_name: 'B', qty: 0, taxed: true },
    ])
  })

  it('sans ligne, le champ n’est pas envoyé', () => {
    expect(toPayload(INVOICE, { customer: 'c', items: [], total: null })).toEqual({ customer: 'c' })
  })

  it('n’envoie jamais une clé hors DocType enfant', () => {
    const out = toPayload(INVOICE, { customer: 'c', items: [{ item_name: 'A', intrus: 1 }], total: null })
    expect(JSON.stringify(out)).not.toContain('intrus')
  })

  it('modifier une cellule, ajouter ou retirer une ligne sont des modifications ; remettre l’état d’origine n’en est plus une', () => {
    const saved = recordValues(INVOICE, rec({ customer: 'c', items: [{ item_name: 'A', qty: 1, rate: 2, taxed: false }] }))
    expect(isDirty(INVOICE, structuredClone(saved), saved)).toBe(false)
    const edited = structuredClone(saved)
    ;(edited.items as Row[])[0]!.qty = 3
    expect(isDirty(INVOICE, edited, saved)).toBe(true)
    ;(edited.items as Row[])[0]!.qty = 1
    expect(isDirty(INVOICE, edited, saved)).toBe(false)
    expect(isDirty(INVOICE, { ...saved, items: [...(saved.items as Row[]), newRow(child)] }, saved)).toBe(true)
    expect(isDirty(INVOICE, { ...saved, items: [] }, saved)).toBe(true)
  })
})

describe('erreurs du serveur sur les lignes', () => {
  const err = (details: { field: string; msg: string; type?: string }[]) => new ApiError(422, 'validation_failed', 'x', details)

  it('l’erreur d’une cellule se range sous sa clé complète', () => {
    const m = mapServerErrors(err([{ field: 'items.1.rate', msg: 'Champ obligatoire', type: 'missing' }]), INVOICE)
    expect(m.fields).toEqual({ 'items.1.rate': 'Champ obligatoire' })
    expect(m.general).toBe('Corrigez les champs en erreur')
  })

  it('un Link introuvable dans une ligne garde le message du serveur', () => {
    const m = mapServerErrors(err([{ field: 'items.0.item', msg: 'Article introuvable : \'X\'', type: 'link_not_found' }]), INVOICE)
    expect(m.fields['items.0.item']).toBe('Article introuvable : \'X\'')
  })

  it('un champ inconnu reste un message général', () => {
    const m = mapServerErrors(err([{ field: 'nope.0.x', msg: 'Autre', type: 'x' }]), INVOICE)
    expect(m.fields).toEqual({})
    expect(m.general).toBe('Autre')
  })
})

function mountGrid(rows: Row[], extra: Record<string, unknown> = {}) {
  return mount(TableField, {
    props: { field: items, child, modelValue: rows, required: true, readOnly: false, errors: {}, formId: 'f', parentValues: {}, ...extra },
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {} } },
    },
  })
}

describe('grille de lignes', () => {
  it('affiche une colonne par champ visible (le champ caché n’y est pas) et signale les colonnes obligatoires', () => {
    const w = mountGrid([])
    const heads = w.findAll('th').map((h) => h.text())
    expect(heads).toEqual(['N°', 'Article*', 'Quantité*', 'Prix*', 'Taxé', ''])
    expect(w.find('[data-testid="table-empty"]').exists()).toBe(true)
  })

  it('« Ajouter une ligne » émet les lignes actuelles plus une ligne neuve', async () => {
    const existing = [{ item_name: 'A', qty: 1, rate: 2, taxed: false, note: null }]
    const w = mountGrid(existing)
    await w.find('[data-testid="add-row-items"]').trigger('click')
    const [rows] = w.emitted('update:modelValue')!.at(-1) as [Row[]]
    expect(rows).toHaveLength(2)
    expect(rows[0]).toEqual(existing[0])
    expect(rows[1]).toEqual(newRow(child))
  })

  it('supprimer la ligne N retire seulement celle-là', async () => {
    const data: Row[] = [{ item_name: 'A' }, { item_name: 'B' }, { item_name: 'C' }]
    const w = mountGrid(data)
    await w.find('[data-testid="remove-row-1"]').trigger('click')
    const [rows] = w.emitted('update:modelValue')!.at(-1) as [Row[]]
    expect(rows.map((r) => r.item_name)).toEqual(['A', 'C'])
  })

  it('une modification de cellule est annoncée avec sa ligne et son champ, sans toucher aux autres lignes', async () => {
    const w = mountGrid([{ item_name: 'A', qty: 1, rate: 2, taxed: false }])
    const input = w.find('[data-col="item_name"] input')
    ;(input.element as HTMLInputElement).value = 'Z'
    await input.trigger('input')
    expect(w.emitted('cell')!.at(-1)).toEqual([0, 'item_name', 'Z'])
  })

  it('montre l’erreur sous la cellule fautive et marque la ligne', () => {
    const w = mountGrid([{ item_name: null, qty: 1, rate: 2, taxed: false }], { errors: { 'items.0.item_name': 'Champ obligatoire' } })
    expect(w.find('[data-testid="error-items.0.item_name"]').text()).toBe('Champ obligatoire')
    expect(w.find('tr[data-row="0"]').classes()).toContain('bad')
  })

  it('en lecture seule : du texte, aucune saisie, et rien qui MODIFIE la table', () => {
    const w = mountGrid([{ item_name: 'A', qty: 3, rate: 2, taxed: true }], { readOnly: true })
    expect(w.find('input').exists()).toBe(false)
    expect(w.text()).toContain('A')
    expect(w.text()).toContain('Oui')
    // Ni ajout ni suppression : un document figé ne change pas de lignes.
    expect(w.find('[data-testid="remove-row-0"]').exists()).toBe(false)
    expect(w.find('[data-testid="add-row-items"]').exists()).toBe(false)
    // « Ouvrir la ligne », en revanche, EXISTE : c'est ce qui rend lisible une ligne dont la grille ne montre que
    // quelques colonnes. Lire n'est pas modifier — et la fiche ouverte est elle aussi en lecture seule.
    expect(w.find('[data-testid="open-row-0"]').exists()).toBe(true)
  })
})

// ── Une table LARGE : ce que le jeu ERP donne vraiment (une ligne de facture a 70 champs visibles et en désigne 5) ──
const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })
const WIDE_CHILD: ChildMeta = {
  name: 'WideLine',
  label: 'Ligne large',
  fields: [
    f({ fieldname: 'item', label: 'Article', fieldtype: 'Text', in_list_view: true, required: true }),
    f({ fieldname: 'qty', label: 'Quantité', fieldtype: 'Number', in_list_view: true }),
    f({ fieldname: 'rate', label: 'Prix', fieldtype: 'Number', in_list_view: true }),
    f({ fieldname: 'warehouse', label: 'Entrepôt', fieldtype: 'Text' }),
    f({ fieldname: 'batch', label: 'Lot', fieldtype: 'Text' }),
    f({ fieldname: 'serial', label: 'Série', fieldtype: 'Text' }),
    f({ fieldname: 'remark', label: 'Remarque', fieldtype: 'Text' }),
    f({ fieldname: 'secret', label: 'Caché', fieldtype: 'Text', hidden: true }),
  ],
}
const WIDE_FIELD = f({ fieldname: 'lines', label: 'Lignes', fieldtype: 'Table', options: 'WideLine' })
const mountedWide: ReturnType<typeof mount>[] = []
function mountWide(rows: Row[], extra: Record<string, unknown> = {}) {
  // `attachTo` : le Dialog de PrimeVue est TÉLÉPORTÉ dans `body`, il n'est pas dans le wrapper. Et chaque montage est
  // démonté après le test, sinon la boîte téléportée resterait dans un `body` qu'on vide (rejets `insertBefore`).
  const w = mount(TableField, {
    props: { field: WIDE_FIELD, child: WIDE_CHILD, modelValue: rows, required: false, readOnly: false, errors: {}, formId: 'f', parentValues: {}, ...extra },
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {} } },
    },
  })
  mountedWide.push(w)
  return w
}
/** La fiche de ligne, cherchée dans le corps du document. */
const sheet = () => document.body.querySelector<HTMLElement>('[data-testid="row-sheet"]')
const inSheet = (selector: string) => sheet()!.querySelector<HTMLElement>(selector)!

describe('une table large reste lisible', () => {
  const heads = (w: ReturnType<typeof mountWide>) => w.findAll('th').map((h) => h.text()).filter((x) => x && x !== 'N°')

  it('la grille ne montre que les colonnes que la définition DÉSIGNE (`in_list_view`)', () => {
    // Sept champs visibles, trois désignés : la grille en montre trois, dans l'ordre de la définition.
    expect(heads(mountWide([{ item: 'A' }]))).toEqual(['Article*', 'Quantité', 'Prix'])
  })

  it('sans colonne désignée, la grille en montre quatre — la règle du moteur pour la liste d’un type', () => {
    const child = { ...WIDE_CHILD, fields: WIDE_CHILD.fields.map((c) => ({ ...c, in_list_view: false })) }
    expect(heads(mountWide([{ item: 'A' }], { child }))).toEqual(['Article*', 'Quantité', 'Prix', 'Entrepôt'])
  })

  it('la FICHE de la ligne montre TOUS ses champs visibles, et le champ caché reste caché', async () => {
    const w = mountWide([{ item: 'A', warehouse: 'Nord', batch: 'L1' }])
    await w.find('[data-testid="open-row-0"]').trigger('click')
    expect(sheet()).not.toBeNull()
    expect([...sheet()!.querySelectorAll('.row-sheet-field')].map((d) => d.getAttribute('data-field'))).toEqual(
      ['item', 'qty', 'rate', 'warehouse', 'batch', 'serial', 'remark'],
    )
  })

  it('la fiche est un TABLEAU : un champ par ligne, son nom à gauche, sa valeur à droite', async () => {
    const w = mountWide([{ item: 'A', warehouse: 'Nord' }])
    await w.find('[data-testid="open-row-0"]').trigger('click')
    // Une paire nom/valeur par champ, pas deux lignes empilées : 64 champs tenaient sur 128 lignes.
    const field = inSheet('[data-field="warehouse"]')
    expect(field.querySelector('dt')?.textContent?.trim()).toBe('Entrepôt')
    expect(field.querySelector('dd')).not.toBeNull()
    expect(sheet()!.querySelectorAll('.row-sheet-field > dt')).toHaveLength(7)
  })

  it('en saisie le nom est un vrai `label for` ; figé, il n’étiquette rien et reste un `dt`', async () => {
    const edit = mountWide([{ item: 'A' }])
    await edit.find('[data-testid="open-row-0"]').trigger('click')
    const forAttr = inSheet('[data-field="warehouse"] label')!.getAttribute('for')
    expect(forAttr).toBeTruthy()
    expect(document.getElementById(forAttr!)).not.toBeNull() // il désigne le champ, le clic donne le focus
    edit.unmount()
    document.body.innerHTML = ''

    // Renseigné : en lecture, la fiche ne montre que ce qui l'est.
    const frozen = mountWide([{ item: 'A', warehouse: 'Nord' }], { readOnly: true })
    await frozen.find('[data-testid="open-row-0"]').trigger('click')
    // Figé, il n'y a aucun champ : un `label for` ne pointerait sur rien.
    expect(inSheet('[data-field="warehouse"]').querySelector('label')).toBeNull()
    expect(inSheet('[data-field="warehouse"]').querySelector('dt')?.textContent?.trim()).toBe('Entrepôt')
  })

  it('en LECTURE la fiche ne montre que les champs renseignés, et dit combien elle en cache', async () => {
    const w = mountWide([{ item: 'A', qty: 0, rate: null, warehouse: '', batch: 'L1', serial: undefined }], { readOnly: true })
    await w.find('[data-testid="open-row-0"]').trigger('click')
    // `0` est une VALEUR, `null`, `''` et absent n'en sont pas.
    expect([...sheet()!.querySelectorAll('.row-sheet-field')].map((d) => d.getAttribute('data-field'))).toEqual(['item', 'qty', 'batch'])
    expect(inSheet('[data-testid="row-sheet-empty"]').textContent).toContain('4')
  })

  it('on peut redemander les champs vides, et la fiche suivante repart courte', async () => {
    const w = mountWide([{ item: 'A' }, { item: 'B' }], { readOnly: true })
    await w.find('[data-testid="open-row-0"]').trigger('click')
    expect(sheet()!.querySelectorAll('.row-sheet-field')).toHaveLength(1)
    inSheet('[data-testid="row-sheet-empty"]').click()
    await nextTick()
    expect(sheet()!.querySelectorAll('.row-sheet-field')).toHaveLength(7)

    inSheet('[data-testid="row-sheet-close"]').click()
    await nextTick()
    await w.find('[data-testid="open-row-1"]').trigger('click')
    expect(sheet()!.querySelectorAll('.row-sheet-field')).toHaveLength(1)
  })

  it('en SAISIE rien n’est caché : un champ vide est celui qu’on vient remplir', async () => {
    const w = mountWide([{ item: 'A' }])
    await w.find('[data-testid="open-row-0"]').trigger('click')
    expect(sheet()!.querySelectorAll('.row-sheet-field')).toHaveLength(7)
    expect(sheet()!.querySelector('[data-testid="row-sheet-empty"]')).toBeNull()
  })

  it('saisir un champ REPLIÉ depuis la fiche remonte sur la bonne ligne', async () => {
    const w = mountWide([{ item: 'A' }, { item: 'B' }])
    await w.find('[data-testid="open-row-1"]').trigger('click')
    const input = inSheet('[data-field="batch"] input') as HTMLInputElement
    input.value = 'L7'
    input.dispatchEvent(new Event('input'))
    await nextTick()
    expect(w.emitted('cell')?.at(-1)).toEqual([1, 'batch', 'L7'])
  })

  it('la fiche montre les valeurs de SA ligne', async () => {
    const w = mountWide([{ item: 'A', remark: 'première' }, { item: 'B', remark: 'seconde' }])
    await w.find('[data-testid="open-row-1"]').trigger('click')
    expect((inSheet('[data-field="remark"] input') as HTMLInputElement).value).toBe('seconde')
  })

  it('supprimer depuis la fiche retire CETTE ligne et referme', async () => {
    const w = mountWide([{ item: 'A' }, { item: 'B' }])
    await w.find('[data-testid="open-row-0"]').trigger('click')
    inSheet('[data-testid="row-sheet-remove"]')!.click()
    await nextTick()
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([[{ item: 'B' }]])
    expect(sheet()).toBeNull()
  })
})

afterEach(() => {
  // Démonter AVANT de vider le corps : une boîte téléportée laissée là fait échouer `insertBefore`.
  while (mountedWide.length) mountedWide.pop()!.unmount()
  document.body.innerHTML = ''
})
