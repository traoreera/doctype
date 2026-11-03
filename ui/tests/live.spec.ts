import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { useLiveDoctypes } from '@/composables/useLive'
import { createMemoryHistory, createRouter } from 'vue-router'
import { listProps } from '@/router'
import { SERVICES } from '@/lib/services'
import type { DocRecord } from '@/lib/types'
import FormView from '@/views/FormView.vue'
import ListView from '@/views/ListView.vue'
import { CUSTOMER } from './fixtures'
import { TASK, doc, mountWith, page, unmountAll } from './views-fixtures'
import KanbanView from '@/components/views/KanbanView.vue'
import CalendarView from '@/components/views/CalendarView.vue'
import NumberCard from '@/components/blocks/NumberCard.vue'
import ShortcutGrid from '@/components/blocks/ShortcutGrid.vue'

/** Le temps réel simulé : ce que `RealtimeClient` offre aux pages (s'abonner à un type), et de quoi « recevoir » un signal. */
class FakeRealtime {
  listeners = new Map<string, Set<(dt: string) => void>>()
  onDocChanged(dt: string, cb: (dt: string) => void) {
    if (!this.listeners.has(dt)) this.listeners.set(dt, new Set())
    this.listeners.get(dt)!.add(cb)
    return () => this.listeners.get(dt)!.delete(cb)
  }
  onBell() {
    return () => {}
  }
  emit(dt: string) {
    this.listeners.get(dt)?.forEach((cb) => cb(dt))
  }
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
const settle = async () => {
  await wait(450) // le regroupement des signaux (400 ms)
  await flushPromises()
}
const rec = (version: number, name: string): DocRecord => ({
  doctype: 'Customer', name: 'C1', owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version, docstatus: 0,
  amended_from: null, data: { customer_name: name, type: 'Particulier' },
})

const mounted: { unmount: () => void }[] = []
afterEach(() => {
  unmountAll()
  for (const w of mounted.splice(0)) w.unmount()
  document.body.innerHTML = ''
})

async function form(get: ReturnType<typeof vi.fn>) {
  const realtime = new FakeRealtime()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/app/:doctype/:name', name: 'form', component: FormView, props: true }, { path: '/app/:doctype', name: 'list', component: { render: () => null } }, { path: '/app/:doctype/new', name: 'new', component: { render: () => null } }] })
  const api = { meta: vi.fn(async () => CUSTOMER), doctypes: vi.fn(async () => []), get, history: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })), search: vi.fn(async () => []), list: vi.fn(async () => page([])) }
  await router.push('/app/Customer/C1')
  await router.isReady()
  const w = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api, realtime } } } })
  mounted.push(w)
  await flushPromises()
  return { w, realtime }
}

const nameInput = (w: { find: (s: string) => { element: Element } }) => w.find('[data-field="customer_name"] input').element as HTMLInputElement

describe('documents en direct', () => {
  it('une fiche intacte suit en silence la version enregistrée ailleurs', async () => {
    const get = vi.fn().mockResolvedValueOnce(rec(1, 'Avant')).mockResolvedValue(rec(2, 'Après'))
    const { w, realtime } = await form(get)
    expect(nameInput(w).value).toBe('Avant')
    realtime.emit('Invoice') // un autre type : rien n'est relu
    realtime.emit('Customer')
    realtime.emit('Customer') // deux signaux rapprochés : UNE relecture
    await settle()
    expect(get).toHaveBeenCalledTimes(2)
    expect(nameInput(w).value).toBe('Après')
    expect(w.find('[data-testid="form-changed-elsewhere"]').exists()).toBe(false)
  })

  it('une fiche en cours de saisie garde la saisie et le dit, puis relit à la demande', async () => {
    const get = vi.fn().mockResolvedValueOnce(rec(1, 'Avant')).mockResolvedValue(rec(2, 'Après'))
    const { w, realtime } = await form(get)
    await w.find('[data-field="customer_name"] input').setValue('Ma saisie')
    realtime.emit('Customer')
    await settle()
    expect(nameInput(w).value).toBe('Ma saisie')
    expect(w.find('[data-testid="form-changed-elsewhere"]').exists()).toBe(true)
    await w.find('[data-testid="form-reload-changed"]').trigger('click')
    await flushPromises()
    expect(nameInput(w).value).toBe('Après')
    expect(w.find('[data-testid="form-changed-elsewhere"]').exists()).toBe(false)
  })

  it('mon propre enregistrement (même version) ne dérange rien', async () => {
    const get = vi.fn().mockResolvedValue(rec(1, 'Avant'))
    const { w, realtime } = await form(get)
    await w.find('[data-field="customer_name"] input').setValue('Ma saisie')
    realtime.emit('Customer')
    await settle()
    expect(nameInput(w).value).toBe('Ma saisie')
    expect(w.find('[data-testid="form-changed-elsewhere"]').exists()).toBe(false)
  })

  it('une liste se relit quand un document de son type change', async () => {
    const realtime = new FakeRealtime()
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/app/:doctype', name: 'list', component: ListView, props: listProps }, { path: '/app/:doctype/new', name: 'new', component: { render: () => null } }, { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } }] })
    const list = vi.fn(async () => page([doc('t1', { title: 'Poser' })]))
    const aggregate = vi.fn(async () => ({ group: null, metrics: [], rows: [], truncated: false }))
    const api = { meta: async () => TASK, list, aggregate, listViews: vi.fn(async () => []) }
    await router.push('/app/Task')
    await router.isReady()
    const lw = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api, realtime } } } })
    mounted.push(lw)
    await flushPromises()
    const before = list.mock.calls.length
    realtime.emit('Task')
    await settle()
    expect(list.mock.calls.length).toBe(before + 1)
  })
})


