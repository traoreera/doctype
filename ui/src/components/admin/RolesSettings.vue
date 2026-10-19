<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref } from 'vue'
import { useAuth, useLoad } from '@/composables/useAuth'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import type { AuthPermission, AuthRole } from '@/lib/xauth'
import { useSession } from '@/stores/session'

/**
 * Rôles et permissions (xauth) : ceux de la plateforme (lecture seule) et ceux de l'organisation (créer, modifier les permissions, supprimer).
 * Un rôle est un ensemble de permissions que des membres reçoivent d'un coup ; les permissions des plugins (`sales:invoice:read`…) y figurent
 * dès que le plugin les a déclarées.
 */
const auth = useAuth()
const session = useSession()
const notify = useNotify()
const confirm = useConfirm()
const tenant = computed(() => session.tenant ?? '')

const platform = useLoad(() => auth.globalRoles())
const own = useLoad(async () => (await auth.tenantRoles(tenant.value)).filter((r) => r.tenant_id))
const grantable = useLoad(() => auth.grantable(tenant.value))
onMounted(() => void Promise.all([platform.reload(), own.reload(), grantable.reload()]))

const open = ref<string | null>(null)
const groups = (list: AuthPermission[]): [string, AuthPermission[]][] => {
  const by = new Map<string, AuthPermission[]>()
  for (const p of list) by.set(p.group ?? '—', [...(by.get(p.group ?? '—') ?? []), p])
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b, 'fr'))
}
const allGroups = computed(() => groups(grantable.data.value ?? []))

// ── Créer ─────────────────────────────────────────────────
const creating = ref(false)
const draft = ref<{ name: string; description: string; permissions: Set<string> }>({ name: '', description: '', permissions: new Set() })
const filter = ref('')
const problem = ref<string | null>(null)
const visible = (p: AuthPermission): boolean => !filter.value.trim() || `${p.name} ${p.description ?? ''}`.toLowerCase().includes(filter.value.trim().toLowerCase())

function pick(name: string, on: boolean): void {
  const next = new Set(draft.value.permissions)
  on ? next.add(name) : next.delete(name)
  draft.value.permissions = next
}

async function create(): Promise<void> {
  problem.value = null
  try {
    await auth.createRole(tenant.value, draft.value.name.trim(), draft.value.description.trim() || null, [...draft.value.permissions])
    notify.success(t.admin.roles.created)
    creating.value = false
    draft.value = { name: '', description: '', permissions: new Set() }
    await own.reload()
  } catch (e) {
    problem.value = describeError(e)
  }
}

// ── Modifier / supprimer ──────────────────────────────────
const has = (r: AuthRole, name: string): boolean => r.permissions.some((p) => p.name === name)

async function toggle(r: AuthRole, p: AuthPermission, on: boolean): Promise<void> {
  try {
    if (on) await auth.addRolePermission(tenant.value, r.id, p.name)
    else await auth.removeRolePermission(tenant.value, r.id, p.name)
  } catch (e) {
    notify.error(e)
  }
  await own.reload()
}

function ask(r: AuthRole): void {
  confirm.require({
    header: t.admin.roles.delete,
    message: t.admin.roles.confirmDelete(r.name),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.admin.roles.delete,
    rejectLabel: t.admin.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      try {
        await auth.deleteRole(tenant.value, r.id)
        notify.success(t.admin.roles.deleted)
      } catch (e) {
        notify.error(e)
      }
      await own.reload()
    },
  })
}
</script>

