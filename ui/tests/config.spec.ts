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
    expect(cfg).toEqual({ baseUrl: '/app/xcore-doctype', loginUrl: '/app/auth/login' })
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
