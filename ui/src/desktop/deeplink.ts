/**
 * Les LIENS PROFONDS que l'application de bureau accepte (`erp://…`, le schéma qu'xauth émet : `vendor/auth/src/utils/deeplink.py`).
 *
 * Un lien profond est une entrée NON fiable : n'importe quel programme, n'importe quelle page web peut demander au système d'ouvrir
 * `erp://…`. Vocabulaire fermé, donc — deux cibles et rien d'autre — et aucune n'agit par elle-même :
 *
 * - `erp://oauth-callback?…` : le retour d'un fournisseur d'identité. Il n'est PAS une session : il est remis à `session.completeOAuth`,
 *   qui exige le nonce `app_state` de la tentative en cours dans cette fenêtre (`host.oauth.strict`). Sans tentative en cours, il est ignoré.
 * - `erp://invite?token=…` : une invitation ; elle mène à l'écran `#/invite`, où l'utilisateur décide.
 *
 * Tout le reste est refusé avec sa raison (`erp://oauth-link`, la liaison d'un compte, n'a pas d'écran dans le Desk).
 */
export const DEEP_LINK_SCHEME = 'erp'

export type DeepLink =
  | { kind: 'oauth'; query: Record<string, string> }
  | { kind: 'route'; path: string; query: Record<string, string> }
  | { kind: 'refused'; reason: string }

export function readDeepLink(raw: string): DeepLink {
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    return { kind: 'refused', reason: 'lien illisible' }
  }
  if (url.protocol !== `${DEEP_LINK_SCHEME}:`) return { kind: 'refused', reason: `schéma « ${url.protocol} » inconnu (attendu : ${DEEP_LINK_SCHEME}:)` }
  // `erp://oauth-callback?…` : la cible est l'hôte de l'adresse ; `erp:oauth-callback` (sans `//`) la met dans le chemin.
  const target = (url.hostname || url.pathname).replace(/^\/+|\/+$/g, '')
  const query: Record<string, string> = {}
  for (const [key, value] of url.searchParams) query[key] = value
  if (target === 'oauth-callback') return { kind: 'oauth', query }
  if (target === 'invite') {
    return query.token ? { kind: 'route', path: '/invite', query: { token: query.token } } : { kind: 'refused', reason: 'invitation sans jeton' }
  }
  return { kind: 'refused', reason: `cible « ${target} » non prise en charge` }
}
