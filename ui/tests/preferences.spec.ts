import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import { brandCss, brandPalette, contrastOn, luminance, mix } from '@/lib/brand'
import { datePattern, formatDate, setDateFormat } from '@/lib/format'
import { SERVICES } from '@/lib/services'
import { usePreferences } from '@/stores/preferences'
import type { OrgPreferences } from '@/lib/types'

afterEach(() => {
  setDateFormat(undefined)
  document.getElementById('desk-brand')?.remove()
  document.body.innerHTML = ''
  document.title = ''
})

describe('couleur de l’organisation', () => {
  it('ne prend que #rrggbb : tout le reste est ignoré (la couleur finit dans une feuille de style)', () => {
    for (const bad of ['red', '#abc', '#12345g', 'url(x)', '#2a78d6;}body{display:none', '', null, undefined, 12 as never]) {
      expect(brandPalette(bad as string)).toBeNull()
      expect(brandCss(bad as string)).toBe('')
    }
    expect(brandPalette('#2A78D6')?.light.color).toBe('#2a78d6')
  })

  it('le texte sur la teinte est noir ou blanc, celui qui contraste', () => {
    expect(contrastOn('#ffffff')).toBe('#111111')
    expect(contrastOn('#ffe066')).toBe('#111111') // jaune clair
    expect(contrastOn('#000000')).toBe('#ffffff')
    expect(contrastOn('#2a78d6')).toBe('#ffffff')
    expect(luminance('#000000')).toBe(0)
    expect(luminance('#ffffff')).toBeCloseTo(1, 5)
  })

  it('survol et appui assombrissent en clair, éclaircissent en sombre ; une teinte trop sombre est éclaircie en thème sombre', () => {
    const p = brandPalette('#2a78d6')!
    expect(luminance(p.light.hover)).toBeLessThan(luminance(p.light.color))
    expect(luminance(p.light.active)).toBeLessThan(luminance(p.light.hover))
    expect(luminance(p.dark.hover)).toBeGreaterThan(luminance(p.dark.color))
    const navy = brandPalette('#0a1a3a')!
    expect(navy.light.color).toBe('#0a1a3a')
    expect(luminance(navy.dark.color)).toBeGreaterThan(luminance('#0a1a3a')) // illisible sur fond sombre : éclaircie
    expect(mix('#000000', 1)).toBe('#ffffff')
    expect(mix('#ffffff', -1)).toBe('#000000')
  })

  it('le CSS pose les variables de PrimeVue pour les deux thèmes', () => {
    const css = brandCss('#2a78d6')
    expect(css).toContain(':root{--p-primary-color:#2a78d6;')
    expect(css).toContain(':root.app-dark{--p-primary-color:')
    expect(css).toContain('--p-primary-contrast-color:#ffffff')
  })
})

describe('format de date de l’organisation', () => {
  it('JJ/MM/AAAA par défaut, AAAA-MM-JJ et MM/JJ/AAAA au choix, une valeur inconnue ramène au défaut', () => {
    expect(formatDate('2026-10-19')).toBe('19/10/2026')
    expect(datePattern()).toBe('dd/mm/yy')
    setDateFormat('AAAA-MM-JJ')
    expect(formatDate('2026-10-19')).toBe('2026-10-19')
    expect(datePattern()).toBe('yy-mm-dd')
    setDateFormat('MM/JJ/AAAA')
    expect(formatDate('2026-10-19')).toBe('10/19/2026')
    expect(datePattern()).toBe('mm/dd/yy')
    setDateFormat('JJ.MM.AAAA')
    expect(formatDate('2026-10-19')).toBe('19/10/2026')
    expect(formatDate('pas une date')).toBe('pas une date')
  })
})

