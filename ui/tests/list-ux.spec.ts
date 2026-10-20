import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { listProps } from '@/router'
import ListView from '@/views/ListView.vue'
import { SERVICES } from '@/lib/services'
import type { DocRecord, ListResult } from '@/lib/types'
import { TASK, doc, page } from './views-fixtures'

const mounted: { unmount: () => void }[] = []
afterEach(() => {
  // Démonter AVANT de vider la page : une liste encore vivante garderait son écouteur d'Échap sur `window` et corrigerait un DOM disparu.
  for (const w of mounted.splice(0)) w.unmount()
  document.body.innerHTML = ''
})

async function open(rows: DocRecord[], path = '/app/Task') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/app/:doctype', name: 'list', component: ListView, props: listProps },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  const list = vi.fn(async (): Promise<ListResult> => page(rows, rows.length))
  const api = { meta: async () => TASK, list, listViews: async () => [], aggregate: vi.fn(), doctypes: async () => [], search: async () => [] }
  await router.push(path)
  await router.isReady()
  const w = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } } },
  })
  mounted.push(w)
  await flushPromises()
  return { w, router, list }
}

const rows = () => [doc('t1', { title: 'Poser' }), doc('t2', { title: 'Peindre' })]
const tick = async (w: Awaited<ReturnType<typeof open>>['w'], name: string) => {
  const line = w.findAll('tbody tr').find((r) => r.text().includes(name))!
  await line.find('input[type="checkbox"]').setValue(true)
  await flushPromises()
}

describe('liste : un clic ouvre, une case coche', () => {
  it('un clic sur la ligne ouvre le document', async () => {
    const { w, router } = await open(rows())
    await w.findAll('tbody tr')[0]!.find('td:last-child').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('form')
    expect(router.currentRoute.value.params.name).toBe('t1')
  })

  it('cocher une ligne montre la barre de sélection et n’ouvre pas le document', async () => {
    const { w, router } = await open(rows())
    expect(w.find('[data-testid="selection-bar"]').exists()).toBe(false)
    await tick(w, 'Poser')
    const bar = w.find('[data-testid="selection-bar"]')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('1')
    expect(router.currentRoute.value.name).toBe('list')
  })

  it('« Tout désélectionner » ferme la barre', async () => {
    const { w } = await open(rows())
    await tick(w, 'Poser')
    await w.find('[data-testid="clear-selection"]').trigger('click')
    expect(w.find('[data-testid="selection-bar"]').exists()).toBe(false)
  })

  it('Échap vide la sélection, mais pas quand on tape dans un champ', async () => {
    const { w } = await open(rows())
    await tick(w, 'Poser')
    const search = w.find('[data-testid="quick-search"]').element as HTMLInputElement
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(w.find('[data-testid="selection-bar"]').exists()).toBe(true) // l'Échap du champ de recherche lui appartient
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(w.find('[data-testid="selection-bar"]').exists()).toBe(false)
  })
})

describe('liste vide', () => {
  it('sans filtre, un bouton propose de créer le premier document', async () => {
    const { w } = await open([])
    expect(w.find('[data-testid="create-first"]').exists()).toBe(true)
  })

  it('un filtre qui ne donne rien n’invite pas à créer : c’est le filtre qui cache les documents', async () => {
    const { w } = await open([], '/app/Task?filters=' + encodeURIComponent(JSON.stringify({ title: 'zzz' })))
    expect(w.find('[data-testid="create-first"]').exists()).toBe(false)
  })
})
