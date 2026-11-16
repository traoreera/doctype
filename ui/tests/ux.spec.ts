import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { afterEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import RecordCards from '@/components/RecordCards.vue'
import { isAutoSection, isWide } from '@/lib/form'
import { piIcon } from '@/lib/icons'
import { SERVICES } from '@/lib/services'
import { MAX_SIDEBAR_GROUPS, sidebarFields } from '@/lib/sidebar'
import type { DocRecord, DocTypeMeta, FieldMeta } from '@/lib/types'
import { CUSTOMER } from './fixtures'

const f = (fieldname: string, fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({ fieldname, fieldtype, required: false, hidden: false, in_list_view: false, ...extra })
const metaOf = (fields: FieldMeta[], list_columns: string[] = []): DocTypeMeta => ({ ...CUSTOMER, fields, list_columns, layout: [] })

describe('piIcon : une icône qui existe, sinon celle de repli', () => {
  it('garde un nom bien formé', () => {
    expect(piIcon('receipt')).toBe('pi pi-receipt')
    expect(piIcon('arrow-right')).toBe('pi pi-arrow-right')
  })

  it('un nom absent, vide ou mal formé donne le repli, jamais une classe injectée', () => {
    expect(piIcon(null)).toBe('pi pi-file')
    expect(piIcon(undefined, 'box')).toBe('pi pi-box')
    expect(piIcon('', 'box')).toBe('pi pi-box')
    expect(piIcon('x" onclick="y')).toBe('pi pi-file')
    expect(piIcon('Receipt')).toBe('pi pi-file') // les noms PrimeIcons sont en minuscules
  })
})

describe('sidebarFields : ce que la barre latérale d’une liste propose', () => {
  it('un type sans champ à choix n’a pas de barre latérale (sinon son tableau serait écrasé dans la colonne étroite)', () => {
    expect(sidebarFields(metaOf([f('title', 'Text'), f('qty', 'Number')]))).toEqual([])
  })

  it('Select, Link et Booléen seulement, les colonnes de la liste d’abord, trois au plus', () => {
    const meta = metaOf(
      [f('a', 'Select'), f('b', 'Link'), f('c', 'Boolean'), f('d', 'Select'), f('t', 'Text')],
      ['t', 'd', 'c'],
    )
    const names = sidebarFields(meta).map((x) => x.fieldname)
    expect(names).toEqual(['d', 'c', 'a'])
    expect(names).toHaveLength(MAX_SIDEBAR_GROUPS)
  })

  it('un champ caché n’y figure pas', () => {
    expect(sidebarFields(metaOf([f('a', 'Select', { hidden: true }), f('b', 'Link')])).map((x) => x.fieldname)).toEqual(['b'])
  })
})

describe('formulaire en deux colonnes', () => {
  it('seuls les champs longs prennent toute la largeur', () => {
    expect(['LongText', 'Code', 'JSON', 'Table', 'TextEditor'].every((t) => isWide(f('x', t as FieldMeta['fieldtype'])))).toBe(true)
    expect(['Text', 'Number', 'Date', 'Link', 'Select'].some((t) => isWide(f('x', t as FieldMeta['fieldtype'])))).toBe(false)
  })

  it('seule une section sans titre, non repliable et à une colonne est rangée automatiquement', () => {
    const s = { section: null, collapsible: false, collapsed: false, columns: [['a', 'b']] }
    expect(isAutoSection(s)).toBe(true)
    expect(isAutoSection({ ...s, section: 'Crédit' })).toBe(false)
    expect(isAutoSection({ ...s, collapsible: true })).toBe(false)
    expect(isAutoSection({ ...s, columns: [['a'], ['b']] })).toBe(false) // composée par l'auteur du type
  })
})

describe('RecordCards : la liste d’un téléphone', () => {
  const row = (name: string, data: Record<string, unknown>): DocRecord => ({ doctype: 'Customer', name, owner: 'u', creation: '', modified: '2026-01-01T00:00:00+00:00', version: 1, docstatus: 0, amended_from: null, data })
  const columns = CUSTOMER.fields.filter((x) => ['customer_name', 'type', 'credit_limit', 'country', 'since'].includes(x.fieldname))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/app/:doctype/:name', name: 'form', component: { render: () => null } }] })

  function cards(selection: DocRecord[] = [], selectable = true) {
    const rows = [row('c1', { customer_name: 'ACME', type: 'Entreprise', credit_limit: 5, country: 'FR', since: '2026-01-01' }), row('c2', { customer_name: 'Bob' })]
    return mount(RecordCards, {
      props: { doctype: 'Customer', meta: CUSTOMER, rows, columns, selection, selectable, cell: (field: FieldMeta, r: DocRecord) => String(r.data[field.fieldname] ?? ''), title: (r: DocRecord) => r.name },
      global: {
        plugins: [PrimeVue, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { list: async () => ({ items: [], total: 0 }), meta: async () => CUSTOMER } } },
      },
      attachTo: document.body,
    })
  }
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('une carte par document, trois champs au plus sous le titre, les champs vides omis', () => {
    const w = cards()
    expect(w.findAll('[data-testid^="card-"]')).toHaveLength(2)
    expect(w.find('[data-testid="card-c1"]').findAll('dt')).toHaveLength(3)
    expect(w.find('[data-testid="card-c2"]').findAll('dt')).toHaveLength(0)
    w.unmount()
  })

  it('toucher la carte ouvre le document', async () => {
    const w = cards()
    await w.find('[data-testid="card-c2"]').trigger('click')
    expect(w.emitted('open')?.[0]?.[0]).toMatchObject({ name: 'c2' })
    w.unmount()
  })

  it('la case à cocher choisit le document sans l’ouvrir', async () => {
    const w = cards()
    const box = w.find('[data-testid="pick-c1"]')
    const input = box.element.tagName === 'INPUT' ? box : box.find('input')
    await input.trigger('click') // le clic remonte jusqu'à la carte, qui ne doit pas s'ouvrir
    await input.setValue(true)
    await flushPromises()
    expect(w.emitted('open')).toBeUndefined()
    expect((w.emitted('update:selection')?.[0]?.[0] as DocRecord[]).map((r) => r.name)).toEqual(['c1'])
    w.unmount()
  })

  it('sans action en lot possible, pas de case à cocher', () => {
    const w = cards([], false)
    expect(w.find('[data-testid="pick-c1"]').exists()).toBe(false)
    w.unmount()
  })

  it('une carte choisie le montre', () => {
    const w = cards([row('c1', {})])
    expect(w.find('[data-testid="card-c1"]').classes()).toContain('picked')
    expect(w.find('[data-testid="card-c2"]').classes()).not.toContain('picked')
    w.unmount()
  })
})
