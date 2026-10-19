import { describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiError, normalizeError, type ClientContext } from '../src/lib/api'
import type { HttpRequest, HttpResponse, Transport } from '../src/lib/transport'

function setup(responses: HttpResponse[], ctx: Partial<ClientContext> = {}) {
  const requests: HttpRequest[] = []
  const transport: Transport = {
    async request(req) {
      requests.push(req)
      return responses.shift() ?? { status: 200, json: {} }
    },
  }
  const onUnauthorized = vi.fn()
  const context: ClientContext = { baseUrl: '/app/xcore-doctype', token: 'tok', tenant: 'acme', ...ctx }
  return { api: new ApiClient(transport, () => context, onUnauthorized), requests, onUnauthorized }
}

describe('requêtes', () => {
  it('envoie le jeton et le tenant sur chaque appel, et rien d’autre qu’il ne faut', async () => {
    const { api, requests } = setup([{ status: 200, json: [] }])
    await api.doctypes()
    expect(requests[0]).toMatchObject({
      method: 'GET',
      url: '/app/xcore-doctype/meta',
      headers: { Authorization: 'Bearer tok', 'X-Tenant-ID': 'acme', Accept: 'application/json' },
    })
    expect(requests[0]!.headers['Content-Type']).toBeUndefined()
    expect(requests[0]!.body).toBeUndefined()
  })

  it('omet les en-têtes quand il n’y a ni jeton ni tenant', async () => {
    const { api, requests } = setup([{ status: 200, json: [] }], { token: null, tenant: null })
    await api.doctypes()
    expect(Object.keys(requests[0]!.headers)).toEqual(['Accept'])
  })

  it('encode les noms de DocType et de document (espaces, barres, accents)', async () => {
    const { api, requests } = setup([{ status: 200, json: {} }, { status: 200, json: {} }, { status: 204, json: null }])
    await api.meta('Sales Invoice')
    await api.get('Sales Invoice', 'a/b?c#d é')
    await api.remove('Customer', '../x')
    expect(requests.map((r) => r.url)).toEqual([
      '/app/xcore-doctype/meta/Sales%20Invoice',
      '/app/xcore-doctype/doc/Sales%20Invoice/a%2Fb%3Fc%23d%20%C3%A9',
      '/app/xcore-doctype/doc/Customer/..%2Fx',
    ])
  })

  it('compose la liste : filtres en JSON, tri, pagination', async () => {
    const { api, requests } = setup([{ status: 200, json: { items: [], total: 0, limit: 20, offset: 40 } }])
    await api.list('Customer', {
      filters: { type: 'Entreprise', credit_limit: ['>=', 100] },
      order_by: 'credit_limit',
      order: 'desc',
      limit: 20,
      offset: 40,
    })
    const url = new URL(requests[0]!.url, 'http://x')
    expect(url.pathname).toBe('/app/xcore-doctype/list/Customer')
    expect(JSON.parse(url.searchParams.get('filters')!)).toEqual({ type: 'Entreprise', credit_limit: ['>=', 100] })
    expect(Object.fromEntries([...url.searchParams].filter(([k]) => k !== 'filters'))).toEqual({
      order_by: 'credit_limit',
      order: 'desc',
      limit: '20',
      offset: '40',
    })
  })

  it('n’envoie pas de paramètre vide', async () => {
    const { api, requests } = setup([{ status: 200, json: {} }])
    await api.list('Customer', { filters: {} })
    expect(requests[0]!.url).toBe('/app/xcore-doctype/list/Customer')
  })

  it('crée et met à jour avec le corps attendu par le serveur', async () => {
    const { api, requests } = setup([{ status: 201, json: {} }, { status: 200, json: {} }])
    await api.create('Customer', { customer_name: 'A' })
    await api.update('Customer', 'n1', 3, { customer_name: 'B' })
    expect(requests[0]).toMatchObject({ method: 'POST', body: { data: { customer_name: 'A' } } })
    expect(requests[1]).toMatchObject({ method: 'PUT', url: '/app/xcore-doctype/doc/Customer/n1', body: { version: 3, data: { customer_name: 'B' } } })
    expect(requests[1]!.headers['Content-Type']).toBe('application/json')
  })

  it('cherche pour un champ Link', async () => {
    const { api, requests } = setup([{ status: 200, json: { items: [{ name: 'n', title: 'T' }] } }])
    expect(await api.search('Country', 'fra & co', 5)).toEqual([{ name: 'n', title: 'T' }])
    expect(requests[0]!.url).toBe('/app/xcore-doctype/search/Country?q=fra+%26+co&limit=5')
  })
})

