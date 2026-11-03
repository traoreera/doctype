import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError, normalizeError } from '@/lib/api'
import { describeError } from '@/composables/useNotify'
import { SERVICES } from '@/lib/services'
import { moduleAction, type MarketModule, type MyModules } from '@/lib/xlicense'
import ModulesView from '@/views/ModulesView.vue'

const asked = vi.hoisted(() => [] as { accept?: () => unknown }[])
vi.mock('primevue/useconfirm', async (original) => ({
  ...(await original<object>()),
  useConfirm: () => ({ require: (o: { accept?: () => unknown }) => asked.push(o) }),
}))

const mod = (o: Partial<MarketModule> & Pick<MarketModule, 'code'>): MarketModule => ({
  label: o.code, description: null, free: true, price: 0, currency: 'EUR', period: 'month', active: true, sellable: true,
  taken: false, source: null, expires_at: null, usable: false, ...o,
})

describe('moduleAction : ce que l’écran propose', () => {
  it('rien à qui ne gère pas l’organisation', () => {
    expect(moduleAction(mod({ code: 'crm' }), false)).toBeNull()
  })
  it('gratuit : prendre puis rendre ; payant : s’abonner puis gérer au portail', () => {
    expect(moduleAction(mod({ code: 'crm' }), true)).toBe('take')
    expect(moduleAction(mod({ code: 'crm', taken: true, source: 'free' }), true)).toBe('drop')
    expect(moduleAction(mod({ code: 'pos', free: false, price: 20 }), true)).toBe('subscribe')
    expect(moduleAction(mod({ code: 'pos', free: false, taken: true, source: 'paid' }), true)).toBe('portal')
  })
  it('un payant sans tarif, un module retiré du catalogue, un module résilié en fin de période : aucun geste', () => {
    expect(moduleAction(mod({ code: 'pos', free: false, sellable: false }), true)).toBeNull()
    expect(moduleAction(mod({ code: 'old', active: false }), true)).toBeNull()
    expect(moduleAction(mod({ code: 'x', taken: true, source: 'granted', expires_at: '2026-12-01T00:00:00+00:00' }), true)).toBeNull()
  })
})

describe('les refus de la licence se lisent', () => {
  it('le contrôle d’xlicense (sans « detail ») garde son code et son message', () => {
    const e = normalizeError(403, { error: 'module_not_in_plan', module: 'crm', message: 'Le module « crm » n’est pas inclus.' })
    expect(e.isModuleNotLicensed).toBe(true)
    expect(describeError(e)).toBe('Le module « crm » n’est pas inclus.') // pas un « interdit » générique : il dit quoi faire
    const lic = normalizeError(402, { error: 'license_required', state: 'expired', message: 'La licence a expiré.' })
    expect(lic.isPaymentRequired && lic.code === 'license_required').toBe(true)
    expect(describeError(lic)).toBe('La licence a expiré.')
  })
})

describe('la page « Mes modules »', () => {
  const mounted: { unmount: () => void }[] = []
  afterEach(() => {
    mounted.splice(0).forEach((w) => w.unmount())
    asked.length = 0
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  async function open(data: MyModules, extra: Record<string, unknown> = {}) {
    const license = { modules: vi.fn(async () => data), take: vi.fn(async () => ({})), drop: vi.fn(async () => ({})), checkout: vi.fn(async () => ({ url: 'https://pay.example/s/1' })), portal: vi.fn(async () => 'https://pay.example/portal'), ...extra }
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ModulesView }] })
    await router.push('/')
    const w = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {}, license } } } })
    mounted.push(w)
    await flushPromises()
    return { w, license }
  }

  const base: MyModules = { state: 'active', valid: true, trial: false, all_modules: false, can_manage: true, modules: [] }

  it('un gratuit se prend d’un clic ; un payant envoie à la caisse ouverte par le SERVEUR', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign, reload: vi.fn() })
    const { w, license } = await open({ ...base, modules: [mod({ code: 'crm', label: 'CRM' }), mod({ code: 'pos', label: 'Caisse', free: false, price: 20, currency: 'XOF' })] })
    expect(w.find('[data-module="pos"] [data-testid="module-price"]').text()).toContain('/ mois')
    await w.find('[data-module="crm"] [data-testid="module-take"]').trigger('click')
    await flushPromises()
    expect(license.take).toHaveBeenCalledWith('crm')
    await w.find('[data-module="pos"] [data-testid="module-subscribe"]').trigger('click')
    await flushPromises()
    expect(license.checkout).toHaveBeenCalledWith('pos')
    expect(assign).toHaveBeenCalledWith('https://pay.example/s/1')
    vi.unstubAllGlobals()
  })

  it('rendre demande confirmation ; un module payé se gère au portail', async () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign, reload: vi.fn() })
    const { w, license } = await open({ ...base, modules: [mod({ code: 'crm', taken: true, source: 'free', usable: true }), mod({ code: 'pos', free: false, taken: true, source: 'paid', usable: true })] })
    await w.find('[data-module="crm"] [data-testid="module-drop"]').trigger('click')
    expect(license.drop).not.toHaveBeenCalled() // pas sans confirmation
    await asked[0]!.accept!()
    await flushPromises()
    expect(license.drop).toHaveBeenCalledWith('crm')
    await w.find('[data-module="pos"] [data-testid="module-portal"]').trigger('click')
    await flushPromises()
    expect(assign).toHaveBeenCalledWith('https://pay.example/portal')
    vi.unstubAllGlobals()
  })

  it('un simple membre lit sans bouton ; l’essai et une licence bloquée sont dits', async () => {
    const { w } = await open({ ...base, can_manage: false, trial: true, modules: [mod({ code: 'crm', usable: true })] })
    expect(w.find('[data-testid="modules-readonly"]').exists()).toBe(true)
    expect(w.find('[data-testid="modules-trial"]').exists()).toBe(true)
    expect(w.find('[data-module="crm"] button').exists()).toBe(false)
    expect(w.find('[data-testid="module-status"]').text()).toBe('Ouvert (essai)')
    const blocked = await open({ ...base, valid: false, state: 'expired' })
    expect(blocked.w.find('[data-testid="modules-blocked"]').exists()).toBe(true)
  })

  it('sans xlicense, la page le dit au lieu d’échouer', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ModulesView }] })
    await router.push('/')
    const w = mount({ template: '<RouterView />' }, { global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {}, license: null } } } })
    mounted.push(w)
    await flushPromises()
    expect(w.find('[data-testid="modules-failed"]').text()).toContain('pas installée')
  })

  it('un refus du serveur devient une notification, la page reste', async () => {
    const take = vi.fn(async () => {
      throw new ApiError(403, 'http_403', 'Réservé aux administrateurs de l’organisation')
    })
    const { w } = await open({ ...base, modules: [mod({ code: 'crm' })] }, { take })
    await w.find('[data-testid="module-take"]').trigger('click')
    await flushPromises()
    expect(take).toHaveBeenCalled()
    expect(w.find('[data-module="crm"]').exists()).toBe(true)
  })
})
