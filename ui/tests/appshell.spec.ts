import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import { NAV_KEY, readNav } from '@/lib/navstate'
import { SERVICES } from '@/lib/services'
import type { Workspace } from '@/lib/types'
import { useTheme } from '@/stores/theme'

const WORKSPACES: Workspace[] = [
  {
    name: 'sales',
    label: 'Ventes',
    icon: 'shopping-cart',
    blocks: [
      { type: 'header', text: 'Facturation' },
      { type: 'shortcut', doctype: 'Invoice', label: 'Facture', icon: 'receipt', create: true },
      { type: 'header', text: 'Clients' },
      { type: 'shortcut', doctype: 'Customer', label: 'Client', icon: 'users', create: true },
      { type: 'shortcut', doctype: 'Country', label: 'Pays', create: true },
      // Un réglage n'est pas une entrée de l'application : il est dans Paramètres (le serveur ne l'envoie plus, le Desk ne l'affiche pas non plus).
      { type: 'shortcut', doctype: 'Sales Settings', label: 'Réglages des ventes', create: false, single: true },
    ],
  },
  {
    name: 'stock',
    label: 'Stock',
    icon: 'warehouse',
    blocks: [{ type: 'shortcut', doctype: 'Bin', label: 'Solde', create: false }],
  },
]

let narrowNow = false
const mounted: { unmount: () => void }[] = []
// Les menus et la barre de commande se montent dans <body> : sans nettoyage, un test retrouverait ceux du précédent.
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})
beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  narrowNow = false
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: narrowNow, addEventListener: () => {}, removeEventListener: () => {} })),
  )
})