describe('erreurs', () => {
  it.each([
    [{ detail: { code: 'validation_failed', msg: 'validation_failed', details: [{ field: 'a', msg: 'Champ obligatoire', type: 'missing' }] } }, 422, 'validation_failed', 'a'],
    [{ detail: { code: 'permission_denied', msg: 'Accès refusé' } }, 403, 'permission_denied', undefined],
    [{ detail: { code: 'version_conflict', msg: 'modifié' } }, 409, 'version_conflict', undefined],
  ])('forme xcore-doctype %#', (body, status, code, field) => {
    const e = normalizeError(status, body)
    expect(e).toBeInstanceOf(ApiError)
    expect([e.status, e.code, e.details[0]?.field]).toEqual([status, code, field])
  })

  it('forme d’authentification : détail en texte', () => {
    expect(normalizeError(401, { detail: 'Token manquant' })).toMatchObject({ status: 401, code: 'http_401', message: 'Token manquant' })
  })

  it('forme du TenantMiddleware : `error` + `detail` texte', () => {
    expect(normalizeError(400, { error: 'invalid_tenant', detail: 'Identifiant de tenant invalide ou réservé.' })).toMatchObject({
      code: 'invalid_tenant',
      message: 'Identifiant de tenant invalide ou réservé.',
    })
  })

  it('forme de validation FastAPI : tableau', () => {
    const e = normalizeError(422, { detail: [{ loc: ['body', 'data'], msg: 'Field required', type: 'missing' }] })
    expect(e).toMatchObject({ code: 'validation_failed' })
    expect(e.details).toEqual([{ field: 'data', msg: 'Field required', type: 'missing' }])
  })

  it.each([null, undefined, 'texte', 42, [], {}])('corps inattendu %j', (body) => {
    expect(normalizeError(500, body)).toMatchObject({ status: 500, code: 'http_500', message: 'Erreur 500' })
  })

  it('un 401 renouvelé : la requête est rejouée UNE fois avec le nouveau jeton, et réussit', async () => {
    const requests: HttpRequest[] = []
    const replies: HttpResponse[] = [{ status: 401, json: { detail: 'Token expiré' } }, { status: 200, json: [] }]
    const context = { baseUrl: '/api', token: 'old', tenant: 't' }
    const transport: Transport = { async request(r) { requests.push(r); return replies.shift()! } }
    const api = new ApiClient(transport, () => context, async () => {
      context.token = 'new' // la session s'est renouvelée
      return true
    })
    await expect(api.doctypes()).resolves.toEqual([])
    expect(requests.map((r) => r.headers.Authorization)).toEqual(['Bearer old', 'Bearer new'])
  })

  it('un 401 qui persiste après renouvellement n’est pas rejoué sans fin', async () => {
    const requests: HttpRequest[] = []
    const transport: Transport = { async request(r) { requests.push(r); return { status: 401, json: { detail: 'révoqué' } } } }
    const api = new ApiClient(transport, () => ({ baseUrl: '/api', token: 't', tenant: null }), async () => true)
    await expect(api.doctypes()).rejects.toMatchObject({ status: 401 })
    expect(requests).toHaveLength(2) // l'original et UN rejeu
  })

  it('un 401 sans renouvellement possible lève tout de suite', async () => {
    const { api, onUnauthorized } = setup([{ status: 401, json: { detail: 'Token expiré' } }])
    onUnauthorized.mockResolvedValue(false)
    await expect(api.doctypes()).rejects.toMatchObject({ status: 401 })
  })

  it('un 401 prévient la session une fois, puis lève', async () => {
    const { api, onUnauthorized } = setup([{ status: 401, json: { detail: 'Token invalide ou expiré' } }])
    await expect(api.doctypes()).rejects.toMatchObject({ status: 401 })
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('un 403 ou un 404 ne déconnectent pas', async () => {
    const { api, onUnauthorized } = setup([{ status: 403, json: { detail: { code: 'permission_denied' } } }, { status: 404, json: null }])
    await expect(api.doctypes()).rejects.toMatchObject({ status: 403 })
    await expect(api.get('Customer', 'x')).rejects.toMatchObject({ status: 404 })
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('reconnaît un conflit de version', async () => {
    const { api } = setup([{ status: 409, json: { detail: { code: 'version_conflict', msg: 'x' } } }])
    const err = await api.update('Customer', 'n', 1, {}).catch((e) => e)
    expect(err.isConflict).toBe(true)
  })

  it('laisse remonter une panne réseau telle quelle', async () => {
    const transport: Transport = { request: () => Promise.reject(new TypeError('boom')) }
    const api = new ApiClient(transport, () => ({ baseUrl: '', token: null, tenant: null }))
    await expect(api.doctypes()).rejects.toThrow('boom')
  })
})

describe('habillages (overrides)', () => {
  it('envoie la disposition d’un formulaire et la retire', async () => {
    const { api, requests } = setup([{ status: 200, json: { doctype: 'Order', config: {} } }, { status: 200, json: { doctype: 'Order', config: {} } }, { status: 204, json: null }])
    await api.formOverride('Order')
    await api.saveFormOverride('Order', { fields: { a: { label: 'A' } } })
    await api.removeFormOverride('Order')
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      'GET /app/xcore-doctype/override/form/Order',
      'PUT /app/xcore-doctype/override/form/Order',
      'DELETE /app/xcore-doctype/override/form/Order',
    ])
    expect(requests[1]!.body).toEqual({ config: { fields: { a: { label: 'A' } } } })
  })

  it('envoie le tableau de bord d’un type et le retire', async () => {
    const { api, requests } = setup([{ status: 200, json: { doctype: 'Stock Entry', config: { blocks: [] } } }, { status: 204, json: null }])
    await api.saveDashboardOverride('Stock Entry', { blocks: [{ type: 'header', text: 'x' }] })
    await api.removeDashboardOverride('Stock Entry')
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      'PUT /app/xcore-doctype/override/dashboard/Stock%20Entry?scope=org',
      'DELETE /app/xcore-doctype/override/dashboard/Stock%20Entry?scope=org',
    ])
    expect(requests[0]!.body).toEqual({ config: { blocks: [{ type: 'header', text: 'x' }] } })
  })

  it('enregistre pour moi (`scope=user`) ou pour l’organisation (par défaut), espace comme tableau de bord', async () => {
    const { api, requests } = setup([
      { status: 200, json: {} },
      { status: 204, json: null },
      { status: 200, json: {} },
      { status: 204, json: null },
    ])
    await api.saveWorkspaceOverride('stock', { label: 'S' }, 'user')
    await api.removeWorkspaceOverride('stock', 'user')
    await api.saveDashboardOverride('Invoice', { blocks: [] }, 'user')
    await api.removeDashboardOverride('Invoice', 'user')
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      'PUT /app/xcore-doctype/override/workspace/stock?scope=user',
      'DELETE /app/xcore-doctype/override/workspace/stock?scope=user',
      'PUT /app/xcore-doctype/override/dashboard/Invoice?scope=user',
      'DELETE /app/xcore-doctype/override/dashboard/Invoice?scope=user',
    ])
  })

  it('envoie le corps d’un espace et le retire', async () => {
    const { api, requests } = setup([{ status: 200, json: { name: 'Stock Entry', config: {} } }, { status: 204, json: null }])
    await api.saveWorkspaceOverride('Stock Entry', { label: 'Stock' })
    await api.removeWorkspaceOverride('Stock Entry')
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      'PUT /app/xcore-doctype/override/workspace/Stock%20Entry?scope=org',
      'DELETE /app/xcore-doctype/override/workspace/Stock%20Entry?scope=org',
    ])
  })
})
