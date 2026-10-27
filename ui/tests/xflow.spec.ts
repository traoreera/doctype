import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api'
import type { Transport } from '@/lib/transport'
import { XflowClient, statusTone, xflowBase, xflowTime } from '@/lib/xflow'

function client(answer: (url: string, method: string) => { status: number; json: unknown }) {
  const seen: { url: string; method: string; headers: Record<string, string> }[] = []
  const transport = {
    async request(r: { url: string; method: string; headers: Record<string, string> }) {
      seen.push(r)
      return answer(r.url, r.method)
    },
  } as unknown as Transport
  return { c: new XflowClient(transport, () => ({ baseUrl: '/app/xflow', token: 'tok', tenant: 'acme' })), seen }
}

describe('le client de xflow', () => {
  it('xflow refuse en 200 (`status: error`) : le client en fait une ApiError, introuvable = 404', async () => {
    const { c } = client(() => ({ status: 200, json: { status: 'error', msg: 'Workflow introuvable.', code: 'not_found' } }))
    const refused = await c.flow('x').catch((e: unknown) => e)
    expect(refused).toBeInstanceOf(ApiError)
    expect((refused as ApiError).status).toBe(404)
    expect((refused as ApiError).message).toBe('Workflow introuvable.')
    const { c: c2 } = client(() => ({ status: 200, json: { status: 'error', msg: 'invalide', code: 'register_error' } }))
    expect(((await c2.deploy({ name: 'x', trigger: { type: 'manual' }, steps: [] }).catch((e: unknown) => e)) as ApiError).status).toBe(422)
  })

  it('même session que le Desk (jeton, organisation), racine à côté de xcore-doctype', async () => {
    const { c, seen } = client(() => ({ status: 200, json: { status: 'ok', runs: [] } }))
    await c.runs('longues_demandes', 10)
    expect(seen[0]!.url).toBe('/app/xflow/executions?limit=10&workflow_name=longues_demandes')
    expect(seen[0]!.headers).toMatchObject({ Authorization: 'Bearer tok', 'X-Tenant-ID': 'acme' })
    expect(xflowBase('/app/xcore-doctype')).toBe('/app/xflow')
  })

  it('installé ou non, lisible ou non : 404 = absent, 403 = réservé', async () => {
    expect(await client(() => ({ status: 200, json: { status: 'ok', workflows: [] } })).c.availability()).toBe('yes')
    expect(await client(() => ({ status: 404, json: { detail: 'Not Found' } })).c.availability()).toBe('absent')
    expect(await client(() => ({ status: 403, json: { detail: 'Permissions manquantes' } })).c.availability()).toBe('reserved')
  })

  it('une date sans fuseau est de l’UTC (xflow écrit en UTC, sa base perd le fuseau) ; le ton d’un statut', () => {
    expect(xflowTime('2026-10-27T16:45:37.227984')).toBe('2026-10-27T16:45:37.227984Z')
    expect(xflowTime('2026-10-27T16:45:37+00:00')).toBe('2026-10-27T16:45:37+00:00')
    expect(xflowTime(null)).toBeNull()
    expect(['success', 'failed', 'running', 'cancelled'].map(statusTone)).toEqual(['ok', 'bad', 'warn', 'neutral'])
  })
})
