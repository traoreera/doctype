import DataTable from 'primevue/datatable'
import MultiSelect from 'primevue/multiselect'
import Select from 'primevue/select'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ReportView from '@/components/views/ReportView.vue'
import { downloadText } from '@/lib/download'
import { ApiError } from '@/lib/api'
import type { AggregateResult, Filters } from '@/lib/types'
import { TASK, captureToasts, doc, flushPromises, mountWith, page } from './views-fixtures'

vi.mock('@/lib/download', () => ({ downloadText: vi.fn() }))

beforeEach(() => {
  localStorage.clear()
  vi.mocked(downloadText).mockClear()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-17T10:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})
const toasts = captureToasts()

const metric = (fn: 'count' | 'sum', field: string | null = null, decimals: number | null = null) => ({ function: fn, field, label: null, decimals })
const totals = (...values: (number | string | null)[]): AggregateResult => ({
  group: null,
  metrics: [metric('count'), metric('sum', 'hours')],
  rows: [{ key: null, values }],
  truncated: false,
})
const byState: AggregateResult = {
  group: { field: 'state', bucket: null, fieldtype: 'Select', options: ['À faire', 'En cours', 'Fait'] },
  metrics: [metric('count'), metric('sum', 'hours')],
  rows: [
    { key: 'Fait', values: [3, 12.5] },
    { key: 'À faire', values: [2, 4] },
  ],
  truncated: false,
}

const tasks = [doc('t1', { title: 'Poser', hours: 3 }), doc('t2', { title: 'Peindre', hours: 1.5 })]
function api(over: Record<string, unknown> = {}) {
  return {
    list: vi.fn(async () => page(tasks, 2)),
    aggregate: vi.fn(async (_d: string, q?: { group_by?: string }) => (q?.group_by ? byState : totals(2, 4.5))),
    ...over,
  }
}
const open = async (a: ReturnType<typeof api>, filters: Filters = {}) => {
  const out = mountWith(ReportView, { doctype: 'Task', meta: TASK, filters }, a)
  await flushPromises()
  return out
}
const headers = (w: Awaited<ReturnType<typeof open>>['w']) => w.findAll('thead th').map((th) => th.text())

describe('rapport : les documents', () => {
  it('les colonnes de la liste par défaut, la première ouvre le document', async () => {
    const { w } = await open(api())
    expect(headers(w)).toEqual(['Titre', 'État', 'Heures'])
    expect(w.find('[data-testid="open-t1"]').attributes('href')).toBe('/app/Task/t1')
    expect(w.find('[data-testid="open-t1"]').text()).toBe('Poser')
  })

  it('la requête porte les filtres, le tri et la page', async () => {
    const a = api()
    await open(a, { state: 'Fait' })
    expect(a.list).toHaveBeenCalledWith('Task', { filters: { state: 'Fait' }, order_by: 'modified', order: 'desc', limit: 20, offset: 0 })
  })

  it('trier relit la liste dans ce tri ; changer de page garde le tri', async () => {
    const a = api()
    const { w } = await open(a)
    w.findComponent(DataTable).vm.$emit('sort', { sortField: 'hours', sortOrder: 1 })
    await flushPromises()
    expect(a.list).toHaveBeenLastCalledWith('Task', expect.objectContaining({ order_by: 'hours', order: 'asc', offset: 0 }))
    w.findComponent(DataTable).vm.$emit('page', { first: 20, rows: 20 })
    await flushPromises()
    expect(a.list).toHaveBeenLastCalledWith('Task', expect.objectContaining({ order_by: 'hours', order: 'asc', offset: 20 }))
  })

  it('le pied de tableau donne le total des colonnes numériques, calculé par le serveur sur TOUS les documents', async () => {
    const a = api()
    const { w } = await open(a, { state: 'Fait' })
    expect(a.aggregate).toHaveBeenCalledWith('Task', { metrics: ['count', 'sum:hours'], filters: { state: 'Fait' } })
    const foot = w.findAll('tfoot td').map((td) => td.text())
    expect(foot).toEqual(['Total', '', '4,5'])
  })

  it('une colonne Decimal ajoutée s’additionne exactement', async () => {
    const a = api({
      aggregate: vi.fn(async () => ({ group: null, metrics: [metric('count'), metric('sum', 'hours'), metric('sum', 'cost', 2)], rows: [{ key: null, values: [2, 4.5, '1234.50'] }], truncated: false })),
    })
    const { w } = await open(a)
    w.findComponent(MultiSelect).vm.$emit('update:modelValue', ['title', 'hours', 'cost'])
    await flushPromises()
    expect(a.aggregate).toHaveBeenLastCalledWith('Task', { metrics: ['count', 'sum:hours', 'sum:cost'], filters: {} })
    expect(w.findAll('tfoot td').map((td) => td.text().replace(/\s/g, ' '))).toEqual(['Total', '4,5', '1 234,50'])
  })

  it('une erreur du serveur est dite', async () => {
    const a = api({
      list: vi.fn(async () => {
        throw new ApiError(500, 'internal_error', 'Erreur interne')
      }),
    })
    const { w } = await open(a)
    expect(w.find('[data-testid="report-error"]').exists()).toBe(true)
  })
})

