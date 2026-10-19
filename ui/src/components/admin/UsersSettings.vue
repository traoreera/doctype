<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref } from 'vue'
import { useAuth, useLoad } from '@/composables/useAuth'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import type { AuthMember, AuthPermission, AuthRole } from '@/lib/xauth'
import { useSession } from '@/stores/session'

/**
 * Utilisateurs de l'organisation (xauth) : qui en est membre, avec quel rôle, quelles permissions en plus ; inviter quelqu'un, révoquer une
 * invitation, retirer un membre. Réservé aux administrateurs de l'organisation : xauth refuse (403) et la page le dit.
 */
const auth = useAuth()
const session = useSession()
const notify = useNotify()
const confirm = useConfirm()
const tenant = computed(() => session.tenant ?? '')

const members = useLoad(() => auth.members(tenant.value))
const roles = useLoad(async () => [...(await auth.globalRoles()), ...(await auth.tenantRoles(tenant.value)).filter((r) => r.tenant_id)])
const invites = useLoad(() => auth.invites(tenant.value))
onMounted(() => void Promise.all([members.reload(), roles.reload(), invites.reload()]))

const roleOptions = computed(() => (roles.data.value ?? []).map((r) => ({ value: r.id, label: r.name })))
const roleName = (id: string | null): string => (roles.data.value ?? []).find((r) => r.id === id)?.name ?? t.admin.users.noRole
const pending = computed(() => (invites.data.value ?? []).filter((i) => i.is_active && !i.used_at))

async function changeRole(m: AuthMember, role: string): Promise<void> {
  try {
    await auth.setMemberRole(tenant.value, m.user_id, role)
    notify.success(t.admin.users.roleChanged)
  } catch (e) {
    notify.error(e)
  }
  await members.reload()
}

function ask(m: AuthMember): void {
  confirm.require({
    header: t.admin.users.remove,
    message: t.admin.users.confirmRemove(m.email ?? m.user_id),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.admin.users.remove,
    rejectLabel: t.admin.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      try {
        await auth.removeMember(tenant.value, m.user_id)
        notify.success(t.admin.users.removed)
      } catch (e) {
        notify.error(e)
      }
      await members.reload()
    },
  })
}

// ── Invitation ────────────────────────────────────────────
const inviteOpen = ref(false)
const invite = ref<{ email: string; role: string | null }>({ email: '', role: null })
const inviteProblem = ref<string | null>(null)

async function sendInvite(): Promise<void> {
  inviteProblem.value = null
  try {
    await auth.invite(tenant.value, invite.value.email.trim(), invite.value.role)
    notify.success(t.admin.users.invited(invite.value.email.trim()))
    inviteOpen.value = false
    invite.value = { email: '', role: null }
    await invites.reload()
  } catch (e) {
    inviteProblem.value = describeError(e)
  }
}

async function revokeInvite(id: string): Promise<void> {
  try {
    await auth.revokeInvite(id)
  } catch (e) {
    notify.error(e)
  }
  await invites.reload()
}

// ── Permissions d'un membre ───────────────────────────────
const permsFor = ref<AuthMember | null>(null)
const effective = ref<Set<string>>(new Set())
const grantable = ref<AuthPermission[]>([])
const permsBusy = ref(false)
const fromRole = computed(() => {
  const ids = new Set(permsFor.value?.role_ids ?? [])
  return new Set((roles.data.value ?? []).filter((r: AuthRole) => ids.has(r.id)).flatMap((r) => r.permissions.map((p) => p.name)))
})
const groups = computed(() => {
  const by = new Map<string, AuthPermission[]>()
  for (const p of grantable.value) by.set(p.group ?? '—', [...(by.get(p.group ?? '—') ?? []), p])
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b, 'fr'))
})

