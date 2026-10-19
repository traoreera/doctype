import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'
import { SERVICES } from '@/lib/services'
import { NetworkError, type HttpRequest, type HttpResponse, type Transport } from '@/lib/transport'
import { createDeskRouter, safeNext } from '@/router'
import { useSession } from '@/stores/session'

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const token = (claims: Record<string, unknown>) => `${b64(claims)}.sig`
const future = () => Math.floor(Date.now() / 1000) + 3600

function setup(reply: HttpResponse | Error = { status: 200, json: {} }, loginUrl: string | null = '/app/auth/login') {
  const sent: HttpRequest[] = []
  const transport: Transport = {
    async request(req) {
      sent.push(req)
      if (reply instanceof Error) throw reply
      return reply
    },
  }
  const app = createApp({})
  const pinia = createPinia()
  app.use(pinia)
  app.provide(SERVICES, { transport, config: { baseUrl: '/api', loginUrl }, api: {} as never })
  const session = app.runWithContext(() => useSession(pinia))
  return { session, sent, pinia }
}

beforeEach(() => sessionStorage.clear())

describe('connexion par e-mail et mot de passe', () => {
  it('un nom d’utilisateur part en `username`, une adresse (qui contient « @ ») en `email` ; espaces ôtés, casse laissée au serveur', async () => {
    const reply = { status: 200, json: { access_token: token({ sub: 'u', tenant_id: 't1', exp: future() }), refresh_token: 'r', tenant_id: 't1' } }
    const byName = setup(reply)
    await byName.session.login('  Alice.M ', 'pw')
    expect(byName.sent[0]!.body).toEqual({ username: 'Alice.M', password: 'pw' })
    const byMail = setup(reply)
    await byMail.session.login(' alice@acme.test ', 'pw', 't1')
    expect(byMail.sent[0]!.body).toEqual({ email: 'alice@acme.test', password: 'pw', tenant_id: 't1' })
  })

  it('succès : le jeton est gardé dans l’onglet, le mot de passe n’est envoyé qu’à login_url', async () => {
    const { session, sent } = setup({ status: 200, json: { access_token: 'abc', tenant_id: 't1' } })
    expect(await session.login('a@b.fr', 'pw')).toEqual({ status: 'ok' })
    expect(session.token).toBe('abc')
    expect(sessionStorage.getItem('xdt.desk.token')).toBe('abc')
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/login', body: { email: 'a@b.fr', password: 'pw' } })
    expect(sent[0]!.body).not.toHaveProperty('tenant_id')
  })

  it('plusieurs organisations : choisit via /select-tenant (la session est déjà ouverte), pas un second login', async () => {
    const reply = { status: 200, json: { access_token: '', refresh_token: 'r0', tenant_id: null, tenants: [{ id: 'a', name: 'Acme' }, { id: 'b', slug: 'beta' }, { name: 'sans id' }] } }
    const { session, sent } = scripted([reply, { status: 200, json: { access_token: token({ sub: 'a', tenant_id: 'b', exp: future() }), refresh_token: 'r1', tenant_id: 'b' } }])
    const out = await session.login('a@b.fr', 'pw')
    expect(out).toEqual({ status: 'tenant', tenants: [{ id: 'a', name: 'Acme' }, { id: 'b', name: 'beta' }] })
    expect(session.token).toBeNull()
    expect(session.refreshToken).toBeNull() // rien n'est adopté tant que l'organisation n'est pas choisie
    expect(await session.selectTenant('b')).toEqual({ status: 'ok' })
    expect(sent[1]).toMatchObject({ method: 'POST', url: '/app/auth/select-tenant', body: { refresh_token: 'r0', tenant_id: 'b' } })
    expect(session.token).not.toBeNull()
    expect(session.tenant).toBe('b')
    expect(session.refreshToken).toBe('r1')
  })

  it('double authentification : le défi est retenu, puis le code échange les jetons définitifs', async () => {
    const { session, sent } = scripted([
      { status: 200, json: { mfa_required: true, mfa_token: 'chal', access_token: '', tenant_id: 't1' } },
      { status: 200, json: { access_token: token({ sub: 'u', tenant_id: 't1', exp: future() }), refresh_token: 'r1', tenant_id: 't1' } },
    ])
    expect(await session.login('a@b.fr', 'pw')).toEqual({ status: 'mfa' })
    expect(session.token).toBeNull()
    expect(await session.verifyMfa('123456')).toEqual({ status: 'ok' })
    expect(sent[1]).toMatchObject({ method: 'POST', url: '/app/auth/auth/verify-mfa', body: { mfa_token: 'chal', code: '123456' } })
    expect(session.tenant).toBe('t1')
  })

  it('compte sans organisation : les jetons sont gardés, l’application propose de créer ou rejoindre', async () => {
    const { session } = setup({ status: 200, json: { access_token: token({ sub: 'u', exp: future() }), refresh_token: 'r0', onboarding_required: true } })
    expect(await session.login('a@b.fr', 'pw')).toEqual({ status: 'onboarding' })
    expect(session.token).not.toBeNull()
    expect(session.refreshToken).toBe('r0')
  })

  it.each([
    ['double authentification sans défi', { mfa_required: true, access_token: 'x' }, /double authentification/],
    ['réponse sans jeton', { tenant_id: 't1' }, /refusé/],
  ])('%s : refusé, aucun jeton conservé', async (_n, json, message) => {
    const { session } = setup({ status: 200, json })
    const out = await session.login('a@b.fr', 'pw')
    expect(out).toMatchObject({ status: 'error', message: expect.stringMatching(message) })
    expect(session.token).toBeNull()
    expect(sessionStorage.getItem('xdt.desk.token')).toBeNull()
  })

  it.each([
    [401, /incorrect/],
    [400, /incorrect/],
    [422, /incorrect/],
    [429, /Trop de tentatives/],
    [500, /refusé/],
  ])('statut %i', async (status, message) => {
    const { session } = setup({ status, json: null })
    expect(await session.login('a@b.fr', 'pw')).toMatchObject({ status: 'error', message: expect.stringMatching(message) })
  })

  it('serveur injoignable', async () => {
    const { session } = setup(new NetworkError('x'))
    expect(await session.login('a@b.fr', 'pw')).toMatchObject({ status: 'error', message: expect.stringMatching(/injoignable/) })
  })

  it('sans login_url, aucune requête n’est envoyée (jamais le mot de passe vers une adresse devinée)', async () => {
    const { session, sent } = setup({ status: 200, json: { access_token: 'abc' } }, null)
    expect(session.canPasswordLogin).toBe(false)
    expect(await session.login('a@b.fr', 'pw')).toMatchObject({ status: 'error' })
    expect(sent).toEqual([])
  })
})

