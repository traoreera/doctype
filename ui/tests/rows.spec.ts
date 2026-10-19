import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { describe, expect, it, vi } from 'vitest'
import TableField from '@/components/TableField.vue'
import { cellStates, childOf, newValues, rowContext, toPayload, validate, fieldStates, type Row, type Values } from '@/lib/form'
import { todayIn } from '@/lib/format'
import { SERVICES } from '@/lib/services'
import { unitLinks, unitOf, unitSource } from '@/lib/units'
import type { DocRecord } from '@/lib/types'
import { MOVE } from './fixtures'

const lines = MOVE.fields.find((f) => f.fieldname === 'items')!
const child = childOf(MOVE, lines)!
const doc = (purpose: string | null, rows: Row[]): Values => ({ ...newValues(MOVE), purpose, posting_date: '2026-06-15', items: rows })
const row = (extra: Row = {}): Row => ({ item: 'vis', qty: '2.000', source_warehouse: null, target_warehouse: null, ...extra })
const check = (v: Values) => validate(MOVE, v, fieldStates(MOVE, v, null))

describe('conditions d’une ligne : la ligne ET son parent', () => {
  it('le parent se lit par parent_<champ>, les tableaux n’y sont pas', () => {
    expect(rowContext({ a: 1 }, { purpose: 'Sortie', items: [{}] })).toEqual({ a: 1, parent_purpose: 'Sortie' })
  })

  it.each([
    ['Réception', { source: false, target: true }],
    ['Sortie', { source: true, target: false }],
    ['Transfert', { source: true, target: true }],
    [null, { source: true, target: true }], // nature pas encore choisie : on ne cache rien
  ])('%s : source %j', (purpose, expected) => {
    const s = cellStates(child, row(), { purpose })
    expect({ source: s.source_warehouse!.visible, target: s.target_warehouse!.visible }).toEqual(expected)
  })

  it('obligatoire selon la nature, pas selon la colonne', () => {
    const s = (purpose: string) => cellStates(child, row(), { purpose })
    expect([s('Réception').source_warehouse!.required, s('Réception').target_warehouse!.required]).toEqual([false, true])
    expect([s('Sortie').source_warehouse!.required, s('Sortie').target_warehouse!.required]).toEqual([true, false])
    expect([s('Transfert').source_warehouse!.required, s('Transfert').target_warehouse!.required]).toEqual([true, true])
  })
})

describe('validation et envoi des lignes', () => {
  it('une cellule rendue obligatoire par la nature est signalée sous sa cellule', () => {
    expect(check(doc('Sortie', [row()]))).toEqual({ 'items.0.source_warehouse': 'Champ obligatoire' })
    expect(check(doc('Réception', [row()]))).toEqual({ 'items.0.target_warehouse': 'Champ obligatoire' })
    expect(check(doc('Transfert', [row(), row({ source_warehouse: 'A', target_warehouse: 'B' })]))).toEqual({
      'items.0.source_warehouse': 'Champ obligatoire',
      'items.0.target_warehouse': 'Champ obligatoire',
    })
  })

  it('la cellule que la nature écarte n’est jamais exigée', () => {
    expect(check(doc('Sortie', [row({ source_warehouse: 'A' })]))).toEqual({})
    expect(check(doc('Réception', [row({ target_warehouse: 'B' })]))).toEqual({})
  })

  it('une valeur laissée dans une cellule écartée ne part pas : l’utilisateur ne la voit plus', () => {
    // L'utilisateur avait choisi « Transfert », rempli les deux entrepôts, puis changé la nature en « Réception ».
    const payload = toPayload(MOVE, doc('Réception', [row({ source_warehouse: 'A', target_warehouse: 'B' })]))
    expect(payload.items).toEqual([{ item: 'vis', qty: '2.000', target_warehouse: 'B' }])
  })

  it('changer la nature d’un document change les erreurs de ses lignes', () => {
    const rows = [row({ target_warehouse: 'B' })]
    expect(check(doc('Réception', rows))).toEqual({})
    expect(check(doc('Transfert', rows))).toEqual({ 'items.0.source_warehouse': 'Champ obligatoire' })
  })
})