async function openPerms(m: AuthMember): Promise<void> {
  permsFor.value = m
  permsBusy.value = true
  try {
    const [eff, grant] = await Promise.all([auth.memberPermissions(tenant.value, m.user_id), auth.grantable(tenant.value)])
    effective.value = new Set(eff)
    grantable.value = grant
  } catch (e) {
    notify.error(e)
    permsFor.value = null
  } finally {
    permsBusy.value = false
  }
}

/** Les rôles s'ADDITIONNENT : le principal se change dans la liste des membres, les autres s'ajoutent ou se retirent ici. */
async function toggleRole(role: AuthRole, on: boolean): Promise<void> {
  const m = permsFor.value
  if (!m || role.id === m.primary_role_id) return
  try {
    if (on) await auth.addMemberRole(tenant.value, m.user_id, role.id)
    else await auth.removeMemberRole(tenant.value, m.user_id, role.id)
    notify.success(on ? t.admin.users.roleAdded : t.admin.users.roleRemoved)
  } catch (e) {
    notify.error(e)
  }
  await members.reload()
  permsFor.value = (members.data.value ?? []).find((x) => x.user_id === m.user_id) ?? m
  try {
    effective.value = new Set(await auth.memberPermissions(tenant.value, m.user_id))
  } catch {
    // les droits effectifs se relisent à la prochaine ouverture
  }
}

async function toggle(p: AuthPermission, on: boolean): Promise<void> {
  const m = permsFor.value
  if (!m || fromRole.value.has(p.name)) return
  const next = new Set(effective.value)
  on ? next.add(p.name) : next.delete(p.name)
  effective.value = next // l'écran suit tout de suite ; un refus le remet
  try {
    if (on) await auth.grantMember(tenant.value, m.user_id, p.name)
    else await auth.revokeMember(tenant.value, m.user_id, p.name)
  } catch (e) {
    notify.error(e)
    const back = new Set(effective.value)
    on ? back.delete(p.name) : back.add(p.name)
    effective.value = back
  }
}
</script>

