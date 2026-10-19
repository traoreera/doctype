import { ApiClient, type ClientContext } from './api'
import type { Transport } from './transport'

/**
 * Le client d'xauth (l'authentification : comptes, organisations, rôles, sessions, journal d'audit, courrier). Même transport, même jeton, même
 * renouvellement sur un 401 que le client des documents (`ApiClient`) ; seule la racine change (`/app/auth`, déduite de `login_url`).
 */

export interface AuthUser {
  id: string
  email: string
  username?: string | null
  is_active: boolean
  mfa_enabled: boolean
  has_password?: boolean
}

export interface AuthSession {
  id: string
  tenant_id: string | null
  ip_address: string | null
  device_fingerprint: string | null
  last_seen: string
  expires_at: string
  is_current?: boolean
}

export interface AuthPermission {
  id: string
  name: string
  description: string | null
  source_plugin: string | null
  tenant_grantable: boolean
  group: string | null
  active: boolean
}

export interface AuthRole {
  id: string
  name: string
  tenant_id: string | null
  description: string | null
  permissions: AuthPermission[]
}

export interface AuthMember {
  user_id: string
  email: string | null
  primary_role_id: string | null
  role_ids: string[]
  is_owner: boolean
}

export interface AuthInvite {
  id: string
  tenant_id: string
  email: string
  token: string
  role_id: string | null
  expires_at: string
  used_at: string | null
  is_active: boolean
  invited_by: string
}

export interface AuditEntry {
  id: string
  user_id: string | null
  action: string
  resource: string | null
  resource_id: string | null
  ip_address: string | null
  user_agent: string | null
  created_at: string
}

export interface AuthTenant {
  id: string
  name: string
  slug: string
  created_at: string
  is_owner?: boolean
  license_state?: string | null
}

/** Un compte vu par l'administrateur de la plateforme (`/admin/users`). */
export interface AdminUser {
  id: string
  email: string
  username?: string | null
  is_active: boolean
  mfa_enabled: boolean
  created_at: string
}

/** Un compte externe (Google, GitHub…) lié au mien. */
export interface LinkedAccount {
  provider: string
  provider_email: string | null
  provider_name: string | null
  provider_avatar: string | null
  linked_at: string
}

/** Une invitation reçue par mon adresse (`/invites/me`), avec le nom lisible de l'organisation. */
export interface ReceivedInvite extends AuthInvite {
  tenant_name: string | null
}

export interface AuthNotification {
  id: string
  user_id: string
  tenant_id: string | null
  title: string
  message: string | null
  type: string
  link: string | null
  is_read: boolean
  created_at: string
}

export interface MfaSetup {
  secret: string
  otpauth_url: string
  qr_code: string | null
  backup_codes: string[]
}

export interface MailStatus {
  name: string
  status: string
  configured: boolean
  simulated: boolean
  smtp_host: string
  smtp_port: number
  use_tls?: boolean
  from: string
  sent: number
  failed: number
  queued: number
  queue_size?: number
  last_sent_at: number | null
  reachable: boolean | null
  detail: string | null
}

const enc = encodeURIComponent

export class XauthClient extends ApiClient {
  constructor(transport: Transport, context: () => ClientContext, onUnauthorized?: () => boolean | Promise<boolean> | void) {
    super(transport, context, onUnauthorized)
  }

  // ── Mon compte ──────────────────────────────────────────
  me(): Promise<AuthUser> {
    return this.call('GET', '/me')
  }

  /** `otp` : le code de l'application d'authentification, que le serveur EXIGE quand la double authentification est active. */
  async changePassword(current: string, next: string, otp?: string): Promise<void> {
    await this.call('PATCH', '/account/password', { body: { current_password: current, new_password: next, ...(otp ? { opt_code: otp } : {}) } })
  }

  async changeEmail(email: string, password: string, otp?: string): Promise<void> {
    await this.call('PATCH', '/account/email', { body: { new_email: email, password, ...(otp ? { opt_code: otp } : {}) } })
  }

  /** Choisit ou change son nom d'utilisateur (une seconde façon de se connecter) ; rend le nom tel que le serveur l'a rangé (minuscules). */
  async changeUsername(username: string, password: string, otp?: string): Promise<string> {
    return (await this.call<{ username: string }>('PATCH', '/account/username', { body: { username, password, ...(otp ? { opt_code: otp } : {}) } })).username
  }

  mfaSetup(): Promise<MfaSetup> {
    return this.call('POST', '/mfa/setup')
  }

  async mfaEnable(code: string): Promise<void> {
    await this.call('POST', '/mfa/enable', { body: { code } })
  }

  async mfaDisable(code: string): Promise<void> {
    await this.call('DELETE', '/mfa/', { body: { code } })
  }

  sessions(): Promise<AuthSession[]> {
    return this.call('GET', '/auth/sessions')
  }