describe('store des préférences', () => {
  const setup = (prefs: () => Promise<OrgPreferences>) => {
    const pinia = createPinia()
    const app = { provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/x', loginUrl: null }, api: { preferences: prefs } } } }
    // Un store qui se sert de `useServices` demande le contexte d'une application : on le fournit comme le Desk.
    const host = mount({ template: '<div />' }, { global: { plugins: [pinia], provide: app.provide } })
    let store!: ReturnType<typeof usePreferences>
    host.vm.$.appContext.app.runWithContext(() => (store = usePreferences(pinia)))
    return { store, host }
  }

  it('applique nom, couleur et format ; le titre de l’onglet porte le nom', async () => {
    const { store } = setup(async () => ({ organization_name: ' ACME ', brand_color: '#2a78d6', date_format: 'AAAA-MM-JJ' }))
    await store.load()
    expect(store.name).toBe('ACME')
    expect(document.getElementById('desk-brand')?.textContent).toContain('--p-primary-color:#2a78d6')
    expect(formatDate('2026-10-19')).toBe('2026-10-19')
    expect(document.title).toBe('ACME')
  })

  it('une organisation sans réglage garde le Desk tel quel, et un échec n’est pas une erreur', async () => {
    const { store } = setup(async () => {
      throw new Error('hors ligne')
    })
    await store.load()
    expect(store.name).toBe('Desk')
    expect(document.getElementById('desk-brand')).toBeNull()
  })

  it('une couleur invalide n’injecte rien ; retirer la couleur retire la feuille', async () => {
    let value: OrgPreferences = { brand_color: 'red;}' }
    const { store } = setup(async () => value)
    await store.load()
    expect(document.getElementById('desk-brand')).toBeNull()
    value = { brand_color: '#2a78d6' }
    await store.load(true)
    expect(document.getElementById('desk-brand')).not.toBeNull()
    value = {}
    await store.load(true)
    expect(document.getElementById('desk-brand')).toBeNull()
  })

  it('la déconnexion efface tout : l’organisation suivante n’hérite de rien', async () => {
    const { store } = setup(async () => ({ organization_name: 'ACME', brand_color: '#2a78d6', date_format: 'MM/JJ/AAAA', logo: { path: 'files/acme/x?exp=1&sig=2', filename: 'l.png', content_type: 'image/png', size: 1, expires: 9e9 } }))
    await store.load()
    expect(store.logoUrl).toBe('/app/x/files/acme/x?exp=1&sig=2')
    store.reset()
    expect(store.name).toBe('Desk')
    expect(store.logoUrl).toBeNull()
    expect(document.getElementById('desk-brand')).toBeNull()
    expect(formatDate('2026-10-19')).toBe('19/10/2026')
  })
})

describe('barre du haut', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })))
  })

  async function shell(prefs: OrgPreferences) {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'home', component: { render: () => null } }, { path: '/tasks', name: 'tasks', component: { render: () => null } }, { path: '/settings/:doctype?', name: 'settings', component: { render: () => null } }] })
    await router.push('/')
    const w = mount(AppShell, {
      slots: { default: '<p>contenu</p>' },
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/x', loginUrl: null }, api: { doctypes: async () => [], workspaces: async () => [], preferences: async () => prefs } } },
      },
    })
    await flushPromises()
    return w
  }

  it('montre le nom et le logo de l’organisation à la place de « Desk »', async () => {
    const w = await shell({ organization_name: 'ACME', logo: { path: 'files/acme/x?exp=1&sig=2', filename: 'l.png', content_type: 'image/png', size: 1, expires: 9e9 } })
    expect(w.find('[data-testid="brand-name"]').text()).toBe('ACME')
    expect(w.find('[data-testid="brand-logo"]').attributes('src')).toBe('/app/x/files/acme/x?exp=1&sig=2')
    w.unmount()
  })

  it('sans réglage : « Desk » et l’icône d’origine', async () => {
    const w = await shell({})
    expect(w.find('[data-testid="brand-name"]').text()).toBe('Desk')
    expect(w.find('[data-testid="brand-logo"]').exists()).toBe(false)
    w.unmount()
  })

  it('un logo qui ne charge pas est redemandé une fois (lien expiré), puis abandonné pour l’icône', async () => {
    const preferences = vi.fn(async () => ({ organization_name: 'ACME', logo: { path: 'files/acme/x?exp=1&sig=2', filename: 'l.png', content_type: 'image/png', size: 1, expires: 9e9 } }))
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'home', component: { render: () => null } }, { path: '/tasks', name: 'tasks', component: { render: () => null } }, { path: '/settings/:doctype?', name: 'settings', component: { render: () => null } }] })
    await router.push('/')
    const w = mount(AppShell, {
      slots: { default: '<p>contenu</p>' },
      attachTo: document.body,
      global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/x', loginUrl: null }, api: { doctypes: async () => [], workspaces: async () => [], preferences } } } },
    })
    await flushPromises()
    expect(preferences).toHaveBeenCalledTimes(1)
    await w.find('[data-testid="brand-logo"]').trigger('error')
    await flushPromises()
    expect(preferences).toHaveBeenCalledTimes(2) // redemandé
    await w.find('[data-testid="brand-logo"]').trigger('error')
    await flushPromises()
    expect(w.find('[data-testid="brand-logo"]').exists()).toBe(false)
    expect(preferences).toHaveBeenCalledTimes(2) // pas de boucle
    w.unmount()
  })
})
