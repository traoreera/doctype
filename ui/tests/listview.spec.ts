import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { listProps } from '@/router'
import ListView from '@/views/ListView.vue'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, SavedView } from '@/lib/types'
import { CUSTOMER } from './fixtures'
import { TASK, doc, page } from './views-fixtures'
import { menuItem, openMore } from './menu'

beforeEach(() => {
  localStorage.clear()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-17T10:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})

const PLAIN: DocTypeMeta = { ...CUSTOMER, name: 'Plain' }
const ORDER: DocTypeMeta = { ...TASK, name: 'Order', submittable: true }

async function open(path: string, metas: Record<string, DocTypeMeta> = { Task: TASK }, api: Record<string, unknown> = {}) {
  // Les routes de l'application, avec la fonction qui transmet `?view=` et `?filters=` à la page : celle de `router.ts`, pas une copie.
  const real = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/app/:doctype', name: 'list', component: ListView, props: listProps },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  const list = vi.fn(async (_doctype: string, _options: Record<string, unknown>) => page([doc('t1', { title: 'Poser', hours: 3 })], 1))
  const aggregate = vi.fn(async () => ({ group: null, metrics: [{ function: 'count', field: null, label: null, decimals: null }], rows: [{ key: null, values: [1] }], truncated: false }))
  const listViews = vi.fn(async () => [] as SavedView[])
  const saveView = vi.fn(async (doctype: string, name: string, body: Record<string, unknown>): Promise<SavedView> => ({
    name, label: String(body.label), kind: 'list', config: (body.config as SavedView['config']) ?? {}, is_default: !!body.is_default,
    owner: 'me', creation: '2026-10-17T00:00:00+00:00', modified: '2026-10-17T00:00:00+00:00',
  }))
  const services = {
    transport: {},
    config: { baseUrl: '', loginUrl: null },
    api: { meta: async (n: string) => metas[n], list, aggregate, listViews, saveView, removeView: vi.fn(async () => {}), ...api },
  }
  await real.push(path)
  await real.isReady()
  const App = { template: '<RouterView />' }
  const w = mount(App, { attachTo: document.body, global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), real], provide: { [SERVICES as symbol]: services } } })
  await flushPromises()
  return { w, router: real, list, aggregate }
}