  async revokeSession(id: string): Promise<void> {
    await this.call('DELETE', `/auth/sessions/${enc(id)}`)
  }

  async revokeAllSessions(): Promise<void> {
    await this.call('DELETE', '/auth/sessions')
  }

  // ── Organisation : membres, invitations, rôles ──────────
  tenant(id: string): Promise<AuthTenant> {
    return this.call('GET', `/tenants/${enc(id)}`)
  }

  members(tenant: string): Promise<AuthMember[]> {
    return this.call('GET', `/rbac/tenants/${enc(tenant)}/members`)
  }

  async removeMember(tenant: string, user: string): Promise<void> {
    await this.call('DELETE', `/tenants/${enc(tenant)}/members/${enc(user)}`)
  }

  async setMemberRole(tenant: string, user: string, role: string): Promise<void> {
    await this.call('POST', `/rbac/tenants/${enc(tenant)}/members/${enc(user)}/role`, { body: { role_id: role } })
  }

  memberPermissions(tenant: string, user: string): Promise<string[]> {
    return this.call('GET', `/rbac/tenants/${enc(tenant)}/members/${enc(user)}/permissions`)
  }

  async grantMember(tenant: string, user: string, permission: string): Promise<void> {
    await this.call('POST', `/rbac/tenants/${enc(tenant)}/members/${enc(user)}/permissions`, { body: { permission_name: permission } })
  }

  async revokeMember(tenant: string, user: string, permission: string): Promise<void> {
    await this.call('DELETE', `/rbac/tenants/${enc(tenant)}/members/${enc(user)}/permissions/${enc(permission)}`)
  }

  invites(tenant: string): Promise<AuthInvite[]> {
    return this.call('GET', `/invites/${enc(tenant)}`)
  }

  invite(tenant: string, email: string, role: string | null): Promise<AuthInvite> {
    return this.call('POST', '/invites/', { body: { tenant_id: tenant, email, ...(role ? { role_id: role } : {}) } })
  }

  async revokeInvite(id: string): Promise<void> {
    await this.call('DELETE', `/invites/${enc(id)}`)
  }

  /** Les rôles de la plateforme (admin, user, tenant_admin…), avec leurs permissions. */
  globalRoles(): Promise<AuthRole[]> {
    return this.call('GET', '/rbac/roles')
  }

  tenantRoles(tenant: string): Promise<AuthRole[]> {
    return this.call('GET', `/rbac/tenants/${enc(tenant)}/roles`)
  }

  /** Les permissions qu'une organisation peut accorder (`tenant_grantable`), avec leur groupe et leur description. */
  grantable(tenant: string): Promise<AuthPermission[]> {
    return this.call('GET', `/rbac/tenants/${enc(tenant)}/grantable`)
  }

  createRole(tenant: string, name: string, description: string | null, permissions: string[]): Promise<AuthRole> {
    return this.call('POST', `/rbac/tenants/${enc(tenant)}/roles`, { body: { name, description, permissions } })
  }

  async deleteRole(tenant: string, role: string): Promise<void> {
    await this.call('DELETE', `/rbac/tenants/${enc(tenant)}/roles/${enc(role)}`)
  }

  addRolePermission(tenant: string, role: string, permission: string): Promise<AuthRole> {
    return this.call('POST', `/rbac/tenants/${enc(tenant)}/roles/${enc(role)}/permissions`, { body: { permission_name: permission } })
  }

  removeRolePermission(tenant: string, role: string, permission: string): Promise<AuthRole> {
    return this.call('DELETE', `/rbac/tenants/${enc(tenant)}/roles/${enc(role)}/permissions/${enc(permission)}`)
  }

  /** Les rôles supplémentaires d'un membre (le rôle principal reste `setMemberRole`). */
  async addMemberRole(tenant: string, user: string, role: string): Promise<void> {
    await this.call('POST', `/rbac/tenants/${enc(tenant)}/members/${enc(user)}/roles`, { body: { role_id: role } })
  }

  async removeMemberRole(tenant: string, user: string, role: string): Promise<void> {
    await this.call('DELETE', `/rbac/tenants/${enc(tenant)}/members/${enc(user)}/roles/${enc(role)}`)
  }

  // ── Mon compte, suite ───────────────────────────────────
  /** Définit un premier mot de passe (compte créé par un fournisseur externe, qui n'en a pas). */
  async setPassword(password: string): Promise<void> {
    await this.call('POST', '/password/set', { body: { new_password: password } })
  }

  linkedAccounts(): Promise<LinkedAccount[]> {
    return this.call('GET', '/oauth/me/accounts')
  }

  async unlinkProvider(provider: string): Promise<void> {
    await this.call('DELETE', `/oauth/${enc(provider)}/unlink`)
  }

