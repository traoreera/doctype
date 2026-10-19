import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import { ConfirmationEventBus } from 'primevue'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AccountSettings from '@/components/admin/AccountSettings.vue'
import OrganisationSettings from '@/components/admin/OrganisationSettings.vue'
import PermissionsCatalog from '@/components/admin/PermissionsCatalog.vue'
import PlatformAccounts from '@/components/admin/PlatformAccounts.vue'
import UsersSettings from '@/components/admin/UsersSettings.vue'
import NotificationsMenu from '@/components/NotificationsMenu.vue'
import { ApiError } from '@/lib/api'
import { SERVICES } from '@/lib/services'
import { decodeEntities, safeTarget, useNotifications } from '@/stores/notifications'

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
const token = (extra: Record<string, unknown> = {}) => `${b64({ sub: 'u-me', user: { email: 'me@acme.test' }, tenant_id: 't-1', exp: Math.floor(Date.now() / 1000) + 600, ...extra })}.sig`
beforeEach(() => {
  sessionStorage.clear()
  sessionStorage.setItem('xdt.desk.token', token())
})

type Api = Record<string, (...a: never[]) => unknown>
const router = () => createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'home', component: { render: () => null } }, { path: '/invite', name: 'invite', component: { render: () => null } }, { path: '/settings/:doctype?', name: 'settings', component: { render: () => null } }] })
const provide = (auth: Api | null) => ({ [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/xcore-doctype', loginUrl: '/app/auth/login' }, api: { doctypes: async () => [], workspaces: async () => [] }, auth } })
function setup(component: unknown, auth: Api): VueWrapper {
  return mount(component as never, { attachTo: document.body, global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router()], provide: provide(auth) } }) as unknown as VueWrapper
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
const type = async (selector: string, value: string) => {
  const el = document.querySelector<HTMLInputElement>(selector)!
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

const ME = { id: 'u-me', email: 'me@acme.test', is_active: true, mfa_enabled: false, has_password: true }

describe('Mon compte : la suite', () => {
  const base = (over: Api = {}): Api => ({ me: async () => ME, sessions: async () => [], linkedAccounts: async () => [], myInvites: async () => [], ...over })

  it('un compte sans mot de passe (créé par un fournisseur) en DÉFINIT un, sans demander l’ancien', async () => {
    const setPassword = vi.fn(async () => undefined)
    const w = setup(AccountSettings, base({ me: async () => ({ ...ME, has_password: false }), setPassword }))
    await flushPromises()
    expect(w.find('[data-testid="account-password"]').exists()).toBe(false)
    await w.find('#pw-next').setValue('Nouveau-Passw0rd1')
    await w.find('#pw-confirm').setValue('Autre-Passw0rd1')
    await click(w, 'password-set')
    expect(w.find('[data-testid="password-problem"]').text()).toContain('pas identiques')
    expect(setPassword).not.toHaveBeenCalled()
    await w.find('#pw-confirm').setValue('Nouveau-Passw0rd1')
    await click(w, 'password-set')
    expect(setPassword).toHaveBeenCalledWith('Nouveau-Passw0rd1')
  })

  it('un compte avec mot de passe garde le formulaire de changement', async () => {
    const w = setup(AccountSettings, base())
    await flushPromises()
    expect(w.find('[data-testid="account-password"]').exists()).toBe(true)
    expect(w.find('[data-testid="account-set-password"]').exists()).toBe(false)
  })

  it('les comptes liés se listent et se délient ; sans eux, le dit', async () => {
    const unlinkProvider = vi.fn(async () => undefined)
    const linkedAccounts = vi.fn(async () => [{ provider: 'github', provider_email: 'moi@github.test', provider_name: null, provider_avatar: null, linked_at: '2026-10-01T00:00:00Z' }])
    const w = setup(AccountSettings, base({ linkedAccounts, unlinkProvider }))
    await flushPromises()
    expect(w.find('[data-testid="linked-account"]').text()).toContain('github')
    await click(w, 'linked-unlink')
    expect(unlinkProvider).toHaveBeenCalledWith('github')
    const none = setup(AccountSettings, base())
    await flushPromises()
    expect(none.find('[data-testid="linked-none"]').exists()).toBe(true)
  })

  it('les invitations reçues mènent à l’écran d’invitation avec leur jeton', async () => {
    const myInvites = async () => [{ id: 'i1', tenant_id: 't-9', tenant_name: 'Acme Corp', email: 'me@acme.test', token: 'tok-9', role_id: null, expires_at: '2026-10-30T00:00:00Z', used_at: null, is_active: true, invited_by: 'u-x' }]
    const w = setup(AccountSettings, base({ myInvites }))
    await flushPromises()
    const row = w.find('[data-testid="received-invite"]')
    expect(row.text()).toContain('Acme Corp')
    expect(row.find('[data-testid="received-invite-open"]').attributes('href')).toBe('/invite?token=tok-9')
  })

  it('une panne des comptes liés ou des invitations n’empêche pas le reste de s’afficher', async () => {
    const w = setup(AccountSettings, base({ linkedAccounts: async () => { throw new Error('hors ligne') }, myInvites: async () => { throw new Error('hors ligne') } }))
    await flushPromises()
    expect(w.find('[data-testid="account-email"]').text()).toBe('me@acme.test')
    expect(w.find('[data-testid="admin-failed"]').exists()).toBe(false)
  })
})

describe('Mon compte : nom d’utilisateur et code OTP', () => {
  const base = (over: Api = {}): Api => ({ me: async () => ME, sessions: async () => [], linkedAccounts: async () => [], myInvites: async () => [], ...over })

  it('sans nom, le dit ; avec un nom, l’affiche', async () => {
    const none = setup(AccountSettings, base())
    await flushPromises()
    expect(none.find('[data-testid="account-username-none"]').exists()).toBe(true)
    expect(none.find('[data-testid="account-username-change"]').text()).toBe('Choisir un nom')
    const named = setup(AccountSettings, base({ me: async () => ({ ...ME, username: 'chef' }) }))
    await flushPromises()
    expect(named.find('[data-testid="account-username"]').text()).toBe('chef')
    expect(named.find('[data-testid="account-username-change"]').text()).toBe('Changer de nom')
  })

  it('choisir un nom : il doit respecter la règle et le mot de passe est exigé avant l’envoi', async () => {
    const changeUsername = vi.fn(async () => 'chef')
    const w = setup(AccountSettings, base({ changeUsername }))
    await flushPromises()
    await click(w, 'account-username-change')
    const save = () => document.querySelector<HTMLButtonElement>('[data-testid="username-save"]')!
    await type('[data-testid="username-new"]', 'Pas Valide')
    await type('#username-password', 'Secret-Passw0rd1')
    expect(save().disabled).toBe(true)
    await type('[data-testid="username-new"]', ' Chef ')
    expect(save().disabled).toBe(false) // la casse et les espaces ne comptent pas : le serveur range en minuscules
    await type('#username-password', '')
    expect(save().disabled).toBe(true)
    await type('#username-password', 'Secret-Passw0rd1')
    save().click()
    await flushPromises()
    expect(changeUsername).toHaveBeenCalledWith('Chef', 'Secret-Passw0rd1', undefined)
  })

  it('un refus du serveur (nom pris) reste dans la fenêtre', async () => {
    const changeUsername = vi.fn(async () => {
      throw new ApiError(409, 'http_409', 'Ce nom d’utilisateur est déjà pris')
    })
    const w = setup(AccountSettings, base({ changeUsername }))
    await flushPromises()
    await click(w, 'account-username-change')
    await type('[data-testid="username-new"]', 'rita')
    await type('#username-password', 'Secret-Passw0rd1')
    document.querySelector<HTMLButtonElement>('[data-testid="username-save"]')!.click()
    await flushPromises()
    expect(document.querySelector('[data-testid="username-problem"]')?.textContent).toContain('déjà pris')
  })

  it('un compte sans mot de passe (fournisseur externe) ne propose pas de nom : le serveur exige le mot de passe', async () => {
    const w = setup(AccountSettings, base({ me: async () => ({ ...ME, has_password: false }) }))
    await flushPromises()
    expect(w.find('[data-testid="account-username-change"]').exists()).toBe(false)
  })

  it('avec la double authentification, le code est demandé et part avec le changement (mot de passe, nom, adresse)', async () => {
    const changePassword = vi.fn(async () => undefined)
    const changeUsername = vi.fn(async () => 'chef')
    const changeEmail = vi.fn(async () => undefined)
    const w = setup(AccountSettings, base({ me: async () => ({ ...ME, mfa_enabled: true }), changePassword, changeUsername, changeEmail }))
    await flushPromises()
    await w.find('#pw-current').setValue('Ancien-Passw0rd')
    await w.find('#pw-next').setValue('Nouveau-Passw0rd1')
    await w.find('#pw-confirm').setValue('Nouveau-Passw0rd1')
    await w.find('[data-testid="password-otp"]').setValue(' 123456 ')
    await click(w, 'password-save')
    expect(changePassword).toHaveBeenCalledWith('Ancien-Passw0rd', 'Nouveau-Passw0rd1', '123456')

    await click(w, 'account-username-change')
    await type('[data-testid="username-new"]', 'chef')
    await type('#username-password', 'Secret-Passw0rd1')
    await type('[data-testid="username-otp"]', '654321')
    document.querySelector<HTMLButtonElement>('[data-testid="username-save"]')!.click()
    await flushPromises()
    expect(changeUsername).toHaveBeenCalledWith('chef', 'Secret-Passw0rd1', '654321')
  })

  it('sans double authentification, aucun champ de code', async () => {
    const w = setup(AccountSettings, base())
    await flushPromises()
    expect(w.find('[data-testid="password-otp"]').exists()).toBe(false)
  })
})

const TENANTS = [
  { id: 't-1', name: 'Acme', slug: 'acme', created_at: '2026-01-01T00:00:00Z', is_owner: true, license_state: 'trial' },
  { id: 't-2', name: 'Globex', slug: 'globex', created_at: '2026-02-01T00:00:00Z', is_owner: false, license_state: 'trial' },
]

describe('Organisation', () => {
  const base = (over: Api = {}): Api => ({ tenants: async () => TENANTS, tenantSettings: async () => ({ theme: 'bleu' }), ...over })

  it('liste mes organisations, la courante et celles que je possède', async () => {
    const w = setup(OrganisationSettings, base())
    await flushPromises()
    const rows = w.findAll('[data-testid="organisation"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('courante')
    expect(rows[0]!.text()).toContain('propriétaire')
    expect(rows[0]!.find('[data-testid="organisation-open"]').exists()).toBe(false)
    expect(rows[1]!.find('[data-testid="organisation-open"]').exists()).toBe(true)
  })

  it('le propriétaire renomme ; un non-propriétaire ne voit qu’un message', async () => {
    const renameTenant = vi.fn(async () => TENANTS[0]!)
    const w = setup(OrganisationSettings, base({ renameTenant }))
    await flushPromises()
    expect(w.find('[data-testid="organisation-rename"]').attributes('disabled')).toBeDefined() // rien n'a changé
    await w.find('[data-testid="organisation-name"]').setValue('  Acme SA ')
    await click(w, 'organisation-rename')
    expect(renameTenant).toHaveBeenCalledWith('t-1', 'Acme SA')

    sessionStorage.setItem('xdt.desk.token', token({ tenant_id: 't-2' }))
    const other = setup(OrganisationSettings, base())
    await flushPromises()
    expect(other.find('[data-testid="organisation-owner-only"]').exists()).toBe(true)
    expect(other.find('[data-testid="organisation-rename"]').exists()).toBe(false)
  })

  it('les réglages libres : un objet JSON, sinon refusé avant l’envoi', async () => {
    const saveTenantSettings = vi.fn(async () => undefined)
    const w = setup(OrganisationSettings, base({ saveTenantSettings }))
    await flushPromises()
    const area = w.find('[data-testid="organisation-settings-text"]')
    expect((area.element as HTMLTextAreaElement).value).toContain('"theme": "bleu"')
    await area.setValue('[1, 2]')
    await click(w, 'organisation-settings-save')
    expect(w.find('[data-testid="organisation-settings-problem"]').exists()).toBe(true)
    await area.setValue('{ pas du json')
    await click(w, 'organisation-settings-save')
    expect(saveTenantSettings).not.toHaveBeenCalled()
    await area.setValue('{"theme": "vert"}')
    await click(w, 'organisation-settings-save')
    expect(saveTenantSettings).toHaveBeenCalledWith('t-1', { theme: 'vert' })
  })

  it('créer une organisation : l’identifiant court est proposé et contrôlé', async () => {
    const createTenant = vi.fn(async () => ({ ...TENANTS[0]!, id: 't-3', name: 'Été Corp', slug: 'ete-corp' }))
    const w = setup(OrganisationSettings, base({ createTenant }))
    await flushPromises()
    await click(w, 'organisation-create-open')
    await type('[data-testid="organisation-new-name"]', 'Été Corp')
    document.querySelector<HTMLInputElement>('[data-testid="organisation-new-name"]')!.dispatchEvent(new Event('blur'))
    await flushPromises()
    expect(document.querySelector<HTMLInputElement>('[data-testid="organisation-new-slug"]')!.value).toBe('ete-corp')
    await type('[data-testid="organisation-new-slug"]', 'Pas Valide')
    expect(document.querySelector<HTMLButtonElement>('[data-testid="organisation-create"]')!.disabled).toBe(true)
    await type('[data-testid="organisation-new-slug"]', 'ete-corp')
    document.querySelector<HTMLButtonElement>('[data-testid="organisation-create"]')!.click()
    await flushPromises()
    expect(createTenant).toHaveBeenCalledWith('Été Corp', 'ete-corp')
  })

  it('la suppression n’est proposée qu’à l’administrateur de la plateforme, et demande confirmation', async () => {
    const deleteTenant = vi.fn(async () => undefined)
    expect(setup(OrganisationSettings, base()).find('[data-testid="organisation-danger"]').exists()).toBe(false)
    sessionStorage.setItem('xdt.desk.token', token({ permissions: ['admin:*'] }))
    const w = setup(OrganisationSettings, base({ deleteTenant }))
    await flushPromises()
    await confirmWith(false, () => click(w, 'organisation-delete'))
    expect(deleteTenant).not.toHaveBeenCalled()
    await confirmWith(true, () => click(w, 'organisation-delete'))
    expect(deleteTenant).toHaveBeenCalledWith('t-1')
  })
})

const ACCOUNTS = [
  { id: 'u-me', email: 'me@acme.test', is_active: true, mfa_enabled: true, created_at: '2026-01-01T00:00:00Z' },
  { id: 'u-rita', email: 'rita@acme.test', is_active: true, mfa_enabled: false, created_at: '2026-02-01T00:00:00Z' },
  { id: 'u-off', email: 'off@acme.test', is_active: false, mfa_enabled: false, created_at: '2026-03-01T00:00:00Z' },
]

describe('Comptes de la plateforme', () => {
  it('réservé à l’administrateur de la plateforme', async () => {
    const w = setup(PlatformAccounts, { adminUsers: async () => { throw forbidden() } })
    await flushPromises()
    expect(w.find('[data-testid="admin-forbidden"]').text()).toContain('plateforme')
  })

  it('ni désactiver ni supprimer son propre compte ; désactiver, réactiver, supprimer les autres', async () => {
    const setUserActive = vi.fn(async () => ACCOUNTS[1]!)
    const deleteUser = vi.fn(async () => undefined)
    const w = setup(PlatformAccounts, { adminUsers: async () => ACCOUNTS, setUserActive, deleteUser })
    await flushPromises()
    const rows = w.findAll('[data-testid="account"]')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.text()).toContain('—') // sans nom d'utilisateur
    expect(rows[0]!.find('[data-testid="account-disable"]').exists()).toBe(false)
    expect(rows[0]!.find('[data-testid="account-delete"]').exists()).toBe(false)
    await rows[1]!.find('[data-testid="account-disable"]').trigger('click')
    expect(setUserActive).toHaveBeenCalledWith('u-rita', false)
    await rows[2]!.find('[data-testid="account-enable"]').trigger('click')
    expect(setUserActive).toHaveBeenCalledWith('u-off', true)
    await confirmWith(false, async () => void (await rows[1]!.find('[data-testid="account-delete"]').trigger('click')))
    expect(deleteUser).not.toHaveBeenCalled()
    await confirmWith(true, async () => void (await rows[1]!.find('[data-testid="account-delete"]').trigger('click')))
    expect(deleteUser).toHaveBeenCalledWith('u-rita')
  })

  it('« Voir plus » lit la page suivante quand la première est pleine', async () => {
    const full = Array.from({ length: 50 }, (_, i) => ({ ...ACCOUNTS[1]!, id: `u-${i}`, email: `u${i}@acme.test` }))
    const adminUsers = vi.fn(async (page: { offset?: number }) => (page.offset ? [ACCOUNTS[2]!] : full))
    const w = setup(PlatformAccounts, { adminUsers } as unknown as Api)
    await flushPromises()
    await click(w, 'accounts-more')
    expect(adminUsers).toHaveBeenLastCalledWith({ limit: 50, offset: 50 })
    expect(w.findAll('[data-testid="account"]')).toHaveLength(51)
  })

  it('l’activité d’un compte s’ouvre dans une fenêtre', async () => {
    const userAudit = vi.fn(async () => [{ id: 'e1', user_id: 'u-rita', action: 'login.success', resource: 'session', resource_id: null, ip_address: '10.0.0.9', user_agent: null, created_at: '2026-10-19T10:00:00Z' }])
    const w = setup(PlatformAccounts, { adminUsers: async () => ACCOUNTS, userAudit })
    await flushPromises()
    await w.findAll('[data-testid="account-activity"]')[1]!.trigger('click')
    await flushPromises()
    expect(userAudit).toHaveBeenCalledWith('u-rita')
    expect(document.querySelector('[data-testid="activity-entry"]')?.textContent).toContain('login.success')
  })
})

const PERMS = [
  { id: 'p1', name: 'sales:invoice:read', description: 'Consulter Facture', source_plugin: 'sales', tenant_grantable: true, group: 'sales-invoice', active: true },
  { id: 'p2', name: 'stock:item:write', description: 'Créer/modifier Article', source_plugin: 'stock', tenant_grantable: false, group: 'stock-item', active: true },
]

describe('Catalogue des permissions', () => {
  const ROLE = { id: 'r1', name: 'admin', tenant_id: null, description: null, permissions: [PERMS[0]!] }
  const base = (over: Api = {}): Api => ({ permissions: async () => PERMS, globalRoles: async () => [ROLE], ...over })

  it('groupées par plugin, filtrables, avec ce qu’une organisation peut déléguer', async () => {
    const w = setup(PermissionsCatalog, base())
    await flushPromises()
    expect(w.findAll('[data-testid="permission-group"]')).toHaveLength(2)
    expect(w.find('[data-testid="permission-count"]').text()).toBe('2 permissions')
    expect(w.findAll('[data-testid="permission"]')[0]!.text()).toContain('délégable')
    await w.find('[data-testid="permission-filter"]').setValue('article')
    expect(w.findAll('[data-testid="permission"]')).toHaveLength(1)
    await w.find('[data-testid="permission-filter"]').setValue('zzz')
    expect(w.find('[data-testid="permission-none"]').exists()).toBe(true)
  })

  it('réservé à l’administrateur de la plateforme', async () => {
    const w = setup(PermissionsCatalog, base({ permissions: async () => { throw forbidden() } }))
    await flushPromises()
    expect(w.find('[data-testid="admin-forbidden"]').exists()).toBe(true)
  })

  it('un rôle de la plateforme reçoit ou perd une permission PAR IDENTIFIANT', async () => {
    const addGlobalRolePermission = vi.fn(async () => ROLE)
    const removeGlobalRolePermission = vi.fn(async () => ROLE)
    const w = setup(PermissionsCatalog, base({ addGlobalRolePermission, removeGlobalRolePermission }))
    await flushPromises()
    await click(w, 'global-role-edit')
    await w.find('#gp-r1-p1').trigger('click')
    await flushPromises()
    expect(removeGlobalRolePermission).toHaveBeenCalledWith('r1', 'p1')
    await w.find('#gp-r1-p2').trigger('click')
    await flushPromises()
    expect(addGlobalRolePermission).toHaveBeenCalledWith('r1', 'p2')
  })

  it('créer un rôle de la plateforme', async () => {
    const createGlobalRole = vi.fn(async () => ROLE)
    const w = setup(PermissionsCatalog, base({ createGlobalRole }))
    await flushPromises()
    await click(w, 'global-role-create-open')
    await type('[data-testid="global-role-name"]', 'Auditeur')
    document.querySelector<HTMLButtonElement>('[data-testid="global-role-save"]')!.click()
    await flushPromises()
    expect(createGlobalRole).toHaveBeenCalledWith('Auditeur', null)
  })
})

describe('Utilisateurs : rôles supplémentaires', () => {
  const ROLES = [
    { id: 'r-admin', name: 'admin', tenant_id: null, description: null, permissions: [] },
    { id: 'r-user', name: 'user', tenant_id: null, description: null, permissions: [] },
  ]
  it('le rôle principal est figé, les autres s’ajoutent et se retirent, puis les droits effectifs sont relus', async () => {
    let roleIds = ['r-user']
    const addMemberRole = vi.fn(async (_t: string, _u: string, r: string) => void (roleIds = [...roleIds, r]))
    const removeMemberRole = vi.fn(async (_t: string, _u: string, r: string) => void (roleIds = roleIds.filter((x) => x !== r)))
    const memberPermissions = vi.fn(async () => [])
    const w = setup(UsersSettings, {
      members: async () => [{ user_id: 'u-rita', email: 'rita@acme.test', primary_role_id: 'r-user', role_ids: roleIds, is_owner: false }],
      globalRoles: async () => ROLES,
      tenantRoles: async () => [],
      invites: async () => [],
      memberPermissions,
      grantable: async () => [],
      addMemberRole,
      removeMemberRole,
    } as unknown as Api)
    await flushPromises()
    await click(w, 'member-permissions')
    const dialog = () => document.querySelector('[data-testid="member-roles"]')!
    expect(dialog().querySelector<HTMLInputElement>('[data-testid="role-user"] input')!.disabled).toBe(true) // principal
    dialog().querySelector<HTMLInputElement>('[data-testid="role-admin"] input')!.click()
    await flushPromises()
    expect(addMemberRole).toHaveBeenCalledWith('t-1', 'u-rita', 'r-admin')
    expect(memberPermissions).toHaveBeenCalledTimes(2) // à l'ouverture, puis après le changement
    expect(dialog().querySelector<HTMLInputElement>('[data-testid="role-admin"] input')!.checked).toBe(true)
    dialog().querySelector<HTMLInputElement>('[data-testid="role-admin"] input')!.click()
    await flushPromises()
    expect(removeMemberRole).toHaveBeenCalledWith('t-1', 'u-rita', 'r-admin')
  })
})

describe('Notifications', () => {
  const N = (id: string, over: Record<string, unknown> = {}) => ({ id, user_id: 'u-me', tenant_id: 't-1', title: `Titre ${id}`, message: null, type: 'SYSTEM', link: null, is_read: false, created_at: '2026-10-19T10:00:00Z', ...over })

  it('le texte échappé par le serveur s’affiche tel qu’il a été écrit', () => {
    expect(decodeEntities('Tom &amp; Jerry &lt;b&gt; &quot;x&quot; l&#x27;été')).toBe('Tom & Jerry <b> "x" l\'été')
    expect(decodeEntities(null)).toBe('')
    expect(decodeEntities('&amp;lt;')).toBe('&lt;') // une seule passe : pas de double décodage
  })

  it('un lien ne mène que dans le Desk ou en https : tout le reste est une donnée d’autrui', () => {
    expect(safeTarget('#/app/Invoice/FA-1')).toEqual({ kind: 'desk', path: '/app/Invoice/FA-1' })
    expect(safeTarget('https://acme.test/x')).toEqual({ kind: 'external', url: 'https://acme.test/x' })
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'http://acme.test', '/app/auth/logout', '//evil.test', '#', null, '']) expect(safeTarget(bad)).toBeNull()
  })

  function store(auth: Api) {
    const pinia = createPinia()
    setActivePinia(pinia)
    return mount({ render: () => null }, { global: { plugins: [pinia], provide: provide(auth) } }) && useNotifications()
  }

  it('lire, tout lire, supprimer : l’écran suit tout de suite, un refus le remet', async () => {
    const markNotificationRead = vi.fn(async () => undefined)
    const markAllNotificationsRead = vi.fn(async () => { throw new Error('hors ligne') })
    const deleteNotification = vi.fn(async () => { throw new Error('hors ligne') })
    const s = store({ notifications: async () => [N('a'), N('b')], markNotificationRead, markAllNotificationsRead, deleteNotification })
    await s.refresh()
    expect(s.unread).toBe(2)
    await s.markRead('a')
    expect(s.unread).toBe(1)
    expect(markNotificationRead).toHaveBeenCalledWith('a')
    await s.markRead('a')
    expect(markNotificationRead).toHaveBeenCalledTimes(1) // déjà lue : rien à envoyer
    await s.markAll()
    expect(s.unread).toBe(1) // refusé : remis comme avant
    await s.dismiss('b')
    expect(s.items.map((n) => n.id)).toEqual(['a', 'b']) // refusé : la notification reste
  })

  function menu(auth: Api) {
    const r = router()
    const pinia = createPinia()
    return { r, w: mount(NotificationsMenu, { attachTo: document.body, global: { plugins: [PrimeVue, pinia, r], provide: provide(auth) } }) }
  }

  it('la cloche porte le nombre de non lues, la liste s’ouvre, un lien du Desk est suivi', async () => {
    const markNotificationRead = vi.fn(async () => undefined)
    const { r, w } = menu({ notifications: async () => [N('a', { link: '#/settings', title: 'Tom &amp; Jerry' }), N('b', { is_read: true, link: 'javascript:alert(1)' })], markNotificationRead })
    await flushPromises()
    expect(w.find('[data-testid="notifications-count"]').text()).toBe('1')
    await w.find('[data-testid="notifications-open"]').trigger('click')
    await flushPromises()
    const items = document.querySelectorAll<HTMLElement>('[data-testid="notification"]')
    expect(items).toHaveLength(2)
    expect(items[0]!.textContent).toContain('Tom & Jerry')
    expect(items[1]!.querySelector<HTMLButtonElement>('.notif-body')!.disabled).toBe(true) // lue et sans destination sûre
    items[0]!.querySelector<HTMLButtonElement>('.notif-body')!.click()
    await flushPromises()
    expect(markNotificationRead).toHaveBeenCalledWith('a')
    expect(r.currentRoute.value.path).toBe('/settings')
  })

  it('sans notification, le dit ; une panne le dit aussi', async () => {
    const empty = menu({ notifications: async () => [] })
    await flushPromises()
    await empty.w.find('[data-testid="notifications-open"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-testid="notifications-empty"]')).not.toBeNull()
    expect(empty.w.find('[data-testid="notifications-count"]').exists()).toBe(false)
  })
})
