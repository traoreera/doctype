import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import { ConfirmationEventBus } from 'primevue'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AccountSettings from '@/components/admin/AccountSettings.vue'
import ApiSettings from '@/components/admin/ApiSettings.vue'
import AuditSettings from '@/components/admin/AuditSettings.vue'
import MailSettings from '@/components/admin/MailSettings.vue'
import RolesSettings from '@/components/admin/RolesSettings.vue'
import UsersSettings from '@/components/admin/UsersSettings.vue'
import { ApiError } from '@/lib/api'
import { SERVICES } from '@/lib/services'
import SettingsView from '@/views/SettingsView.vue'
import { flushPromises as flush } from './views-fixtures'

afterEach(() => {
  document.body.innerHTML = ''
})

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
beforeEach(() => {
  sessionStorage.clear()
  sessionStorage.setItem('xdt.desk.token', `${b64({ sub: 'u-me', user: { email: 'me@acme.test' }, tenant_id: 't-1', exp: Math.floor(Date.now() / 1000) + 600 })}.sig`)
})

type Api = Record<string, (...a: never[]) => unknown>
function setup(component: unknown, auth: Api, extra: { api?: Api; props?: Record<string, unknown>; route?: string } = {}): VueWrapper {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/:doctype?', name: 'settings', component: { render: () => null } }, { path: '/', name: 'home', component: { render: () => null } }] })
  return mount(component as never, {
    props: (extra.props ?? {}) as never,
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/xcore-doctype', loginUrl: '/app/auth/login' }, api: { doctypes: async () => [], workspaces: async () => [], ...(extra.api ?? {}) }, auth } },
    },
  }) as unknown as VueWrapper
}
const forbidden = () => new ApiError(403, 'http_403', 'Accès refusé')
const click = async (w: VueWrapper, id: string) => {
  await w.find(`[data-testid="${id}"]`).trigger('click')
  await flushPromises()
}
const confirmWith = async (accept: boolean, action: () => Promise<void>) => {
  const on = (o: { accept?: () => void; reject?: () => void }) => (accept ? o.accept : o.reject)?.()
  ConfirmationEventBus.on('confirm', on as never)
  await action()
  await flushPromises()
  ConfirmationEventBus.off('confirm', on as never)
}

const ME = { id: 'u-me', email: 'me@acme.test', is_active: true, mfa_enabled: false }
const SESSIONS = [
  { id: 's1', tenant_id: 't-1', ip_address: '10.0.0.1', device_fingerprint: null, last_seen: '2026-10-19T10:00:00Z', expires_at: '2026-10-26T10:00:00Z', is_current: true },
  { id: 's2', tenant_id: 't-1', ip_address: '10.0.0.2', device_fingerprint: null, last_seen: '2026-10-18T10:00:00Z', expires_at: '2026-10-25T10:00:00Z', is_current: false },
]

