/**
 * Deux onglets d'une même session (C3) : un onglet dupliqué copie le jeton de renouvellement, qui ne sert qu'UNE fois chez xauth. Ils se le
 * passent (`lib/tabsync`) au lieu de se déconnecter l'un l'autre. Le faux serveur refuse un jeton déjà servi, comme xauth.
 */
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { createApp } from 'vue'
import { SERVICES } from '@/lib/services'
import { browserTabSync, memoryTabHub, type TabSync } from '@/lib/tabsync'
import type { HttpRequest, Transport } from '@/lib/transport'
import { useSession } from '@/stores/session'

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const access = (n: number, ttl = 3600) => `${b64({ sub: 'alice', tenant_id: 't1', exp: Math.floor(Date.now() / 1000) + ttl, n })}.sig`

/** xauth en miniature : un jeton de renouvellement ne sert qu'une fois ; le réutiliser est refusé (401). */
function server(ttl = 3600) {
  const live = new Set<string>()
  const seen: string[] = []
  let n = 0
  const issue = () => {
    n += 1
    const refresh = `r${n}`
    live.add(refresh)
    return { access_token: access(n, ttl), refresh_token: refresh, tenant_id: 't1' }
  }
  const transport: Transport = {
    async request(req: HttpRequest) {
      if (req.url.endsWith('/login')) return { status: 200, json: issue() }
      const used = (req.body as { refresh_token?: string }).refresh_token ?? ''
      if (req.url.endsWith('/refresh')) {
        seen.push(used)
        if (!live.delete(used)) return { status: 401, json: { detail: 'refresh token already used' } }
        return { status: 200, json: issue() }
      }
      if (req.url.endsWith('/logout')) {
        live.delete(used)
        return { status: 200, json: {} }
      }
      return { status: 404, json: null }
    },
  }
  return { transport, seen, live }
}

function tab(transport: Transport, tabs: TabSync) {
  const app = createApp({})
  const pinia = createPinia()
  app.use(pinia)
  app.provide(SERVICES, { transport, config: { baseUrl: '/api', loginUrl: '/app/auth/login' }, api: {} as never, tabs })
  const session = app.runWithContext(() => useSession(pinia))
  session.start()
  return session
}

const settle = () => new Promise((r) => setTimeout(r, 0))

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

