import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { listProps } from '@/router'
import { SERVICES } from '@/lib/services'
import type { DocRecord } from '@/lib/types'
import FormView from '@/views/FormView.vue'
import ListView from '@/views/ListView.vue'
import { CUSTOMER } from './fixtures'
import { TASK, doc, page } from './views-fixtures'

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
