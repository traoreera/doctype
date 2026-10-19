import { describe, expect, it } from 'vitest'
import { decodeClaims, displayName, isExpired, tenantOf } from '../src/lib/token'

const b64url = (o: unknown) => btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(o)))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const jwt = (claims: unknown) => `${b64url({ alg: 'RS256' })}.${b64url(claims)}.signature`
const dev = (claims: unknown) => `${b64url(claims)}.deadbeef`

describe('lecture des revendications', () => {
  it('lit un JWT (xauth) et un jeton de développement', () => {
    expect(decodeClaims(jwt({ sub: 'u1', tenant_id: 't1' }))).toMatchObject({ sub: 'u1', tenant_id: 't1' })
    expect(decodeClaims(dev({ sub: 'u2', tenant_id: 't2' }))).toMatchObject({ sub: 'u2', tenant_id: 't2' })
  })

  it('décode l’UTF-8 (un nom accentué ne devient pas du mojibake)', () => {
    expect(decodeClaims(jwt({ sub: 'Éloïse 日本' }))?.sub).toBe('Éloïse 日本')
  })

  it.each(['', 'abc', 'a.b.c.d', '....', '%%%.sig', b64url('texte') + '.sig', b64url([1, 2]) + '.sig', b64url(null) + '.sig'])(
    'rejette un jeton illisible %j',
    (t) => {
      expect(decodeClaims(t)).toBeNull()
    },
  )

  it('un jeton lisible sans revendication donne un objet vide, donc aucun tenant', () => {
    expect(decodeClaims('e30.sig')).toEqual({})
    expect(tenantOf(decodeClaims('e30.sig'))).toBeNull()
  })

  it('le tenant vient de la revendication directe, sinon de user.tenant_id (xauth)', () => {
    expect(tenantOf({ tenant_id: 'a', user: { tenant_id: 'b' } })).toBe('a')
    expect(tenantOf({ user: { tenant_id: 'b' } })).toBe('b')
    expect(tenantOf({})).toBeNull()
    expect(tenantOf(null)).toBeNull()
    expect(tenantOf({ tenant_id: '' })).toBeNull()
    expect(tenantOf({ tenant_id: 5 as unknown as string })).toBeNull()
  })

  it('détecte l’expiration', () => {
    expect(isExpired({ exp: 1000 }, 2_000_000)).toBe(true)
    expect(isExpired({ exp: 10_000 }, 2_000_000)).toBe(false)
    expect(isExpired({}, 2_000_000)).toBe(false)
    expect(isExpired(null)).toBe(false)
  })

  it('choisit un nom à afficher', () => {
    expect(displayName({ sub: 'u1', email: 'a@b.fr' })).toBe('a@b.fr') // xauth pose l'e-mail à la racine
    expect(displayName({ sub: 'u1', user: { email: 'a@b.fr' } })).toBe('a@b.fr') // jeton de développement
    expect(displayName({ sub: 'u1', email: 'racine@b.fr', user: { email: 'nested@b.fr' } })).toBe('racine@b.fr')
    expect(displayName({ sub: 'u1' })).toBe('u1')
    expect(displayName(null)).toBe('')
  })
})