describe('jeton collé et session', () => {
  it('accepte un jeton lisible et non expiré, retient le tenant et le nom', () => {
    const { session } = setup()
    expect(session.useToken(`  ${token({ sub: 'u1', tenant_id: 'acme', user: { email: 'a@b.fr' }, exp: future() })}  `)).toBe(true)
    expect(session.authenticated).toBe(true)
    expect(session.tenant).toBe('acme')
    expect(session.user).toBe('a@b.fr')
  })

  it.each([['du texte', 'pas-un-jeton'], ['un jeton expiré', token({ sub: 'u', exp: 1 })], ['vide', '  ']])('refuse %s', (_n, raw) => {
    const { session } = setup()
    expect(session.useToken(raw)).toBe(false)
    expect(session.token).toBeNull()
  })

  it('un jeton expiré en mémoire ne compte pas comme connecté', () => {
    sessionStorage.setItem('xdt.desk.token', token({ sub: 'u', exp: 1 }))
    expect(setup().session.authenticated).toBe(false)
  })

  it('le jeton survit à un rechargement de l’onglet (sessionStorage), pas à la déconnexion', () => {
    sessionStorage.setItem('xdt.desk.token', token({ sub: 'u', exp: future() }))
    const { session } = setup()
    expect(session.authenticated).toBe(true)
    session.logout()
    expect(session.token).toBeNull()
    expect(sessionStorage.getItem('xdt.desk.token')).toBeNull()
  })

  it('un 401 déconnecte et laisse un message pour l’écran de connexion', () => {
    const { session } = setup()
    session.useToken(token({ sub: 'u', exp: future() }))
    session.expire()
    expect(session.authenticated).toBe(false)
    expect(session.notice).toMatch(/session a expiré/)
    session.expire() // sans jeton : sans effet, pas de message écrasé
    expect(session.notice).toMatch(/session a expiré/)
  })
})

