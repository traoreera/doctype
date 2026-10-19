<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { computed, onMounted, ref } from 'vue'
import { useAuth, useLoad } from '@/composables/useAuth'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import type { AuthPermission, AuthRole } from '@/lib/xauth'

/**
 * Catalogue des permissions et rôles de la PLATEFORME (xauth, réservé à l'administrateur de la plateforme) : tout ce que les plugins ont déclaré
 * (`rbac.declare`), et les rôles globaux (admin, user, tenant_admin…) que chaque organisation reçoit. Les permissions s'y accordent par identifiant.
 */
const auth = useAuth()
const notify = useNotify()
const perms = useLoad(() => auth.permissions())
const roles = useLoad(() => auth.globalRoles())
onMounted(() => void Promise.all([perms.reload(), roles.reload()]))

const filter = ref('')
const matches = (p: AuthPermission): boolean => {
  const q = filter.value.trim().toLowerCase()
  return !q || `${p.name} ${p.description ?? ''} ${p.source_plugin ?? ''}`.toLowerCase().includes(q)
}
const grouped = computed(() => {
  const by = new Map<string, AuthPermission[]>()
  for (const p of (perms.data.value ?? []).filter(matches)) {
    const key = p.source_plugin ?? p.group ?? '—'
    by.set(key, [...(by.get(key) ?? []), p])
  }
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b, 'fr'))
})
const total = computed(() => grouped.value.reduce((n, [, list]) => n + list.length, 0))

// ── Rôles de la plateforme ────────────────────────────────
const editing = ref<string | null>(null)
const granted = (role: AuthRole, p: AuthPermission): boolean => role.permissions.some((x) => x.id === p.id)

async function toggle(role: AuthRole, p: AuthPermission, on: boolean): Promise<void> {
  try {
    if (on) await auth.addGlobalRolePermission(role.id, p.id)
    else await auth.removeGlobalRolePermission(role.id, p.id)
  } catch (e) {
    notify.error(e)
  }
  await roles.reload()
}

const creating = ref(false)
const draft = ref({ name: '', description: '' })
const problem = ref<string | null>(null)

async function create(): Promise<void> {
  problem.value = null
  try {
    await auth.createGlobalRole(draft.value.name.trim(), draft.value.description.trim() || null)
    notify.success(t.admin.permissions.roleCreated)
    creating.value = false
    draft.value = { name: '', description: '' }
    await roles.reload()
  } catch (e) {
    problem.value = describeError(e)
  }
}
</script>

<template>
  <div class="admin-page" data-testid="permissions-settings">
    <Message v-if="perms.forbidden.value || roles.forbidden.value" severity="info" class="banner-error" data-testid="admin-forbidden">{{ t.admin.platformOnly }}</Message>
    <Message v-else-if="perms.error.value || roles.error.value" severity="error" class="banner-error" data-testid="admin-failed">{{ perms.error.value ?? roles.error.value }}</Message>

    <template v-else>
      <section class="desk-card admin-card" data-testid="platform-roles">
        <header class="collab-card-head">
          <h3>{{ t.admin.permissions.platformRoles }}</h3>
          <span class="desk-spacer" />
          <Button :label="t.admin.permissions.newRole" icon="pi pi-plus" size="small" data-testid="global-role-create-open" @click="creating = true" />
        </header>
        <ul class="admin-list">
          <li v-for="r in roles.data.value ?? []" :key="r.id" data-testid="global-role">
            <div class="admin-row">
              <div>
                <strong>{{ r.name }}</strong>
                <span class="desk-muted"> · {{ t.admin.roles.permissions(r.permissions.length) }}</span>
                <span v-if="r.description" class="desk-muted admin-sub">{{ r.description }}</span>
              </div>
              <Button :label="editing === r.id ? t.admin.cancel : t.admin.permissions.editRole" text size="small" severity="secondary" data-testid="global-role-edit" @click="editing = editing === r.id ? null : r.id" />
            </div>
            <div v-if="editing === r.id" class="admin-perms" data-testid="global-role-permissions">
              <div v-for="[plugin, list] in grouped" :key="plugin" class="admin-perm-group">
                <h4>{{ plugin }}</h4>
                <label v-for="p in list" :key="p.id" class="admin-perm" :data-testid="`gp-${p.name}`">
                  <Checkbox binary :model-value="granted(r, p)" :input-id="`gp-${r.id}-${p.id}`" @update:model-value="toggle(r, p, $event as boolean)" />
                  <span>{{ p.description ?? p.name }}<small class="admin-mono">{{ p.name }}</small></span>
                </label>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <section class="desk-card admin-card" data-testid="permission-catalog">
        <header class="collab-card-head">
          <h3>{{ t.admin.permissions.title }}</h3>
          <span class="desk-spacer" />
          <span class="desk-muted" data-testid="permission-count">{{ t.admin.permissions.count(total) }}</span>
        </header>
        <p class="desk-muted admin-hint">{{ t.admin.permissions.intro }}</p>
        <InputText v-model="filter" :placeholder="t.admin.permissions.search" data-testid="permission-filter" />
        <p v-if="!total" class="desk-muted" data-testid="permission-none">{{ t.admin.permissions.none }}</p>
        <div v-for="[plugin, list] in grouped" :key="plugin" class="admin-perm-group" data-testid="permission-group">
          <h4>{{ t.admin.permissions.plugin }} : {{ plugin }}</h4>
          <ul class="admin-list">
            <li v-for="p in list" :key="p.id" class="admin-row" data-testid="permission">
              <div>
                <strong>{{ p.description ?? p.name }}</strong>
                <span class="desk-muted admin-sub admin-mono">{{ p.name }}</span>
              </div>
              <div>
                <span v-if="p.tenant_grantable" class="status-pill ok">{{ t.admin.permissions.grantable }}</span>
                <span v-if="!p.active" class="status-pill">{{ t.admin.permissions.inactive }}</span>
              </div>
            </li>
          </ul>
        </div>
      </section>
    </template>

    <Dialog v-model:visible="creating" modal :header="t.admin.permissions.newRole" :style="{ width: '420px', maxWidth: '95vw' }">
      <form class="admin-form" @submit.prevent="create">
        <label class="bf-field"><span>{{ t.admin.permissions.roleName }}</span><InputText v-model="draft.name" maxlength="64" data-testid="global-role-name" /></label>
        <label class="bf-field"><span>{{ t.admin.roles.description }}</span><InputText v-model="draft.description" maxlength="200" /></label>
        <Message v-if="problem" severity="error" class="banner-error" data-testid="global-role-problem">{{ problem }}</Message>
      </form>
      <template #footer>
        <Button :label="t.admin.cancel" text severity="secondary" @click="creating = false" />
        <Button :label="t.admin.save" :disabled="!draft.name.trim()" data-testid="global-role-save" @click="create" />
      </template>
    </Dialog>
  </div>
</template>