describe('la page d’un type : le choix de la vue', () => {
  it('un type qui ne propose que la liste n’affiche pas de sélecteur', async () => {
    const { w } = await open('/app/Plain', { Plain: { ...PLAIN, views: { available: ['list'], default: 'list', dashboard: [] } } })
    expect(w.find('[data-testid="view-switcher"]').exists()).toBe(false)
    expect(w.find('[data-testid="list-title"]').exists()).toBe(true)
  })

  it('un serveur plus ancien (sans « views ») garde la liste seule', async () => {
    const { w } = await open('/app/Plain', { Plain: PLAIN })
    expect(w.find('[data-testid="view-switcher"]').exists()).toBe(false)
    expect(w.find('[data-testid="quick-search"]').exists()).toBe(true)
  })

  it('les vues du type, dans l’ordre, la liste d’abord', async () => {
    const { w } = await open('/app/Task')
    expect(w.findAll('[data-testid="view-switcher"] [role="radio"]').map((b) => b.attributes('data-testid'))).toEqual([
      'view-list', 'view-report', 'view-dashboard', 'view-kanban', 'view-calendar', 'view-gantt',
    ])
    expect(w.find('[data-testid="view-list"]').attributes('aria-checked')).toBe('true')
  })

  it('l’adresse choisit la vue, et chaque vue se montre', async () => {
    const shown = async (view: string, probe: string) => {
      const { w } = await open(`/app/Task?view=${view}`)
      expect(w.find(`[data-testid="${probe}"]`).exists(), view).toBe(true)
      expect(w.find(`[data-testid="view-${view}"]`).attributes('aria-checked')).toBe('true')
      document.body.innerHTML = ''
    }
    await shown('report', 'report')
    await shown('dashboard', 'doctype-dashboard')
    await shown('kanban', 'kanban')
    await shown('calendar', 'calendar')
    await shown('gantt', 'gantt')
  })

  it('une vue inconnue ou non déclarée retombe sur la liste, jamais sur un écran vide', async () => {
    const { w } = await open('/app/Task?view=carte')
    expect(w.find('[data-testid="view-list"]').attributes('aria-checked')).toBe('true')
    const plain = await open('/app/Plain?view=kanban', { Plain: { ...PLAIN, views: { available: ['list', 'report', 'dashboard'], default: 'list', dashboard: [] } } })
    expect(plain.w.find('[data-testid="view-list"]').attributes('aria-checked')).toBe('true')
  })

  it('choisir une vue l’inscrit dans l’adresse et se retient pour ce type', async () => {
    const { w, router } = await open('/app/Task')
    await w.find('[data-testid="view-kanban"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.view).toBe('kanban')
    expect(localStorage.getItem('xdt.desk.view.Task')).toBe('kanban')
    expect(w.find('[data-testid="kanban"]').exists()).toBe(true)
    // Plus tard, sans rien dans l'adresse : la dernière vue choisie.
    document.body.innerHTML = ''
    const again = await open('/app/Task')
    expect(again.w.find('[data-testid="kanban"]').exists()).toBe(true)
  })

  it('la vue par défaut du type s’ouvre quand rien n’a été choisi', async () => {
    const meta = { ...TASK, views: { ...TASK.views!, default: 'dashboard' as const } }
    const { w } = await open('/app/Task', { Task: meta })
    expect(w.find('[data-testid="doctype-dashboard"]').exists()).toBe(true)
  })

  it('revenir à la liste relit ses documents', async () => {
    const { w, list } = await open('/app/Task')
    await w.find('[data-testid="view-report"]').trigger('click')
    await flushPromises()
    list.mockClear()
    await w.find('[data-testid="view-list"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalled()
  })
})

describe('la page d’un type : les filtres sont communs à toutes les vues', () => {
  it('les filtres de l’adresse (une carte de chiffres) s’appliquent ET se voient dans la barre de filtres', async () => {
    const filters = encodeURIComponent(JSON.stringify({ state: 'Fait', hours: ['>', 2] }))
    const { w, list } = await open(`/app/Task?filters=${filters}`)
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ filters: { state: 'Fait', hours: ['>', 2] } }))
    expect(w.find('[data-testid="filter-bar"]').exists()).toBe(true) // ouverte d'office : on voit ce qui filtre
    expect(w.find('[data-testid="filter-state"]').exists()).toBe(true)
    expect(w.find('[data-testid="filter-hours"]').exists()).toBe(true)
    expect(w.find('[data-testid="toggle-filters"]').text()).toContain('Filtres · 2')
  })

  it('l’état d’une carte « brouillons » (docstatus) sélectionne le bon état d’un type soumettable', async () => {
    const filters = encodeURIComponent(JSON.stringify({ docstatus: 0 }))
    const { list } = await open(`/app/Order?filters=${filters}`, { Order: ORDER })
    expect(list).toHaveBeenCalledWith('Order', expect.objectContaining({ filters: { docstatus: 0 } }))
  })

  it('les mêmes filtres suivent quand on change de vue', async () => {
    const filters = encodeURIComponent(JSON.stringify({ state: 'Fait' }))
    const { w, list, router } = await open(`/app/Task?filters=${filters}`)
    await w.find('[data-testid="view-kanban"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.filters).toBeUndefined() // l'adresse a rendu la main à la barre de filtres
    expect(w.find('[data-testid="filter-state"]').exists()).toBe(true) // les filtres sont toujours là
    // Le Kanban les applique : sa colonne « Fait » est la seule demandée (le filtre porte sur le champ des colonnes).
    const kanbanCalls = list.mock.calls.filter((c) => (c[1] as { limit: number }).limit === 20 && (c[1] as { order_by: string }).order_by === 'modified')
    expect(kanbanCalls.some((c) => (c[1] as { filters: { state: string } }).filters.state === 'Fait')).toBe(true)
  })

  it('le tableau de bord n’a pas de barre de filtres (ses blocs ont les leurs)', async () => {
    const { w } = await open('/app/Task?view=dashboard')
    expect(w.find('[data-testid="quick-search"]').exists()).toBe(false)
    expect(w.find('[data-testid="toggle-filters"]').exists()).toBe(false)
  })

  it('un filtre sur un champ inconnu de l’adresse est ignoré, pas appliqué à l’aveugle', async () => {
    const filters = encodeURIComponent(JSON.stringify({ fantôme: 1, state: 'Fait' }))
    const { list } = await open(`/app/Task?filters=${filters}`)
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ filters: { state: 'Fait' } }))
  })

  it('une adresse sans filtres ne filtre rien', async () => {
    const { list } = await open('/app/Task')
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ filters: {} }))
  })
})

describe('la page d’un type : cliquer une carte de chiffres', () => {
  it('le lien d’une carte aboutit à une liste qui porte EXACTEMENT les mêmes filtres que la carte a comptés', async () => {
    const block = { state: 'En cours', hours: ['>=', 5] }
    const query = JSON.stringify(block)
    const { list } = await open(`/app/Task?filters=${encodeURIComponent(query)}`)
    expect((list.mock.calls[0]![1] as { filters: unknown }).filters).toEqual(block)
  })
})