describe('rapport : les colonnes choisies', () => {
  it('le choix se retient pour ce type sur cet appareil', async () => {
    const first = await open(api())
    first.w.findComponent(MultiSelect).vm.$emit('update:modelValue', ['title', 'prio', 'hours'])
    await flushPromises()
    expect(headers(first.w)).toEqual(['Titre', 'Priorité', 'Heures'])
    expect(JSON.parse(localStorage.getItem('xdt.desk.columns.Task')!)).toEqual(['title', 'prio', 'hours'])
    document.body.innerHTML = ''
    const again = await open(api())
    expect(headers(again.w)).toEqual(['Titre', 'Priorité', 'Heures'])
  })

  it('une colonne qui n’existe plus est oubliée ; « colonnes par défaut » rétablit la liste', async () => {
    localStorage.setItem('xdt.desk.columns.Task', JSON.stringify(['title', 'supprimée']))
    const { w } = await open(api())
    expect(headers(w)).toEqual(['Titre'])
    expect(w.find('[data-testid="report-reset"]').exists()).toBe(true)
    await w.find('[data-testid="report-reset"]').trigger('click')
    await flushPromises()
    expect(headers(w)).toEqual(['Titre', 'État', 'Heures'])
    expect(w.find('[data-testid="report-reset"]').exists()).toBe(false)
  })

  it('les colonnes système (nom, propriétaire, modification) sont proposées', async () => {
    const { w } = await open(api())
    const options = (w.findComponent(MultiSelect).props('options') as { key: string }[]).map((o) => o.key)
    expect(options).toEqual(expect.arrayContaining(['name', 'owner', 'creation', 'modified']))
    expect(options).not.toContain('docstatus') // pas de statut pour un type qui ne se soumet pas
    expect(options).not.toContain('lines')
  })
})

describe('rapport : regrouper', () => {
  const group = async (w: Awaited<ReturnType<typeof open>>['w'], value: string) => {
    const selects = w.findAllComponents(Select)
    selects[0]!.vm.$emit('update:modelValue', value)
    await flushPromises()
  }

  it('un groupe par valeur : le nombre et le total de chaque colonne numérique, puis le total général', async () => {
    const a = api()
    const { w } = await open(a, { prio: 'Haute' })
    await group(w, 'state')
    expect(a.aggregate).toHaveBeenCalledWith('Task', { group_by: 'state', bucket: undefined, metrics: ['count', 'sum:hours'], filters: { prio: 'Haute' }, limit: 400 })
    const rows = w.findAll('[data-testid^="report-group-row-"]').map((r) => r.findAll('td, th').map((c) => c.text()))
    expect(rows).toEqual([['Fait', '3', '12,5'], ['À faire', '2', '4']])
    expect(w.findAll('[data-testid="report-total"] td, [data-testid="report-total"] th').map((c) => c.text())).toEqual(['Total', '2', '4,5'])
    expect(headers(w)).toEqual(['État', 'Nombre', 'Heures'])
    expect(w.find('[data-testid="report-grouped"]').exists()).toBe(true)
  })

  it('cliquer un groupe filtre sur sa valeur : la liste revient avec ce filtre', async () => {
    const { w } = await open(api())
    await group(w, 'state')
    await w.find('[data-testid="report-group-row-0"] button').trigger('click')
    expect(w.emitted('drill')).toEqual([['state', 'Fait']])
    await flushPromises()
    expect(w.find('[data-testid="report-grouped"]').exists()).toBe(false) // le regroupement est levé : on lit les documents
  })

  it('regrouper par date propose la période, et un groupe de dates n’est pas cliquable', async () => {
    const byMonth: AggregateResult = {
      group: { field: 'start', bucket: 'month', fieldtype: 'Date' },
      metrics: [metric('count'), metric('sum', 'hours')],
      rows: [{ key: '2026-10', values: [2, 4.5] }],
      truncated: false,
    }
    const a = api({ aggregate: vi.fn(async (_d: string, q?: { group_by?: string }) => (q?.group_by ? byMonth : totals(2, 4.5))) })
    const { w } = await open(a)
    expect(w.find('[data-testid="report-bucket"]').exists()).toBe(false)
    await group(w, 'start')
    expect(w.find('[data-testid="report-bucket"]').exists()).toBe(true)
    expect(a.aggregate).toHaveBeenCalledWith('Task', expect.objectContaining({ group_by: 'start', bucket: 'month' }))
    expect(w.find('[data-testid="report-group-row-0"] button').exists()).toBe(false)
    expect(w.find('[data-testid="report-group-row-0"] td').text()).toMatch(/oct.*26/)
  })

  it('un groupe sans valeur s’appelle « (vide) » et ne se clique pas', async () => {
    const res: AggregateResult = { ...byState, rows: [{ key: null, values: [1, 1] }] }
    const a = api({ aggregate: vi.fn(async (_d: string, q?: { group_by?: string }) => (q?.group_by ? res : totals(1, 1))) })
    const { w } = await open(a)
    await group(w, 'state')
    expect(w.find('[data-testid="report-group-row-0"] td').text()).toBe('(vide)')
    expect(w.find('[data-testid="report-group-row-0"] button').exists()).toBe(false)
  })

  it('« Aucun regroupement » revient aux documents', async () => {
    const { w } = await open(api())
    await group(w, 'state')
    await group(w, '')
    expect(w.find('[data-testid="report-grouped"]').exists()).toBe(false)
    expect(headers(w)).toEqual(['Titre', 'État', 'Heures'])
  })
})