async function shell(start = '/', content = '<p>contenu</p>') {
  const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
  sessionStorage.setItem('xdt.desk.token', `${b64({ sub: 'alice.martin', user: { email: 'alice.martin@example.com' }, tenant_id: 'acme', exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/tasks', name: 'tasks', component: { render: () => null } },
      { path: '/files', name: 'files', component: { render: () => null } },
      { path: '/w/:name', name: 'workspace', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  await router.push(start)
  const pinia = createPinia()
  const w = mount(AppShell, {
    slots: { default: content },
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, pinia, router],
      provide: {
        [SERVICES as symbol]: {
          transport: {},
          config: { baseUrl: '', loginUrl: null },
          api: {
            doctypes: async () => [
              { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
              { name: 'Customer', label: 'Client', module: 'sales', create: true },
              { name: 'Bin', label: 'Solde', module: 'stock', create: false },
            ],
            workspaces: async () => WORKSPACES,
          },
        },
      },
    },
  })
  mounted.push(w)
  await flushPromises()
  return { w, router, pinia }
}
type Shell = Awaited<ReturnType<typeof shell>>
const sidebar = (s: Shell) => s.w.find('#desk-sidebar')
const sub = (s: Shell, name: string) => s.w.find(`[data-testid="ws-${name}"] .ws-sub`)

describe('barre latérale par espaces', () => {
  it('liste les espaces avec leur icône, rangés comme le serveur les envoie', async () => {
    const s = await shell()
    expect(s.w.findAll('.ws-item .ws-main').map((e) => e.text())).toEqual(['Ventes', 'Stock'])
    expect(s.w.find('[data-testid="ws-sales"] .ws-main i').classes()).toContain('pi-shopping-cart')
    expect(sidebar(s).text()).toContain('Accueil')
  })

  it('sans choix, seul l’espace de la page courante est déplié', async () => {
    const s = await shell('/app/Invoice')
    expect(sub(s, 'sales').exists()).toBe(true)
    expect(sub(s, 'sales').findAll('a').map((a) => a.text())).toEqual(['Facture', 'Client', 'Pays']) // les raccourcis, pas les titres
    expect(sub(s, 'stock').exists()).toBe(false)
  })

  it('chaque entrée porte l’icône de son type (une icône par défaut sinon), et les sous-modules ont leur titre', async () => {
    const s = await shell('/app/Invoice')
    expect(s.w.find('[data-testid="nav-Invoice"] i').classes()).toContain('pi-receipt')
    expect(s.w.find('[data-testid="nav-Customer"] i').classes()).toContain('pi-users')
    expect(s.w.find('[data-testid="nav-Country"] i').classes()).toContain('pi-file') // pas d'icône déclarée : jamais un vide
    expect(sub(s, 'sales').findAll('.ws-section').map((b) => b.text())).toEqual(['Facturation', 'Clients'])
  })

  it('un seul groupe ne montre pas de titre ; replier un sous-module masque ses entrées, et le choix est gardé', async () => {
    const s = await shell('/w/stock')
    expect(sub(s, 'stock').findAll('.ws-section').length).toBe(0) // un seul groupe : une liste, pas un titre
    const t = await shell('/app/Invoice')
    await t.w.find('[data-testid="ws-section-sales-Clients"]').trigger('click')
    expect(sub(t, 'sales').findAll('a').map((a) => a.text())).toEqual(['Facture'])
    expect(t.w.find('[data-testid="ws-section-sales-Clients"]').attributes('aria-expanded')).toBe('false')
    expect(readNav().expanded).toMatchObject({ 'sales/Clients': false })
    await t.w.find('[data-testid="ws-section-sales-Clients"]').trigger('click')
    expect(sub(t, 'sales').findAll('a').length).toBe(3)
  })

  it('la page d’un espace le déplie aussi', async () => {
    const s = await shell('/w/stock')
    expect(sub(s, 'stock').exists()).toBe(true)
    expect(sub(s, 'sales').exists()).toBe(false)
  })

  it('un clic sur la flèche déplie ou replie, et le choix est gardé', async () => {
    const s = await shell('/')
    expect(sub(s, 'stock').exists()).toBe(false)
    await s.w.find('[data-testid="ws-toggle-stock"]').trigger('click')
    expect(sub(s, 'stock').exists()).toBe(true)
    expect(s.w.find('[data-testid="ws-toggle-stock"]').attributes('aria-expanded')).toBe('true')
    expect(readNav().expanded).toEqual({ stock: true })
    await s.w.find('[data-testid="ws-toggle-stock"]').trigger('click')
    expect(sub(s, 'stock').exists()).toBe(false)
    expect(readNav().expanded).toEqual({ stock: false })
  })

  it('un choix explicite l’emporte sur la page courante', async () => {
    localStorage.setItem(NAV_KEY, JSON.stringify({ collapsed: false, expanded: { sales: false } }))
    const s = await shell('/app/Invoice')
    expect(sub(s, 'sales').exists()).toBe(false)
  })

  it('l’espace de la page est en évidence', async () => {
    const s = await shell('/app/Bin')
    expect(s.w.find('[data-testid="ws-stock"] .ws-row').classes()).toContain('router-link-active')
    expect(s.w.find('[data-testid="ws-sales"] .ws-row').classes()).not.toContain('router-link-active')
  })
})

describe('barre latérale repliable (grand écran)', () => {
  it('le bouton la replie, la page prend la place, et le choix est gardé', async () => {
    const s = await shell()
    expect(s.w.find('.desk-shell').classes()).not.toContain('collapsed')
    await s.w.find('[data-testid="nav-toggle"]').trigger('click')
    expect(s.w.find('.desk-shell').classes()).toContain('collapsed')
    expect(s.w.find('[data-testid="nav-toggle"]').attributes('aria-expanded')).toBe('false')
    expect(readNav().collapsed).toBe(true)
    await s.w.find('[data-testid="nav-toggle"]').trigger('click')
    expect(s.w.find('.desk-shell').classes()).not.toContain('collapsed')
  })

  it('une barre repliée au dernier passage le reste', async () => {
    localStorage.setItem(NAV_KEY, JSON.stringify({ collapsed: true, expanded: {} }))
    const s = await shell()
    expect(s.w.find('.desk-shell').classes()).toContain('collapsed')
  })
})

describe('tiroir (écran étroit)', () => {
  it('le bouton ouvre et ferme le tiroir sans toucher au repli de grand écran', async () => {
    narrowNow = true
    const s = await shell()
    const toggle = s.w.find('[data-testid="nav-toggle"]')
    expect(sidebar(s).attributes('data-open')).toBe('false')
    await toggle.trigger('click')
    expect(sidebar(s).attributes('data-open')).toBe('true')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(s.w.find('[data-testid="nav-backdrop"]').exists()).toBe(true)
    await toggle.trigger('click')
    expect(sidebar(s).attributes('data-open')).toBe('false')
    expect(readNav().collapsed).toBe(false) // le tiroir n'est pas le repli
  })

  it('choisir une page referme le tiroir', async () => {
    narrowNow = true
    const s = await shell()
    await s.w.find('[data-testid="nav-toggle"]').trigger('click')
    await s.router.push('/app/Invoice')
    await flushPromises()
    expect(sidebar(s).attributes('data-open')).toBe('false')
  })

  it('Échap et le fond referment le tiroir', async () => {
    narrowNow = true
    const s = await shell()
    await s.w.find('[data-testid="nav-toggle"]').trigger('click')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(sidebar(s).attributes('data-open')).toBe('false')
    await s.w.find('[data-testid="nav-toggle"]').trigger('click')
    await s.w.find('[data-testid="nav-backdrop"]').trigger('click')
    expect(sidebar(s).attributes('data-open')).toBe('false')
  })
})

describe('en-tête', () => {
  it('l’avatar porte les initiales de l’utilisateur et ouvre son menu', async () => {
    const s = await shell()
    expect(s.w.find('[data-testid="user-menu"]').text()).toBe('AM')
    await s.w.find('[data-testid="user-menu"]').trigger('click')
    await flushPromises()
    const labels = [...document.body.querySelectorAll('.p-menu-item-label')].map((e) => e.textContent)
    expect(labels).toEqual(expect.arrayContaining(['alice.martin@example.com', 'Clair', 'Sombre', 'Automatique', 'Se déconnecter']))
  })

  it('« Fichiers » est dans le menu de l’utilisateur et mène à la page des fichiers', async () => {
    const s = await shell()
    await s.w.find('[data-testid="user-menu"]').trigger('click')
    await flushPromises()
    const files = [...document.body.querySelectorAll('.p-menu-item')].find((e) => e.textContent?.trim() === 'Fichiers')!
    ;(files.querySelector('a, .p-menu-item-link') as HTMLElement).click()
    await flushPromises()
    expect(s.router.currentRoute.value.name).toBe('files')
  })

  it('choisir « Sombre » dans le menu change le thème', async () => {
    const s = await shell()
    await s.w.find('[data-testid="user-menu"]').trigger('click')
    await flushPromises()
    const dark = [...document.body.querySelectorAll('.p-menu-item')].find((e) => e.textContent?.includes('Sombre'))!
    ;(dark.querySelector('a, .p-menu-item-link') as HTMLElement).click()
    await flushPromises()
    expect(useTheme(s.pinia).mode).toBe('dark')
  })

  it('la recherche ouvre la barre de commande, comme Ctrl+G et Ctrl+K', async () => {
    const s = await shell()
    expect(document.body.querySelector('[data-testid="palette"]')).toBeNull()
    await s.w.find('[data-testid="search"]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-testid="palette-input"]')).not.toBeNull()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', ctrlKey: true })) // rebascule : ferme
    await flushPromises()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))
    await flushPromises()
    expect(document.body.querySelector('[data-testid="palette-input"]')).not.toBeNull()
  })

  it('le raccourci du navigateur n’est pas laissé passer', async () => {
    await shell()
    const e = new KeyboardEvent('keydown', { key: 'g', ctrlKey: true, cancelable: true })
    window.dispatchEvent(e)
    expect(e.defaultPrevented).toBe(true)
    const other = new KeyboardEvent('keydown', { key: 'g', cancelable: true }) // un « g » tapé dans un champ ne doit pas être avalé
    window.dispatchEvent(other)
    expect(other.defaultPrevented).toBe(false)
  })
})

describe('titre de la page, focus et lien d’évitement', () => {
  it('le titre de l’onglet est « Page · Organisation », d’après le h1 de la page', async () => {
    await shell('/', '<h1>Factures</h1>')
    await vi.waitFor(() => expect(document.title).toMatch(/^Factures · /))
  })

  it('un changement de page pose le focus sur son titre, pas le premier affichage', async () => {
    const s = await shell('/', '<h1 id="t">Factures</h1>')
    await vi.waitFor(() => expect(document.title).toMatch(/^Factures · /))
    expect(document.activeElement?.id).not.toBe('t') // la première page ne vole pas le focus
    await s.router.push('/tasks')
    await vi.waitFor(() => expect(document.activeElement?.id).toBe('t'))
  })

  it('une page sans titre amène le focus sur la zone principale', async () => {
    const s = await shell('/', '<p>contenu</p>')
    await s.router.push('/tasks')
    await vi.waitFor(() => expect(document.activeElement?.id).toBe('desk-main'), { timeout: 3000 })
  })

  it('le lien « Aller au contenu » est le premier de la page et mène au titre', async () => {
    const s = await shell('/', '<h1 id="t">Factures</h1>')
    const first = s.w.find('a, button')
    expect(first.attributes('data-testid')).toBe('skip-link')
    await s.w.find('[data-testid="skip-link"]').trigger('click')
    expect(document.activeElement?.id).toBe('t')
  })
})

describe('tiroir : clavier et lecteur d’écran (C3.2)', () => {
  const tab = (shift = false) => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, cancelable: true }))

  it('ouvert, c’est une fenêtre modale : le focus y entre, la page est inerte, Tab tourne entre le menu et le tiroir', async () => {
    narrowNow = true
    const s = await shell()
    const nav = sidebar(s)
    expect(nav.attributes('inert')).toBeDefined() // fermé hors écran : Tab ne s'y perd pas
    await s.w.find('[data-testid="nav-toggle"]').trigger('click')
    await flushPromises()
    expect(nav.attributes('inert')).toBeUndefined()
    expect(nav.attributes('role')).toBe('dialog')
    expect(nav.attributes('aria-modal')).toBe('true')
    expect(s.w.find('#desk-main').attributes('inert')).toBeDefined()
    const items = [...nav.element.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')]
    expect(document.activeElement).toBe(items[0]) // le premier lien du tiroir
    items.at(-1)!.focus()
    tab()
    expect(document.activeElement).toBe(s.w.find('[data-testid="nav-toggle"]').element) // du dernier, on revient au bouton du menu
    tab(true)
    expect(document.activeElement).toBe(items.at(-1)) // et Maj+Tab repart en arrière dans le tiroir
  })

  it('Échap et le fond rendent le focus au bouton du menu ; fermé, rien n’est modal', async () => {
    narrowNow = true
    const s = await shell()
    const toggle = s.w.find('[data-testid="nav-toggle"]')
    await toggle.trigger('click')
    await flushPromises()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(document.activeElement).toBe(toggle.element)
    expect(sidebar(s).attributes('role')).toBeUndefined()
    expect(s.w.find('#desk-main').attributes('inert')).toBeUndefined()
    await toggle.trigger('click')
    await flushPromises()
    await s.w.find('[data-testid="nav-backdrop"]').trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(toggle.element)
  })

  it('sur grand écran, la barre latérale n’est jamais modale ni inerte', async () => {
    const s = await shell()
    expect(sidebar(s).attributes('inert')).toBeUndefined()
    expect(sidebar(s).attributes('role')).toBeUndefined()
  })
})
