import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import WorkspaceView from '@/views/WorkspaceView.vue'
import { SERVICES } from '@/lib/services'
import type { Workspace } from '@/lib/types'

const STOCK: Workspace = {
  name: 'stock',
  label: 'Stock',
  icon: 'warehouse',
  description: 'Réceptions, sorties et transferts',
  blocks: [
    { type: 'header', text: 'Opérations' },
    { type: 'shortcut', doctype: 'Stock Entry', label: 'Mouvement de stock', icon: 'arrow-right-arrow-left', create: true },
    { type: 'shortcut', doctype: 'Bin', label: 'Solde', create: false },
    { type: 'header', text: 'Référentiel' },
    { type: 'shortcut', doctype: 'Item', label: 'Article', create: true },
  ],
}

async function open(name: string, api: Record<string, unknown> = {}, workspaces: Workspace[] = [STOCK]) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
    ],
  })
  const list = vi.fn(async (doctype: string, _query?: unknown) => ({ items: [], total: { 'Stock Entry': 1234, Bin: 2, Item: 0 }[doctype] ?? 0, limit: 1, offset: 0 }))
  const w = mount(WorkspaceView, {
    props: { name },
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { workspaces: async () => workspaces, list, ...api } } },
    },
  })
  await flushPromises()
  return { w, list }
}

describe('page d’un espace de travail', () => {
  it('titre, icône, description, sections et raccourcis dans l’ordre du serveur', async () => {
    const { w } = await open('stock')
    expect(w.find('[data-testid="workspace-title"]').text()).toBe('Stock')
    expect(w.find('.ws-icon').classes()).toContain('pi-warehouse')
    expect(w.text()).toContain('Réceptions, sorties et transferts')
    expect(w.findAll('.home-title').map((h) => h.text())).toEqual(['Opérations', 'Référentiel'])
    expect(w.findAll('.home-card').map((c) => c.find('.home-label').text())).toEqual(['Mouvement de stock', 'Solde', 'Article'])
  })

  it('chaque raccourci mène à la liste de son type, et « + » à sa création, là où le serveur l’offre', async () => {
    const { w } = await open('stock')
    expect(w.find('[data-testid="shortcut-Bin"]').attributes('href')).toBe('/app/Bin')
    expect(w.find('[data-testid="new-Stock Entry"]').attributes('href')).toBe('/app/Stock%20Entry/new')
    expect(w.find('[data-testid="new-Item"]').exists()).toBe(true)
    expect(w.find('[data-testid="new-Bin"]').exists()).toBe(false) // pas de « + » sans droit de créer
  })

  it('chaque raccourci montre le nombre de documents, formaté ; un zéro est un chiffre', async () => {
    const { w } = await open('stock')
    expect(w.find('[data-testid="count-Stock Entry"]').text().replace(/\s/g, ' ')).toBe('1 234')
    expect(w.find('[data-testid="count-Bin"]').text()).toBe('2')
    expect(w.find('[data-testid="count-Item"]').text()).toBe('0')
  })

  it('les chiffres se lisent par une requête d’un seul document, jamais la liste entière', async () => {
    const { list } = await open('stock')
    expect(list).toHaveBeenCalledTimes(3)
    for (const call of list.mock.calls) expect(call[1]).toEqual({ limit: 1 })
  })

  it('un compte qui ne peut pas lire un type : la tuile reste, sans chiffre, plutôt que d’échouer', async () => {
    const list = vi.fn(async (d: string) => {
      if (d === 'Bin') throw new Error('403')
      return { items: [], total: 5, limit: 1, offset: 0 }
    })
    const { w } = await open('stock', { list })
    expect(w.find('[data-testid="shortcut-Bin"]').exists()).toBe(true)
    expect(w.find('[data-testid="count-Bin"]').exists()).toBe(false)
    expect(w.find('[data-testid="count-Item"]').text()).toBe('5')
  })

  it('un espace inconnu, ou qu’aucun droit ne montre, le dit et ramène à l’accueil', async () => {
    const { w } = await open('inconnu')
    expect(w.find('[data-testid="workspace-missing"]').text()).toContain('n’existe pas')
    expect(w.find('[data-testid="workspace-missing"] a').attributes('href')).toBe('/')
  })

  it('pendant le chargement, on ne dit pas « introuvable »', async () => {
    let release!: (w: Workspace[]) => void
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { render: () => null } },
        { path: '/app/:doctype', name: 'list', component: { render: () => null } },
        { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      ],
    })
    const w = mount(WorkspaceView, {
      props: { name: 'stock' },
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { workspaces: () => new Promise<Workspace[]>((r) => (release = r)), list: vi.fn() } } },
      },
    })
    await flushPromises()
    expect(w.find('[data-testid="workspace-missing"]').exists()).toBe(false)
    expect(w.text()).toContain('Chargement')
    release([STOCK])
    await flushPromises()
    expect(w.find('[data-testid="workspace-title"]').exists()).toBe(true)
  })
})