<template>
  <div class="admin-page" data-testid="users-settings">
    <Message v-if="members.forbidden.value" severity="info" class="banner-error" data-testid="admin-forbidden">{{ t.admin.adminOnly }}</Message>
    <Message v-else-if="members.error.value" severity="error" class="banner-error" data-testid="admin-failed">{{ members.error.value }}</Message>

    <template v-else>
      <section class="desk-card admin-card">
        <header class="collab-card-head">
          <h3>{{ t.admin.users.members }}</h3>
          <span class="desk-spacer" />
          <Button :label="t.admin.users.invite" icon="pi pi-user-plus" size="small" data-testid="invite-open" @click="inviteOpen = true" />
        </header>
        <table class="admin-table" data-testid="members">
          <thead><tr><th>{{ t.admin.account.email }}</th><th>{{ t.admin.users.role }}</th><th /></tr></thead>
          <tbody>
            <tr v-for="m in members.data.value ?? []" :key="m.user_id" data-testid="member">
              <td>
                {{ m.email ?? m.user_id }}
                <span v-if="m.is_owner" class="status-pill ok">{{ t.admin.users.owner }}</span>
                <span v-if="m.user_id === session.claims?.sub" class="status-pill">{{ t.collab.me }}</span>
              </td>
              <td>
                <Select :model-value="m.primary_role_id" :options="roleOptions" option-label="label" option-value="value" size="small" :placeholder="t.admin.users.noRole" :disabled="m.user_id === session.claims?.sub" data-testid="member-role" @update:model-value="changeRole(m, $event)" />
              </td>
              <td class="admin-actions">
                <Button icon="pi pi-key" text rounded size="small" severity="secondary" :aria-label="t.admin.users.permissions" :title="t.admin.users.permissions" data-testid="member-permissions" @click="openPerms(m)" />
                <Button v-if="m.user_id !== session.claims?.sub && !m.is_owner" icon="pi pi-user-minus" text rounded size="small" severity="danger" :aria-label="t.admin.users.remove" :title="t.admin.users.remove" data-testid="member-remove" @click="ask(m)" />
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="desk-card admin-card" data-testid="invitations">
        <h3>{{ t.admin.users.invitations }}</h3>
        <p v-if="!pending.length" class="desk-muted" data-testid="invitations-empty">{{ t.admin.users.noInvitations }}</p>
        <ul v-else class="admin-list">
          <li v-for="i in pending" :key="i.id" class="admin-row" data-testid="invitation">
            <div>
              <strong>{{ i.email }}</strong> <span class="desk-muted">· {{ roleName(i.role_id) }} · {{ t.admin.users.expiresOn(relativeTime(i.expires_at)) }}</span>
            </div>
            <Button :label="t.admin.users.revokeInvite" text size="small" severity="secondary" data-testid="invitation-revoke" @click="revokeInvite(i.id)" />
          </li>
        </ul>
      </section>
    </template>

    <Dialog v-model:visible="inviteOpen" modal :header="t.admin.users.invite" :style="{ width: '440px', maxWidth: '95vw' }" data-testid="invite-dialog">
      <form class="admin-form" @submit.prevent="sendInvite">
        <label class="bf-field"><span>{{ t.admin.users.inviteEmail }}</span><InputText v-model="invite.email" type="email" data-testid="invite-email" /></label>
        <label class="bf-field"><span>{{ t.admin.users.role }}</span><Select v-model="invite.role" :options="roleOptions" option-label="label" option-value="value" show-clear :placeholder="t.admin.users.noRole" data-testid="invite-role" /></label>
        <Message v-if="inviteProblem" severity="error" class="banner-error" data-testid="invite-problem">{{ inviteProblem }}</Message>
      </form>
      <template #footer>
        <Button :label="t.admin.cancel" text severity="secondary" @click="inviteOpen = false" />
        <Button :label="t.admin.users.invite" icon="pi pi-send" :disabled="!invite.email.trim()" data-testid="invite-send" @click="sendInvite" />
      </template>
    </Dialog>

    <Dialog :visible="!!permsFor" modal :header="t.admin.users.permissionsOf(permsFor?.email ?? '')" :style="{ width: '560px', maxWidth: '95vw' }" data-testid="permissions-dialog" @update:visible="permsFor = null">
      <div class="admin-perm-group" data-testid="member-roles">
        <h4>{{ t.admin.users.roles }}</h4>
        <p class="desk-muted admin-hint">{{ t.admin.users.rolesHint }}</p>
        <label v-for="r in roles.data.value ?? []" :key="r.id" class="admin-perm" :data-testid="`role-${r.name}`">
          <Checkbox :model-value="(permsFor?.role_ids ?? []).includes(r.id)" binary :disabled="permsBusy || r.id === permsFor?.primary_role_id" :input-id="`mrole-${r.id}`" @update:model-value="toggleRole(r, $event as boolean)" />
          <span>{{ r.name }}<small v-if="r.id === permsFor?.primary_role_id">{{ t.admin.users.primary }}</small></span>
        </label>
      </div>
      <p class="desk-muted admin-hint">{{ t.admin.users.permissionsHint }}</p>
      <div class="admin-perms">
        <div v-for="[group, items] in groups" :key="group" class="admin-perm-group">
          <h4>{{ group }}</h4>
          <label v-for="p in items" :key="p.id" class="admin-perm" :data-testid="`perm-${p.name}`">
            <Checkbox :model-value="effective.has(p.name)" binary :disabled="permsBusy || fromRole.has(p.name)" :input-id="`perm-${p.name}`" @update:model-value="toggle(p, $event)" />
            <span>{{ p.description ?? p.name }}<small>{{ p.name }}<template v-if="fromRole.has(p.name)"> · {{ t.admin.users.fromRole }}</template></small></span>
          </label>
        </div>
      </div>
    </Dialog>
  </div>
</template>
