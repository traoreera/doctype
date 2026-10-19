import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import { ApiError } from '@/lib/api'
import { fieldStates } from '@/lib/form'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'
import { useMeta } from '@/stores/meta'
import { useProfile } from '@/stores/profile'
import ProfileView from '@/views/ProfileView.vue'

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  sessionStorage.setItem('xdt.desk.token', `${b64({ sub: 'u-alice', email: 'alice.martin@example.com', tenant_id: 'acme', exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`)
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })))
})
afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

const SUMMARIES = [{ name: 'User Profile', label: 'Profil', module: 'iam', create: true }]
const record = (data: Record<string, unknown>) => ({ doctype: 'User Profile', name: 'u-alice', owner: 'u-alice', creation: '', modified: '', version: 1, docstatus: 0, amended_from: null, data })

function store(api: Record<string, unknown>, summaries: unknown = SUMMARIES) {
  const pinia = createPinia()
  const app = createApp({})
  app.use(pinia)
  app.provide(SERVICES, { transport: {} as never, config: { baseUrl: '', loginUrl: null }, api: { doctypes: async () => summaries, ...api } as never })
  return app.runWithContext(async () => {
    const meta = useMeta(pinia)
    if (summaries !== null) await meta.loadSummaries()
    return { profile: useProfile(pinia), meta }
  })
}

describe('mon profil (stores/profile)', () => {
  it('disponible seulement si le DocType est offert à cet utilisateur ; inconnu tant que les types ne sont pas lus', async () => {
    expect((await store({}, [{ name: 'Invoice', label: 'F', module: 'x' }])).profile.available).toBe(false)
    expect((await store({})).profile.available).toBe(true)
    const pinia = createPinia()
    const app = createApp({})
    app.use(pinia).provide(SERVICES, { transport: {} as never, config: { baseUrl: '', loginUrl: null }, api: {} as never })
    expect(app.runWithContext(() => useProfile(pinia)).available).toBeNull()
  })

  it('lit MON profil (nommé comme mon compte) : nom, photo, fonction ; le nom vide ne compte pas', async () => {
    const get = vi.fn(async () => record({ full_name: 'Alice Martin', photo: 'f'.repeat(32), job_title: '  ' }))
    const { profile } = await store({ get })
    await profile.load()
    expect(get).toHaveBeenCalledWith('User Profile', 'u-alice')
    expect([profile.fullName, profile.photo, profile.jobTitle, profile.label]).toEqual(['Alice Martin', 'f'.repeat(32), null, 'Alice Martin'])
  })

  it('sans profil (404) on garde l’adresse ; une panne ne change rien', async () => {
    const calls: unknown[] = []
    const get = vi.fn(async () => {
      calls.push(1)
      if (calls.length === 1) return record({ full_name: 'Alice Martin' })
      if (calls.length === 2) throw new ApiError(500, 'http_500', 'hors ligne')
      throw new ApiError(404, 'document_not_found', 'introuvable')
    })
    const { profile } = await store({ get })
    await profile.load()
    await profile.load(true)
    expect(profile.fullName).toBe('Alice Martin') // une panne : on garde ce qu'on avait
    await profile.load(true)
    expect(profile.fullName).toBeNull()
    expect(profile.label).toBe('alice.martin@example.com') // l'adresse du jeton
  })

  it('ne demande rien si le DocType n’est pas offert', async () => {
    const get = vi.fn()
    const { profile } = await store({ get }, [])
    await profile.load()
    expect(get).not.toHaveBeenCalled()
  })

  it('exists() : oui, non (404), et une panne se propage', async () => {
    const answers: (() => unknown)[] = [() => record({}), () => { throw new ApiError(404, 'document_not_found', 'x') }, () => { throw new ApiError(500, 'http_500', 'x') }]
    const { profile } = await store({ get: async () => answers.shift()!() })
    expect(await profile.exists()).toBe(true)
    expect(await profile.exists()).toBe(false)
    await expect(profile.exists()).rejects.toThrow()
  })
})

