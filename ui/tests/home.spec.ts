import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import { writeRecents, recentsKey } from '@/lib/recents'
import { SERVICES } from '@/lib/services'
import type { DocTypeSummary } from '@/lib/types'

const SUMMARIES: DocTypeSummary[] = [
  { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
  { name: 'Customer', label: 'Client', module: 'sales', create: false },
  { name: 'Bin', label: 'Solde', module: 'stock', create: false },
  { name: 'Stock Entry', label: 'Mouvement de stock', module: 'stock', create: true },
]

const WORKSPACES = [
  { name: 'sales', label: 'Ventes', icon: 'shopping-cart', description: 'Clients et factures', blocks: [] },
  { name: 'stock', label: 'Stock', icon: 'warehouse', blocks: [] },
]

async function mountHome(summaries: DocTypeSummary[] = SUMMARIES, recent: object[] = [], workspaces = WORKSPACES) {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
  sessionStorage.setItem('xdt.desk.token', `${b64({ sub: 'alice', tenant_id: 'acme', exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`)
  writeRecents(recentsKey('acme', 'alice'), recent as never)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/w/:name', name: 'workspace', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  const pinia = createPinia()
  const w = mount(HomeView, {
    global: {
      plugins: [PrimeVue, pinia, router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { doctypes: async () => summaries, workspaces: async () => workspaces } } },
    },
  })
  const { useMeta } = await import('@/stores/meta')
  await useMeta(pinia).loadSummaries()
  await flushPromises()
  return w
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('accueil', () => {
  it('les espaces de travail s’ouvrent depuis l’accueil, avec leur icône et leur description', async () => {
    const w = await mountHome()
    expect(w.findAll('.home-space strong').map((e) => e.text())).toEqual(['Ventes', 'Stock'])
    expect(w.find('[data-testid="space-sales"]').attributes('href')).toBe('/w/sales')
    expect(w.find('[data-testid="space-sales"] .pi').classes()).toContain('pi-shopping-cart')
    expect(w.find('[data-testid="space-sales"]').text()).toContain('Clients et factures')
  })

  it('les documents récents s’affichent avec leur titre et leur type, les plus récents d’abord', async () => {
    const now = Date.now()
    const w = await mountHome(SUMMARIES, [
      { doctype: 'Invoice', name: 'FA-2', title: 'Facture ACME', at: now },
      { doctype: 'Customer', name: 'c1', title: 'ACME SAS', at: now - 60_000 },
    ])
    const rows = w.findAll('.recent-row')
    expect(rows.map((r) => r.find('.recent-title').text())).toEqual(['Facture ACME', 'ACME SAS'])
    expect(rows[0]!.find('.recent-type').text()).toBe('Facture')
  })

  it('un récent dont le type n’est plus accessible n’apparaît pas', async () => {
    const w = await mountHome(SUMMARIES, [{ doctype: 'Secret', name: 's', title: 'Dossier', at: Date.now() }])
    expect(w.find('[data-testid="recents"]').exists()).toBe(false)
  })

  it('sans aucun récent, pas de section vide', async () => {
    expect((await mountHome()).find('[data-testid="recents"]').exists()).toBe(false)
  })

  it('un compte sans aucun droit le dit', async () => {
    expect((await mountHome([], [], [])).find('[data-testid="no-doctype"]').exists()).toBe(true)
  })
})
