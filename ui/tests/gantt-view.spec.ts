import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GanttView from '@/components/views/GanttView.vue'
import { ApiError } from '@/lib/api'
import { DAY_PX } from '@/lib/gantt'
import type { DocTypeMeta, Filters } from '@/lib/types'
import { TASK, doc, flushPromises, mountWith, page } from './views-fixtures'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-17T10:00:00Z')) // un samedi
})
afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

const open = async (api: Record<string, unknown>, over: { meta?: DocTypeMeta; filters?: Filters } = {}) => {
  const out = mountWith(GanttView, { doctype: 'Task', meta: over.meta ?? TASK, filters: over.filters ?? {} }, api)
  await flushPromises()
  return out
}

const px = (el: { attributes: (n: string) => string | undefined }, prop: 'left' | 'width') => Number(new RegExp(`${prop}: ([\\d.]+)px`).exec(el.attributes('style') ?? '')?.[1])
const jobs = [
  doc('a', { title: 'Terrassement', start: '2026-10-10', stop: '2026-10-12', prio: 'Haute' }),
  doc('b', { title: 'Gros œuvre', start: '2026-10-20', stop: '2026-10-25' }),
]

describe('Gantt', () => {
  it('demande les documents du plus ancien début au plus récent, avec les filtres de l’utilisateur', async () => {
    const list = vi.fn(async () => page(jobs))
    await open({ list }, { filters: { state: 'Fait' } })
    expect(list).toHaveBeenCalledWith('Task', { filters: { state: 'Fait' }, order_by: 'start', order: 'asc', limit: 200 })
  })

  it('une ligne par document, son titre à gauche, sa barre cliquable vers le document', async () => {
    const { w } = await open({ list: async () => page(jobs) })
    expect(w.findAll('.gantt-row:not(.gantt-head) .gantt-label').map((x) => x.text())).toEqual(['Terrassement', 'Gros œuvre'])
    expect(w.find('[data-testid="gantt-bar-a"]').attributes('href')).toBe('/app/Task/a')
    expect(w.find('[data-testid="gantt-bar-a"]').attributes('title')).toBe('Terrassement · 10/10/2026 → 12/10/2026')
  })

  it('la position et la largeur d’une barre suivent ses dates (zoom semaine : 12 px par jour)', async () => {
    const { w } = await open({ list: async () => page(jobs) })
    const a = w.find('[data-testid="gantt-bar-a"]')
    const b = w.find('[data-testid="gantt-bar-b"]')
    const day = DAY_PX.week
    // La plage commence 3 jours avant le premier début (07/10) ; a dure 3 jours (10, 11, 12), b dure 6 jours (20 à 25).
    expect(px(a, 'left')).toBe(3 * day)
    expect(px(a, 'width')).toBe(3 * day - 2)
    expect(px(b, 'left')).toBe(13 * day) // du 07 au 20
    expect(px(b, 'width')).toBe(6 * day - 2)
  })

  it('le zoom change l’échelle sans changer les dates', async () => {
    const { w } = await open({ list: async () => page(jobs) })
    await w.find('[data-testid="gantt-zoom-day"]').trigger('click')
    expect(px(w.find('[data-testid="gantt-bar-a"]'), 'left')).toBe(3 * DAY_PX.day)
    await w.find('[data-testid="gantt-zoom-month"]').trigger('click')
    expect(px(w.find('[data-testid="gantt-bar-a"]'), 'left')).toBe(3 * DAY_PX.month)
    expect(w.find('[data-testid="gantt-zoom-month"]').attributes('aria-checked')).toBe('true')
  })

  it('la teinte suit la valeur du champ de couleur', async () => {
    const { w } = await open({ list: async () => page(jobs) })
    expect(w.find('[data-testid="gantt-bar-a"]').classes()).toContain('c-1') // « Haute »
    expect(w.find('[data-testid="gantt-bar-b"]').classes()).toContain('c-none')
  })

  it('la ligne « aujourd’hui » est dans la plage, ou n’est pas là', async () => {
    const { w } = await open({ list: async () => page(jobs) })
    expect(w.find('[data-testid="gantt-today"]').exists()).toBe(true) // le 17 est entre le 07 et le 28
    const past = await open({ list: async () => page([doc('x', { start: '2025-01-10', stop: '2025-01-12' })]) })
    expect(past.w.find('[data-testid="gantt-today"]').exists()).toBe(false)
  })

  it('un document sans début ou sans fin n’a pas de barre ; une fin avant le début donne une barre d’un jour', async () => {
    const list = async () => page([doc('ok', { start: '2026-10-10', stop: '2026-10-09' }), doc('sans-fin', { start: '2026-10-10' }), doc('sans-debut', { stop: '2026-10-10' })])
    const { w } = await open({ list })
    expect(w.findAll('[data-testid^="gantt-bar-"]').map((b) => b.attributes('data-testid'))).toEqual(['gantt-bar-ok'])
    expect(px(w.find('[data-testid="gantt-bar-ok"]'), 'width')).toBe(DAY_PX.week - 2)
  })

  it('aucun document à placer : le dit', async () => {
    const { w } = await open({ list: async () => page([doc('x', {})]) })
    expect(w.find('[data-testid="gantt-empty"]').exists()).toBe(true)
    expect(w.find('[data-testid^="gantt-bar-"]').exists()).toBe(false)
  })

  it('plus de documents que la limite, ou une erreur : le dit', async () => {
    const big = await open({ list: async () => page(jobs, 500) })
    expect(big.w.find('[data-testid="gantt-truncated"]').text()).toContain('Affichage limité à 2 documents')
    const broken = await open({
      list: async () => {
        throw new ApiError(500, 'internal_error', 'Erreur interne')
      },
    })
    expect(broken.w.find('[data-testid="gantt-error"]').exists()).toBe(true)
    expect(broken.w.find('[data-testid="gantt-empty"]').exists()).toBe(false) // une erreur n'est pas « rien à afficher »
  })

  it('de nouveaux filtres relisent les documents', async () => {
    const list = vi.fn(async () => page(jobs))
    const { w } = await open({ list })
    await w.setProps({ filters: { state: 'Fait' } })
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(2)
  })
})
