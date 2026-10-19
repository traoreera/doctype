<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'
import { onMounted, ref } from 'vue'
import { useAuth } from '@/composables/useAuth'
import { describeError, useNotify } from '@/composables/useNotify'
import { ApiError } from '@/lib/api'
import { t } from '@/i18n'
import { formatTimestamp, relativeTime } from '@/lib/format'
import type { AdminUser, AuditEntry } from '@/lib/xauth'
import { useSession } from '@/stores/session'

/**
 * Les comptes de la plateforme (xauth, réservé à l'administrateur de la plateforme) : tous les utilisateurs de l'installation, toutes organisations
 * confondues. On peut désactiver un compte (ses sessions sont fermées), le réactiver, le supprimer, lire son activité.
 */
const PAGE = 50
const auth = useAuth()
const notify = useNotify()
const confirm = useConfirm()
const session = useSession()
const accounts = ref<AdminUser[]>([])
const more = ref(false)
const loading = ref(false)
const forbidden = ref(false)
const failure = ref<string | null>(null)

async function load(reset: boolean): Promise<void> {
  loading.value = true
  try {
    const page = await auth.adminUsers({ limit: PAGE, offset: reset ? 0 : accounts.value.length })
    accounts.value = reset ? page : [...accounts.value, ...page]
    more.value = page.length === PAGE
    forbidden.value = false
    failure.value = null
  } catch (e) {
    forbidden.value = e instanceof ApiError && e.status === 403
    failure.value = forbidden.value ? null : describeError(e)
  } finally {
    loading.value = false
  }
}
onMounted(() => void load(true))

const isMe = (u: AdminUser): boolean => u.id === session.claims?.sub

async function setActive(u: AdminUser, active: boolean): Promise<void> {
  try {
    await auth.setUserActive(u.id, active)
    notify.success(active ? t.admin.accounts.enabledDone : t.admin.accounts.disabledDone)
  } catch (e) {
    notify.error(e)
  }
  await load(true)
}

function askDelete(u: AdminUser): void {
  confirm.require({
    header: t.admin.accounts.delete,
    message: t.admin.accounts.confirmDelete(u.email),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.admin.accounts.delete,
    rejectLabel: t.admin.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      try {
        await auth.deleteUser(u.id)
        notify.success(t.admin.accounts.deleted)
      } catch (e) {
        notify.error(e)
      }
      await load(true)
    },
  })
}

// ── Activité d'un compte ──────────────────────────────────
const activityOf = ref<AdminUser | null>(null)
const activity = ref<AuditEntry[]>([])
const activityBusy = ref(false)

async function openActivity(u: AdminUser): Promise<void> {
  activityOf.value = u
  activity.value = []
  activityBusy.value = true
  try {
    activity.value = await auth.userAudit(u.id)
  } catch (e) {
    notify.error(e)
    activityOf.value = null
  } finally {
    activityBusy.value = false
  }
}
</script>

<template>
  <div class="admin-page" data-testid="accounts-settings">
    <Message v-if="forbidden" severity="info" class="banner-error" data-testid="admin-forbidden">{{ t.admin.platformOnly }}</Message>
    <Message v-else-if="failure" severity="error" class="banner-error" data-testid="admin-failed">{{ failure }}</Message>
    <section v-else class="desk-card admin-card">
      <header class="collab-card-head">
        <h3>{{ t.admin.accounts.title }}</h3>
        <span class="desk-spacer" />
        <Button icon="pi pi-refresh" text rounded size="small" severity="secondary" :aria-label="t.admin.reload" :loading="loading" @click="load(true)" />
      </header>
      <p class="desk-muted admin-hint">{{ t.admin.accounts.intro }}</p>
      <p v-if="!accounts.length && !loading" class="desk-muted" data-testid="accounts-empty">{{ t.admin.accounts.empty }}</p>
      <table v-else class="admin-table" data-testid="accounts">
        <thead><tr><th>{{ t.admin.accounts.email }}</th><th>{{ t.admin.accounts.username }}</th><th>{{ t.admin.accounts.state }}</th><th>{{ t.admin.accounts.mfa }}</th><th>{{ t.admin.accounts.created }}</th><th /></tr></thead>
        <tbody>
          <tr v-for="u in accounts" :key="u.id" data-testid="account">
            <td>{{ u.email }} <span v-if="isMe(u)" class="status-pill">{{ t.admin.accounts.you }}</span></td>
            <td class="admin-mono">{{ u.username ?? '—' }}</td>
            <td><span class="status-pill" :class="{ ok: u.is_active }">{{ u.is_active ? t.admin.accounts.active : t.admin.accounts.disabled }}</span></td>
            <td>{{ u.mfa_enabled ? t.admin.yes : t.admin.no }}</td>
            <td><time :title="formatTimestamp(u.created_at)">{{ relativeTime(u.created_at) }}</time></td>
            <td class="admin-actions">
              <Button :label="t.admin.accounts.activity" text size="small" severity="secondary" data-testid="account-activity" @click="openActivity(u)" />
              <template v-if="!isMe(u)">
                <Button v-if="u.is_active" :label="t.admin.accounts.disable" text size="small" severity="secondary" data-testid="account-disable" @click="setActive(u, false)" />
                <Button v-else :label="t.admin.accounts.enable" text size="small" severity="secondary" data-testid="account-enable" @click="setActive(u, true)" />
                <Button :label="t.admin.accounts.delete" text size="small" severity="danger" data-testid="account-delete" @click="askDelete(u)" />
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      <Button v-if="more" :label="t.admin.accounts.more" text size="small" :loading="loading" data-testid="accounts-more" @click="load(false)" />
    </section>

    <Dialog :visible="!!activityOf" modal :header="activityOf ? t.admin.accounts.activityOf(activityOf.email) : ''" :style="{ width: '640px', maxWidth: '95vw' }" data-testid="activity-dialog" @update:visible="!$event && (activityOf = null)">
      <p v-if="!activity.length && !activityBusy" class="desk-muted" data-testid="activity-empty">{{ t.admin.accounts.noActivity }}</p>
      <table v-else class="admin-table">
        <tbody>
          <tr v-for="e in activity" :key="e.id" data-testid="activity-entry">
            <td><time :title="formatTimestamp(e.created_at)">{{ relativeTime(e.created_at) }}</time></td>
            <td class="admin-mono">{{ e.action }}</td>
            <td class="admin-mono">{{ e.ip_address ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </Dialog>
  </div>
</template>
