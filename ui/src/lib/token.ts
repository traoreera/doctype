/**
 * Lecture (SANS vérification) des revendications d'un jeton, pour savoir quel tenant annoncer dans `X-Tenant-ID`.
 *
 * Le serveur reste seul juge : il vérifie la signature et exige que l'en-tête corresponde à la revendication
 * (D17). Rien de ce qui est lu ici ne donne un droit ; sans lecture, le Desk ne saurait simplement pas quel tenant
 * demander. Deux formes sont reconnues : un JWT (`en-tête.charge.signature`, xauth) et le jeton de développement
 * d'xcore-doctype (`charge.signature`).
 */

export interface Claims {
  sub?: string
  tenant_id?: string
  exp?: number
  /** xauth pose l'e-mail à la racine du JWT (pas dans `user`, qui est la forme du jeton de développement). */
  email?: string
  user?: { email?: string; tenant_id?: string }
  [key: string]: unknown
}

function base64UrlToText(segment: string): string {
  const b64 = segment.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(segment.length / 4) * 4, '=')
  const bin = atob(b64)
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))
}

export function decodeClaims(token: string): Claims | null {
  const parts = token.split('.')
  const payload = parts.length === 3 ? parts[1] : parts.length === 2 ? parts[0] : undefined
  if (!payload) return null
  try {
    const claims = JSON.parse(base64UrlToText(payload))
    return claims && typeof claims === 'object' && !Array.isArray(claims) ? (claims as Claims) : null
  } catch {
    return null
  }
}

export function tenantOf(claims: Claims | null): string | null {
  if (!claims) return null
  const t = claims.tenant_id ?? claims.user?.tenant_id
  return typeof t === 'string' && t ? t : null
}

export function isExpired(claims: Claims | null, now = Date.now()): boolean {
  return typeof claims?.exp === 'number' && claims.exp * 1000 <= now
}

/** Nom à afficher pour l'utilisateur connecté. */
export function displayName(claims: Claims | null): string {
  return claims?.email ?? claims?.user?.email ?? (typeof claims?.sub === 'string' ? claims.sub : '')
}