describe('Mon compte', () => {
  const base = (over: Api = {}): Api => ({ me: async () => ME, sessions: async () => SESSIONS, ...over })

  it('montre mon adresse et mes sessions ; la session courante ne se déconnecte pas elle-même', async () => {
    const w = setup(AccountSettings, base())
    await flushPromises()
    expect(w.find('[data-testid="account-email"]').text()).toBe('me@acme.test')
    const rows = w.findAll('[data-testid="session"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.find('[data-testid="session-revoke"]').exists()).toBe(false)
    expect(rows[1]!.find('[data-testid="session-revoke"]').exists()).toBe(true)
  })

  it('déconnecter une session, ou toutes, puis relire', async () => {
    const revokeSession = vi.fn(async () => undefined)
    const revokeAllSessions = vi.fn(async () => undefined)
    const sessions = vi.fn(async () => SESSIONS)
    const w = setup(AccountSettings, base({ revokeSession, revokeAllSessions, sessions }))
    await flushPromises()
    await click(w, 'session-revoke')
    expect(revokeSession).toHaveBeenCalledWith('s2')
    await click(w, 'sessions-revoke-all')
    expect(revokeAllSessions).toHaveBeenCalled()
    expect(sessions.mock.calls.length).toBeGreaterThanOrEqual(3)
  })

  it('un nouveau mot de passe : différent de sa confirmation ou hors règle, il ne part pas ; valide, il part', async () => {
    const changePassword = vi.fn(async () => undefined)
    const w = setup(AccountSettings, base({ changePassword }))
    await flushPromises()
    const type = async (id: string, v: string) => w.find(`#${id}`).setValue(v)
    await type('pw-current', 'Ancien-Passw0rd')
    await type('pw-next', 'Nouveau-Passw0rd1')
    await type('pw-confirm', 'Autre-Passw0rd1')
    await click(w, 'password-save')
    expect(w.find('[data-testid="password-problem"]').text()).toContain('pas identiques')
    await type('pw-next', 'faible')
    await type('pw-confirm', 'faible')
    await click(w, 'password-save')
    expect(w.find('[data-testid="password-problem"]').text()).toContain('majuscule')
    expect(changePassword).not.toHaveBeenCalled()
    await type('pw-next', 'Nouveau-Passw0rd1')
    await type('pw-confirm', 'Nouveau-Passw0rd1')
    await click(w, 'password-save')
    expect(changePassword).toHaveBeenCalledWith('Ancien-Passw0rd', 'Nouveau-Passw0rd1', undefined)
    expect((w.find('#pw-next').element as HTMLInputElement).value).toBe('') // les champs sont vidés
  })

  it('un refus du serveur (mot de passe actuel faux) s’affiche', async () => {
    const changePassword = vi.fn(async () => {
      throw new ApiError(400, 'http_400', 'Mot de passe actuel incorrect')
    })
    const w = setup(AccountSettings, base({ changePassword }))
    await flushPromises()
    await w.find('#pw-current').setValue('Mauvais-Passw0rd1')
    await w.find('#pw-next').setValue('Nouveau-Passw0rd1')
    await w.find('#pw-confirm').setValue('Nouveau-Passw0rd1')
    await click(w, 'password-save')
    expect(w.find('[data-testid="password-problem"]').text()).toContain('incorrect')
  })

  it('activer la double authentification : la clé et les codes de secours s’affichent, puis le code la confirme', async () => {
    let enabled = false
    const mfaSetup = vi.fn(async () => ({ secret: 'ABCD2345', otpauth_url: 'otpauth://totp/x?secret=ABCD2345', qr_code: null, backup_codes: ['AAA111', 'BBB222'] }))
    const mfaEnable = vi.fn(async () => void (enabled = true))
    const w = setup(AccountSettings, base({ me: async () => ({ ...ME, mfa_enabled: enabled }), mfaSetup, mfaEnable }))
    await flushPromises()
    await click(w, 'mfa-start')
    expect((w.find('[data-testid="mfa-secret"]').element as HTMLInputElement).value).toBe('ABCD2345')
    expect(w.find('[data-testid="mfa-backup"]').text()).toContain('AAA111')
    await w.find('[data-testid="mfa-code"]').setValue(' 123456 ')
    await click(w, 'mfa-confirm')
    expect(mfaEnable).toHaveBeenCalledWith('123456')
    expect(w.find('[data-testid="mfa-secret"]').exists()).toBe(false) // le secret n'est plus montré
    expect(w.find('[data-testid="account-mfa"]').text()).toContain('Activée')
  })

  it('désactiver la double authentification demande un code', async () => {
    const mfaDisable = vi.fn(async () => undefined)
    const w = setup(AccountSettings, base({ me: async () => ({ ...ME, mfa_enabled: true }), mfaDisable }))
    await flushPromises()
    expect(w.find('[data-testid="mfa-confirm"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="mfa-code"]').setValue('654321')
    await click(w, 'mfa-confirm')
    expect(mfaDisable).toHaveBeenCalledWith('654321')
  })

  it('un code refusé reste affiché', async () => {
    const mfaEnable = vi.fn(async () => {
      throw new ApiError(400, 'http_400', 'Code invalide')
    })
    const w = setup(AccountSettings, base({ mfaSetup: async () => ({ secret: 's', otpauth_url: 'u', qr_code: null, backup_codes: [] }), mfaEnable }))
    await flushPromises()
    await click(w, 'mfa-start')
    await w.find('[data-testid="mfa-code"]').setValue('000000')
    await click(w, 'mfa-confirm')
    expect(w.find('[data-testid="mfa-problem"]').text()).toContain('invalide')
  })

  it('changer d’adresse envoie la nouvelle adresse ET le mot de passe', async () => {
    const changeEmail = vi.fn(async () => undefined)
    const w = setup(AccountSettings, base({ changeEmail }))
    await flushPromises()
    await click(w, 'account-email-change')
    document.querySelector<HTMLInputElement>('[data-testid="email-new"]')!.value = 'nouvelle@acme.test'
    document.querySelector<HTMLInputElement>('[data-testid="email-new"]')!.dispatchEvent(new Event('input'))
    const pw = document.querySelector<HTMLInputElement>('#email-password')!
    pw.value = 'Secret-Passw0rd1'
    pw.dispatchEvent(new Event('input'))
    await flush()
    document.querySelector<HTMLElement>('[data-testid="email-save"]')!.click()
    await flush()
    expect(changeEmail).toHaveBeenCalledWith('nouvelle@acme.test', 'Secret-Passw0rd1', undefined)
  })
})

const ROLES = [
  { id: 'r-admin', name: 'admin', tenant_id: null, description: 'Tout', permissions: [{ id: 'p1', name: 'sales:invoice:read', description: 'Consulter Facture', source_plugin: 'sales', tenant_grantable: true, group: 'sales-invoice', active: true }] },
  { id: 'r-user', name: 'user', tenant_id: null, description: null, permissions: [] },
]
const GRANTABLE = [
  { id: 'p1', name: 'sales:invoice:read', description: 'Consulter Facture', source_plugin: 'sales', tenant_grantable: true, group: 'sales-invoice', active: true },
  { id: 'p2', name: 'sales:invoice:write', description: 'Créer/modifier Facture', source_plugin: 'sales', tenant_grantable: true, group: 'sales-invoice', active: true },
]
const MEMBERS = [
  { user_id: 'u-me', email: 'me@acme.test', primary_role_id: 'r-admin', role_ids: ['r-admin'], is_owner: true },
  { user_id: 'u-rita', email: 'rita@acme.test', primary_role_id: 'r-user', role_ids: ['r-user'], is_owner: false },
]

describe('Utilisateurs', () => {
  const base = (over: Api = {}): Api => ({ members: async () => MEMBERS, globalRoles: async () => ROLES, tenantRoles: async () => [], invites: async () => [], ...over })

  it('réservé aux administrateurs : un 403 est dit, sans erreur', async () => {
    const w = setup(UsersSettings, base({ members: async () => { throw forbidden() } }))
    await flushPromises()
    expect(w.find('[data-testid="admin-forbidden"]').exists()).toBe(true)
    expect(w.find('[data-testid="admin-failed"]').exists()).toBe(false)
    expect(w.find('[data-testid="members"]').exists()).toBe(false)
  })

  it('une panne autre qu’un refus est dite comme une panne', async () => {
    const w = setup(UsersSettings, base({ members: async () => { throw new Error('hors ligne') } }))
    await flushPromises()
    expect(w.find('[data-testid="admin-failed"]').exists()).toBe(true)
  })

  it('les membres avec leur rôle ; ni changer son propre rôle ni se retirer soi-même, ni le propriétaire', async () => {
    const w = setup(UsersSettings, base())
    await flushPromises()
    const rows = w.findAll('[data-testid="member"]')
    expect(rows.map((r) => r.text())).toEqual([expect.stringContaining('me@acme.test'), expect.stringContaining('rita@acme.test')])
    expect(rows[0]!.text()).toContain('propriétaire')
    expect(rows[0]!.find('[data-testid="member-remove"]').exists()).toBe(false)
    expect(rows[1]!.find('[data-testid="member-remove"]').exists()).toBe(true)
  })

  it('changer le rôle d’un membre', async () => {
    const setMemberRole = vi.fn(async () => undefined)
    const w = setup(UsersSettings, base({ setMemberRole }))
    await flushPromises()
    const select = w.findAllComponents({ name: 'Select' }).find((s) => s.attributes('data-testid') === 'member-role' && s.props('modelValue') === 'r-user')!
    select.vm.$emit('update:modelValue', 'r-admin')
    await flushPromises()
    expect(setMemberRole).toHaveBeenCalledWith('t-1', 'u-rita', 'r-admin')
  })

  it('retirer un membre demande confirmation', async () => {
    const removeMember = vi.fn(async () => undefined)
    const w = setup(UsersSettings, base({ removeMember }))
    await flushPromises()
    await confirmWith(false, () => click(w, 'member-remove'))
    expect(removeMember).not.toHaveBeenCalled()
    await confirmWith(true, () => click(w, 'member-remove'))
    expect(removeMember).toHaveBeenCalledWith('t-1', 'u-rita')
  })

  it('inviter : adresse trimée et rôle ; un refus du serveur reste dans la fenêtre', async () => {
    const invite = vi.fn(async () => ({}))
    const w = setup(UsersSettings, base({ invite }))
    await flushPromises()
    await click(w, 'invite-open')
    const email = document.querySelector<HTMLInputElement>('[data-testid="invite-email"]')!
    email.value = '  nouveau@acme.test '
    email.dispatchEvent(new Event('input'))
    await flush()
    document.querySelector<HTMLElement>('[data-testid="invite-send"]')!.click()
    await flush()
    expect(invite).toHaveBeenCalledWith('t-1', 'nouveau@acme.test', null)
  })

  it('une invitation refusée s’affiche', async () => {
    const invite = vi.fn(async () => {
      throw new ApiError(400, 'http_400', 'Déjà membre')
    })
    const w = setup(UsersSettings, base({ invite }))
    await flushPromises()
    await click(w, 'invite-open')
    const email = document.querySelector<HTMLInputElement>('[data-testid="invite-email"]')!
    email.value = 'rita@acme.test'
    email.dispatchEvent(new Event('input'))
    await flush()
    document.querySelector<HTMLElement>('[data-testid="invite-send"]')!.click()
    await flush()
    expect(document.querySelector('[data-testid="invite-problem"]')?.textContent).toContain('Déjà membre')
  })

  it('les invitations en attente (actives, non utilisées) se révoquent', async () => {
    const revokeInvite = vi.fn(async () => undefined)
    const invites = async () => [
      { id: 'i1', tenant_id: 't-1', email: 'attente@acme.test', token: 'x', role_id: 'r-user', expires_at: '2026-10-30T00:00:00Z', used_at: null, is_active: true, invited_by: 'u-me' },
      { id: 'i2', tenant_id: 't-1', email: 'deja@acme.test', token: 'y', role_id: null, expires_at: '2026-10-30T00:00:00Z', used_at: '2026-10-19T00:00:00Z', is_active: true, invited_by: 'u-me' },
      { id: 'i3', tenant_id: 't-1', email: 'morte@acme.test', token: 'z', role_id: null, expires_at: '2026-10-01T00:00:00Z', used_at: null, is_active: false, invited_by: 'u-me' },
    ]
    const w = setup(UsersSettings, base({ invites, revokeInvite }))
    await flushPromises()
    expect(w.findAll('[data-testid="invitation"]').map((i) => i.text())).toEqual([expect.stringContaining('attente@acme.test')])
    await click(w, 'invitation-revoke')
    expect(revokeInvite).toHaveBeenCalledWith('i1')
  })

  it('les permissions d’un membre : celles de son rôle sont cochées et figées, les autres s’accordent et se retirent', async () => {
    const grantMember = vi.fn(async () => undefined)
    const revokeMember = vi.fn(async () => undefined)
    const w = setup(UsersSettings, base({ memberPermissions: async () => ['sales:invoice:read'], grantable: async () => GRANTABLE, grantMember, revokeMember }))
    await flushPromises()
    const rows = w.findAll('[data-testid="member"]')
    await rows[1]!.find('[data-testid="member-permissions"]').trigger('click')
    await flushPromises()
    const dialog = () => document.querySelector('[data-testid="permissions-dialog"]')!
    expect(dialog().textContent).toContain('Consulter Facture')
    const read = dialog().querySelector<HTMLInputElement>('[data-testid="perm-sales:invoice:read"] input')!
    expect(read.checked).toBe(true) // effective, mais…
    // « sales:invoice:read » n'est PAS dans le rôle `user` de ce membre : c'est un accord direct, qui se retire.
    read.click()
    await flush()
    expect(revokeMember).toHaveBeenCalledWith('t-1', 'u-rita', 'sales:invoice:read')
    const write = dialog().querySelector<HTMLInputElement>('[data-testid="perm-sales:invoice:write"] input')!
    write.click()
    await flush()
    expect(grantMember).toHaveBeenCalledWith('t-1', 'u-rita', 'sales:invoice:write')
  })

  it('une permission donnée par le rôle est cochée et désactivée', async () => {
    const w = setup(UsersSettings, base({ memberPermissions: async () => ['sales:invoice:read'], grantable: async () => GRANTABLE }))
    await flushPromises()
    await w.findAll('[data-testid="member"]')[0]!.find('[data-testid="member-permissions"]').trigger('click') // u-me : rôle admin, qui donne `read`
    await flushPromises()
    const read = document.querySelector<HTMLInputElement>('[data-testid="perm-sales:invoice:read"] input')!
    expect(read.checked && read.disabled).toBe(true)
    expect(document.querySelector('[data-testid="perm-sales:invoice:read"]')!.textContent).toContain('donnée par son rôle')
  })

  it('un refus du serveur remet la case comme elle était', async () => {
    const grantMember = vi.fn(async () => {
      throw forbidden()
    })
    const w = setup(UsersSettings, base({ memberPermissions: async () => [], grantable: async () => GRANTABLE, grantMember }))
    await flushPromises()
    await w.findAll('[data-testid="member"]')[1]!.find('[data-testid="member-permissions"]').trigger('click')
    await flushPromises()
    const write = document.querySelector<HTMLInputElement>('[data-testid="perm-sales:invoice:write"] input')!
    write.click()
    await flush()
    expect(grantMember).toHaveBeenCalled()
    expect(document.querySelector<HTMLInputElement>('[data-testid="perm-sales:invoice:write"] input')!.checked).toBe(false)
  })
})

describe('Rôles', () => {
  const OWN = { id: 'r-own', name: 'Comptable', tenant_id: 't-1', description: 'Factures', permissions: [GRANTABLE[0]!] }
  const base = (over: Api = {}): Api => ({ globalRoles: async () => ROLES, tenantRoles: async () => [OWN, ...ROLES], grantable: async () => GRANTABLE, ...over })

  it('sépare les rôles de la plateforme (lecture seule) de ceux de l’organisation', async () => {
    const w = setup(RolesSettings, base())
    await flushPromises()
    expect(w.findAll('[data-testid="tenant-role"]').map((r) => r.text())).toEqual([expect.stringContaining('Comptable')])
    expect(w.findAll('[data-testid="platform-role"]').map((r) => r.text().split('·')[0]!.trim())).toEqual(['admin', 'user'])
  })

  it('sans rôle propre, le dit', async () => {
    const w = setup(RolesSettings, base({ tenantRoles: async () => ROLES }))
    await flushPromises()
    expect(w.find('[data-testid="roles-empty"]').exists()).toBe(true)
  })

  it('créer un rôle avec ses permissions', async () => {
    const createRole = vi.fn(async () => OWN)
    const w = setup(RolesSettings, base({ createRole }))
    await flushPromises()
    await click(w, 'role-create-open')
    const name = document.querySelector<HTMLInputElement>('[data-testid="role-name"]')!
    name.value = 'Vendeur'
    name.dispatchEvent(new Event('input'))
    await flush()
    document.querySelector<HTMLInputElement>('[data-testid="np-sales:invoice:write"] input')!.click()
    await flush()
    document.querySelector<HTMLElement>('[data-testid="role-save"]')!.click()
    await flush()
    expect(createRole).toHaveBeenCalledWith('t-1', 'Vendeur', null, ['sales:invoice:write'])
  })

  it('le filtre de la fenêtre ne garde que les permissions qui correspondent', async () => {
    const w = setup(RolesSettings, base())
    await flushPromises()
    await click(w, 'role-create-open')
    const filter = document.querySelector<HTMLInputElement>('[data-testid="role-filter"]')!
    filter.value = 'créer'
    filter.dispatchEvent(new Event('input'))
    await flush()
    expect(document.querySelector('[data-testid="np-sales:invoice:write"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="np-sales:invoice:read"]')).toBeNull()
  })

  it('ajouter ou retirer une permission d’un rôle, puis relire', async () => {
    const addRolePermission = vi.fn(async () => OWN)
    const removeRolePermission = vi.fn(async () => OWN)
    const w = setup(RolesSettings, base({ addRolePermission, removeRolePermission }))
    await flushPromises()
    await click(w, 'role-toggle')
    const boxes = w.findAll('[data-testid="role-permissions"] input[type="checkbox"], [data-testid="role-permissions"] input')
    expect(boxes.length).toBeGreaterThan(0)
    const read = w.find('#rp-r-own-sales\\:invoice\\:read')
    await read.trigger('click')
    await flushPromises()
    expect(removeRolePermission).toHaveBeenCalledWith('t-1', 'r-own', 'sales:invoice:read')
    await w.find('#rp-r-own-sales\\:invoice\\:write').trigger('click')
    await flushPromises()
    expect(addRolePermission).toHaveBeenCalledWith('t-1', 'r-own', 'sales:invoice:write')
  })

  it('supprimer un rôle demande confirmation', async () => {
    const deleteRole = vi.fn(async () => undefined)
    const w = setup(RolesSettings, base({ deleteRole }))
    await flushPromises()
    await confirmWith(false, () => click(w, 'role-delete'))
    expect(deleteRole).not.toHaveBeenCalled()
    await confirmWith(true, () => click(w, 'role-delete'))
    expect(deleteRole).toHaveBeenCalledWith('t-1', 'r-own')
  })

  it('un membre non administrateur voit les rôles de la plateforme et un message pour les siens', async () => {
    const w = setup(RolesSettings, base({ tenantRoles: async () => { throw forbidden() } }))
    await flushPromises()
    expect(w.find('[data-testid="admin-forbidden"]').exists()).toBe(true)
    expect(w.find('[data-testid="role-create-open"]').exists()).toBe(false)
    expect(w.findAll('[data-testid="platform-role"]')).toHaveLength(2)
  })
})

describe('Courrier', () => {
  const STATUS = { name: 'email', status: 'ready', configured: false, simulated: true, smtp_host: '', smtp_port: 587, from: 'noreply@example.com', sent: 3, failed: 0, queued: 3, last_sent_at: null, reachable: false, detail: 'SMTP non configuré (envoi simulé)' }

  it('sans serveur SMTP : le mode simulé est dit clairement, avec comment le régler', async () => {
    const w = setup(MailSettings, { mailStatus: async () => STATUS })
    await flushPromises()
    expect(w.find('[data-testid="mail-mode"]').text()).toContain('Simulé')
    expect(w.find('[data-testid="mail-configure"]').exists()).toBe(true)
    expect(w.find('[data-testid="mail-server"]').text()).toBe('—')
    expect(w.find('[data-testid="mail-counters"]').text()).toContain('3 envoyé')
  })

  it('avec un serveur : son adresse et s’il répond', async () => {
    const w = setup(MailSettings, { mailStatus: async () => ({ ...STATUS, configured: true, simulated: false, smtp_host: 'smtp.acme.test', use_tls: true, reachable: false, detail: 'SMTP inaccessible : timeout' }) })
    await flushPromises()
    expect(w.find('[data-testid="mail-server"]').text()).toBe('smtp.acme.test:587 (TLS)')
    expect(w.find('[data-testid="mail-reachable"]').text()).toContain('Non')
    expect(w.find('[data-testid="mail-reachable"]').text()).toContain('timeout')
    expect(w.find('[data-testid="mail-configure"]').exists()).toBe(false)
  })

  it('réservé à l’administrateur de la plateforme', async () => {
    const w = setup(MailSettings, { mailStatus: async () => { throw forbidden() } })
    await flushPromises()
    expect(w.find('[data-testid="admin-forbidden"]').text()).toContain('plateforme')
  })

  it('le message d’essai part vers l’adresse donnée, et le résultat dit s’il est simulé', async () => {
    const mailTest = vi.fn(async () => ({ sent: true, simulated: true }))
    const mailStatus = vi.fn(async () => STATUS)
    const w = setup(MailSettings, { mailStatus, mailTest })
    await flushPromises()
    expect(w.find('[data-testid="mail-send"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="mail-to"]').setValue(' moi@acme.test ')
    await click(w, 'mail-send')
    expect(mailTest).toHaveBeenCalledWith('moi@acme.test')
    expect(mailStatus).toHaveBeenCalledTimes(2) // les compteurs se rafraîchissent
  })
})

describe('Journal d’audit', () => {
  const entry = (i: number, over: Record<string, unknown> = {}) => ({ id: `e${i}`, user_id: 'u-me', action: 'login.success', resource: 'session', resource_id: `abcdef12-${i}`, ip_address: '10.0.0.1', user_agent: null, created_at: '2026-10-19T10:00:00Z', ...over })

  it('les événements, avec le nom de leur auteur', async () => {
    const audit = vi.fn(async () => [entry(1), entry(2, { user_id: null, action: 'user.registered' })])
    const w = setup(AuditSettings, { audit }, { api: {} })
    await flushPromises()
    expect(audit).toHaveBeenCalledWith('t-1', { limit: 50, offset: 0 })
    const rows = w.findAll('[data-testid="audit-entry"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('login.success')
    expect(rows[1]!.text()).toContain('user.registered')
    expect(w.find('[data-testid="audit-more"]').exists()).toBe(false) // moins d'une page : rien de plus
  })

  it('« Voir plus » lit la page suivante quand la première est pleine', async () => {
    const full = Array.from({ length: 50 }, (_, i) => entry(i))
    const audit = vi.fn(async (_t: string, page: { offset?: number }) => (page.offset ? [entry(99)] : full))
    const w = setup(AuditSettings, { audit } as unknown as Api)
    await flushPromises()
    await click(w, 'audit-more')
    expect(audit).toHaveBeenLastCalledWith('t-1', { limit: 50, offset: 50 })
    expect(w.findAll('[data-testid="audit-entry"]')).toHaveLength(51)
  })

  it('réservé aux administrateurs ; vide, le dit', async () => {
    const w = setup(AuditSettings, { audit: async () => { throw forbidden() } })
    await flushPromises()
    expect(w.find('[data-testid="admin-forbidden"]').exists()).toBe(true)
    expect(w.find('[data-testid="audit-empty"]').exists()).toBe(false)
    const empty = setup(AuditSettings, { audit: async () => [] })
    await flushPromises()
    expect(empty.find('[data-testid="audit-empty"]').exists()).toBe(true)
  })
})

describe('Accès API', () => {
  it('les adresses, l’organisation à envoyer et la documentation ; le jeton est masqué jusqu’à ce qu’on le demande', async () => {
    const w = setup(ApiSettings, {})
    await flushPromises()
    expect((w.find('[data-testid="api-base"]').element as HTMLInputElement).value).toMatch(/\/app\/xcore-doctype$/)
    expect((w.find('[data-testid="api-auth"]').element as HTMLInputElement).value).toMatch(/\/app\/auth$/)
    expect((w.find('[data-testid="api-tenant"]').element as HTMLInputElement).value).toBe('t-1')
    expect(w.find('[data-testid="api-docs"]').attributes('href')).toMatch(/\/docs$/)
    expect(w.find('[data-testid="api-docs"]').attributes('rel')).toContain('noopener')
    const value = () => (w.find('[data-testid="api-token-value"]').element as HTMLInputElement).value
    expect(value()).toBe('•'.repeat(24))
    await click(w, 'api-token-toggle')
    expect(value()).toContain('.sig')
    expect(w.find('[data-testid="api-example"]').text()).toContain('X-Tenant-ID: t-1')
    expect(w.find('[data-testid="api-example"]').text()).not.toContain('.sig') // l'exemple n'écrit jamais le jeton lui-même
  })

  it('copier le jeton passe par le presse-papiers', async () => {
    const writeText = vi.fn(async () => undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const w = setup(ApiSettings, {})
    await flushPromises()
    await click(w, 'api-token-copy')
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('.sig'))
    vi.unstubAllGlobals()
  })
})

describe('Paramètres : rubriques système', () => {
  const open = async (path: string, auth: Api | null, permissions: string[] | null = null, summaries: unknown[] = []) => {
    const pinia = createPinia()
    pinia.state.value.session = { permissions, token: sessionStorage.getItem('xdt.desk.token') } // permissions déjà lues (null : pas encore)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/:doctype?', name: 'settings', component: SettingsView, props: true }, { path: '/app/:doctype', name: 'list', component: { render: () => null } }, { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } }] })
    await router.push(path)
    const w = mount({ template: '<RouterView />' }, {
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, pinia, router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/xcore-doctype', loginUrl: auth ? '/app/auth/login' : null }, api: { doctypes: async () => summaries, workspaces: async () => [] }, auth } },
      },
    })
    await flushPromises()
    return w
  }

  it('connecté à xauth : le groupe « Système » et ses rubriques ; celles de la plateforme ne sont proposées qu’à son administrateur', async () => {
    const w = await open('/settings', { me: async () => ME, sessions: async () => [] })
    expect(w.findAll('[data-testid^="system-"]').map((a) => a.text())).toEqual(['Mon compte', 'Organisation', 'Utilisateurs', 'Rôles et permissions', 'Journal d’audit', 'Accès API'])
    const hidden = await open('/settings/~accounts', { me: async () => ME }, [])
    expect(hidden.find('[data-testid="settings-unknown"]').exists()).toBe(true) // une adresse écrite à la main ne contourne pas le menu
    sessionStorage.setItem('xdt.desk.token', `${b64({ sub: 'u-me', user: { email: 'me@acme.test' }, tenant_id: 't-1', permissions: ['admin:*'], exp: Math.floor(Date.now() / 1000) + 600 })}.sig`)
    const admin = await open('/settings', { me: async () => ME })
    expect(admin.findAll('[data-testid^="system-"]').map((a) => a.text())).toEqual(['Mon compte', 'Organisation', 'Utilisateurs', 'Rôles et permissions', 'Catalogue des permissions', 'Comptes de la plateforme', 'Courrier', 'Journal d’audit', 'Accès API'])
    expect(w.find('[data-testid="system-users"]').attributes('href')).toBe('/settings/~users')
  })

  it('quand les DocTypes d’accès existent, membres, rôles et permissions ouvrent leur LISTE ; sinon la page d’administration reste le repli', async () => {
    const types = ['Member', 'Role', 'Permission'].map((name) => ({ name, label: name, module: 'iam' }))
    const w = await open('/settings', { me: async () => ME, sessions: async () => [] }, [], types)
    expect(w.find('[data-testid="system-users"]').attributes('href')).toBe('/app/Member')
    expect(w.find('[data-testid="system-roles"]').attributes('href')).toBe('/app/Role')
    expect(w.find('[data-testid="system-permissions"]').attributes('href')).toBe('/app/Permission') // même sans être administrateur de la plateforme : le serveur filtre ce qu'on voit
    expect(w.find('[data-testid="system-users"] .settings-out').exists()).toBe(true)
    expect(w.find('[data-testid="system-organisation"]').attributes('href')).toBe('/settings/~organisation') // le reste : pages d'xauth
    // Un seul type lisible : les autres retombent sur la page d'administration.
    const some = await open('/settings', { me: async () => ME, sessions: async () => [] }, [], [types[0]!])
    expect(some.find('[data-testid="system-users"]').attributes('href')).toBe('/app/Member')
    expect(some.find('[data-testid="system-roles"]').attributes('href')).toBe('/settings/~roles')
    expect(some.find('[data-testid="system-permissions"]').exists()).toBe(false) // et celle du catalogue reste réservée à la plateforme
  })

  it('sans xauth (jeton collé) : aucune rubrique système, et son adresse est inconnue', async () => {
    const w = await open('/settings', null)
    expect(w.find('[data-testid="settings-group-system"]').exists()).toBe(false)
    const direct = await open('/settings/~users', null)
    expect(direct.find('[data-testid="settings-unknown"]').exists()).toBe(true)
  })

  it('une rubrique s’ouvre sur place, avec son titre ; une clé inconnue est dite', async () => {
    const w = await open('/settings/~account', { me: async () => ME, sessions: async () => SESSIONS })
    expect(w.find('[data-testid="system-title"]').text()).toBe('Mon compte')
    expect(w.find('[data-testid="account-settings"]').exists()).toBe(true)
    const bad = await open('/settings/~nope', { me: async () => ME })
    expect(bad.find('[data-testid="settings-unknown"]').exists()).toBe(true)
  })
})