describe('habillage d’un espace de travail', () => {
  // Les fenêtres des tests précédents restent dans le DOM (le composant n'est pas démonté) : on vise toujours la DERNIÈRE.
  const last = (selector: string): HTMLElement => {
    const all = document.querySelectorAll<HTMLElement>(selector)
    return all[all.length - 1]!
  }
  async function type(value: string): Promise<void> {
    const text = last('[data-testid="override-text"]') as HTMLTextAreaElement
    text.value = value
    text.dispatchEvent(new Event('input'))
    await flushPromises()
  }

  it('ouvre l’éditeur pré-rempli du contenu affiché, et enregistre le JSON validé par le serveur', async () => {
    const saveWorkspaceOverride = vi.fn(async () => ({ name: 'stock', config: {} }))
    const { w } = await open('stock', { saveWorkspaceOverride })
    await w.find('[data-testid="workspace-edit"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="workspace-json"]').trigger('click')
    await flushPromises()
    const text = last('[data-testid="override-text"]') as HTMLTextAreaElement
    expect(text.value).toContain('"label": "Stock"')
    expect(text.value).toContain('"doctype": "Stock Entry"')
    await type(JSON.stringify({ label: 'Caisse', blocks: STOCK.blocks }))
    last('[data-testid="override-save"]').click()
    await flushPromises()
    expect(saveWorkspaceOverride).toHaveBeenCalledWith('stock', expect.objectContaining({ label: 'Caisse' }), 'user') // « pour moi » par défaut
  })

  it('refuse un JSON invalide sans rien envoyer au serveur', async () => {
    const saveWorkspaceOverride = vi.fn(async () => ({ name: 'stock', config: {} }))
    const { w } = await open('stock', { saveWorkspaceOverride })
    await w.find('[data-testid="workspace-edit"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="workspace-json"]').trigger('click')
    await flushPromises()
    await type('{ pas du json')
    last('[data-testid="override-save"]').click()
    await flushPromises()
    expect(saveWorkspaceOverride).not.toHaveBeenCalled()
    expect(last('[data-testid="override-parse-error"]')).toBeTruthy()
  })

  it('« Revenir au YAML » retire l’habillage', async () => {
    const removeWorkspaceOverride = vi.fn(async () => undefined)
    const { w } = await open('stock', { removeWorkspaceOverride })
    await w.find('[data-testid="workspace-edit"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="workspace-json"]').trigger('click')
    await flushPromises()
    last('[data-testid="override-reset"]').click()
    await flushPromises()
    expect(removeWorkspaceOverride).toHaveBeenCalledWith('stock', 'user')
  })
})

describe('sous-modules d’un espace (barre latérale)', () => {
  it('range les raccourcis sous le titre qui les précède, sans les titres vides ni les réglages', async () => {
    const { workspaceSections } = await import('@/lib/sections')
    const store = { sections: workspaceSections }
    const w = {
      name: 'x',
      label: 'X',
      blocks: [
        { type: 'header', text: 'Vue d’ensemble' }, // que des chiffres : pas un sous-module
        { type: 'number_card', label: 'N', doctype: 'A', function: 'count' },
        { type: 'shortcut', doctype: 'Lonely', label: 'Seul', create: false }, // raccourci rattaché à « Vue d’ensemble »
        { type: 'header', text: 'Opérations' },
        { type: 'shortcut', doctype: 'A', label: 'A', create: false },
        { type: 'shortcut', doctype: 'Cfg', label: 'Réglages', create: false, single: true },
        { type: 'shortcut', doctype: 'B', label: 'B', create: false },
        { type: 'header', text: 'Vide' },
      ],
    } as unknown as Workspace
    expect(store.sections(w).map((s) => [s.title, s.items.map((i) => i.doctype)])).toEqual([
      ['Vue d’ensemble', ['Lonely']],
      ['Opérations', ['A', 'B']],
    ])
    expect(store.sections({ ...w, blocks: [{ type: 'shortcut', doctype: 'A', label: 'A', create: false }] } as unknown as Workspace)).toEqual([{ title: null, items: expect.any(Array) }])
  })
})