describe('les autres vues suivent aussi', () => {
  it('le Kanban se relit, mais pas pendant qu’on tient une carte', async () => {
    const realtime = new FakeRealtime()
    const list = vi.fn(async () => page([doc('t1', { state: 'À faire' })]))
    const { w } = mountWith(KanbanView, { doctype: 'Task', meta: TASK, filters: {} }, { list }, undefined, { realtime })
    await flushPromises()
    const before = list.mock.calls.length
    realtime.emit('Task')
    await settle()
    expect(list.mock.calls.length).toBe(before + 3) // une requête par colonne
    await w.find('[data-testid="kanban-card-t1"]').trigger('dragstart', { dataTransfer: { setData() {}, effectAllowed: '' } })
    realtime.emit('Task')
    await settle()
    expect(list.mock.calls.length).toBe(before + 3) // la carte tenue ne disparaît pas sous le curseur
  })

  it('le calendrier se relit', async () => {
    const realtime = new FakeRealtime()
    const list = vi.fn(async () => page([]))
    mountWith(CalendarView, { doctype: 'Task', meta: TASK, filters: {} }, { list }, undefined, { realtime })
    await flushPromises()
    const before = list.mock.calls.length
    realtime.emit('Task')
    await settle()
    expect(list.mock.calls.length).toBeGreaterThan(before)
  })

  it('une carte chiffrée se relit sans repasser par « chargement »', async () => {
    const realtime = new FakeRealtime()
    const answer = (n: number) => ({ group: null, metrics: [{ function: 'count', field: null, label: null, decimals: null }], rows: [{ key: null, values: [n] }], truncated: false })
    let release!: () => void
    const slow = new Promise<void>((r) => (release = r))
    const aggregate = vi.fn().mockResolvedValueOnce(answer(3)).mockImplementationOnce(async () => (await slow, answer(4)))
    const { w } = mountWith(NumberCard, { block: { type: 'number_card', label: 'Total', doctype: 'Task', function: 'count' } }, { aggregate }, undefined, { realtime })
    await flushPromises()
    expect(w.text()).toContain('3')
    realtime.emit('Task')
    await settle() // le signal est parti, la réponse pas encore là : l'ancien chiffre reste, pas de squelette
    expect(aggregate).toHaveBeenCalledTimes(2)
    expect(w.text()).toContain('3')
    expect(w.find('.p-skeleton').exists()).toBe(false)
    release()
    await flushPromises()
    expect(aggregate).toHaveBeenCalledTimes(2)
    expect(w.text()).toContain('4')
  })

  it('les compteurs des raccourcis : seul celui du type changé est relu', async () => {
    const realtime = new FakeRealtime()
    const list = vi.fn(async (dt: string) => page([], dt === 'Task' ? 2 : 5))
    mountWith(ShortcutGrid, { shortcuts: [{ doctype: 'Task', label: 'Tâches' }, { doctype: 'Customer', label: 'Clients' }] }, { list }, undefined, { realtime })
    await flushPromises()
    list.mockClear()
    realtime.emit('Task')
    await settle()
    expect(list.mock.calls.map((c) => c[0])).toEqual(['Task'])
  })
})


describe('useLiveDoctypes', () => {
  it('suit la liste des types : un type retiré n’est plus écouté, un type ajouté l’est', async () => {
    const realtime = new FakeRealtime()
    const types = ref(['Task', 'Customer'])
    const seen: string[] = []
    const Probe = defineComponent({ setup: () => (useLiveDoctypes(() => types.value, (dt) => seen.push(dt), 10), () => h('i')) })
    const w = mount(Probe, { global: { provide: { [SERVICES as symbol]: { realtime } } } })
    mounted.push(w)
    types.value = ['Customer', 'Invoice']
    await nextTick()
    realtime.emit('Task')
    realtime.emit('Invoice')
    await wait(30)
    expect(seen).toEqual(['Invoice'])
    w.unmount()
    mounted.splice(mounted.indexOf(w), 1)
    expect([...realtime.listeners.values()].every((set) => set.size === 0)).toBe(true) // tout est rendu au démontage
  })
})