describe('redirection après connexion', () => {
  it.each([
    ['/app/Customer', '/app/Customer'],
    ['/app/Customer/abc', '/app/Customer/abc'],
    ['//evil.example', '/'],
    ['https://evil.example', '/'],
    ['/\\evil.example', '/'],
    ['javascript:alert(1)', '/'],
    ['', '/'],
    [undefined, '/'],
    [['/a'], '/'],
  ])('%j -> %s', (next, expected) => {
    expect(safeNext(next)).toBe(expected)
  })
})

describe('garde du routeur', () => {
  it('sans session, une page protégée renvoie à la connexion en retenant la destination', async () => {
    const { pinia } = setup()
    const router = createDeskRouter(pinia)
    await router.push('/app/Customer')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.next).toBe('/app/Customer')
  })

  it('connecté, l’écran de connexion renvoie à l’accueil', async () => {
    const { session, pinia } = setup()
    session.useToken(token({ sub: 'u', exp: future() }))
    const router = createDeskRouter(pinia)
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('un jeton expiré est jeté en chemin', async () => {
    sessionStorage.setItem('xdt.desk.token', token({ sub: 'u', exp: 1 }))
    const { session, pinia } = setup()
    const router = createDeskRouter(pinia)
    await router.push('/app/Customer')
    expect(router.currentRoute.value.name).toBe('login')
    expect(session.token).toBeNull()
  })
})

// ── Renouvellement de la session (xauth : /refresh, jeton de renouvellement à usage unique) ─────────────────────
import { afterEach, vi } from 'vitest'
import { authUrl } from '@/lib/config'

/** Un transport qui répond dans l'ordre et se souvient de ce qu'on lui a envoyé. */
function scripted(replies: (HttpResponse | Error)[]) {
  const sent: HttpRequest[] = []
  const queue = [...replies]
  const transport: Transport = {
    async request(req) {
      sent.push(req)
      const next = queue.shift() ?? { status: 200, json: {} }
      if (next instanceof Error) throw next
      return next
    },
  }
  const app = createApp({})
  const pinia = createPinia()
  app.use(pinia)
  app.provide(SERVICES, { transport, config: { baseUrl: '/api', loginUrl: '/app/auth/login' }, api: {} as never })
  return { session: app.runWithContext(() => useSession(pinia)), sent }
}

async function loggedIn(replies: (HttpResponse | Error)[] = [], exp = future()) {
  const ctx = scripted([{ status: 200, json: { access_token: token({ sub: 'a', exp }), refresh_token: 'r1', tenant_id: 't' } }, ...replies])
  await ctx.session.login('a@b.fr', 'pw')
  ctx.sent.length = 0 // on ne regarde que ce qui suit la connexion
  return ctx
}

afterEach(() => vi.useRealTimers())

describe('adresses d’xauth', () => {
  it('le renouvellement et la déconnexion se déduisent de l’adresse de connexion', () => {
    expect(authUrl('/app/auth/login', 'refresh')).toBe('/app/auth/refresh')
    expect(authUrl('https://auth.example.com/app/auth/login', 'logout')).toBe('https://auth.example.com/app/auth/logout')
    expect(authUrl('/connexion', 'refresh')).toBeNull() // pas de forme reconnue : rien de deviné
    expect(authUrl(null, 'refresh')).toBeNull()
  })
})

describe('renouvellement', () => {
  it('la connexion garde le jeton de renouvellement, dans l’onglet', async () => {
    const { session } = await loggedIn()
    expect(session.refreshToken).toBe('r1')
    expect(sessionStorage.getItem('xdt.desk.refresh')).toBe('r1')
  })

  it('un renouvellement réussi remplace LES DEUX jetons (celui de renouvellement est à usage unique)', async () => {
    const fresh = token({ sub: 'a', exp: future() + 100 })
    const { session, sent } = await loggedIn([{ status: 200, json: { access_token: fresh, refresh_token: 'r2' } }])
    expect(await session.refresh()).toBe('ok')
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/refresh', body: { refresh_token: 'r1' } })
    expect([session.token, session.refreshToken]).toEqual([fresh, 'r2'])
    expect(sessionStorage.getItem('xdt.desk.refresh')).toBe('r2')
  })

  it('deux demandes simultanées n’en font qu’une : un jeton à usage unique ne se présente pas deux fois', async () => {
    const { session, sent } = await loggedIn([{ status: 200, json: { access_token: token({ sub: 'a', exp: future() }), refresh_token: 'r2' } }])
    const [a, b] = await Promise.all([session.refresh(), session.refresh()])
    expect([a, b]).toEqual(['ok', 'ok'])
    expect(sent).toHaveLength(1)
  })

  it('refusé par le serveur : « rejected », la session reste à l’appelant (qui la fermera)', async () => {
    const { session } = await loggedIn([{ status: 401, json: { detail: 'Invalid or expired refresh token' } }])
    expect(await session.refresh()).toBe('rejected')
  })

  it('serveur injoignable ou malade : « offline », rien n’est perdu', async () => {
    const { session } = await loggedIn([new NetworkError('x'), { status: 503, json: null }])
    expect(await session.refresh()).toBe('offline')
    expect(await session.refresh()).toBe('offline')
    expect(session.token).not.toBeNull() // une panne passagère ne déconnecte personne
  })

  it('sans jeton de renouvellement (jeton de développement) : « none », aucune requête', async () => {
    const { session, sent } = setup()
    expect(session.useToken(token({ sub: 'a', exp: future() }))).toBe(true)
    expect(await session.refresh()).toBe('none')
    expect(sent).toHaveLength(0)
  })

  it('une réponse sans jeton d’accès n’est pas un succès', async () => {
    const { session } = await loggedIn([{ status: 200, json: { nothing: true } }])
    expect(await session.refresh()).toBe('rejected')
  })
})

describe('calendrier', () => {
  it('le jeton est renouvelé UNE minute avant son expiration, puis le calendrier repart du nouveau jeton', async () => {
    vi.useFakeTimers()
    const now = Date.now()
    const exp = Math.floor(now / 1000) + 300 // expire dans 5 min
    const next = token({ sub: 'a', exp: Math.floor(now / 1000) + 1800 })
    const { session, sent } = await loggedIn([{ status: 200, json: { access_token: next, refresh_token: 'r2' } }], exp)
    const stop = session.start()
    await vi.advanceTimersByTimeAsync(239_000)
    expect(sent).toHaveLength(0) // pas avant : 300 s - 60 s = 240 s
    await vi.advanceTimersByTimeAsync(2_000)
    expect(sent).toHaveLength(1)
    expect(session.token).toBe(next)
    stop()
  })

  it('un renouvellement refusé met fin à la session avec son message', async () => {
    vi.useFakeTimers()
    const exp = Math.floor(Date.now() / 1000) + 120
    const { session } = await loggedIn([{ status: 401, json: null }], exp)
    session.start()
    await vi.advanceTimersByTimeAsync(61_000)
    expect(session.token).toBeNull()
    expect(session.notice).toMatch(/expiré/)
  })

  it('hors ligne, on réessaie plus tard au lieu de déconnecter', async () => {
    vi.useFakeTimers()
    const now = Date.now()
    const exp = Math.floor(now / 1000) + 90
    const next = token({ sub: 'a', exp: Math.floor(now / 1000) + 3600 })
    const { session, sent } = await loggedIn([new NetworkError('x'), { status: 200, json: { access_token: next, refresh_token: 'r2' } }], exp)
    session.start()
    await vi.advanceTimersByTimeAsync(31_000)
    expect(sent).toHaveLength(1)
    expect(session.token).not.toBeNull()
    await vi.advanceTimersByTimeAsync(31_000)
    expect(sent).toHaveLength(2)
    expect(session.token).toBe(next)
  })

  it('un jeton non renouvelable fait prévenir DEUX minutes avant son expiration', async () => {
    vi.useFakeTimers()
    const { session } = setup()
    session.useToken(token({ sub: 'a', exp: Math.floor(Date.now() / 1000) + 300 }))
    session.start()
    await vi.advanceTimersByTimeAsync(170_000)
    expect(session.expiringSoon).toBe(false)
    await vi.advanceTimersByTimeAsync(11_000)
    expect(session.expiringSoon).toBe(true)
  })
})

describe('déconnexion', () => {
  it('voulue : la session est aussi révoquée côté serveur', async () => {
    const { session, sent } = await loggedIn([{ status: 204, json: null }])
    session.logout()
    await Promise.resolve()
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/logout', body: { refresh_token: 'r1' } })
    expect([session.token, session.refreshToken]).toEqual([null, null])
    expect(sessionStorage.getItem('xdt.desk.refresh')).toBeNull()
  })

  it('même si le serveur ne répond pas, on se déconnecte', async () => {
    const { session } = await loggedIn([new NetworkError('x')])
    expect(() => session.logout()).not.toThrow()
    expect(session.token).toBeNull()
  })

  it('forcée (jeton expiré) : rien à révoquer, aucune requête', async () => {
    const { session, sent } = await loggedIn()
    session.expire()
    expect(sent).toHaveLength(0)
    expect(session.notice).toMatch(/expiré/)
  })
})

describe('inscription, mot de passe, invitations, OAuth, organisations', () => {
  it('inscription avec un nom d’utilisateur : il part rogné ; vide, il ne part pas', async () => {
    const named = setup({ status: 201, json: {} })
    await named.session.register('a@b.fr', 'pw', '  rita.m ')
    expect(named.sent[0]!.body).toEqual({ email: 'a@b.fr', password: 'pw', username: 'rita.m' })
    const blank = setup({ status: 201, json: {} })
    await blank.session.register('a@b.fr', 'pw', '   ')
    expect(blank.sent[0]!.body).toEqual({ email: 'a@b.fr', password: 'pw' })
  })

  it('inscription : POST /register, aucun jeton adopté', async () => {
    const { session, sent } = setup({ status: 201, json: { id: 'u' } })
    expect(await session.register('a@b.fr', 'pw')).toEqual({ status: 'ok' })
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/register', body: { email: 'a@b.fr', password: 'pw' } })
    expect(sent[0]!.headers.Authorization).toBeUndefined()
    expect(session.token).toBeNull()
  })

  it('inscription refusée : le detail du serveur est montré', async () => {
    const { session } = setup({ status: 400, json: { detail: 'Email already registered' } })
    expect(await session.register('a@b.fr', 'pw')).toEqual({ status: 'error', message: 'Email already registered' })
  })

  it('mot de passe oublié : POST /password/forgot, réponse neutre', async () => {
    const { session, sent } = setup({ status: 202, json: null })
    expect(await session.requestPasswordReset('a@b.fr')).toEqual({ status: 'ok' })
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/password/forgot', body: { email: 'a@b.fr' } })
  })

  it('réinitialisation : le jeton du lien part dans le corps', async () => {
    const { session, sent } = setup({ status: 200, json: {} })
    expect(await session.resetPassword('tok', 'Nouveau-1')).toEqual({ status: 'ok' })
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/password/reset', body: { token: 'tok', new_password: 'Nouveau-1' } })
  })

  it('invitation : se lit sans session, s’accepte avec le jeton de la session', async () => {
    const { session, sent } = scripted([
      { status: 200, json: { email: 'a@b.fr', tenant_name: 'Acme' } },
      { status: 200, json: {} },
    ])
    expect(await session.fetchInvite('inv 1')).toEqual({ email: 'a@b.fr', tenantName: 'Acme' })
    expect(sent[0]).toMatchObject({ method: 'GET', url: '/app/auth/invites/token/inv%201' })
    expect(sent[0]!.headers.Authorization).toBeUndefined()
    session.useToken(token({ sub: 'u', exp: future() }))
    expect(await session.acceptInvite('inv 1')).toEqual({ status: 'ok' })
    expect(sent[1]).toMatchObject({ method: 'POST', url: '/app/auth/invites/accept', body: { token: 'inv 1' } })
    expect(sent[1]!.headers.Authorization).toBe(`Bearer ${session.token}`)
  })

  it('OAuth : l’URL d’autorisation revient sur le Desk et retient un nonce', async () => {
    const { session, sent } = setup({ status: 200, json: { auth_url: 'https://github.com/login/oauth/authorize?x=1' } })
    expect(await session.oauthAuthorizeUrl('github')).toBe('https://github.com/login/oauth/authorize?x=1')
    const url = new URL(String(sent[0]!.url), 'http://localhost')
    expect(url.pathname).toBe('/app/auth/oauth/github/authorize')
    expect(url.searchParams.get('redirect')).toBe(`${globalThis.location.origin}/api/desk/#/oauth`)
    expect(url.searchParams.get('app_state')).toMatch(/^[0-9a-f]{32}$/)
    expect(sessionStorage.getItem('xdt.desk.oauth')).toBe(url.searchParams.get('app_state'))
  })

  it('OAuth : un nonce étranger est refusé, un retour au bon nonce ouvre la session', async () => {
    const { session } = scripted([
      { status: 200, json: { auth_url: 'https://x' } },
      { status: 200, json: { auth_url: 'https://x' } },
    ])
    expect(await session.oauthAuthorizeUrl('github')).toBe('https://x')
    const good = sessionStorage.getItem('xdt.desk.oauth')!
    expect(session.completeOAuth({ access_token: 'forged', app_state: 'autre' })).toMatchObject({ status: 'error' })
    sessionStorage.setItem('xdt.desk.oauth', good)
    expect(session.completeOAuth({ app_state: good, access_token: 'abc', refresh_token: 'r1' })).toEqual({ status: 'ok' })
    expect([session.token, session.refreshToken]).toEqual(['abc', 'r1'])
    expect(sessionStorage.getItem('xdt.desk.oauth')).toBeNull()
  })

  it('OAuth : plusieurs organisations → le choix est demandé, sans adopter de session', () => {
    const { session } = setup()
    const tenants = btoa(JSON.stringify([{ id: 'a', name: 'Acme' }, { id: 'b', slug: 'beta' }, { name: 'sans id' }]))
    expect(session.completeOAuth({ tenants, refresh_token: 'r0', access_token: '' })).toEqual({
      status: 'tenant',
      tenants: [{ id: 'a', name: 'Acme' }, { id: 'b', name: 'beta' }],
    })
    expect(session.token).toBeNull()
    expect(session.refreshToken).toBeNull()
  })

  it('changement d’organisation : échange le jeton de renouvellement courant', async () => {
    const { session, sent } = await loggedIn([
      { status: 200, json: { access_token: token({ sub: 'a', tenant_id: 'b', exp: future() }), refresh_token: 'r2' } },
    ])
    expect(await session.switchTenant('b')).toEqual({ status: 'ok' })
    expect(sent[0]).toMatchObject({ method: 'POST', url: '/app/auth/select-tenant', body: { refresh_token: 'r1', tenant_id: 'b' } })
    expect(session.tenant).toBe('b')
  })

  it('annuaire des organisations : les entrées sans identifiant sont écartées', async () => {
    const { session, sent } = await loggedIn([
      { status: 200, json: [{ id: 'a', name: 'Acme' }, { id: 'b', slug: 'beta' }, { name: 'sans id' }] },
    ])
    await session.loadTenants()
    expect(sent[0]).toMatchObject({ method: 'GET', url: '/app/auth/tenants/' })
    expect(session.tenants).toEqual([{ id: 'a', name: 'Acme' }, { id: 'b', name: 'beta' }])
  })

  it('mes permissions se lisent chez xauth (le jeton ne les porte pas) ; `admin:*` fait l’administrateur de la plateforme', async () => {
    const { session, sent } = await loggedIn([{ status: 200, json: ['sales:invoice:read', 7, 'admin:*'] }])
    expect(session.permissions).toBeNull()
    expect(session.platformAdmin).toBe(false)
    await session.loadPermissions()
    expect(sent[0]).toMatchObject({ method: 'GET', url: '/app/auth/rbac/me/permissions' })
    expect(session.permissions).toEqual(['sales:invoice:read', 'admin:*'])
    expect(session.platformAdmin).toBe(true)
  })

  it('une lecture refusée ne change rien : pas d’administrateur par défaut', async () => {
    const { session } = await loggedIn([{ status: 500, json: { detail: 'oups' } }])
    await session.loadPermissions()
    expect(session.permissions).toBeNull()
    expect(session.platformAdmin).toBe(false)
  })
})