async function shell(api: Record<string, unknown>) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/profile', name: 'profile', component: { render: () => null } },
      { path: '/tasks', name: 'tasks', component: { render: () => null } },
      { path: '/w/:name', name: 'workspace', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  await router.push('/')
  const w = mount(AppShell, {
    slots: { default: '<p>contenu</p>' },
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { doctypes: async () => SUMMARIES, workspaces: async () => [], ...api } } },
    },
  })
  await flushPromises()
  return { w, router }
}

describe('la barre du haut', () => {
  it('montre MON nom et MA photo, et propose « Mon profil »', async () => {
    const { w, router } = await shell({ get: async () => record({ full_name: 'Alice Martin', photo: '' }) })
    const avatar = w.find('[data-testid="user-menu"]')
    expect(avatar.text()).toBe('AM')
    expect(avatar.attributes('title')).toBe('Alice Martin')
    await avatar.trigger('click')
    await flushPromises()
    const menu = document.body.textContent ?? ''
    expect(menu).toContain('Alice Martin')
    expect(menu).toContain('alice.martin@example.com') // l'adresse reste visible sous le nom
    const entry = [...document.querySelectorAll<HTMLElement>('.p-menu-item-link')].find((e) => e.textContent?.includes('Mon profil'))!
    entry.click()
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('profile')
  })

  it('une photo remplace les initiales', async () => {
    const { w } = await shell({ get: async () => record({ full_name: 'Alice Martin', photo: 'f'.repeat(32) }), fileLinks: async () => ({}) })
    expect(w.find('[data-testid="user-menu"] .desk-avatar, [data-testid="user-menu"] img, [data-testid="user-menu"] i').exists()).toBe(true)
    expect(w.find('[data-testid="user-menu"]').text()).not.toBe('AM')
  })

  it('sans le DocType, ni nom affiché ni « Mon profil » : l’adresse et ses initiales', async () => {
    const { w } = await shell({ doctypes: async () => [], get: vi.fn() })
    expect(w.find('[data-testid="user-menu"]').attributes('title')).toBe('alice.martin@example.com') // pas de nom affiché : l'adresse
    await w.find('[data-testid="user-menu"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).not.toContain('Mon profil')
  })
})

describe('« Mon profil » (ProfileView)', () => {
  async function go(get: () => Promise<unknown>) {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { render: () => null } },
        { path: '/profile', name: 'profile', component: ProfileView },
        { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
        { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
      ],
    })
    await router.push('/profile')
    mount({ template: '<RouterView />' }, { global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { get, doctypes: async () => SUMMARIES } } } } })
    await flushPromises()
    return router.currentRoute.value
  }

  it('mène à ma fiche quand elle existe', async () => {
    const route = await go(async () => record({}))
    expect([route.name, route.params.doctype, route.params.name]).toEqual(['form', 'User Profile', 'u-alice'])
  })

  it('mène à sa création quand elle n’existe pas encore', async () => {
    const route = await go(async () => {
      throw new ApiError(404, 'document_not_found', 'x')
    })
    expect([route.name, route.params.doctype]).toEqual(['new', 'User Profile'])
  })

  it('une panne renvoie à l’accueil au lieu d’un écran vide', async () => {
    expect((await go(async () => { throw new ApiError(500, 'http_500', 'hors ligne') })).name).toBe('home')
  })
})

describe('champ en lecture seule (read_only)', () => {
  const field = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })
  const META = { name: 'Member', fields: [field({ fieldname: 'email', fieldtype: 'Text', read_only: true }), field({ fieldname: 'note', fieldtype: 'Text' })], layout: [], list_columns: [], rights: {} } as unknown as DocTypeMeta

  it('est verrouillé pour un document existant comme pour un nouveau, jamais un autre champ', () => {
    const values = { email: 'a@b.fr', note: 'x' }
    expect(fieldStates(META, values, values).email!.readOnly).toBe(true)
    expect(fieldStates(META, values, null).email!.readOnly).toBe(true)
    expect(fieldStates(META, values, values).note!.readOnly).toBe(false)
  })
})