describe('défaut « aujourd’hui »', () => {
  it('un nouveau document porte la date du jour de l’ORGANISATION', () => {
    expect(newValues(MOVE).posting_date).toBe(todayIn('Europe/Paris'))
    expect(newValues(MOVE).posting_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('c’est le jour du fuseau de l’organisation, pas celui du navigateur ni d’UTC', () => {
    const lateUtc = new Date('2026-06-15T22:30:00Z')
    expect(todayIn('UTC', lateUtc)).toBe('2026-06-15')
    expect(todayIn('Europe/Paris', lateUtc)).toBe('2026-06-16') // minuit passé à Paris
    expect(todayIn('Pacific/Auckland', lateUtc)).toBe('2026-06-16')
    expect(todayIn('America/Los_Angeles', lateUtc)).toBe('2026-06-15')
  })

  it('un fuseau inconnu ne casse pas le formulaire', () => {
    expect(todayIn('Nulle/Part', new Date('2026-06-15T12:00:00'))).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('un défaut ordinaire reste ce qu’il est', () => {
    expect(newValues({ ...MOVE, fields: [{ ...MOVE.fields[0]!, default: 'Sortie' }] }).purpose).toBe('Sortie')
  })
})

describe('unités', () => {
  const qty = child.fields.find((f) => f.fieldname === 'qty')!
  const read = (target: string, name: string, fieldname: string) => (target === 'Item' && name === 'vis' && fieldname === 'uom' ? 'kg' : undefined)

  it('la forme item.uom suit le Link, puis lit le champ de sa cible', () => {
    expect(unitSource(qty, child.fields)).toMatchObject({ field: 'uom', link: { fieldname: 'item' } })
    expect(unitOf(qty, child.fields, { item: 'vis' }, read)).toBe('kg')
  })

  it('pas d’unité tant que le document n’est pas lu, ou si l’article est vide ou inconnu', () => {
    expect(unitOf(qty, child.fields, { item: 'vis' }, () => undefined)).toBeNull()
    expect(unitOf(qty, child.fields, { item: null }, read)).toBeNull()
    expect(unitOf(qty, child.fields, { item: 'inconnu' }, read)).toBeNull()
  })

  it('la forme uom lit un champ du même document', () => {
    const own = { ...qty, unit_from: 'unit' }
    expect(unitOf(own, [own], { unit: 'm' }, read)).toBe('m')
    expect(unitOf(own, [own], { unit: '' }, read)).toBeNull()
  })

  it('un champ sans unit_from, ou dont le Link est introuvable, n’a pas d’unité', () => {
    expect(unitSource({ ...qty, unit_from: undefined }, child.fields)).toBeNull()
    expect(unitSource({ ...qty, unit_from: 'ghost.uom' }, child.fields)).toBeNull()
  })

  it('les articles à lire se rassemblent par DocType cible', () => {
    const wanted = unitLinks(child.fields, [{ item: 'vis' }, { item: 'ecrou' }, { item: 'vis' }, {}])
    expect([...wanted.entries()].map(([k, v]) => [k, [...v].sort()])).toEqual([['Item', ['ecrou', 'vis']]])
  })
})

function mountGrid(purpose: string | null, rows: Row[], extra: Record<string, unknown> = {}, api: Record<string, unknown> = {}) {
  return mount(TableField, {
    props: { field: lines, child, modelValue: rows, required: true, readOnly: false, errors: {}, formId: 'f', parentValues: { purpose }, ...extra },
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: {
        [SERVICES as symbol]: {
          transport: {},
          config: { baseUrl: '', loginUrl: null },
          api: {
            meta: vi.fn(async () => ({ name: 'Item', fields: [], title_field: 'item_name' })),
            list: vi.fn(async () => ({ items: [{ name: 'vis', data: { item_name: 'Vis', uom: 'kg' } }], total: 1, limit: 1, offset: 0 })),
            search: vi.fn(async () => []),
            ...api,
          },
        },
      },
    },
  })
}
const heads = (w: ReturnType<typeof mountGrid>) => w.findAll('th').map((h) => h.text())

describe('grille : colonnes selon la nature', () => {
  it('une réception n’a pas de colonne « source », une sortie pas de « cible »', () => {
    expect(heads(mountGrid('Réception', [row()]))).toEqual(['N°', 'Article*', 'Quantité*', 'Entrepôt cible*', ''])
    expect(heads(mountGrid('Sortie', [row()]))).toEqual(['N°', 'Article*', 'Quantité*', 'Entrepôt source*', ''])
    expect(heads(mountGrid('Transfert', [row()]))).toEqual(['N°', 'Article*', 'Quantité*', 'Entrepôt source*', 'Entrepôt cible*', ''])
  })

  it('sans ligne, les bonnes colonnes sont déjà là pour la première', () => {
    expect(heads(mountGrid('Sortie', []))).toEqual(['N°', 'Article*', 'Quantité*', 'Entrepôt source*', ''])
  })

  it('changer la nature fait apparaître et disparaître les colonnes', async () => {
    const w = mountGrid('Réception', [row()])
    await w.setProps({ parentValues: { purpose: 'Transfert' } })
    expect(heads(w)).toContain('Entrepôt source*')
    await w.setProps({ parentValues: { purpose: 'Sortie' } })
    expect(heads(w)).not.toContain('Entrepôt cible*')
  })

  it('la colonne qui existe pour une autre ligne reste, avec une cellule vide ici', () => {
    // Deux lignes cohérentes avec le même parent : ici le parent écarte la « source » pour TOUTES les lignes, donc pas de colonne.
    const w = mountGrid('Réception', [row(), row()])
    expect(w.find('[data-col="source_warehouse"]').exists()).toBe(false)
  })
})

describe('grille : unités et saisie au clavier', () => {
  it('l’unité de l’article s’affiche à côté de la quantité une fois l’article lu', async () => {
    const w = mountGrid('Réception', [row()])
    await flushPromises()
    expect(w.find('[data-testid="unit-0-qty"]').text()).toBe('kg')
  })

  it('en lecture seule, l’unité suit la valeur', async () => {
    const w = mountGrid('Réception', [row({ qty: '12.500', target_warehouse: 'p' })], { readOnly: true })
    await flushPromises()
    expect(w.find('[data-col="qty"]').text()).toBe('12,500kg')
  })

  it('une seule lecture des articles pour toutes les lignes', async () => {
    const list = vi.fn(async (_target: string, _query: unknown) => ({ items: [], total: 0, limit: 1, offset: 0 }))
    mountGrid('Réception', [row(), row({ item: 'vis' }), row({ item: 'ecrou' })], {}, { list })
    await flushPromises()
    const itemCalls = list.mock.calls.filter(([t]) => t === 'Item')
    expect(itemCalls).toHaveLength(1)
    expect(itemCalls[0]![1]).toMatchObject({ filters: { name: ['in', ['vis', 'ecrou']] } })
  })

  it('Tab dans la dernière cellule de la dernière ligne ajoute une ligne', async () => {
    const w = mountGrid('Réception', [row({ target_warehouse: 'p' })], {}, {})
    const emitted = () => (w.emitted('update:modelValue') ?? []).length
    const inputs = w.findAll('tbody tr:last-child td:not(.act) input')
    await inputs[inputs.length - 1]!.trigger('keydown', { key: 'Tab' })
    expect(emitted()).toBe(1)
    expect((w.emitted('update:modelValue')![0]![0] as Row[]).length).toBe(2)
  })

  it('Tab ailleurs, Maj+Tab et la lecture seule n’ajoutent rien', async () => {
    const w = mountGrid('Réception', [row({ target_warehouse: 'p' })])
    const inputs = w.findAll('tbody tr:last-child td:not(.act) input')
    await inputs[0]!.trigger('keydown', { key: 'Tab' }) // pas la dernière cellule
    await inputs[inputs.length - 1]!.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(w.emitted('update:modelValue')).toBeUndefined()
    const ro = mountGrid('Réception', [row({ target_warehouse: 'p' })], { readOnly: true })
    await ro.trigger('keydown', { key: 'Tab' })
    expect(ro.emitted('update:modelValue')).toBeUndefined()
  })
})
