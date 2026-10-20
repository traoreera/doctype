import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import ToastEventBus from 'primevue/toasteventbus'
import { afterEach, beforeEach, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { SERVICES } from '@/lib/services'
import type { DocRecord, DocTypeMeta, FieldMeta, ListResult } from '@/lib/types'

const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })

/** Un type de documents avec toutes les vues : Kanban sur `state`, Calendrier et Gantt sur `start` → `stop`, teinte par `prio`. */
export const TASK: DocTypeMeta = {
  name: 'Task',
  label: 'Tâche',
  module: 'shop',
  title_field: 'title',
  timezone: 'UTC',
  fields: [
    f({ fieldname: 'title', label: 'Titre', fieldtype: 'Text', required: true, in_list_view: true }),
    f({ fieldname: 'state', label: 'État', fieldtype: 'Select', options: ['À faire', 'En cours', 'Fait'], default: 'À faire', required: true, in_list_view: true }),
    f({ fieldname: 'prio', label: 'Priorité', fieldtype: 'Select', options: ['Basse', 'Haute'] }),
    f({ fieldname: 'start', label: 'Début', fieldtype: 'Date' }),
    f({ fieldname: 'stop', label: 'Fin', fieldtype: 'Date' }),
    f({ fieldname: 'hours', label: 'Heures', fieldtype: 'Number', in_list_view: true }),
    f({ fieldname: 'cost', label: 'Coût', fieldtype: 'Decimal', decimals: 2 }),
  ],
  layout: [{ section: null, collapsible: false, collapsed: false, columns: [['title', 'state', 'prio', 'start', 'stop', 'hours', 'cost']] }],
  list_columns: ['title', 'state', 'hours'],
  rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
  views: {
    available: ['list', 'report', 'dashboard', 'kanban', 'calendar', 'gantt'],
    default: 'list',
    kanban: { field: 'state', card_fields: ['hours'] },
    calendar: { start: 'start', end: 'stop', title: 'title', color: 'prio' },
    gantt: { start: 'start', end: 'stop', title: 'title', color: 'prio' },
    dashboard: [{ type: 'number_card', label: 'Total', doctype: 'Task', function: 'count' }],
  },
}

export const doc = (name: string, data: Record<string, unknown>, extra: Partial<DocRecord> = {}): DocRecord => ({
  doctype: 'Task',
  name,
  owner: 'alice',
  creation: '2026-10-01T08:00:00+00:00',
  modified: '2026-10-10T08:00:00+00:00',
  version: 1,
  docstatus: 0,
  amended_from: null,
  data: { title: name, state: 'À faire', ...data },
  ...extra,
})

export const page = (items: DocRecord[], total = items.length): ListResult => ({ items, total, limit: 20, offset: 0 })

export function router(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
}

/** Monte un composant avec le client d'API simulé que le test fournit. */
/** Les composants montés par `mountWith` et pas encore démontés (voir `unmountAll`). */
const mounted: VueWrapper[] = []

/**
 * À appeler en `afterEach` AVANT de vider `document.body` : une fenêtre PrimeVue se téléporte dans le corps de la page ; effacer
 * celui-ci pendant que le composant vit encore laisse Vue corriger plus tard un nœud sans parent (« insertBefore » de null).
 */
export function unmountAll(): void {
  for (const w of mounted.splice(0)) w.unmount()
}

export function mountWith(component: unknown, props: Record<string, unknown>, api: Record<string, unknown> = {}, r: Router = router()) {
  const w = mount(component as never, {
    props: props as never,
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), r],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { meta: async () => TASK, ...api } } },
    },
  }) as unknown as VueWrapper
  mounted.push(w)
  return { w, router: r }
}

export { flushPromises, vi }

/** Les notifications émises pendant un test (le composant `Toast` n'est pas monté : on écoute le bus). */
export function captureToasts(): { summaries: () => string[] } {
  const seen: { severity: string; summary: string }[] = []
  const on = (m: { severity: string; summary: string }) => seen.push(m)
  beforeEach(() => {
    seen.length = 0
    ToastEventBus.on('add', on as never)
  })
  afterEach(() => ToastEventBus.off('add', on as never))
  return { summaries: () => seen.map((m) => m.summary) }
}