  /** Les invitations en attente pour mon adresse. */
  myInvites(): Promise<ReceivedInvite[]> {
    return this.call('GET', '/invites/me')
  }

  myPermissions(): Promise<string[]> {
    return this.call('GET', '/rbac/me/permissions')
  }

  // ── Organisations ───────────────────────────────────────
  tenants(): Promise<AuthTenant[]> {
    return this.call('GET', '/tenants/')
  }

  createTenant(name: string, slug: string): Promise<AuthTenant> {
    return this.call('POST', '/tenants/', { body: { name, slug } })
  }

  renameTenant(id: string, name: string): Promise<AuthTenant> {
    return this.call('PATCH', `/tenants/${enc(id)}`, { body: { name } })
  }

  async tenantSettings(id: string): Promise<Record<string, unknown>> {
    return (await this.call<{ settings: Record<string, unknown> }>('GET', `/tenants/${enc(id)}/settings`)).settings
  }

  async saveTenantSettings(id: string, settings: Record<string, unknown>): Promise<void> {
    await this.call('PUT', `/tenants/${enc(id)}/settings`, { body: { settings } })
  }

  async deleteTenant(id: string): Promise<void> {
    await this.call('DELETE', `/tenants/${enc(id)}`)
  }

  // ── Plateforme (administrateur `admin:*`) ───────────────
  adminUsers(page: { limit?: number; offset?: number } = {}): Promise<AdminUser[]> {
    const params = new URLSearchParams()
    if (page.limit !== undefined) params.set('limit', String(page.limit))
    if (page.offset !== undefined) params.set('offset', String(page.offset))
    return this.call('GET', '/admin/users', { params })
  }

  setUserActive(id: string, active: boolean): Promise<AdminUser> {
    return this.call('PATCH', `/admin/users/${enc(id)}`, { body: { is_active: active } })
  }

  async deleteUser(id: string): Promise<void> {
    await this.call('DELETE', `/admin/users/${enc(id)}`)
  }

  userAudit(id: string): Promise<AuditEntry[]> {
    return this.call('GET', `/audit/users/${enc(id)}`)
  }

  /** Toutes les permissions connues de la plateforme (déclarées par les plugins). */
  permissions(): Promise<AuthPermission[]> {
    return this.call('GET', '/rbac/permissions')
  }

  createGlobalRole(name: string, description: string | null): Promise<AuthRole> {
    return this.call('POST', '/rbac/roles', { body: { name, description } })
  }

  /** NB : ici l'API prend l'identifiant de la permission, pas son nom (contrairement aux rôles d'organisation). */
  addGlobalRolePermission(role: string, permissionId: string): Promise<AuthRole> {
    return this.call('POST', `/rbac/roles/${enc(role)}/permissions`, { body: { permission_id: permissionId } })
  }

  removeGlobalRolePermission(role: string, permissionId: string): Promise<AuthRole> {
    return this.call('DELETE', `/rbac/roles/${enc(role)}/permissions/${enc(permissionId)}`)
  }

  // ── Notifications ───────────────────────────────────────
  notifications(page: { limit?: number; offset?: number } = {}): Promise<AuthNotification[]> {
    const params = new URLSearchParams()
    if (page.limit !== undefined) params.set('limit', String(page.limit))
    if (page.offset !== undefined) params.set('offset', String(page.offset))
    return this.call('GET', '/notifications', { params })
  }

  async markNotificationRead(id: string): Promise<void> {
    await this.call('PATCH', `/notifications/${enc(id)}/read`)
  }

  async markAllNotificationsRead(): Promise<void> {
    await this.call('PATCH', '/notifications/read-all')
  }

  async deleteNotification(id: string): Promise<void> {
    await this.call('DELETE', `/notifications/${enc(id)}`)
  }

  // ── Journal, courrier ───────────────────────────────────
  audit(tenant: string, page: { limit?: number; offset?: number } = {}): Promise<AuditEntry[]> {
    const params = new URLSearchParams()
    if (page.limit !== undefined) params.set('limit', String(page.limit))
    if (page.offset !== undefined) params.set('offset', String(page.offset))
    return this.call('GET', `/audit/tenants/${enc(tenant)}`, { params })
  }

  mailStatus(): Promise<MailStatus> {
    return this.call('GET', '/mail/status')
  }

  mailTest(to: string): Promise<{ sent: boolean; simulated: boolean }> {
    return this.call('POST', '/mail/test', { body: { to } })
  }
}

/** La racine d'xauth, déduite de l'adresse de connexion (`/app/auth/login` -> `/app/auth`) ; `null` sans `login_url` (jeton collé : pas d'xauth connu). */
export function authBase(loginUrl: string | null): string | null {
  return loginUrl && /\/login$/.test(loginUrl) ? loginUrl.replace(/\/login$/, '') : null
}
