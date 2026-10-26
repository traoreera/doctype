import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import CommandPalette from '@/components/CommandPalette.vue'
import { recentsKey, writeRecents } from '@/lib/recents'
import { SERVICES } from '@/lib/services'
import { useMeta } from '@/stores/meta'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { useWorkspaces } from '@/stores/workspaces'

const mounted: { unmount: () => void }[] = []
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  document.body.innerHTML = ''
})
beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

async function palette() {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
  sessionStorage.setItem('xdt.desk.token', `${b64({ sub: 'alice', tenant_id: 'acme', exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`)
  writeRecents(recentsKey('acme', 'alice'), [{ doctype: 'Invoice', name: 'FA-2026-00001', title: 'Facture ACME', at: Date.now() }])
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/w/:name', name: 'workspace', component: { render: () => null } },
      { path: '/tasks', name: 'tasks', component: { render: () => null } },
      { path: '/shared', name: 'shared', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  await router.push('/')
  const pinia = createPinia()
  const open = ref(true)
  const Host = defineComponent({ render: () => h(CommandPalette, { open: open.value, 'onUpdate:open': (v: boolean) => (open.value = v) }) })
  const api = {
    doctypes: async () => [
      { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
      { name: 'Bin', label: 'Solde', module: 'stock', create: false },
    ],
    workspaces: async () => [{ name: 'stock', label: 'Stock', icon: 'warehouse', blocks: [] }],
    globalSearch: async (q: string) =>
      q.includes('acme') ? [{ doctype: 'Invoice', label: 'Facture', name: 'FA-1', title: 'Résultat acme' }] : [],
  }
  const w = mount(Host, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, pinia, router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } } },
  })
  mounted.push(w)
  await useMeta(pinia).loadSummaries()
  await useWorkspaces(pinia).load()
  await flushPromises()
  return { w, router, pinia, open }
}
const input = () => document.body.querySelector('[data-testid="palette-input"]') as HTMLInputElement
const labels = () => [...document.body.querySelectorAll('.palette-item .palette-label')].map((e) => e.textContent)
const type = async (text: string) => {
  input().value = text
  input().dispatchEvent(new Event('input'))
  await flushPromises()
}
const press = async (key: string) => {
  input().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  await flushPromises()
}

describe('barre de commande', () => {
  it('à l’ouverture : champ vide et sélectionné, les récents puis les espaces', async () => {
    await palette()
    await flushPromises()
    expect(input().value).toBe('')
    expect(document.activeElement).toBe(input())
    expect(labels().slice(0, 4)).toEqual(['Facture ACME', 'Accueil', 'Mes tâches', 'Stock'])
    expect([...document.body.querySelectorAll('.palette-group')].map((g) => g.textContent)).toContain('Récents')
  })

  it('filtre en tapant, et dit quand rien ne correspond', async () => {
    await palette()
    await type('solde')
    expect(labels()).toEqual(['Solde'])
    await type('zzzz')
    expect(document.body.querySelector('[data-testid="palette-empty"]')).not.toBeNull()
  })

  it('Entrée va à la commande surlignée et referme', async () => {
    const { router, open } = await palette()
    await type('solde')
    await press('Enter')
    expect(router.currentRoute.value.fullPath).toBe('/app/Bin')
    expect(open.value).toBe(false)
  })

  it('les flèches changent la sélection, et bouclent', async () => {
    const { router } = await palette()
    await type('facture')
    expect(labels()).toEqual(['Facture ACME', 'Facture', 'Nouveau : Facture'])
    await press('ArrowDown')
    await press('ArrowDown')
    await press('Enter')
    expect(router.currentRoute.value.fullPath).toBe('/app/Invoice/new')
  })

  it('ArrowUp depuis la première ligne boucle sur la dernière', async () => {
    const { router } = await palette()
    await type('facture')
    await press('ArrowUp')
    await press('Enter')
    expect(router.currentRoute.value.name).toBe('new')
  })

  it('« créer » n’est proposé que là où le serveur l’offre', async () => {
    await palette()
    await type('nouveau')
    expect(labels()).toEqual(['Nouveau : Facture']) // pas de « Nouveau : Solde »
  })

  it('un récent ouvre le document', async () => {
    const { router } = await palette()
    await type('acme')
    await press('Enter')
    expect(router.currentRoute.value.fullPath).toBe('/app/Invoice/FA-2026-00001')
  })

  it('cherche aussi dans les documents (recherche globale), et un résultat ouvre son formulaire', async () => {
    const { router } = await palette()
    await type('acme')
    await new Promise((r) => setTimeout(r, 220)) // la recherche globale est débouncée
    await flushPromises()
    expect(labels()).toContain('Résultat acme')
    const rows = [...document.body.querySelectorAll('.palette-item')] as HTMLElement[]
    rows.find((r) => r.textContent?.includes('Résultat acme'))!.click()
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/app/Invoice/FA-1')
  })

  it('un espace ouvre sa page', async () => {
    const { router } = await palette()
    await type('stock')
    await press('Enter')
    expect(router.currentRoute.value.fullPath).toBe('/w/stock')
  })

  it('une action change le thème sans changer de page', async () => {
    const { router, pinia } = await palette()
    await type('sombre')
    await press('Enter')
    expect(useTheme(pinia).mode).toBe('dark')
    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it('« se déconnecter » met fin à la session', async () => {
    const { pinia } = await palette()
    expect(useSession(pinia).token).not.toBeNull()
    await type('deconnecter')
    await press('Enter')
    expect(useSession(pinia).token).toBeNull()
  })

  it('un clic sur une ligne la lance', async () => {
    const { router } = await palette()
    await type('solde')
    ;(document.body.querySelector('.palette-item') as HTMLElement).click()
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/app/Bin')
  })

  it('rouverte, elle repart de zéro', async () => {
    const { open } = await palette()
    await type('solde')
    open.value = false
    await flushPromises()
    open.value = true
    await flushPromises()
    expect(input().value).toBe('')
  })
})
