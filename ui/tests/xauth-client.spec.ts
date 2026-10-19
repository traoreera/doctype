import { describe, expect, it } from 'vitest'
import type { HttpRequest, Transport } from '../src/lib/transport'
import { authBase, XauthClient } from '../src/lib/xauth'

function setup() {
  const requests: HttpRequest[] = []
  const transport: Transport = {
    async request(req) {
      requests.push(req)
      return { status: 200, json: [] }
    },
  }
  const client = new XauthClient(transport, () => ({ baseUrl: '/app/auth', token: 'tok', tenant: 't-1' }))
  return { client, requests }
}

describe('XauthClient : les adresses d’xauth', () => {
  it('chaque appel part de la racine d’xauth avec le jeton et l’organisation', async () => {
    const { client, requests } = setup()
    await client.me()
    expect(requests[0]).toMatchObject({ method: 'GET', url: '/app/auth/me', headers: { Authorization: 'Bearer tok', 'X-Tenant-ID': 't-1' } })
  })

  it('le compte : PATCH pour le mot de passe et l’adresse, la MFA par code', async () => {
    const { client, requests } = setup()
    await client.changePassword('a', 'b')
    await client.changeEmail('x@acme.test', 'pw')
    await client.mfaSetup()
    await client.mfaEnable('123456')
    await client.mfaDisable('654321')
    expect(requests.map((r) => [r.method, r.url, r.body])).toEqual([
      ['PATCH', '/app/auth/account/password', { current_password: 'a', new_password: 'b' }],
      ['PATCH', '/app/auth/account/email', { new_email: 'x@acme.test', password: 'pw' }],
      ['POST', '/app/auth/mfa/setup', undefined],
      ['POST', '/app/auth/mfa/enable', { code: '123456' }],
      ['DELETE', '/app/auth/mfa/', { code: '654321' }],
    ])
  })

  it('les sessions, les membres et leurs permissions, avec identifiants encodés', async () => {
    const { client, requests } = setup()
    await client.sessions()
    await client.revokeSession('s/1')
    await client.revokeAllSessions()
    await client.members('t-1')
    await client.setMemberRole('t-1', 'u 1', 'r-1')
    await client.grantMember('t-1', 'u-1', 'sales:invoice:read')
    await client.revokeMember('t-1', 'u-1', 'sales:invoice:read')
    await client.removeMember('t-1', 'u-1')
    expect(requests.map((r) => [r.method, r.url, r.body])).toEqual([
      ['GET', '/app/auth/auth/sessions', undefined],
      ['DELETE', '/app/auth/auth/sessions/s%2F1', undefined],
      ['DELETE', '/app/auth/auth/sessions', undefined],
      ['GET', '/app/auth/rbac/tenants/t-1/members', undefined],
      ['POST', '/app/auth/rbac/tenants/t-1/members/u%201/role', { role_id: 'r-1' }],
      ['POST', '/app/auth/rbac/tenants/t-1/members/u-1/permissions', { permission_name: 'sales:invoice:read' }],
      ['DELETE', '/app/auth/rbac/tenants/t-1/members/u-1/permissions/sales%3Ainvoice%3Aread', undefined],
      ['DELETE', '/app/auth/tenants/t-1/members/u-1', undefined],
    ])
  })

  it('les invitations ne portent un rôle que s’il est choisi', async () => {
    const { client, requests } = setup()
    await client.invite('t-1', 'a@acme.test', null)
    await client.invite('t-1', 'b@acme.test', 'r-1')
    await client.invites('t-1')
    await client.revokeInvite('i-1')
    expect(requests.map((r) => [r.method, r.url, r.body])).toEqual([
      ['POST', '/app/auth/invites/', { tenant_id: 't-1', email: 'a@acme.test' }],
      ['POST', '/app/auth/invites/', { tenant_id: 't-1', email: 'b@acme.test', role_id: 'r-1' }],
      ['GET', '/app/auth/invites/t-1', undefined],
      ['DELETE', '/app/auth/invites/i-1', undefined],
    ])
  })

  it('les rôles, le journal et le courrier', async () => {
    const { client, requests } = setup()
    await client.globalRoles()
    await client.tenantRoles('t-1')
    await client.grantable('t-1')
    await client.createRole('t-1', 'Vendeur', null, ['a:b:c'])
    await client.addRolePermission('t-1', 'r-1', 'a:b:c')
    await client.removeRolePermission('t-1', 'r-1', 'a:b:c')
    await client.deleteRole('t-1', 'r-1')
    await client.audit('t-1', { limit: 50, offset: 100 })
    await client.audit('t-1')
    await client.mailStatus()
    await client.mailTest('moi@acme.test')
    expect(requests.map((r) => [r.method, r.url, r.body])).toEqual([
      ['GET', '/app/auth/rbac/roles', undefined],
      ['GET', '/app/auth/rbac/tenants/t-1/roles', undefined],
      ['GET', '/app/auth/rbac/tenants/t-1/grantable', undefined],
      ['POST', '/app/auth/rbac/tenants/t-1/roles', { name: 'Vendeur', description: null, permissions: ['a:b:c'] }],
      ['POST', '/app/auth/rbac/tenants/t-1/roles/r-1/permissions', { permission_name: 'a:b:c' }],
      ['DELETE', '/app/auth/rbac/tenants/t-1/roles/r-1/permissions/a%3Ab%3Ac', undefined],
      ['DELETE', '/app/auth/rbac/tenants/t-1/roles/r-1', undefined],
      ['GET', '/app/auth/audit/tenants/t-1?limit=50&offset=100', undefined],
      ['GET', '/app/auth/audit/tenants/t-1', undefined],
      ['GET', '/app/auth/mail/status', undefined],
      ['POST', '/app/auth/mail/test', { to: 'moi@acme.test' }],
    ])
  })
})

