/**
 * Étiquettes dans le Desk : les règles d'un nom (jumelles du serveur), la barre d'une fiche (voir, poser, retirer, selon `can_tag`), la liste
 * (filtre `_tag` tenu hors de la barre de filtres, pastilles des lignes, groupe « Étiquettes » de la barre latérale) et « Étiqueter… » en lot.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import BulkActions from '@/components/BulkActions.vue'
import TagBar from '@/components/tags/TagBar.vue'
import { rowsFromFilters } from '@/lib/filters'
import { SERVICES } from '@/lib/services'
import { normalizeTag, supportsTags, withoutTag } from '@/lib/tags'
import type { SavedView } from '@/lib/types'
import { listProps } from '@/router'
import ListView from '@/views/ListView.vue'
import { TASK, captureToasts, doc, mountWith, page, unmountAll } from './views-fixtures'

const toasts = captureToasts()
beforeEach(() => localStorage.clear())
afterEach(() => {
  unmountAll()
  document.body.innerHTML = ''
})

describe('les règles d’un nom (lib/tags)', () => {
  it('normalise comme le serveur et refuse ce qu’il refuserait', () => {
    expect(normalizeTag('  Salon \t 2026  ')).toBe('Salon 2026')
    expect(normalizeTag('É'.repeat(50))).toBe('É'.repeat(50))
    for (const bad of ['', '   ', 'x'.repeat(51), 'a,b', 'a\u0000b', 'a\u007fb']) expect(normalizeTag(bad)).toBeNull()
  })

  it('un réglage ou un type virtuel ne porte pas d’étiquettes', () => {
    expect(supportsTags(TASK)).toBe(true)
    expect(supportsTags({ ...TASK, single: true })).toBe(false)
    expect(supportsTags({ ...TASK, virtual: true })).toBe(false)
    expect(supportsTags(null)).toBe(false)
  })

  it('le filtre `_tag` d’une adresse ou d’une vue devient l’étiquette filtrée, jamais une ligne de la barre de filtres', () => {
    const start = rowsFromFilters({ _tag: ' Urgent ', state: 'Fait' }, TASK.fields, () => 1)
    expect(start.tag).toBe('Urgent')
    expect(start.rows.map((r) => r.field)).toEqual(['state'])
    expect(rowsFromFilters({ _tag: ['in', ['a']] }, TASK.fields, () => 1).tag).toBeNull() // la liste ne montre qu'une égalité
    expect(rowsFromFilters({ _tag: 'a,b' }, TASK.fields, () => 1).tag).toBeNull()
    expect(withoutTag({ _tag: 'x', state: 'Fait' })).toEqual({ state: 'Fait' })
  })
})

describe('la barre d’étiquettes d’une fiche (TagBar)', () => {
  const setup = (canTag = true, tags = ['Urgent']) => {
    const changeTags = vi.fn(async (_d: string, _n: string, body: { add?: string[]; remove?: string[] }) => [
      ...tags.filter((x) => !(body.remove ?? []).includes(x)),
      ...(body.add ?? []),
    ])
    const api = { tags: vi.fn(async () => ({ tags, can_tag: canTag })), changeTags, tagCounts: vi.fn(async () => [{ tag: 'Client', count: 3 }]) }
    return { ...mountWith(TagBar, { doctype: 'Task', name: 't1' }, api), api }
  }

  it('montre les étiquettes, chacune mène à la liste filtrée sur elle', async () => {
    const { w } = setup()
    await flushPromises()
    const link = w.find('[data-testid="tag-Urgent"] a')
    expect(link.text()).toBe('Urgent')
    expect(decodeURIComponent(link.attributes('href')!)).toContain('/app/Task?filters={"_tag":"Urgent"}')
  })

  it('qui ne peut pas écrire le document ne voit ni × ni « + »', async () => {
    const { w } = setup(false)
    await flushPromises()
    expect(w.find('[data-testid="tag-Urgent"]').exists()).toBe(true)
    expect(w.find('[data-testid="tag-remove-Urgent"]').exists()).toBe(false)
    expect(w.find('[data-testid="tag-add"]').exists()).toBe(false)
  })

  it('un document sans étiquette, qu’on ne peut pas écrire, n’a pas de barre du tout', async () => {
    const { w } = setup(false, [])
    await flushPromises()
    expect(w.find('[data-testid="tag-bar"]').exists()).toBe(false)
  })

  it('retirer et ajouter passent par le serveur, qui répond la liste à jour', async () => {
    const { w, api } = setup()
    await flushPromises()
    await w.find('[data-testid="tag-remove-Urgent"]').trigger('click')
    await flushPromises()
    expect(api.changeTags).toHaveBeenLastCalledWith('Task', 't1', { remove: ['Urgent'] })
    expect(w.find('[data-testid="tag-Urgent"]').exists()).toBe(false)

    await w.find('[data-testid="tag-add"]').trigger('click')
    await flushPromises()
    const input = w.find('[data-testid="tag-input"]')
    expect(document.activeElement).toBe(input.element)
    await input.trigger('focus')
    await flushPromises()
    await input.setValue('  client  ')
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    // L'écriture déjà en usage dans le type (« Client ») remplace celle tapée, comme le serveur le ferait.
    expect(api.changeTags).toHaveBeenLastCalledWith('Task', 't1', { add: ['Client'] })
    expect(w.find('[data-testid="tag-Client"]').exists()).toBe(true)
  })

  it('un nom refusé est signalé sans appel au serveur', async () => {
    const { w, api } = setup()
    await flushPromises()
    await w.find('[data-testid="tag-add"]').trigger('click')
    await flushPromises()
    const input = w.find('[data-testid="tag-input"]')
    await input.setValue('x'.repeat(51))
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(w.find('[data-testid="tag-invalid"]').exists()).toBe(true)
    expect(input.attributes('aria-invalid')).toBe('true')
    expect(api.changeTags).not.toHaveBeenCalled()
  })

  it('une étiquette déjà présente (autre casse) ne part pas ; une erreur du serveur est notifiée', async () => {
    const { w, api } = setup()
    await flushPromises()
    await w.find('[data-testid="tag-add"]').trigger('click')
    await flushPromises()
    const input = w.find('[data-testid="tag-input"]')
    await input.setValue('URGENT')
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(api.changeTags).not.toHaveBeenCalled()
    api.changeTags.mockRejectedValueOnce(new Error('refusé'))
    await input.setValue('Neuve')
    await input.trigger('keydown', { key: ',' })
    await flushPromises()
    expect(api.changeTags).toHaveBeenCalledWith('Task', 't1', { add: ['Neuve'] })
    expect(toasts.summaries().length).toBe(1)
  })
})

describe('la liste : filtre, pastilles, barre latérale', () => {
  async function open(path: string, api: Record<string, unknown> = {}) {
    const real = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/app/:doctype', name: 'list', component: ListView, props: listProps },
        { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
        { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
      ],
    })
    const list = vi.fn(async () => page([doc('t1', { title: 'Poser' }), doc('t2', { title: 'Peindre' })], 2))
    const tagCounts = vi.fn(async (_d: string, q: { filters?: Record<string, unknown>; limit?: number }) =>
      q.limit === 1 ? [{ tag: 'Urgent', count: 1 }] : [{ tag: 'Urgent', count: 1 }, { tag: 'Client', count: 2 }],
    )
    const tagsOf = vi.fn(async () => ({ t1: ['Urgent', 'Client'], t2: ['Client'] }))
    const aggregate = vi.fn(async () => ({ group: null, metrics: [], rows: [], truncated: false }))
    const services = {
      transport: {},
      config: { baseUrl: '', loginUrl: null },
      api: { meta: async () => TASK, list, aggregate, tagCounts, tagsOf, listViews: vi.fn(async () => [] as SavedView[]), ...api },
    }
    await real.push(path)
    await real.isReady()
    const w = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), real], provide: { [SERVICES as symbol]: services } } })
    await flushPromises()
    return { w, list, tagCounts, tagsOf }
  }
  const lastFilters = (list: ReturnType<typeof vi.fn>) => (list.mock.calls.at(-1)![1] as { filters: Record<string, unknown> }).filters

  it('les lignes portent leurs étiquettes (une requête pour la page) ; un clic filtre sans ouvrir la ligne', async () => {
    const { w, list, tagsOf } = await open('/app/Task')
    expect(tagsOf).toHaveBeenCalledTimes(1)
    expect(tagsOf).toHaveBeenCalledWith('Task', ['t1', 't2'])
    expect(w.findAll('[data-testid="row-tag-Client"]').length).toBe(2)
    await w.find('[data-testid="row-tag-Urgent"]').trigger('click')
    await flushPromises()
    expect(lastFilters(list)).toEqual({ _tag: 'Urgent' })
    expect(w.find('[data-testid="tag-filter"]').text()).toContain('Urgent')
    await w.find('[data-testid="tag-filter-clear"]').trigger('click')
    await flushPromises()
    expect(lastFilters(list)).toEqual({})
    expect(w.find('[data-testid="tag-filter"]').exists()).toBe(false)
  })

  it('la barre latérale compte les étiquettes dans les AUTRES filtres ; un clic filtre, un second retire', async () => {
    const { w, list, tagCounts } = await open('/app/Task?filters=' + encodeURIComponent(JSON.stringify({ state: 'Fait', _tag: 'Client' })))
    expect(lastFilters(list)).toEqual({ state: 'Fait', _tag: 'Client' })
    expect(tagCounts).toHaveBeenLastCalledWith('Task', { filters: { state: 'Fait' }, limit: 9 })
    expect(w.find('[data-testid="sidebar-tag-Client"]').classes()).toContain('on')
    await w.find('[data-testid="sidebar-tag-Urgent"]').trigger('click')
    await flushPromises()
    expect(lastFilters(list)).toEqual({ state: 'Fait', _tag: 'Urgent' })
    await w.find('[data-testid="sidebar-tag-Urgent"]').trigger('click')
    await flushPromises()
    expect(lastFilters(list)).toEqual({ state: 'Fait' })
  })

  it('un type sans étiquette en usage : ni pastilles demandées, ni groupe « Étiquettes »', async () => {
    const tagsOf = vi.fn(async () => ({}))
    const { w } = await open('/app/Task', { tagCounts: vi.fn(async () => []), tagsOf })
    expect(tagsOf).not.toHaveBeenCalled()
    expect(w.find('[data-testid="sidebar-tags"]').exists()).toBe(false)
  })
})

describe('« Étiqueter… » en lot (BulkActions)', () => {
  it('pose les étiquettes choisies sur la sélection, en une requête', async () => {
    const tagMany = vi.fn(async () => ({ results: [{ name: 't1', ok: true }, { name: 't2', ok: true }], done: 2, failed: 0 }))
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection: [doc('t1', {}), doc('t2', {})] }, { tagMany, tagCounts: vi.fn(async () => []) })
    await w.find('[data-testid="bulk-actions"]').trigger('click')
    await flushPromises()
    const item = [...document.querySelectorAll('.p-menu-item')].find((el) => el.textContent?.includes('Étiqueter')) as HTMLElement
    item.querySelector('a, .p-menu-item-link')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    const input = document.querySelector('[data-testid="bulk-tag-dialog"] [data-testid="tag-input"]') as HTMLInputElement
    input.value = 'Lot 1'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    await flushPromises()
    expect(document.querySelector('[data-testid="bulk-tag-Lot 1"]')).not.toBeNull()
    ;(document.querySelector('[data-testid="bulk-tag-apply"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(tagMany).toHaveBeenCalledWith('Task', ['t1', 't2'], { add: ['Lot 1'] })
    expect(toasts.summaries()).toContain('2 documents étiquetés')
  })

  it('n’est pas proposé à qui ne peut pas écrire le type', async () => {
    const reader = { ...TASK, rights: { ...TASK.rights, write: 'none' as const, delete: 'none' as const } }
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: reader, selection: [doc('t1', {})] }, {})
    expect(w.find('[data-testid="bulk-actions"]').exists()).toBe(false)
  })
})
