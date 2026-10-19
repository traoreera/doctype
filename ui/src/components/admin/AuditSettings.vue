<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import { computed, onMounted, ref } from 'vue'
import { useAuth } from '@/composables/useAuth'
import { describeError } from '@/composables/useNotify'
import { ApiError } from '@/lib/api'
import { t } from '@/i18n'
import { formatTimestamp, relativeTime } from '@/lib/format'
import type { AuditEntry } from '@/lib/xauth'
import { useSession } from '@/stores/session'
import { useUsers } from '@/stores/users'

/** Le journal d'audit de l'organisation (xauth) : connexions, changements de rôle, invitations… du plus récent au plus ancien. */
const PAGE = 50
const auth = useAuth()
const session = useSession()
const users = useUsers()
const tenant = computed(() => session.tenant ?? '')
const entries = ref<AuditEntry[]>([])
const more = ref(false)
const forbidden = ref(false)
const failure = ref<string | null>(null)
const loading = ref(false)

async function load(reset: boolean): Promise<void> {
  loading.value = true
  try {
    const page = await auth.audit(tenant.value, { limit: PAGE, offset: reset ? 0 : entries.value.length })
    entries.value = reset ? page : [...entries.value, ...page]
    more.value = page.length === PAGE
    users.want(page.map((e) => e.user_id))
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
</script>

<template>
  <div class="admin-page" data-testid="audit-settings">
    <Message v-if="forbidden" severity="info" class="banner-error" data-testid="admin-forbidden">{{ t.admin.adminOnly }}</Message>
    <Message v-else-if="failure" severity="error" class="banner-error" data-testid="admin-failed">{{ failure }}</Message>
    <section v-else class="desk-card admin-card">
      <header class="collab-card-head">
        <h3>{{ t.admin.audit.title }}</h3>
        <span class="desk-spacer" />
        <Button icon="pi pi-refresh" text rounded size="small" severity="secondary" :aria-label="t.admin.reload" :loading="loading" data-testid="audit-reload" @click="load(true)" />
      </header>
      <p v-if="!entries.length && !loading" class="desk-muted" data-testid="audit-empty">{{ t.admin.audit.empty }}</p>
      <table v-else class="admin-table">
        <thead><tr><th>{{ t.admin.audit.when }}</th><th>{{ t.admin.audit.who }}</th><th>{{ t.admin.audit.action }}</th><th>{{ t.admin.audit.resource }}</th><th>{{ t.admin.audit.ip }}</th></tr></thead>
        <tbody>
          <tr v-for="e in entries" :key="e.id" data-testid="audit-entry">
            <td><time :title="formatTimestamp(e.created_at)">{{ relativeTime(e.created_at) }}</time></td>
            <td>{{ e.user_id ? users.label(e.user_id) : '—' }}</td>
            <td class="admin-mono">{{ e.action }}</td>
            <td class="admin-mono">{{ [e.resource, e.resource_id ? e.resource_id.slice(0, 8) : null].filter(Boolean).join(' ') || '—' }}</td>
            <td class="admin-mono">{{ e.ip_address ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <Button v-if="more" :label="t.admin.audit.more" text size="small" :loading="loading" data-testid="audit-more" @click="load(false)" />
    </section>
  </div>
</template>
