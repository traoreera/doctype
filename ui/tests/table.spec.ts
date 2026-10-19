import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { describe, expect, it } from 'vitest'
import TableField from '@/components/TableField.vue'
import { ApiError } from '@/lib/api'
import { cellKey, childOf, fieldStates, isDirty, isEmpty, mapServerErrors, newRow, newValues, recordValues, toPayload, validate, type Row } from '@/lib/form'
import { SERVICES } from '@/lib/services'
import type { DocRecord } from '@/lib/types'
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

  it('en lecture seule : du texte, ni champ de saisie ni bouton', () => {
    const w = mountGrid([{ item_name: 'A', qty: 3, rate: 2, taxed: true }], { readOnly: true })
    expect(w.find('input').exists()).toBe(false)
    expect(w.find('button').exists()).toBe(false)
    expect(w.text()).toContain('A')
    expect(w.text()).toContain('Oui')
  })
})