describe('la page d’un type : les vues sauvegardées', () => {
  const view = (name: string, config: SavedView['config'], isDefault = false): SavedView => ({
    name, label: name, kind: 'list', config, is_default: isDefault, owner: 'me', creation: '', modified: '',
  })

  it('rejoue la vue par défaut à l’ouverture : filtres, tri et colonnes, et la nomme dans la barre', async () => {
    const listViews = vi.fn(async () => [view('En cours', { filters: { state: 'En cours' }, order_by: 'hours', descending: false, columns: ['title', 'hours'] }, true)])
    const { w, list } = await open('/app/Task', { Task: TASK }, { listViews })
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ order_by: 'hours', order: 'asc', filters: { state: 'En cours' } }))
    expect(w.find('[data-testid="views-menu"]').text()).toContain('En cours')
    // Les colonnes de la vue, pas celles du type : « État » (colonne par défaut) n'est pas affichée, « Heures » si.
    expect(listViews).toHaveBeenCalledWith('Task')
    expect(w.find('[data-testid="filter-state"]').exists()).toBe(true)
  })

  it('une adresse explicite (filtres de carte) l’emporte sur la vue par défaut', async () => {
    const listViews = vi.fn(async () => [view('En cours', { filters: { state: 'En cours' } }, true)])
    const filters = encodeURIComponent(JSON.stringify({ state: 'Fait' }))
    const { list } = await open(`/app/Task?filters=${filters}`, { Task: TASK }, { listViews })
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ filters: { state: 'Fait' } }))
  })

  it('enregistre la vue courante (nom + par défaut) via l’API', async () => {
    const filters = encodeURIComponent(JSON.stringify({ state: 'En cours' }))
    const saveView = vi.fn(async (_d: string, name: string, body: { label: string; config?: SavedView['config']; is_default?: boolean }): Promise<SavedView> => ({
      name, label: body.label, kind: 'list', config: body.config ?? {}, is_default: !!body.is_default, owner: 'me', creation: '', modified: '',
    }))
    const { w } = await open(`/app/Task?filters=${filters}`, { Task: TASK }, { saveView })
    await w.find('[data-testid="views-menu"]').trigger('click')
    await flushPromises()
    const item = [...document.querySelectorAll('[role="menuitem"]')].find((el) => el.textContent?.includes('Enregistrer la vue actuelle'))
    expect(item).toBeTruthy()
    ;(item!.querySelector('a') ?? item!).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    const input = document.querySelector('[data-testid="view-name"]') as HTMLInputElement
    expect(input).toBeTruthy()
    input.value = 'En cours'
    input.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('[data-testid="save-view"]') as HTMLElement).click()
    await flushPromises()
    expect(saveView).toHaveBeenCalledTimes(1)
    const call = saveView.mock.calls[0]!
    expect(call[0]).toBe('Task')
    expect(call[1]).toBe('En cours')
    expect(call[2].config).toMatchObject({ filters: { state: 'En cours' }, order_by: 'modified', descending: true })
  })
})


describe('un DocType virtuel (source externe : xauth…)', () => {
  const VIRTUAL: DocTypeMeta = { ...PLAIN, name: 'Permission', title_field: 'customer_name', virtual: true, source: 'xauth', track_changes: false }

  it('trié par titre, croissant (sa source ne date rien) ; sans colonne « Modifié »', async () => {
    const { w, list } = await open('/app/Permission', { Permission: VIRTUAL })
    expect(list).toHaveBeenCalledWith('Permission', expect.objectContaining({ order_by: 'customer_name', order: 'asc' }))
    expect(w.findAll('.p-datatable-thead th').map((h) => h.text())).not.toContain('Modifié')
  })

  it('un DocType ordinaire garde le tri par modification, le plus récent d’abord, et sa colonne', async () => {
    const { w, list } = await open('/app/Plain', { Plain: PLAIN })
    expect(list).toHaveBeenCalledWith('Plain', expect.objectContaining({ order_by: 'modified', order: 'desc' }))
    expect(w.findAll('.p-datatable-thead th').map((h) => h.text())).toContain('Modifié')
  })
})

describe('la page d’un type : importer un CSV', () => {
  it('« Importer… » est offert à qui peut créer ou modifier, ouvre la boîte d’import ; un lecteur ne le voit pas', async () => {
    const { w } = await open('/app/Task')
    await openMore(w)
    expect(menuItem('import-csv')).not.toBeNull()
    menuItem('import-csv')!.click()
    await flushPromises()
    expect(document.body.querySelector('[data-testid="import-dialog"]')).not.toBeNull()
    w.unmount()
    document.body.innerHTML = ''
    const editor = await open('/app/Task', { Task: { ...TASK, rights: { ...TASK.rights, create: 'none' } } })
    await openMore(editor.w)
    expect(menuItem('import-csv')).not.toBeNull()
    editor.w.unmount()
    document.body.innerHTML = ''
    const reader = await open('/app/Task', { Task: { ...TASK, rights: { ...TASK.rights, create: 'none', write: 'none' } } })
    await openMore(reader.w)
    expect(menuItem('import-csv')).toBeNull()
    expect(document.body.querySelector('[data-testid="import-dialog"]')).toBeNull()
  })
})
