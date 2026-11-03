import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { licenseBase, XlicenseClient } from '@/lib/xlicense'
import type { HttpRequest, HttpResponse } from '@/lib/transport'

/** Un transport qui note ce qu'on lui demande et répond ce qu'on lui dit. */
function transport(reply: Partial<HttpResponse> = {}) {
  const seen: HttpRequest[] = []
  /** La requête vue, ou un échec parlant : un index sur un tableau peut être `undefined`. */
  const last = (): HttpRequest => {
    const req = seen[seen.length - 1]
    if (!req) throw new Error('aucune requête envoyée')
    return req
  }
  return {
    seen,
    last,
    transport: {
      async request(req: HttpRequest): Promise<HttpResponse> {
        seen.push(req)
        return { status: 200, json: {}, headers: {}, ...reply } as HttpResponse
      },
    },
  }
}

const context = () => ({ baseUrl: '/app/xlicense', token: 'jeton', tenant: 'org-1' })

describe('la racine de xlicense se déduit de celle de l’API', () => {
  it('suit le préfixe de l’hôte, quel qu’il soit', () => {
    expect(licenseBase('/app/xcore-doctype')).toBe('/app/xlicense')
    expect(licenseBase('https://h/plugin/xcore-doctype/')).toBe('https://h/plugin/xlicense')
  })

  it('ne devine rien quand l’adresse n’a pas cette forme', () => {
    // Mieux vaut pas de facturation qu'une racine inventée : les écrans disparaissent, rien n'échoue.
    expect(licenseBase('/api/v2')).toBeNull()
    expect(licenseBase('')).toBeNull()
  })
})

describe('le client de facturation', () => {
  it('ouvre la caisse d’un module et échappe son code', async () => {
    const { transport: tr, last } = transport({ json: { url: 'https://pay/x', session_id: 'cs_1', module: 'wms', price: 19, currency: 'EUR' } })
    const out = await new XlicenseClient(tr, context).checkout('mon module')
    expect(out.url).toBe('https://pay/x')
    expect(last().method).toBe('POST')
    expect(last().url).toBe('/app/xlicense/me/modules/mon%20module/checkout')
  })

  it('confirme en envoyant l’identifiant de session', async () => {
    const { transport: tr, last } = transport({ json: { code: 'wms', taken: true, source: 'paid' } })
    const out = await new XlicenseClient(tr, context).confirm('wms', 'cs_1')
    expect(out.source).toBe('paid')
    expect(last().body).toEqual({ session_id: 'cs_1' })
  })

  it('rend l’adresse du portail du prestataire', async () => {
    const { transport: tr } = transport({ json: { url: 'https://pay/portal' } })
    expect(await new XlicenseClient(tr, context).portal()).toBe('https://pay/portal')
  })
})

describe('un défaut de paiement ne se confond pas avec un refus de droit', () => {
  it('reconnaît le 402 du gate et le refus de la source', () => {
    expect(new ApiError(402, 'license_inactive', 'x').isPaymentRequired).toBe(true)
    expect(new ApiError(422, 'payment_required', 'x').isPaymentRequired).toBe(true)
  })

  it('laisse un 403 être un 403', () => {
    // « Réservé » n'est pas « à payer » : emmener quelqu'un à la caisse pour un droit manquant serait faux.
    expect(new ApiError(403, 'permission_denied', 'x').isPaymentRequired).toBe(false)
    expect(new ApiError(422, 'validation_failed', 'x').isPaymentRequired).toBe(false)
  })

  it('distingue un module hors abonnement', () => {
    expect(new ApiError(403, 'module_not_in_plan', 'x').isModuleNotLicensed).toBe(true)
    expect(new ApiError(403, 'permission_denied', 'x').isModuleNotLicensed).toBe(false)
  })
})