describe('deux onglets d’une même session', () => {
  it('l’onglet dupliqué adopte le jeton renouvelé par l’autre : personne n’est déconnecté, aucun jeton n’est rejoué', async () => {
    const srv = server()
    const hub = memoryTabHub()
    const a = tab(srv.transport, hub.connect())
    await a.login('alice@acme.test', 'pw')
    const b = tab(srv.transport, hub.connect()) // « Dupliquer l'onglet » : il relit la session de l'onglet d'origine
    expect(b.refreshToken).toBe('r1')

    expect(await a.refresh()).toBe('ok')
    await settle() // la diffusion arrive à B
    expect(b.refreshToken).toBe(a.refreshToken)
    expect(await b.refresh()).toBe('ok') // B renouvelle à partir du jeton à jour
    expect(await a.refresh()).toBe('ok') // …et A, à son tour, prend ce que B a obtenu
    expect(a.authenticated && b.authenticated).toBe(true)
    expect(new Set(srv.seen).size).toBe(srv.seen.length) // aucun jeton présenté deux fois
  })

  it('un onglet endormi (qui n’a rien entendu) relit le dernier couple partagé au lieu d’envoyer un jeton usé', async () => {
    const srv = server()
    const hub = memoryTabHub()
    const a = tab(srv.transport, hub.connect())
    await a.login('alice@acme.test', 'pw')
    const sleeping = hub.connect()
    sleeping.subscribe = () => () => {} // il ne reçoit aucune diffusion
    const b = tab(srv.transport, sleeping)
    await a.refresh()
    await a.refresh()
    expect(b.refreshToken).toBe('r1') // toujours l'ancien
    expect(await b.refresh()).toBe('ok')
    expect(b.refreshToken).toBe(a.refreshToken)
    expect(srv.seen).toEqual(['r1', 'r2']) // B n'a rien envoyé : le couple d'A vaut encore longtemps
  })

  it('deux renouvellements simultanés passent l’un après l’autre : un seul échange', async () => {
    const srv = server()
    const hub = memoryTabHub()
    const a = tab(srv.transport, hub.connect())
    await a.login('alice@acme.test', 'pw')
    const b = tab(srv.transport, hub.connect())
    const [ra, rb] = await Promise.all([a.refresh(), b.refresh()])
    expect([ra, rb]).toEqual(['ok', 'ok'])
    expect(srv.seen).toEqual(['r1'])
    expect(a.refreshToken).toBe(b.refreshToken)
  })

  it('un couple partagé lui-même près d’expirer : on renouvelle à partir de SON jeton, jamais du nôtre', async () => {
    const srv = server(30) // des jetons d'accès de 30 s : toujours « près d'expirer »
    const hub = memoryTabHub()
    const quiet = hub.connect()
    quiet.subscribe = () => () => {}
    const a = tab(srv.transport, hub.connect())
    await a.login('alice@acme.test', 'pw')
    const b = tab(srv.transport, quiet)
    await a.refresh() // r1 -> r2, partagé
    expect(await b.refresh()).toBe('ok') // B tient encore r1 (usé) : il présente r2
    expect(srv.seen).toEqual(['r1', 'r2'])
  })

  it('se déconnecter dans un onglet déconnecte les autres onglets de la session, et oublie le couple partagé', async () => {
    const srv = server()
    const hub = memoryTabHub()
    const a = tab(srv.transport, hub.connect())
    await a.login('alice@acme.test', 'pw')
    const b = tab(srv.transport, hub.connect())
    a.logout()
    await settle()
    expect(b.authenticated).toBe(false)
    expect(b.notice).toBe('Vous vous êtes déconnecté dans un autre onglet.')
    expect(hub.storage.size).toBe(0)
  })

  it('une autre connexion (autre lignée) n’est jamais touchée', async () => {
    const srv = server()
    const hub = memoryTabHub()
    const a = tab(srv.transport, hub.connect())
    await a.login('alice@acme.test', 'pw')
    sessionStorage.clear() // un onglet NEUF, pas dupliqué : sa propre connexion
    const c = tab(srv.transport, hub.connect())
    await c.login('alice@acme.test', 'pw')
    a.logout()
    await a.login('alice@acme.test', 'pw')
    await settle()
    expect(c.authenticated).toBe(true)
    await a.refresh()
    await settle()
    expect(c.refreshToken).toBe('r2') // le sien, pas celui d'A
  })
})

describe('browserTabSync', () => {
  it('garde le dernier couple d’une lignée dans localStorage, l’oublie avec l’âge, et fonctionne sans verrou ni canal', async () => {
    const sync = browserTabSync()
    sync.write('L1', { access: 'a', refresh: 'r', at: 1000 })
    sync.write('L2', { access: 'a2', refresh: 'r2', at: 5000 })
    expect(sync.read('L1')).toEqual({ access: 'a', refresh: 'r', at: 1000 })
    localStorage.setItem('xdt.desk.session.bad', 'pas du json')
    localStorage.setItem('autre.chose', 'garde-moi')
    sync.prune(2000, 6000)
    expect(sync.read('L1')).toBeNull()
    expect(sync.read('L2')).not.toBeNull()
    expect(localStorage.getItem('xdt.desk.session.bad')).toBeNull()
    expect(localStorage.getItem('autre.chose')).toBe('garde-moi')
    expect(await sync.withLock('x', async () => 42)).toBe(42)
    sync.remove('L2')
    expect(sync.read('L2')).toBeNull()
  })
})
