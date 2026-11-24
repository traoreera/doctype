/**
 * La configuration de l'application de bureau (`desktop/config.ts`) : une adresse de serveur validée, le reste LU chez ce serveur.
 * Chaque refus nomme sa cause — au démarrage d'une application, il n'y a personne d'autre pour le dire.
 */
import { describe, expect, it } from 'vitest'
import { allowedOrigins, ConfigRefusal, disabledByConfig, loadServerConfig, readDesktopConfig, serverAddress } from '@/desktop/config'
import { NetworkError, type HttpRequest, type HttpResponse, type Transport } from '@/lib/transport'

const SERVER = 'https://erp.exemple.fr/app/xcore-doctype/desk/'

function serving(pages: Record<string, HttpResponse | Error>): { transport: Transport; sent: HttpRequest[] } {
  const sent: HttpRequest[] = []
  return {
    sent,
    transport: {
      async request(req) {
        sent.push(req)
        const page = pages[req.url] ?? { status: 404, json: null }
        if (page instanceof Error) throw page
        return page
      },
    },
  }
}

describe('desk.json', () => {
  it('une adresse suffit ; les extensions sont FERMÉES par défaut, le temps réel ouvert', () => {
    expect(readDesktopConfig({ server: SERVER })).toEqual({ server: SERVER, extensions: false, realtime: true })
    expect(readDesktopConfig(JSON.stringify({ server: SERVER, extensions: true, realtime: false }))).toEqual({ server: SERVER, extensions: true, realtime: false })
  })

  it('la barre finale est ajoutée : `config.json` se lit À CÔTÉ du Desk, pas à la place de son dernier segment', () => {
    expect(readDesktopConfig({ server: 'https://erp.exemple.fr/app/xcore-doctype/desk' }).server).toBe(SERVER)
  })

  it('http en clair seulement vers la machine elle-même', () => {
    expect(readDesktopConfig({ server: 'http://127.0.0.1:8000/app/xcore-doctype/desk/' }).server).toBe('http://127.0.0.1:8000/app/xcore-doctype/desk/')
    expect(readDesktopConfig({ server: 'http://localhost:8000/desk/' }).server).toBe('http://localhost:8000/desk/')
    expect(() => readDesktopConfig({ server: 'http://erp.exemple.fr/desk/' })).toThrow(/https obligatoire/)
    expect(() => readDesktopConfig({ server: 'http://127.0.0.1.evil.example/desk/' })).toThrow(ConfigRefusal)
  })

  it.each([
    ['pas du JSON', '{server:', /n'est pas du JSON/],
    ['pas un objet', '[]', /doit être un objet/],
    ['adresse absente', {}, /« server » manque/],
    ['adresse relative', { server: '/app/xcore-doctype/desk/' }, /n'est pas une adresse absolue/],
    ['un autre schéma', { server: 'file:///srv/desk/' }, /https obligatoire/],
    ['des identifiants dans l’adresse', { server: 'https://admin:secret@erp.exemple.fr/desk/' }, /ne porte pas d'identifiants/],
    ['une requête dans l’adresse', { server: 'https://erp.exemple.fr/desk/?x=1' }, /ni « \? » ni « # »/],
    ['une clé inconnue (faute de frappe)', { server: SERVER, extension: true }, /clé inconnue « extension »/],
    ['un interrupteur qui n’est pas un booléen', { server: SERVER, realtime: 'non' }, /« realtime » vaut true ou false/],
  ])('refuse : %s', (_what, raw, cause) => {
    expect(() => readDesktopConfig(raw)).toThrow(ConfigRefusal)
    expect(() => readDesktopConfig(raw)).toThrow(cause)
  })

  it('ce que la configuration retire est NOMMÉ, pour être annoncé', () => {
    expect(disabledByConfig({ server: SERVER, extensions: true, realtime: true })).toEqual({})
    const off = disabledByConfig({ server: SERVER, extensions: false, realtime: false })
    expect(off.extensions).toContain('desk.json : extensions')
    expect(off.realtime).toContain('desk.json : realtime')
  })
})

describe('la configuration du Desk, lue chez le serveur', () => {
  const desktop = readDesktopConfig({ server: SERVER })

  it('lit le `config.json` du plugin et rend ABSOLUES l’API et la connexion (la page de l’application n’a pas d’origine http)', async () => {
    const { transport, sent } = serving({
      [`${SERVER}config.json`]: {
        status: 200,
        json: { api_base: '/app/xcore-doctype', login_url: '/app/auth/login', extensions: [{ name: 'sales', url: 'ext/sales/index.js?v=1' }], developer: true, realtime: true, automations: true },
      },
    })
    expect(await loadServerConfig(transport, desktop)).toEqual({
      baseUrl: 'https://erp.exemple.fr/app/xcore-doctype',
      loginUrl: 'https://erp.exemple.fr/app/auth/login',
      extensions: [{ name: 'sales', url: `${SERVER}ext/sales/index.js?v=1` }],
      developer: true,
      realtime: true,
      automations: true,
    })
    expect(sent.map((r) => r.url)).toEqual([`${SERVER}config.json`])
  })

  it('un serveur sans connexion configurée reste sans connexion (jeton collé), rien n’est inventé', async () => {
    const { transport } = serving({ [`${SERVER}config.json`]: { status: 200, json: { api_base: '/app/xcore-doctype' } } })
    const config = await loadServerConfig(transport, desktop)
    expect(config.loginUrl).toBeNull()
    expect([config.realtime, config.automations, config.developer, config.extensions]).toEqual([false, false, false, []])
  })

  it('un Desk MONTÉ ailleurs : suit le renvoi vers le `config.json` du plugin, comme le Desk web', async () => {
    const mounted = readDesktopConfig({ server: 'https://erp.exemple.fr/erp/' })
    const { transport } = serving({
      'https://erp.exemple.fr/erp/config.json': { status: 200, json: { api_base: '/app/xcore-doctype', login_url: '/app/auth/login', extensions_from: '/app/xcore-doctype/desk/config.json' } },
      [`${SERVER}config.json`]: { status: 200, json: { api_base: '/app/xcore-doctype', realtime: true, extensions: [{ name: 'crm', url: 'ext/crm/index.js' }] } },
    })
    const config = await loadServerConfig(transport, mounted)
    expect(config.realtime).toBe(true)
    expect(config.extensions).toEqual([{ name: 'crm', url: `${SERVER}ext/crm/index.js` }])
  })

  it('serveur injoignable : le démarrage s’arrête en nommant l’adresse — pas de repli sur une URL devinée, contrairement au navigateur', async () => {
    const { transport } = serving({ [`${SERVER}config.json`]: new NetworkError('Le serveur est injoignable') })
    const failure = (await loadServerConfig(transport, desktop).catch((e: unknown) => e)) as Error
    expect(failure).toBeInstanceOf(ConfigRefusal)
    expect(failure.message).toContain(`${SERVER}config.json`)
    expect(failure.message).toContain('injoignable')
  })

  it.each([
    ['404', { status: 404, json: null }],
    ['une page HTML', { status: 200, json: null }],
    ['du JSON sans api_base', { status: 200, json: { login_url: '/x/login' } }],
  ])('une adresse qui ne sert pas un Desk (%s) est refusée', async (_what, page) => {
    const { transport } = serving({ [`${SERVER}config.json`]: page })
    await expect(loadServerConfig(transport, desktop)).rejects.toThrow(/ne décrit pas un Desk/)
  })

  it('une API ou une connexion annoncée en http clair ailleurs que sur la machine est refusée (les jetons y voyageraient lisibles)', async () => {
    const api = serving({ [`${SERVER}config.json`]: { status: 200, json: { api_base: 'http://api.exemple.fr/app/xcore-doctype' } } })
    await expect(loadServerConfig(api.transport, desktop)).rejects.toThrow(/api_base annoncé par le serveur/)
    const login = serving({ [`${SERVER}config.json`]: { status: 200, json: { api_base: '/app/xcore-doctype', login_url: 'http://sso.exemple.fr/login' } } })
    await expect(loadServerConfig(login.transport, desktop)).rejects.toThrow(/login_url annoncé par le serveur/)
  })

  it('une faute de programmation n’est pas maquillée en refus de configuration', async () => {
    const transport: Transport = {
      async request() {
        throw new TypeError('x is undefined')
      },
    }
    await expect(loadServerConfig(transport, desktop)).rejects.toBeInstanceOf(TypeError)
  })

  it('les origines ouvertes au transport : le serveur, puis l’API et la connexion qu’il annonce', () => {
    expect(allowedOrigins(desktop)).toEqual(['https://erp.exemple.fr'])
    expect(allowedOrigins(desktop, { baseUrl: 'https://api.exemple.fr/app/xcore-doctype', loginUrl: 'https://sso.exemple.fr/app/auth/login' })).toEqual([
      'https://erp.exemple.fr',
      'https://api.exemple.fr',
      'https://sso.exemple.fr',
    ])
    expect(allowedOrigins(desktop, { baseUrl: 'https://erp.exemple.fr/app/xcore-doctype', loginUrl: null })).toEqual(['https://erp.exemple.fr'])
  })

  it('`serverAddress` nomme ce qu’il juge', () => {
    expect(() => serverAddress('ftp://x/', 'login_url')).toThrow(/^login_url : /)
  })
})
