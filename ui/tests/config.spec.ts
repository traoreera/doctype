import { afterEach, describe, expect, it } from 'vitest'
import { loadConfig } from '@/lib/config'
import type { HttpRequest, HttpResponse, Transport } from '@/lib/transport'

const transport = (res: HttpResponse | Error): Transport & { urls: string[] } => {
  const urls: string[] = []
  return {
    urls,
    async request(req: HttpRequest) {
      urls.push(req.url)
      if (res instanceof Error) throw res
      return res
    },
  }
}
const at = (pathname: string) => ({ pathname, href: `http://host:8000${pathname}` })

afterEach(() => {
  delete window.__DESK_CONFIG__
})

describe('configuration', () => {
  it('lit config.json à côté de la page, relativement', async () => {
    const t = transport({ status: 200, json: { api_base: '/app/xcore-doctype/', login_url: '/app/auth/login' } })
    const cfg = await loadConfig(t, at('/app/xcore-doctype/desk/'))
    expect(t.urls).toEqual(['http://host:8000/app/xcore-doctype/desk/config.json'])
    expect(cfg).toEqual({ baseUrl: '/app/xcore-doctype', loginUrl: '/app/auth/login', extensions: [], developer: false, realtime: false })
  })

  it('le mode développeur n’est ouvert que si le serveur dit exactement `true`', async () => {
    const open = await loadConfig(transport({ status: 200, json: { api_base: '/p', developer: true } }), at('/p/desk/'))
    expect(open.developer).toBe(true)
    for (const developer of ['true', 1, null, undefined]) {
      const cfg = await loadConfig(transport({ status: 200, json: { api_base: '/p', developer } }), at('/p/desk/'))
      expect(cfg.developer).toBe(false)
    }
  })

  it('le temps réel n’existe que si le serveur l’annonce exactement `true` (sinon aucun flux n’est demandé)', async () => {
    expect((await loadConfig(transport({ status: 200, json: { api_base: '/p', realtime: true } }), at('/p/desk/'))).realtime).toBe(true)
    for (const realtime of ['true', 1, null, undefined]) {
      expect((await loadConfig(transport({ status: 200, json: { api_base: '/p', realtime } }), at('/p/desk/'))).realtime).toBe(false)
    }
  })

  it('le Desk ne crée son client temps réel que sur annonce', async () => {
    const { createDesk } = await import('@/desk')
    const { SERVICES } = await import('@/lib/services')
    const services = (realtime: boolean) => {
      const desk = createDesk({ request: async () => ({ status: 200, json: null }) }, { baseUrl: '/app/xcore-doctype', loginUrl: null, realtime })
      return desk.app._context.provides[SERVICES as symbol] as { realtime: unknown }
    }
    expect(services(false).realtime).toBeNull()
    expect(services(true).realtime).not.toBeNull()
  })

  it('sans login_url : jeton collé', async () => {
    const cfg = await loadConfig(transport({ status: 200, json: { api_base: '/p', login_url: null } }), at('/p/desk/'))
    expect(cfg.loginUrl).toBeNull()
  })

  it('config.json absent : déduit de l’URL de la page', async () => {
    const cfg = await loadConfig(transport({ status: 404, json: null }), at('/app/xcore-doctype/desk/'))
    expect(cfg.baseUrl).toBe('/app/xcore-doctype')
  })

  it('serveur injoignable : déduit de l’URL, et en développement retombe sur le préfixe habituel', async () => {
    const down = transport(new Error('net'))
    expect((await loadConfig(down, at('/x/desk/'))).baseUrl).toBe('/x')
    expect((await loadConfig(down, at('/'), { dev: true })).baseUrl).toBe('/app/xcore-doctype')
  })

  it('un hôte (application desktop) impose sa configuration sans réseau', async () => {
    window.__DESK_CONFIG__ = { baseUrl: 'https://erp.exemple.fr/app/xcore-doctype/', loginUrl: 'https://erp.exemple.fr/app/auth/login' }
    const t = transport({ status: 500, json: null })
    const cfg = await loadConfig(t, at('/'))
    expect(t.urls).toEqual([])
    expect(cfg.baseUrl).toBe('https://erp.exemple.fr/app/xcore-doctype')
  })

  it('refuse une réponse mal formée', async () => {
    const cfg = await loadConfig(transport({ status: 200, json: { api_base: 5 } }), at('/p/desk/'))
    expect(cfg.baseUrl).toBe('/p')
  })
})
