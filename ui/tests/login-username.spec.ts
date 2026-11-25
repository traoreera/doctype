import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { SERVICES } from '@/lib/services'
import type { HttpRequest, Transport } from '@/lib/transport'
import { t } from '@/i18n'
import LoginView from '@/views/LoginView.vue'
import RegisterView from '@/views/RegisterView.vue'

afterEach(() => {
  document.body.innerHTML = ''
})
beforeEach(() => sessionStorage.clear())

function mountView(component: unknown, reply: { status: number; json: unknown }) {
  const sent: HttpRequest[] = []
  const transport: Transport = {
    async request(req) {
      sent.push(req)
      return req.url.endsWith('/oauth/providers') ? { status: 200, json: { providers: [] } } : reply
    },
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'home', component: { render: () => null } }, { path: '/login', name: 'login', component: { render: () => null } }, { path: '/register', name: 'register', component: { render: () => null } }, { path: '/forgot', name: 'forgot', component: { render: () => null } }] })
  const w = mount(component as never, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport, config: { baseUrl: '/app/xcore-doctype', loginUrl: '/app/auth/login' }, api: {}, auth: null } } },
  })
  return { w, sent }
}
const posts = (sent: HttpRequest[], suffix: string) => sent.filter((r) => r.method === 'POST' && r.url.endsWith(suffix))

describe('Connexion par identifiant', () => {
  it('demande un identifiant libre : nom d’utilisateur OU adresse', async () => {
    // Le champ n'est PAS un `type="email"` : depuis la connexion par nom d'utilisateur, il accepte les deux formes et c'est
    // `session.login` qui envoie `{username}` ou `{email}` selon la présence d'un `@` (process/xauth.md § « Se connecter »).
    const { w } = mountView(LoginView, { status: 401, json: {} })
    await flushPromises()
    const field = w.find('#login-email')
    expect(field.attributes('type')).toBe('text')
    expect(field.attributes('autocomplete')).toBe('username')
    expect(w.find('label[for="login-email"]').text()).toBe(t.login.identifier)
  })

  it('envoie l’adresse et le mot de passe à xauth', async () => {
    const { w, sent } = mountView(LoginView, { status: 401, json: {} })
    await flushPromises()
    await w.find('#login-email').setValue('alice@acme.test')
    await w.find('#login-password').setValue('Secret-Passw0rd1')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(posts(sent, '/login')[0]!.body).toEqual({ email: 'alice@acme.test', password: 'Secret-Passw0rd1' })
    expect(w.find('[data-testid="login-error"]').text()).toBe('Identifiant ou mot de passe incorrect')
  })
})

describe('Inscription avec un nom d’utilisateur', () => {
  it('le nom est facultatif : vide, il ne part pas ; donné, il part', async () => {
    const { w, sent } = mountView(RegisterView, { status: 201, json: {} })
    await flushPromises()
    expect(w.find('label[for="register-username"]').text()).toContain('facultatif')
    await w.find('#register-email').setValue('rita@acme.test')
    await w.find('#register-password').setValue('Secret-Passw0rd1')
    await w.find('#register-confirm').setValue('Secret-Passw0rd1')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(posts(sent, '/register')[0]!.body).toEqual({ email: 'rita@acme.test', password: 'Secret-Passw0rd1' })

    const named = mountView(RegisterView, { status: 201, json: {} })
    await flushPromises()
    await named.w.find('#register-email').setValue('rita@acme.test')
    await named.w.find('[data-testid="register-username"]').setValue('rita.m')
    await named.w.find('#register-password').setValue('Secret-Passw0rd1')
    await named.w.find('#register-confirm').setValue('Secret-Passw0rd1')
    await named.w.find('form').trigger('submit')
    await flushPromises()
    expect(posts(named.sent, '/register')[0]!.body).toEqual({ email: 'rita@acme.test', password: 'Secret-Passw0rd1', username: 'rita.m' })
  })

  it('un refus du serveur (nom pris) s’affiche', async () => {
    const { w } = mountView(RegisterView, { status: 400, json: { detail: 'Ce nom d’utilisateur est déjà pris' } })
    await flushPromises()
    await w.find('#register-email').setValue('rita@acme.test')
    await w.find('[data-testid="register-username"]').setValue('rita')
    await w.find('#register-password').setValue('Secret-Passw0rd1')
    await w.find('#register-confirm').setValue('Secret-Passw0rd1')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="register-error"]').text()).toContain('déjà pris')
  })
})

describe('Une invitée sans organisation, arrivée par le lien d’invitation', () => {
  const jwt = (claims: Record<string, unknown>) =>
    `x.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, ...claims })).replace(/=+$/, '')}.y`

  async function arrive(next: string | null) {
    const sent: HttpRequest[] = []
    const transport: Transport = {
      async request(req) {
        sent.push(req)
        if (req.url.endsWith('/oauth/providers')) return { status: 200, json: { providers: [] } }
        // Comme le vrai xauth : un jeton d'accès SANS organisation, et `onboarding_required`.
        if (req.url.endsWith('/login')) return { status: 200, json: { access_token: jwt({ sub: 'awa' }), refresh_token: 'R1', onboarding_required: true } }
        if (req.url.endsWith('/setup/join')) {
          return { status: 200, json: { access_token: jwt({ sub: 'awa', tenant_id: 'org-1' }), refresh_token: 'R2' } }
        }
        return { status: 404, json: {} }
      },
    }
    const blank = { render: () => null }
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', name: 'home', component: blank }, { path: '/login', name: 'login', component: blank },
      { path: '/onboarding', name: 'onboarding', component: blank }, { path: '/invite', name: 'invite', component: blank },
      { path: '/register', name: 'register', component: blank }, { path: '/forgot', name: 'forgot', component: blank }] })
    await router.push(next ? { name: 'login', query: { next } } : { name: 'login' })
    const w = mount(LoginView as never, {
      attachTo: document.body,
      global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport, config: { baseUrl: '/app/xcore-doctype', loginUrl: '/app/auth/login' }, api: {}, auth: null } } },
    })
    await flushPromises()
    await w.find('#login-email').setValue('awa@acme.test')
    await w.find('#login-password').setValue('Secret-Passw0rd1')
    await w.find('form').trigger('submit')
    await flushPromises()
    return { sent, router }
  }

  it('rejoint l’organisation de l’invitation au lieu d’atterrir sur « Créer une organisation »', async () => {
    const { sent, router } = await arrive('/invite?token=31ba29fc-3224-487b-bc8b-9833598ae311')
    expect(posts(sent, '/setup/join')[0]!.body).toEqual({ refresh_token: 'R1', invite_token: '31ba29fc-3224-487b-bc8b-9833598ae311' })
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('sans invitation, l’écran de création reste le chemin', async () => {
    const { sent, router } = await arrive(null)
    expect(posts(sent, '/setup/join')).toEqual([])
    expect(router.currentRoute.value.name).toBe('onboarding')
  })
})
