import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { SERVICES } from '@/lib/services'
import type { HttpRequest, Transport } from '@/lib/transport'
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

describe('Connexion par nom d’utilisateur', () => {
  it('le champ accepte un nom (il n’est plus de type e-mail, que le navigateur refuserait) et le dit', async () => {
    const { w } = mountView(LoginView, { status: 401, json: {} })
    await flushPromises()
    const field = w.find('#login-email')
    expect(field.attributes('type')).toBe('text')
    expect(field.attributes('autocomplete')).toBe('username')
    expect(w.find('label[for="login-email"]').text()).toBe('Nom d’utilisateur ou adresse e-mail')
  })

  it('un nom part en `username`, une adresse en `email`', async () => {
    const { w, sent } = mountView(LoginView, { status: 401, json: {} })
    await flushPromises()
    await w.find('#login-email').setValue('Alice.M')
    await w.find('#login-password').setValue('Secret-Passw0rd1')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(posts(sent, '/login')[0]!.body).toEqual({ username: 'Alice.M', password: 'Secret-Passw0rd1' })
    expect(w.find('[data-testid="login-error"]').text()).toBe('Identifiant ou mot de passe incorrect')
    await w.find('#login-email').setValue('alice@acme.test')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(posts(sent, '/login')[1]!.body).toEqual({ email: 'alice@acme.test', password: 'Secret-Passw0rd1' })
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