describe('rapport : export CSV', () => {
  const written = () => vi.mocked(downloadText).mock.calls[0]!

  it('toutes les lignes qui correspondent, page à page, dans le tri affiché, au format d’un tableur francophone', async () => {
    const rows = [doc('t1', { title: 'Poser', hours: 3, state: 'Fait' }), doc('t2', { title: 'Peindre', hours: 1.5 })]
    const list = vi.fn(async () => page(rows, 2))
    const { w } = await open(api({ list }), { state: 'Fait' })
    await w.find('[data-testid="report-export"]').trigger('click')
    await flushPromises()
    const [filename, text] = written()
    expect(filename).toBe('task-2026-10-17.csv')
    expect(text).toBe('﻿Titre;État;Heures\r\nPoser;Fait;3\r\nPeindre;À faire;1,5\r\n')
    expect(list).toHaveBeenLastCalledWith('Task', { filters: { state: 'Fait' }, order_by: 'modified', order: 'desc', limit: 200, offset: 0 })
    expect(toasts.summaries().join()).toContain('2 lignes exportées')
  })

  it('un texte qui commence par = + - @ ne devient jamais une formule, un nombre négatif reste un nombre', async () => {
    const rows = [doc('t1', { title: '=HYPERLINK("http://x")', hours: -2 }), doc('t2', { title: '+33 6', hours: 1 })]
    const { w } = await open(api({ list: vi.fn(async () => page(rows, 2)) }))
    await w.find('[data-testid="report-export"]').trigger('click')
    await flushPromises()
    const lines = (written()[1] as string).slice(1).trim().split('\r\n')
    expect(lines[1]).toBe(`"'=HYPERLINK(""http://x"")";À faire;-2`)
    expect(lines[2]).toBe("'+33 6;À faire;1")
  })

  it('plusieurs pages de 200 se suivent jusqu’à la dernière', async () => {
    const all = Array.from({ length: 450 }, (_, i) => doc(`d${i}`, { title: `T${i}` }))
    const list = vi.fn(async (_d: string, q?: { offset?: number; limit?: number }) => page(all.slice(q?.offset ?? 0, (q?.offset ?? 0) + (q?.limit ?? 20)), 450))
    const { w } = await open(api({ list }))
    list.mockClear()
    await w.find('[data-testid="report-export"]').trigger('click')
    await flushPromises()
    expect(list.mock.calls.map((c) => (c[1] as { offset: number }).offset)).toEqual([0, 200, 400])
    expect((written()[1] as string).trim().split('\r\n')).toHaveLength(451) // l'en-tête et 450 lignes
  })

  it('l’export s’arrête à 10 000 lignes et le dit', async () => {
    const chunk = Array.from({ length: 200 }, (_, i) => doc(`d${i}`, {}))
    const list = vi.fn(async () => page(chunk, 25_000))
    const { w } = await open(api({ list }))
    await w.find('[data-testid="report-export"]').trigger('click')
    await flushPromises()
    expect((written()[1] as string).trim().split('\r\n')).toHaveLength(10_001)
    expect(toasts.summaries().join()).toContain('10000')
  })

  it('regroupé, le fichier est le rapport tel qu’on le voit : un groupe par ligne, puis le total', async () => {
    const { w } = await open(api())
    w.findAllComponents(Select)[0]!.vm.$emit('update:modelValue', 'state')
    await flushPromises()
    vi.mocked(downloadText).mockClear()
    await w.find('[data-testid="report-export"]').trigger('click')
    await flushPromises()
    expect(written()[1]).toBe('﻿État;Nombre;Heures\r\nFait;3;12,5\r\nÀ faire;2;4\r\nTotal;2;4,5\r\n')
  })

  it('une erreur pendant l’export est dite et ne produit aucun fichier', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce(page(tasks, 2)) // le chargement de la page
      .mockRejectedValueOnce(new ApiError(500, 'internal_error', 'Erreur interne')) // l'export
    const { w } = await open(api({ list }))
    await w.find('[data-testid="report-export"]').trigger('click')
    await flushPromises()
    expect(downloadText).not.toHaveBeenCalled()
    expect(toasts.summaries()).toContain('Erreur interne')
  })
})