describe('authBase', () => {
  it('se déduit de l’adresse de connexion, et reste inconnue sans elle', () => {
    expect(authBase('/app/auth/login')).toBe('/app/auth')
    expect(authBase('https://sso.acme.test/api/login')).toBe('https://sso.acme.test/api')
    expect(authBase('/app/auth/signin')).toBeNull()
    expect(authBase(null)).toBeNull()
  })
})

describe('XauthClient : le reste d’xauth', () => {
  it('compte, organisations, plateforme et notifications', async () => {
    const { client, requests } = setup()
    await client.setPassword('Nouveau-Passw0rd1')
    await client.linkedAccounts()
    await client.unlinkProvider('git hub')
    await client.myInvites()
    await client.tenants()
    await client.createTenant('Acme', 'acme')
    await client.renameTenant('t-1', 'Acme SA')
    await client.saveTenantSettings('t-1', { a: 1 })
    await client.deleteTenant('t-1')
    await client.addMemberRole('t-1', 'u-1', 'r-1')
    await client.removeMemberRole('t-1', 'u-1', 'r-1')
    await client.adminUsers({ limit: 10, offset: 20 })
    await client.setUserActive('u-1', false)
    await client.deleteUser('u-1')
    await client.userAudit('u-1')
    await client.permissions()
    await client.createGlobalRole('Auditeur', null)
    await client.addGlobalRolePermission('r-1', 'p-1')
    await client.removeGlobalRolePermission('r-1', 'p-1')
    await client.notifications({ limit: 30 })
    await client.markNotificationRead('n-1')
    await client.markAllNotificationsRead()
    await client.deleteNotification('n-1')
    expect(requests.map((r) => [r.method, r.url, r.body])).toEqual([
      ['POST', '/app/auth/password/set', { new_password: 'Nouveau-Passw0rd1' }],
      ['GET', '/app/auth/oauth/me/accounts', undefined],
      ['DELETE', '/app/auth/oauth/git%20hub/unlink', undefined],
      ['GET', '/app/auth/invites/me', undefined],
      ['GET', '/app/auth/tenants/', undefined],
      ['POST', '/app/auth/tenants/', { name: 'Acme', slug: 'acme' }],
      ['PATCH', '/app/auth/tenants/t-1', { name: 'Acme SA' }],
      ['PUT', '/app/auth/tenants/t-1/settings', { settings: { a: 1 } }],
      ['DELETE', '/app/auth/tenants/t-1', undefined],
      ['POST', '/app/auth/rbac/tenants/t-1/members/u-1/roles', { role_id: 'r-1' }],
      ['DELETE', '/app/auth/rbac/tenants/t-1/members/u-1/roles/r-1', undefined],
      ['GET', '/app/auth/admin/users?limit=10&offset=20', undefined],
      ['PATCH', '/app/auth/admin/users/u-1', { is_active: false }],
      ['DELETE', '/app/auth/admin/users/u-1', undefined],
      ['GET', '/app/auth/audit/users/u-1', undefined],
      ['GET', '/app/auth/rbac/permissions', undefined],
      ['POST', '/app/auth/rbac/roles', { name: 'Auditeur', description: null }],
      ['POST', '/app/auth/rbac/roles/r-1/permissions', { permission_id: 'p-1' }],
      ['DELETE', '/app/auth/rbac/roles/r-1/permissions/p-1', undefined],
      ['GET', '/app/auth/notifications?limit=30', undefined],
      ['PATCH', '/app/auth/notifications/n-1/read', undefined],
      ['PATCH', '/app/auth/notifications/read-all', undefined],
      ['DELETE', '/app/auth/notifications/n-1', undefined],
    ])
  })

  it('les réglages d’une organisation sont rendus sans leur enveloppe', async () => {
    const requests: HttpRequest[] = []
    const transport: Transport = { async request(req) { requests.push(req); return { status: 200, json: { tenant_id: 't-1', settings: { a: 1 } } } } }
    const client = new XauthClient(transport, () => ({ baseUrl: '/app/auth', token: 'tok', tenant: 't-1' }))
    expect(await client.tenantSettings('t-1')).toEqual({ a: 1 })
  })
})

describe('XauthClient : nom d’utilisateur et code OTP', () => {
  it('le nom d’utilisateur se change avec le mot de passe, le serveur répond le nom rangé', async () => {
    const requests: HttpRequest[] = []
    const transport: Transport = { async request(req) { requests.push(req); return { status: 200, json: { username: 'chef' } } } }
    const client = new XauthClient(transport, () => ({ baseUrl: '/app/auth', token: 'tok', tenant: 't-1' }))
    expect(await client.changeUsername('Chef', 'pw')).toBe('chef')
    expect(requests[0]).toMatchObject({ method: 'PATCH', url: '/app/auth/account/username', body: { username: 'Chef', password: 'pw' } })
    expect(requests[0]!.body).not.toHaveProperty('opt_code')
  })

  it('le code OTP part (sous le nom `opt_code`, celui du serveur) seulement quand il est donné', async () => {
    const { client, requests } = setup()
    await client.changePassword('a', 'b', '123456')
    await client.changeEmail('x@acme.test', 'pw', '123456')
    await client.changeUsername('chef', 'pw', '123456')
    await client.changePassword('a', 'b')
    expect(requests.map((r) => r.body)).toEqual([
      { current_password: 'a', new_password: 'b', opt_code: '123456' },
      { new_email: 'x@acme.test', password: 'pw', opt_code: '123456' },
      { username: 'chef', password: 'pw', opt_code: '123456' },
      { current_password: 'a', new_password: 'b' },
    ])
  })
})