<template>
  <div class="admin-page" data-testid="roles-settings">
    <Message v-if="platform.error.value && !platform.data.value" severity="error" class="banner-error" data-testid="admin-failed">{{ platform.error.value }}</Message>

    <section class="desk-card admin-card" data-testid="tenant-roles">
      <header class="collab-card-head">
        <h3>{{ t.admin.roles.tenant }}</h3>
        <span class="desk-spacer" />
        <Button v-if="!own.forbidden.value" :label="t.admin.roles.create" icon="pi pi-plus" size="small" data-testid="role-create-open" @click="creating = true" />
      </header>
      <Message v-if="own.forbidden.value" severity="info" class="banner-error" data-testid="admin-forbidden">{{ t.admin.adminOnly }}</Message>
      <p v-else-if="!(own.data.value ?? []).length" class="desk-muted" data-testid="roles-empty">{{ t.admin.roles.none }}</p>
      <ul v-else class="admin-list">
        <li v-for="r in own.data.value ?? []" :key="r.id" data-testid="tenant-role">
          <div class="admin-row">
            <div>
              <strong>{{ r.name }}</strong> <span class="desk-muted">· {{ t.admin.roles.permissions(r.permissions.length) }}</span>
              <span v-if="r.description" class="desk-muted admin-sub">{{ r.description }}</span>
            </div>
            <div class="admin-actions">
              <Button :icon="open === r.id ? 'pi pi-chevron-up' : 'pi pi-chevron-down'" text rounded size="small" severity="secondary" :aria-label="t.admin.users.permissions" data-testid="role-toggle" @click="open = open === r.id ? null : r.id" />
              <Button icon="pi pi-trash" text rounded size="small" severity="danger" :aria-label="t.admin.roles.delete" data-testid="role-delete" @click="ask(r)" />
            </div>
          </div>
          <div v-if="open === r.id" class="admin-perms" data-testid="role-permissions">
            <div v-for="[group, items] in allGroups" :key="group" class="admin-perm-group">
              <h4>{{ group }}</h4>
              <label v-for="p in items" :key="p.id" class="admin-perm">
                <Checkbox :model-value="has(r, p.name)" binary :input-id="`rp-${r.id}-${p.name}`" @update:model-value="toggle(r, p, $event)" />
                <span>{{ p.description ?? p.name }}<small>{{ p.name }}</small></span>
              </label>
            </div>
          </div>
        </li>
      </ul>
    </section>

    <section class="desk-card admin-card" data-testid="platform-roles">
      <h3>{{ t.admin.roles.platform }}</h3>
      <ul class="admin-list">
        <li v-for="r in platform.data.value ?? []" :key="r.id" class="admin-row" data-testid="platform-role">
          <div>
            <strong>{{ r.name }}</strong> <span class="desk-muted">· {{ t.admin.roles.permissions(r.permissions.length) }}</span>
            <span v-if="r.description" class="desk-muted admin-sub">{{ r.description }}</span>
          </div>
          <span class="status-pill">{{ t.admin.roles.readOnly }}</span>
        </li>
      </ul>
    </section>

    <Dialog v-model:visible="creating" modal :header="t.admin.roles.create" :style="{ width: '600px', maxWidth: '95vw' }" data-testid="role-dialog">
      <div class="admin-form" style="max-width: none">
        <label class="bf-field"><span>{{ t.admin.roles.name }}</span><InputText v-model="draft.name" maxlength="64" data-testid="role-name" /></label>
        <label class="bf-field"><span>{{ t.admin.roles.description }}</span><InputText v-model="draft.description" maxlength="200" /></label>
        <label class="bf-field"><span>{{ t.admin.roles.search }}</span><InputText v-model="filter" data-testid="role-filter" /></label>
        <div class="admin-perms">
          <div v-for="[group, items] in allGroups" :key="group" class="admin-perm-group">
            <template v-if="items.some(visible)">
              <h4>{{ group }}</h4>
              <label v-for="p in items.filter(visible)" :key="p.id" class="admin-perm">
                <Checkbox :model-value="draft.permissions.has(p.name)" binary :input-id="`np-${p.name}`" :data-testid="`np-${p.name}`" @update:model-value="pick(p.name, $event)" />
                <span>{{ p.description ?? p.name }}<small>{{ p.name }}</small></span>
              </label>
            </template>
          </div>
        </div>
        <Message v-if="problem" severity="error" class="banner-error" data-testid="role-problem">{{ problem }}</Message>
      </div>
      <template #footer>
        <Button :label="t.admin.cancel" text severity="secondary" @click="creating = false" />
        <Button :label="t.admin.roles.create" :disabled="!draft.name.trim()" data-testid="role-save" @click="create" />
      </template>
    </Dialog>
  </div>
</template>
