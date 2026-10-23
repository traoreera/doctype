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
      provide: {
        [SERVICES as symbol]: {
          transport: {},
          config: { baseUrl: '', loginUrl: null },
          // `aggregate` : les cartes de chiffres de l'accueil le demandent, une fois chacune.
          api: { doctypes: async () => summaries, workspaces: async () => workspaces, aggregate: async () => ({ metrics: [{ function: 'count' }], rows: [{ values: [7] }] }) },
        },
      },
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

  it('les chiffres viennent des espaces : la première carte de chaque espace, puis les suivantes', async () => {
    const card = (label: string, doctype: string) => ({ type: 'number_card', label, doctype, function: 'count' })
    const spaces = [
      { name: 'sales', label: 'Ventes', blocks: [{ type: 'header', text: 'Vue d’ensemble' }, card('Factures', 'Invoice'), card('Chiffre d’affaires', 'Invoice')] },
      { name: 'stock', label: 'Stock', blocks: [card('Mouvements', 'Stock Entry')] },
    ]
    const w = await mountHome(SUMMARIES, [], spaces as never)
    expect(w.findAll('[data-testid="number-card"] .number-card-label').map((e) => e.text())).toEqual(['Factures', 'Mouvements', 'Chiffre d’affaires'])
    expect(w.find('[data-testid="home-figures"]').exists()).toBe(true)
  })

  it('sans carte déclarée par les espaces, pas de section de chiffres', async () => {
    expect((await mountHome()).find('[data-testid="home-figures"]').exists()).toBe(false)
  })

  it('les cartes d’espaces se taisent sur grand écran dès que l’accueil a autre chose à montrer', async () => {
    // La barre latérale liste déjà les espaces : les répéter prenait 224 px des 720 de l'écran. `home-spaces` les réserve aux écrans
    // où le menu est escamoté (desk.css). Sans récent ni tâche, en revanche, elles sont le seul contenu : elles restent partout.
    const bare = await mountHome()
    expect(bare.find('[data-testid="home-spaces"]').classes()).not.toContain('home-spaces')

    const filled = await mountHome(SUMMARIES, [{ doctype: 'Invoice', name: 'FA-2', title: 'Facture ACME', at: Date.now() }])
    expect(filled.find('[data-testid="home-spaces"]').classes()).toContain('home-spaces')
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

describe('accueil : salutation et création rapide', () => {
  it('la salutation suit l’heure et nomme la personne d’après son adresse', async () => {
    const w = await mountHome()
    const hello = w.find('[data-testid="greeting"]').text()
    expect(hello).toMatch(/^(Bonjour|Bonsoir)/)
    expect(hello).toMatch(/^(Bonjour|Bonsoir)(, \S+)?$/)
  })

  it('« Créer » ne propose que les types qu’on peut créer, ceux qu’on a ouverts récemment d’abord', async () => {
    const w = await mountHome(SUMMARIES, [{ doctype: 'Stock Entry', name: 'MS-1', title: 'Réception', at: Date.now() }])
    const chips = w.findAll('[data-testid^="create-"]').map((c) => c.attributes('data-testid'))
    expect(chips).toEqual(['create-Stock Entry', 'create-Invoice']) // Client (create: false) et Solde ne figurent pas
    expect(w.find('[data-testid="create-Invoice"]').attributes('href')).toBe('/app/Invoice/new')
  })

  it('« Créer » met en tête le premier type créable de CHAQUE espace, dans l’ordre des espaces', async () => {
    // Le défaut corrigé : l'alphabet plaçait « Devise » (un référentiel) devant « Facture » et « Mouvement de stock ».
    const summaries: DocTypeSummary[] = [
      { name: 'Devise', label: 'Devise', module: 'geo', create: true },
      { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
      { name: 'Customer', label: 'Client', module: 'sales', create: true },
      { name: 'Stock Entry', label: 'Mouvement de stock', module: 'stock', create: true },
    ]
    const spaces = [
      { name: 'sales', label: 'Ventes', blocks: [{ type: 'shortcut', doctype: 'Invoice', label: 'Facture', create: true }, { type: 'shortcut', doctype: 'Customer', label: 'Client', create: true }] },
      { name: 'stock', label: 'Stock', blocks: [{ type: 'shortcut', doctype: 'Stock Entry', label: 'Mouvement de stock', create: true }] },
      { name: 'geo', label: 'Référentiel', blocks: [{ type: 'shortcut', doctype: 'Devise', label: 'Devise', create: true }] },
    ]
    const w = await mountHome(summaries, [], spaces as never)
    expect(w.findAll('[data-testid^="create-"]').map((c) => c.attributes('data-testid'))).toEqual([
      'create-Invoice', 'create-Stock Entry', 'create-Devise', 'create-Customer',
    ])
  })

  it('un raccourci qu’on ne peut pas créer ne prend pas la place du module', async () => {
    const spaces = [{ name: 'sales', label: 'Ventes', blocks: [{ type: 'shortcut', doctype: 'Bin', label: 'Solde', create: false }, { type: 'shortcut', doctype: 'Invoice', label: 'Facture', create: true }] }]
    const w = await mountHome(SUMMARIES, [], spaces as never)
    expect(w.findAll('[data-testid^="create-"]').map((c) => c.attributes('data-testid'))[0]).toBe('create-Invoice')
  })

  it('un document par utilisateur (un profil) n’est jamais proposé à la création', async () => {
    const w = await mountHome([
      { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
      { name: 'User Profile', label: 'Profil', module: 'iam', create: true, per_user: true },
    ] as DocTypeSummary[])
    expect(w.findAll('[data-testid^="create-"]').map((c) => c.attributes('data-testid'))).toEqual(['create-Invoice'])
  })

  it('sans type créable, pas de rangée vide', async () => {
    const w = await mountHome(SUMMARIES.map((d) => ({ ...d, create: false })))
    expect(w.find('[data-testid="home-create"]').exists()).toBe(false)
  })

  it('les réglages et les types virtuels ne sont pas proposés à la création', async () => {
    const w = await mountHome([
      { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
      { name: 'Settings', label: 'Réglages', module: 'sales', create: true, single: true },
      { name: 'Member', label: 'Membre', module: 'iam', create: true, virtual: true },
    ] as DocTypeSummary[])
    expect(w.findAll('[data-testid^="create-"]').map((c) => c.attributes('data-testid'))).toEqual(['create-Invoice'])
  })
})

