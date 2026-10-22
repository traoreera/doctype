import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import { authUrl } from '@/lib/config'
import { decodeClaims, displayName, isExpired, tenantOf } from '@/lib/token'
import { NetworkError } from '@/lib/transport'
import { useServices } from '@/lib/services'
import { browserTabSync, SHARED_TTL_MS, type TabMessage } from '@/lib/tabsync'

const STORAGE_KEY = 'xdt.desk.token'
const REFRESH_KEY = 'xdt.desk.refresh'
/** La LIGNÉE de la session : un identifiant tiré à chaque connexion, que l'onglet dupliqué copie avec les jetons (`lib/tabsync`). */
const LINEAGE_KEY = 'xdt.desk.lineage'
/** Le nonce `app_state` d'une connexion OAuth en cours : vérifié au retour du fournisseur (contre un callback forgé). */
const OAUTH_STATE_KEY = 'xdt.desk.oauth'
/** Le jeton est renouvelé UNE minute avant son expiration ; sans renouvellement possible, on prévient DEUX minutes avant. */
const RENEW_BEFORE_MS = 60_000
const WARN_BEFORE_MS = 120_000
const RETRY_OFFLINE_MS = 30_000

/** Le jeton reste dans l'onglet (sessionStorage) : il disparaît à sa fermeture et n'est jamais partagé entre onglets. */
function readStored(key = STORAGE_KEY): string | null {
  try {
    return sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStored(token: string | null, key = STORAGE_KEY): void {
  try {
    if (token) sessionStorage.setItem(key, token)
    else sessionStorage.removeItem(key)
  } catch {
    // stockage indisponible (navigation privée…) : la session ne survivra pas à un rechargement, rien de plus
  }
}

/** Le jeton d'accès expire-t-il dans moins de `ms` (ou ne se lit-il pas) ? */
function expiresWithin(access: string | null, ms: number): boolean {
  const exp = access ? decodeClaims(access)?.exp : undefined
  return typeof exp !== 'number' || exp * 1000 - Date.now() <= ms
}

/** En-têtes d'un appel d'authentification ; `Authorization` seulement quand un jeton est fourni (`accept`, annuaire). */
function jsonHeaders(bearer?: string | null): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json', 'Content-Type': 'application/json' }
  if (bearer) headers.Authorization = `Bearer ${bearer}`
  return headers
}

/** Le `detail` d'une erreur FastAPI, quand c'en est un (jamais une trace). */
function detailOf(json: unknown): string | null {
  if (json && typeof json === 'object' && 'detail' in json) {
    const detail = (json as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) return detail
  }
  return null
}

/** Un nonce aléatoire pour `app_state` (OAuth) : sans `crypto`, on retombe sur le temps + le hasard. */
function randomState(): string {
  try {
    const bytes = new Uint8Array(16)
    crypto.getRandomValues(bytes)
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  } catch {
    return `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`
  }
}

/** Les organisations qu'un retour OAuth transporte en base64 (`tenants`) ; une valeur illisible ne propose rien. */
function decodeTenants(encoded: string): TenantChoice[] {
  if (!encoded) return []
  try {
    const raw: unknown = JSON.parse(atob(encoded))
    if (!Array.isArray(raw)) return []
    return raw
      .filter((x): x is Record<string, unknown> => !!x && typeof x === 'object' && typeof (x as { id?: unknown }).id === 'string')
      .map((x) => ({
        id: String(x.id),
        name: typeof x.name === 'string' ? x.name : typeof x.slug === 'string' ? x.slug : null,
      }))
  } catch {
    return []
  }
}

export interface TenantChoice {
  id: string
  name: string | null
}

export type LoginOutcome =
  | { status: 'ok' }
  | { status: 'tenant'; tenants: TenantChoice[] }
  | { status: 'mfa' }
  | { status: 'onboarding' }
  | { status: 'error'; message: string }

/** Le résultat d'une opération d'authentification secondaire (inscription, mot de passe, invitation, changement d'organisation). */
export type SimpleOutcome = { status: 'ok' } | { status: 'error'; message: string }

/** Ce qu'une invitation révèle AVANT d'être acceptée (route publique `/invites/token/{token}`). */
export interface InviteInfo {
  email: string | null
  tenantName: string | null
}

/** Le résultat d'un renouvellement : « ok » ; « rejected » (le serveur refuse : session révoquée ou expirée) ; « offline » ; « none » (rien à renouveler). */
export type RefreshOutcome = 'ok' | 'rejected' | 'offline' | 'none'

interface LoginBody {
  access_token?: unknown
  refresh_token?: unknown
  tenant_id?: unknown
  mfa_required?: unknown
  mfa_token?: unknown
  onboarding_required?: unknown
  tenants?: { id?: unknown; name?: unknown; slug?: unknown }[] | null
}

export const useSession = defineStore('session', () => {
  const { transport, config, tabs: injected } = useServices()
  const tabs = injected ?? browserTabSync()
  const token = ref<string | null>(readStored())
  /** Un jeton de renouvellement à usage unique (xauth le fait tourner à chaque usage). Dans l'onglet, comme le jeton d'accès. */
  const refreshToken = ref<string | null>(readStored(REFRESH_KEY))
  /** Les onglets d'une même lignée (une connexion, et les onglets dupliqués qui en héritent) se passent le jeton de renouvellement. */
  const lineage = ref<string | null>(readStored(LINEAGE_KEY))
  /**
   * Le jeton de renouvellement d'une connexion encore incomplète : il faut choisir une organisation (`/select-tenant`), confirmer une double
   * authentification (`/auth/verify-mfa`) ou créer/rejoindre une organisation (onboarding). Tant qu'il n'est pas échangé, il ne remplace pas la
   * session : le serveur l'a émis mais aucune organisation n'est choisie. En mémoire seulement (un rechargement demande de se reconnecter).
   */
  const pendingRefresh = ref<string | null>(null)
  /** Le challenge de double authentification en attente de code TOTP (5 minutes, côté serveur). */
  const mfaToken = ref<string | null>(null)
  /** Vrai quand le jeton va expirer et ne peut pas être renouvelé (jeton de développement) : le Desk prévient. */
  const expiringSoon = ref(false)
  /** Message à montrer sur l'écran de connexion après une déconnexion forcée. */
  const notice = ref<string | null>(null)
  /** Les autres organisations du compte (pour en changer sans se reconnecter), chargées à la demande. */
  const tenants = ref<TenantChoice[]>([])

  const claims = computed(() => (token.value ? decodeClaims(token.value) : null))
  const tenant = computed(() => tenantOf(claims.value))
  const user = computed(() => displayName(claims.value))
  const authenticated = computed(() => !!token.value && !isExpired(claims.value))
  /**
   * Mes permissions effectives dans l'organisation courante : xauth ne les met PAS dans le jeton, on les lui demande (`/rbac/me/permissions`).
   * `null` tant qu'elles ne sont pas lues. Un confort d'affichage (cacher ce qui serait refusé) : xauth refuse de toute façon ce qu'il ne permet pas.
   */
  const permissions = ref<string[] | null>(null)
  /** Administrateur de la plateforme (`admin:*`), lu dans les permissions, ou dans le jeton quand il les porte (jeton de développement). */
  const platformAdmin = computed(() => {
    const inToken = claims.value?.permissions
    return (permissions.value ?? []).includes('admin:*') || (Array.isArray(inToken) && inToken.includes('admin:*'))
  })
  const canPasswordLogin = computed(() => !!config.loginUrl)

  /**
   * `share` : `new` pour une connexion (une nouvelle lignée), `same` pour un renouvellement (la lignée continue et les autres onglets en
   * sont prévenus), `quiet` pour un couple ADOPTÉ d'un autre onglet (rien à republier).
   */
  function setToken(value: string | null, refresh: string | null = null, share: 'new' | 'same' | 'quiet' = 'new'): void {
    token.value = value
    writeStored(value)
    refreshToken.value = value ? refresh : null // un nouveau jeton sans jeton de renouvellement ne garde pas celui d'une autre session
    writeStored(refreshToken.value, REFRESH_KEY)
    expiringSoon.value = false
    if (!value || !refresh) {
      lineage.value = null
    } else if (share === 'new' || !lineage.value) {
      lineage.value = randomState()
    }
    writeStored(lineage.value, LINEAGE_KEY)
    if (value && refresh && lineage.value && share !== 'quiet') {
      tabs.write(lineage.value, { access: value, refresh, at: Date.now() })
      if (share === 'same') tabs.post({ type: 'rotated', lineage: lineage.value })
    }
  }

  /** Jeton collé à la main (développement) : accepté s'il se lit et n'est pas expiré. Le serveur reste juge de sa validité. */
  function useToken(raw: string): boolean {
    const clean = raw.trim()
    const parsed = decodeClaims(clean)
    if (!parsed || isExpired(parsed)) return false
    notice.value = null
    setToken(clean)
    return true
  }

  /** Applique une réponse d'authentification qui porte les jetons définitifs : la connexion est terminée, plus rien en attente. */
  function adopt(body: LoginBody): boolean {
    if (typeof body.access_token !== 'string' || !body.access_token) return false
    notice.value = null
    pendingRefresh.value = null
    mfaToken.value = null
    setToken(body.access_token, typeof body.refresh_token === 'string' && body.refresh_token ? body.refresh_token : null)
    return true
  }

  /** `identifier` : une adresse e-mail OU un nom d'utilisateur. Ce qui contient « @ » est une adresse (un nom n'en contient jamais) ; xauth tranche de la même façon. */
  async function login(identifier: string, password: string, tenantId?: string): Promise<LoginOutcome> {
    const typed = identifier.trim()
    if (!config.loginUrl) return { status: 'error', message: t.login.unreachable }
    let res
    try {
      res = await transport.request({
        method: 'POST',
        url: config.loginUrl,
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: { ...(typed.includes('@') ? { email: typed } : { username: typed }), password, ...(tenantId ? { tenant_id: tenantId } : {}) },
      })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    if (res.status === 401 || res.status === 400 || res.status === 422) return { status: 'error', message: t.login.badCredentials }
    if (res.status === 429) return { status: 'error', message: t.login.tooMany }
    const body = (res.json ?? {}) as LoginBody
    if (res.status !== 200) return { status: 'error', message: t.login.refused }
    // Plusieurs organisations : le serveur a DÉJÀ ouvert la session (le refresh_token rendu la représente) ; on choisit, on ne refait pas un login.
    const choices = (body.tenants ?? []).filter((x) => typeof x.id === 'string')
    if (!body.tenant_id && choices.length) {
      pendingRefresh.value = typeof body.refresh_token === 'string' && body.refresh_token ? body.refresh_token : null
      return { status: 'tenant', tenants: choices.map((x) => ({ id: String(x.id), name: typeof x.name === 'string' ? x.name : typeof x.slug === 'string' ? x.slug : null })) }
    }
    if (body.mfa_required) {
      if (typeof body.mfa_token === 'string' && body.mfa_token) {
        mfaToken.value = body.mfa_token
        return { status: 'mfa' }
      }
      return { status: 'error', message: t.login.mfaRequired }
    }
    // Compte sans organisation : le jeton d'accès est valide (sans tenant) et servira à créer/rejoindre une organisation.
    if (body.onboarding_required) {
      if (!adopt(body)) return { status: 'error', message: t.login.refused }
      return { status: 'onboarding' }
    }
    if (!adopt(body)) return { status: 'error', message: t.login.refused }
    return { status: 'ok' }
  }

  /** Échange le jeton de la connexion en cours contre une session dans l'organisation choisie (`/select-tenant`), sans redemander l'identité. */
  async function selectTenant(tenantId: string): Promise<LoginOutcome> {
    const url = authUrl(config.loginUrl, 'select-tenant')
    const used = pendingRefresh.value
    if (!url || !used) return { status: 'error', message: t.login.noTenant }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: { refresh_token: used, tenant_id: tenantId } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    const body = (res.json ?? {}) as LoginBody
    if (body.mfa_required && typeof body.mfa_token === 'string' && body.mfa_token) {
      mfaToken.value = body.mfa_token
      return { status: 'mfa' }
    }
    if (res.status === 200 && adopt(body)) return { status: 'ok' }
    return { status: 'error', message: t.login.badCredentials }
  }

  /** Deuxième étape de la double authentification : code TOTP -> jetons définitifs. */
  async function verifyMfa(code: string): Promise<LoginOutcome> {
    const url = authUrl(config.loginUrl, 'auth/verify-mfa')
    const challenge = mfaToken.value
    if (!url || !challenge) return { status: 'error', message: t.login.mfaInvalid }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: { mfa_token: challenge, code } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    const body = (res.json ?? {}) as LoginBody
    if (res.status === 200 && adopt(body)) return { status: 'ok' }
    return { status: 'error', message: t.login.mfaInvalid }
  }

  /** Compte sans organisation : en créer une. */
  function setupCreate(name: string, slug: string): Promise<LoginOutcome> {
    return setup('create', { name, slug })
  }

  /** Compte sans organisation : en rejoindre une par invitation. */
  function setupJoin(inviteToken: string): Promise<LoginOutcome> {
    return setup('join', { invite_token: inviteToken })
  }

  async function setup(step: 'create' | 'join', fields: Record<string, unknown>): Promise<LoginOutcome> {
    const url = authUrl(config.loginUrl, `setup/${step}`)
    const used = pendingRefresh.value ?? refreshToken.value
    if (!url || !used) return { status: 'error', message: t.login.noTenant }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: { refresh_token: used, ...fields } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    const body = (res.json ?? {}) as LoginBody
    if (res.status === 200 && adopt(body)) return { status: 'ok' }
    return { status: 'error', message: t.login.refused }
  }

  // ── Inscription, mot de passe, invitations, OAuth, changement d'organisation ─────

  /** Inscription (xauth `/register`) : crée le compte SANS organisation ; l'utilisateur se connecte ensuite, puis crée/rejoint une organisation. */
  async function register(email: string, password: string, username?: string): Promise<SimpleOutcome> {
    const url = authUrl(config.loginUrl, 'register')
    if (!url) return { status: 'error', message: t.login.unreachable }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: jsonHeaders(), body: { email, password, ...(username?.trim() ? { username: username.trim() } : {}) } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    if (res.status === 201) return { status: 'ok' }
    if (res.status === 429) return { status: 'error', message: t.login.tooMany }
    return { status: 'error', message: detailOf(res.json) ?? t.login.registerFailed }
  }

  /** Demande d'un lien de réinitialisation (`/password/forgot`) : le serveur répond toujours la même chose (pas d'énumération des adresses). */
  async function requestPasswordReset(email: string): Promise<SimpleOutcome> {
    const url = authUrl(config.loginUrl, 'password/forgot')
    if (!url) return { status: 'error', message: t.login.unreachable }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: jsonHeaders(), body: { email } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    if (res.status === 202 || res.status === 200) return { status: 'ok' }
    if (res.status === 429) return { status: 'error', message: t.login.tooMany }
    return { status: 'error', message: detailOf(res.json) ?? t.login.resetRequestFailed }
  }

  /** Réinitialisation (`/password/reset`) avec le jeton reçu par e-mail (lien vers `#/reset?token=…`). */
  async function resetPassword(resetToken: string, newPassword: string): Promise<SimpleOutcome> {
    const url = authUrl(config.loginUrl, 'password/reset')
    if (!url) return { status: 'error', message: t.login.unreachable }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: jsonHeaders(), body: { token: resetToken, new_password: newPassword } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    if (res.status === 200) return { status: 'ok' }
    return { status: 'error', message: detailOf(res.json) ?? t.login.resetFailed }
  }

  /** Détails d'une invitation AVANT acceptation (route publique) : de quelle organisation, pour quelle adresse. */
  async function fetchInvite(inviteToken: string): Promise<InviteInfo | null> {
    const url = authUrl(config.loginUrl, `invites/token/${encodeURIComponent(inviteToken)}`)
    if (!url) return null
    let res
    try {
      res = await transport.request({ method: 'GET', url, headers: { Accept: 'application/json' } })
    } catch (e) {
      if (e instanceof NetworkError) return null
      throw e
    }
    if (res.status !== 200) return null
    const body = res.json as { email?: unknown; tenant_name?: unknown } | null
    return {
      email: typeof body?.email === 'string' ? body.email : null,
      tenantName: typeof body?.tenant_name === 'string' ? body.tenant_name : null,
    }
  }

  /** Accepte une invitation (`/invites/accept`) : il faut être connecté (le jeton porte l'identité qui rejoint l'organisation). */
  async function acceptInvite(inviteToken: string): Promise<SimpleOutcome> {
    const url = authUrl(config.loginUrl, 'invites/accept')
    if (!url) return { status: 'error', message: t.login.unreachable }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: jsonHeaders(token.value), body: { token: inviteToken } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    if (res.status === 200) return { status: 'ok' }
    return { status: 'error', message: detailOf(res.json) ?? t.login.inviteFailed }
  }

  /** Les fournisseurs OAuth actifs (`/oauth/providers`) : vide, le Desk n'affiche aucun bouton. */
  async function oauthProviders(): Promise<string[]> {
    const url = authUrl(config.loginUrl, 'oauth/providers')
    if (!url) return []
    try {
      const res = await transport.request({ method: 'GET', url, headers: { Accept: 'application/json' } })
      const body = res.json as { providers?: unknown } | null
      return Array.isArray(body?.providers) ? body.providers.filter((p): p is string => typeof p === 'string') : []
    } catch {
      return []
    }
  }

  /** L'origine web du Desk : le retour OAuth y revient (`…#/oauth?access_token=…`), jamais un deep-link. */
  function deskBase(): string {
    const base = config.baseUrl.replace(/\/+$/, '')
    return /^https?:\/\//i.test(base) ? base : `${globalThis.location.origin}${base}`
  }

  /** L'URL d'autorisation d'un fournisseur, à ouvrir par le navigateur ; `null` si le serveur refuse la cible de retour. */
  async function oauthAuthorizeUrl(provider: string): Promise<string | null> {
    const url = authUrl(config.loginUrl, `oauth/${encodeURIComponent(provider)}/authorize`)
    if (!url) return null
    const state = randomState()
    writeStored(state, OAUTH_STATE_KEY)
    const query = `?redirect=${encodeURIComponent(`${deskBase()}/desk/#/oauth`)}&app_state=${encodeURIComponent(state)}`
    let res
    try {
      res = await transport.request({ method: 'GET', url: `${url}${query}`, headers: { Accept: 'application/json' } })
    } catch (e) {
      if (e instanceof NetworkError) return null
      throw e
    }
    const body = res.json as { auth_url?: unknown } | null
    if (res.status === 200 && body && typeof body.auth_url === 'string') return body.auth_url
    writeStored(null, OAUTH_STATE_KEY)
    return null
  }

  /** Au retour du fournisseur (`#/oauth?…`) : vérifie le nonce puis adopte la session, ou avance d'une étape (MFA, organisation à créer). */
  function completeOAuth(query: Record<string, unknown>): LoginOutcome {
    const text = (key: string) => (typeof query[key] === 'string' ? (query[key] as string) : '')
    const expected = readStored(OAUTH_STATE_KEY)
    writeStored(null, OAUTH_STATE_KEY)
    if (expected && text('app_state') && text('app_state') !== expected) return { status: 'error', message: t.login.oauthState }
    if (text('error')) return { status: 'error', message: t.login.oauthFailed }
    if (text('mfa_required') === 'true') {
      if (text('mfa_token')) {
        mfaToken.value = text('mfa_token')
        return { status: 'mfa' }
      }
      return { status: 'error', message: t.login.mfaRequired }
    }
    const body: LoginBody = { access_token: text('access_token'), refresh_token: text('refresh_token') }
    // Plusieurs organisations : le serveur ne choisit pas à la place de l'utilisateur (le refresh_token rendu représente la session ouverte).
    const choices = decodeTenants(text('tenants'))
    if (choices.length && !text('tenant_id')) {
      pendingRefresh.value = text('refresh_token') || null
      return { status: 'tenant', tenants: choices }
    }
    if (!adopt(body)) return { status: 'error', message: t.login.oauthFailed }
    return text('onboarding') === 'true' ? { status: 'onboarding' } : { status: 'ok' }
  }

  /** Les organisations dont ce compte est membre (`/tenants/`), pour le sélecteur de la barre latérale. */
  async function loadTenants(): Promise<void> {
    const url = authUrl(config.loginUrl, 'tenants')
    if (!url || !token.value) return
    try {
      const res = await transport.request({ method: 'GET', url: `${url}/`, headers: jsonHeaders(token.value) })
      const body = res.json
      if (res.status !== 200 || !Array.isArray(body)) return
      tenants.value = body
        .filter((x): x is { id: string; name?: unknown; slug?: unknown } => !!x && typeof x === 'object' && typeof (x as { id?: unknown }).id === 'string')
        .map((x) => ({ id: x.id, name: typeof x.name === 'string' ? x.name : typeof x.slug === 'string' ? x.slug : null }))
    } catch {
      // annuaire indisponible : pas de sélecteur, la session reste utilisable
    }
  }

  async function loadPermissions(): Promise<void> {
    const url = authUrl(config.loginUrl, 'rbac')
    if (!url || !token.value) return
    try {
      const res = await transport.request({ method: 'GET', url: `${url}/me/permissions`, headers: jsonHeaders(token.value) })
      if (res.status === 200 && Array.isArray(res.json)) permissions.value = res.json.filter((x): x is string => typeof x === 'string')
    } catch {
      // illisibles : les rubriques réservées restent cachées, les droits eux-mêmes sont jugés par le serveur
    }
  }

  /** Change d'organisation sans se reconnecter (`/select-tenant`), en échangeant le jeton de renouvellement courant. */
  async function switchTenant(tenantId: string): Promise<SimpleOutcome> {
    const url = authUrl(config.loginUrl, 'select-tenant')
    const used = refreshToken.value ?? pendingRefresh.value
    if (!url || !used) return { status: 'error', message: t.login.noTenant }
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: jsonHeaders(), body: { refresh_token: used, tenant_id: tenantId } })
    } catch (e) {
      if (e instanceof NetworkError) return { status: 'error', message: t.login.unreachable }
      throw e
    }
    const body = (res.json ?? {}) as LoginBody
    if (res.status === 200 && adopt(body)) {
      tenants.value = []
      return { status: 'ok' }
    }
    return { status: 'error', message: t.login.refused }
  }

  // ── Renouvellement ──────────────────────────────────────
  let inflight: Promise<RefreshOutcome> | null = null

  /**
   * Échange le jeton de renouvellement contre un nouveau couple de jetons. Un seul échange à la fois DANS l'onglet (single-flight) ET entre
   * les onglets de la lignée (verrou commun) : le jeton est à usage unique. Sous le verrou, on relit d'abord le couple partagé : si un
   * autre onglet a déjà renouvelé, on l'adopte au lieu d'envoyer un jeton déjà usé (que le serveur refuserait).
   */
  function refresh(): Promise<RefreshOutcome> {
    const url = authUrl(config.loginUrl, 'refresh')
    if (!url || !refreshToken.value) return Promise.resolve('none')
    const name = `xdt.desk.refresh.${lineage.value ?? 'tab'}`
    const asked = refreshToken.value
    inflight ??= tabs.withLock(name, () => renew(url, asked)).finally(() => (inflight = null))
    return inflight
  }

  /** `asked` : le jeton qu'on tenait en demandant ; s'il a changé pendant l'attente du verrou, un autre onglet a renouvelé pour nous. */
  async function renew(url: string, asked: string | null): Promise<RefreshOutcome> {
    const used = refreshToken.value
    if (!used) return 'none' // déconnecté pendant l'attente du verrou
    if (used !== asked && !expiresWithin(token.value, RENEW_BEFORE_MS)) return 'ok' // adopté (diffusion) pendant l'attente : rien à échanger
    const shared = lineage.value ? tabs.read(lineage.value) : null
    if (shared && shared.refresh !== used) {
      if (!expiresWithin(shared.access, RENEW_BEFORE_MS)) {
        setToken(shared.access, shared.refresh, 'quiet') // un autre onglet vient de renouveler : son couple vaut pour nous
        return 'ok'
      }
      return exchange(url, shared.refresh) // son couple est lui-même près d'expirer : on renouvelle à partir de SON jeton, pas du nôtre (usé)
    }
    return exchange(url, used)
  }

  async function exchange(url: string, used: string): Promise<RefreshOutcome> {
    let res
    try {
      res = await transport.request({ method: 'POST', url, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: { refresh_token: used } })
    } catch (e) {
      if (e instanceof NetworkError) return 'offline'
      throw e
    }
    const body = (res.json ?? {}) as LoginBody
    if (res.status === 200 && typeof body.access_token === 'string' && body.access_token) {
      setToken(body.access_token, typeof body.refresh_token === 'string' && body.refresh_token ? body.refresh_token : used, 'same')
      return 'ok'
    }
    return res.status >= 500 ? 'offline' : 'rejected' // 5xx : le serveur est malade, la session ne l'est pas
  }

  let timer: ReturnType<typeof setTimeout> | undefined

  /** Programme le prochain renouvellement (ou l'avertissement d'expiration) d'après l'échéance du jeton courant. */
  function schedule(): void {
    clearTimeout(timer)
    const exp = claims.value?.exp
    if (!token.value || typeof exp !== 'number') return
    const left = exp * 1000 - Date.now()
    if (refreshToken.value) {
      timer = setTimeout(async () => {
        const out = await refresh()
        if (out === 'ok') return // setToken a déclenché le calendrier suivant
        if (out === 'offline') timer = setTimeout(schedule, RETRY_OFFLINE_MS)
        else if (out === 'rejected') expire()
      }, Math.max(left - RENEW_BEFORE_MS, 0))
    } else {
      timer = setTimeout(() => (expiringSoon.value = true), Math.max(left - WARN_BEFORE_MS, 0))
    }
  }

  /** Ce qu'un autre onglet de la même lignée annonce : il a renouvelé (on prend son couple) ou il s'est déconnecté (on le suit). */
  function onTab(message: TabMessage): void {
    if (!lineage.value || message.lineage !== lineage.value) return
    if (message.type === 'logout') {
      clear(t.login.loggedOutElsewhere, false)
      return
    }
    const shared = tabs.read(lineage.value)
    if (shared && shared.refresh !== refreshToken.value) setToken(shared.access, shared.refresh, 'quiet')
  }

  /** À appeler une fois au démarrage : le calendrier suit le jeton, quelle que soit la façon dont il change ; l'onglet écoute les autres. */
  function start(): () => void {
    tabs.prune(SHARED_TTL_MS, Date.now())
    const stopTabs = tabs.subscribe(onTab)
    const stop = watch([token, refreshToken], schedule, { immediate: true })
    return () => {
      stop()
      stopTabs()
      clearTimeout(timer)
    }
  }

  // ── Fin de session ──────────────────────────────────────
  /** `forget` : retirer aussi le couple partagé de la lignée (la session est finie pour tous ses onglets). */
  function clear(message: string | null, forget = true): void {
    if (forget && lineage.value) tabs.remove(lineage.value)
    setToken(null)
    pendingRefresh.value = null
    mfaToken.value = null
    tenants.value = []
    notice.value = message
  }

  /** Déconnexion voulue : la session est aussi révoquée côté serveur (au mieux : sans réponse, on se déconnecte quand même). */
  function logout(message: string | null = null): void {
    const url = authUrl(config.loginUrl, 'logout')
    const used = refreshToken.value
    // Les autres onglets de la lignée partagent cette session, que le serveur va révoquer : ils se déconnectent aussi.
    if (lineage.value) tabs.post({ type: 'logout', lineage: lineage.value })
    if (url && used) {
      void transport
        .request({ method: 'POST', url, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: { refresh_token: used } })
        .catch(() => {})
    }
    clear(message)
  }

  /** Appelé quand le serveur répond 401 et que le renouvellement n'a rien donné : jeton expiré ou révoqué. Rien à révoquer. */
  function expire(): void {
    if (token.value) clear(t.login.sessionExpired)
  }

  return {
    token,
    refreshToken,
    mfaToken,
    expiringSoon,
    notice,
    tenants,
    claims,
    tenant,
    user,
    authenticated,
    permissions,
    platformAdmin,
    loadPermissions,
    canPasswordLogin,
    useToken,
    login,
    selectTenant,
    verifyMfa,
    setupCreate,
    setupJoin,
    register,
    requestPasswordReset,
    resetPassword,
    fetchInvite,
    acceptInvite,
    oauthProviders,
    oauthAuthorizeUrl,
    completeOAuth,
    loadTenants,
    switchTenant,
    refresh,
    start,
    logout,
    expire,
  }
})
