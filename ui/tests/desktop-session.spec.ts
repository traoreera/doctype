/**
 * La session dans l'application de bureau (traoreera/doctype#73) : les jetons dans le COFFRE du processus (`desktop/vault.ts`), UNE
 * session pour toutes les fenêtres, un seul échange du jeton de renouvellement à la fois. Le processus est simulé par
 * `memoryVaultHub()` (même sémantique que `src-tauri/src/vault.rs`) ; le faux serveur refuse un jeton déjà servi, comme xauth.
 */
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'
import { desktopHost, OAUTH_DEEP_LINK, type DesktopShell } from '@/desktop/host'
import { LOCK_TTL_MS, memoryVaultHub, REFRESH_LOCK, Vault } from '@/desktop/vault'
import { SERVICES } from '@/lib/services'
import type { HttpRequest, Transport } from '@/lib/transport'
import { useSession } from '@/stores/session'

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const access = (n: number, ttl = 3600, tenant = 't1') => `${b64({ sub: 'alice', tenant_id: tenant, exp: Math.floor(Date.now() / 1000) + ttl, n })}.sig`
const LOGIN = 'https://erp.exemple.fr/app/auth/login'

/** xauth en miniature : un jeton de renouvellement ne sert qu'une fois ; le réutiliser est refusé (401). */
function server(ttl = 3600) {
  const live = new Set<string>()
  const refreshes: string[] = []
  const sent: HttpRequest[] = []
  let n = 0
  const issue = (tenant = 't1') => {
    n += 1
    const refresh = `r${n}`
    live.add(refresh)
    return { access_token: access(n, ttl, tenant), refresh_token: refresh, tenant_id: tenant }
  }
  const transport: Transport = {
    async request(req) {
      sent.push(req)
      if (req.url.endsWith('/login')) return { status: 200, json: issue() }
      if (req.url.includes('/oauth/')) return { status: 200, json: { auth_url: 'https://idp.exemple/authorize?state=s' } }
      const body = (req.body ?? {}) as { refresh_token?: string; tenant_id?: string }
      const used = body.refresh_token ?? ''
      if (req.url.endsWith('/refresh') || req.url.endsWith('/select-tenant')) {
        if (req.url.endsWith('/refresh')) refreshes.push(used)
        await new Promise((r) => setTimeout(r, 5)) // le réseau prend du temps : c'est là que deux fenêtres se croiseraient
        if (!live.delete(used)) return { status: 401, json: { detail: 'refresh token already used' } }
        return { status: 200, json: issue(body.tenant_id) }
      }
      if (req.url.endsWith('/logout')) {
        live.delete(used)
        return { status: 200, json: {} }
      }
      return { status: 404, json: null }
    },
  }
  return { transport, refreshes, live, sent }
}

const shell: DesktopShell = { openExternal: async () => {}, saveFile: async () => true, openWindow: async () => {} }

/** Une fenêtre de l'application : son propre Desk (pinia), le coffre commun. */
async function windowOf(hub: ReturnType<typeof memoryVaultHub>, transport: Transport, label: string) {
  const vault = await Vault.open(hub.backend(label), label)
  const host = desktopHost({ vault, shell, transport })
  const app = createApp({})
  const pinia = createPinia()
  app.use(pinia)
  app.provide(SERVICES, { transport, config: { baseUrl: 'https://erp.exemple.fr/app/xcore-doctype', loginUrl: LOGIN }, api: {} as never, host })
  const session = app.runWithContext(() => useSession(pinia))
  session.start()
  return { session, vault, host }
}

const settle = async (ms = 0) => {
  await new Promise((r) => setTimeout(r, ms))
  await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

describe('les jetons vivent dans le coffre du processus', () => {
  it('rien dans le stockage de la WebView : ni sessionStorage, ni localStorage', async () => {
    const hub = memoryVaultHub()
    const { transport } = server()
    const main = await windowOf(hub, transport, 'main')
    expect(await main.session.login('alice@acme.test', 'pw')).toEqual({ status: 'ok' })
    await settle()
    expect(hub.values.get('xdt.desk.token')).toBe(main.session.token)
    expect(hub.values.get('xdt.desk.refresh')).toBe('r1')
    expect(sessionStorage.length).toBe(0)
    expect(localStorage.length).toBe(0)
  })

  it('une fenêtre rechargée ou ouverte ensuite retrouve la session (le processus vit toujours)', async () => {
    const hub = memoryVaultHub()
    const { transport } = server()
    const main = await windowOf(hub, transport, 'main')
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    const second = await windowOf(hub, transport, 'desk-2')
    expect(second.session.authenticated).toBe(true)
    expect([second.session.token, second.session.refreshToken]).toEqual([main.session.token, 'r1'])
  })

  it('quitter l’application termine la session : un processus neuf démarre sans jeton', async () => {
    const { transport } = server()
    const before = await windowOf(memoryVaultHub(), transport, 'main')
    await before.session.login('alice@acme.test', 'pw')
    await settle()
    const after = await windowOf(memoryVaultHub(), transport, 'main')
    expect(after.session.authenticated).toBe(false)
    expect(after.session.token).toBeNull()
  })
})

describe('plusieurs fenêtres, UNE session', () => {
  async function two(ttl = 3600) {
    const hub = memoryVaultHub()
    const srv = server(ttl)
    const main = await windowOf(hub, srv.transport, 'main')
    const second = await windowOf(hub, srv.transport, 'desk-2')
    return { hub, srv, main, second }
  }

  it('une connexion dans une fenêtre ouvre la session des autres', async () => {
    const { main, second } = await two()
    expect(second.session.authenticated).toBe(false)
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    expect(second.session.authenticated).toBe(true)
    expect(second.session.token).toBe(main.session.token)
    expect(second.session.refreshToken).toBe('r1')
  })

  it('deux fenêtres qui renouvellent EN MÊME TEMPS : un seul échange part, les deux gardent la session', async () => {
    const { srv, main, second } = await two()
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    const [a, b] = await Promise.all([main.session.refresh(), second.session.refresh()])
    await settle()
    expect([a, b]).toEqual(['ok', 'ok'])
    expect(srv.refreshes).toEqual(['r1']) // le jeton à usage unique n'a servi qu'une fois
    expect(main.session.refreshToken).toBe('r2')
    expect(second.session.refreshToken).toBe('r2')
    expect(second.session.token).toBe(main.session.token)
  })

  it('un renouvellement fait ailleurs est adopté sans attendre : la fenêtre n’enverra pas un jeton usé plus tard', async () => {
    const { srv, main, second } = await two()
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    expect(await main.session.refresh()).toBe('ok')
    await settle()
    expect(second.session.refreshToken).toBe('r2')
    expect(await second.session.refresh()).toBe('ok')
    expect(srv.refreshes).toEqual(['r1', 'r2'])
  })

  it('même SANS l’annonce (événement perdu), la relecture sous le verrou évite d’envoyer un jeton déjà servi', async () => {
    const hub = memoryVaultHub()
    const srv = server()
    const main = await windowOf(hub, srv.transport, 'main')
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    // Une fenêtre sourde : son abonnement aux annonces du processus ne livre rien.
    const deaf = hub.backend('desk-2')
    const vault = await Vault.open({ ...deaf, listen: async () => () => {} }, 'desk-2')
    const app = createApp({})
    const pinia = createPinia()
    app.use(pinia)
    app.provide(SERVICES, { transport: srv.transport, config: { baseUrl: 'https://erp.exemple.fr/app/xcore-doctype', loginUrl: LOGIN }, api: {} as never, host: desktopHost({ vault, shell, transport: srv.transport }) })
    const second = app.runWithContext(() => useSession(pinia))
    second.start()
    expect(await main.session.refresh()).toBe('ok')
    await settle()
    expect(second.refreshToken).toBe('r1') // elle n'a rien su
    expect(await second.refresh()).toBe('ok')
    expect(second.refreshToken).toBe('r2')
    expect(srv.refreshes).toEqual(['r1']) // et pourtant elle n'a pas rejoué r1
  })

  it('changer d’organisation dans une fenêtre change la session de toutes', async () => {
    const { main, second } = await two()
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    expect(await main.session.switchTenant('t2')).toEqual({ status: 'ok' })
    await settle()
    expect(main.session.tenant).toBe('t2')
    expect(second.session.tenant).toBe('t2')
    expect(await second.session.refresh()).toBe('ok') // elle tient le jeton de la NOUVELLE session, pas celui que l'échange a usé
  })

  it('se déconnecter dans une fenêtre déconnecte les autres, qui disent pourquoi', async () => {
    const { hub, main, second } = await two()
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    main.session.logout()
    await settle()
    expect(second.session.authenticated).toBe(false)
    expect(second.session.refreshToken).toBeNull()
    expect(second.session.notice).toBeTruthy()
    expect(main.session.notice).toBeNull() // celle qui s'est déconnectée n'a rien à s'expliquer
    expect([...hub.values.keys()]).toEqual([])
  })

  it('une fenêtre fermée en plein échange ne bloque pas les autres : son verrou a une échéance', async () => {
    let now = 1_000
    const hub = memoryVaultHub(() => now)
    const srv = server()
    const main = await windowOf(hub, srv.transport, 'main')
    await main.session.login('alice@acme.test', 'pw')
    await settle()
    expect(await hub.backend('fantome').lock(REFRESH_LOCK, 'fantome', LOCK_TTL_MS)).toBe(true)
    let done = false
    const waiting = main.host.tabs().withLock('le nom que le magasin de session donne', async () => (done = true))
    await settle(100)
    expect(done).toBe(false) // tenu par la fenêtre fantôme
    now += LOCK_TTL_MS + 1
    await waiting
    expect(done).toBe(true)
    expect(hub.locks.size).toBe(0)
  })
})

describe('OAuth par lien profond', () => {
  it('le fournisseur renvoie vers `erp://oauth-callback`, avec un nonce gardé dans la mémoire de LA fenêtre', async () => {
    const hub = memoryVaultHub()
    const { transport, sent } = server()
    const main = await windowOf(hub, transport, 'main')
    expect(OAUTH_DEEP_LINK).toBe('erp://oauth-callback')
    expect(await main.session.oauthAuthorizeUrl('google')).toBe('https://idp.exemple/authorize?state=s')
    const asked = new URL(sent.at(-1)!.url)
    expect(asked.pathname).toBe('/app/auth/oauth/google/authorize')
    expect(asked.searchParams.get('redirect')).toBe('erp://oauth-callback')
    const nonce = asked.searchParams.get('app_state')!
    expect(nonce).toMatch(/^[0-9a-f]{32}$/)
    expect(main.session.oauthPending()).toBe(true)
    await settle()
    expect([...hub.values.values()]).not.toContain(nonce) // ni dans le coffre partagé,
    expect(sessionStorage.length + localStorage.length).toBe(0) // ni dans la WebView
  })

  it('le retour au bon nonce ouvre la session — une seule fois', async () => {
    const { transport, sent } = server()
    const main = await windowOf(memoryVaultHub(), transport, 'main')
    await main.session.oauthAuthorizeUrl('google')
    const nonce = new URL(sent.at(-1)!.url).searchParams.get('app_state')!
    const back = { access_token: access(7), refresh_token: 'r-oauth', app_state: nonce }
    expect(main.session.completeOAuth(back)).toEqual({ status: 'ok' })
    expect(main.session.authenticated).toBe(true)
    expect(main.session.oauthPending()).toBe(false)
    expect(main.session.completeOAuth(back)).toMatchObject({ status: 'error' }) // rejoué : le nonce a servi
  })

  it.each([
    ['un nonce étranger', (nonce: string) => ({ app_state: `${nonce}x` })],
    ['aucun nonce rendu', () => ({})],
    ['un nonce vide', () => ({ app_state: '' })],
  ])('refuse un lien forgé : %s', async (_what, forged) => {
    const { transport, sent } = server()
    const main = await windowOf(memoryVaultHub(), transport, 'main')
    await main.session.oauthAuthorizeUrl('google')
    const nonce = new URL(sent.at(-1)!.url).searchParams.get('app_state')!
    expect(main.session.completeOAuth({ access_token: access(9), refresh_token: 'attaquant', ...forged(nonce) })).toMatchObject({ status: 'error' })
    expect(main.session.authenticated).toBe(false)
    expect(main.session.token).toBeNull()
  })

  it('refuse un lien profond quand AUCUNE connexion n’est en cours — même s’il porte un nonce', async () => {
    const { transport } = server()
    const main = await windowOf(memoryVaultHub(), transport, 'main')
    expect(main.session.oauthPending()).toBe(false)
    expect(main.session.completeOAuth({ access_token: access(9), refresh_token: 'attaquant', app_state: 'abc' })).toMatchObject({ status: 'error' })
    expect(main.session.token).toBeNull()
  })

  it('le lien arrive à toutes les fenêtres : seule celle qui a ouvert le fournisseur attend un retour', async () => {
    const hub = memoryVaultHub()
    const { transport, sent } = server()
    const main = await windowOf(hub, transport, 'main')
    const second = await windowOf(hub, transport, 'desk-2')
    await main.session.oauthAuthorizeUrl('google')
    const nonce = new URL(sent.at(-1)!.url).searchParams.get('app_state')!
    expect(second.session.oauthPending()).toBe(false)
    expect(second.session.completeOAuth({ access_token: access(7), refresh_token: 'r-oauth', app_state: nonce })).toMatchObject({ status: 'error' })
    expect(main.session.completeOAuth({ access_token: access(7), refresh_token: 'r-oauth', app_state: nonce })).toEqual({ status: 'ok' })
    await settle()
    expect(second.session.authenticated).toBe(true) // par le coffre, comme toute connexion
  })

  it('double authentification et choix d’organisation au retour : rien n’est adopté avant la dernière étape', async () => {
    const { transport, sent } = server()
    const main = await windowOf(memoryVaultHub(), transport, 'main')
    await main.session.oauthAuthorizeUrl('google')
    let nonce = new URL(sent.at(-1)!.url).searchParams.get('app_state')!
    expect(main.session.completeOAuth({ mfa_required: 'true', mfa_token: 'défi', app_state: nonce, access_token: '', refresh_token: '' })).toEqual({ status: 'mfa' })
    expect(main.session.token).toBeNull()
    expect(main.session.mfaToken).toBe('défi')
    await main.session.oauthAuthorizeUrl('google')
    nonce = new URL(sent.at(-1)!.url).searchParams.get('app_state')!
    const tenants = btoa(JSON.stringify([{ id: 'a', name: 'Acme' }, { id: 'b', name: 'Beta' }]))
    expect(main.session.completeOAuth({ tenants, refresh_token: 'r0', access_token: '', app_state: nonce })).toMatchObject({ status: 'tenant' })
    expect(main.session.token).toBeNull()
  })
})
