<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { onMounted, ref } from 'vue'
import { useAuth, useLoad } from '@/composables/useAuth'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'

/** Le courrier sortant (l'extension e-mail d'xauth) : configuré ou simulé, le serveur répond-il, combien de messages, et un message d'essai. Plateforme seulement. */
const auth = useAuth()
const notify = useNotify()
const status = useLoad(() => auth.mailStatus())
onMounted(() => void status.reload())

const to = ref('')
const sending = ref(false)

async function test(): Promise<void> {
  sending.value = true
  try {
    const r = await auth.mailTest(to.value.trim())
    if (!r.sent) notify.error(new Error(t.admin.mail.notSent))
    else notify.success(r.simulated ? t.admin.mail.sentSimulated : t.admin.mail.sentReal)
    await status.reload()
  } catch (e) {
    notify.error(e)
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <div class="admin-page" data-testid="mail-settings">
    <Message v-if="status.forbidden.value" severity="info" class="banner-error" data-testid="admin-forbidden">{{ t.admin.platformOnly }}</Message>
    <Message v-else-if="status.error.value" severity="error" class="banner-error" data-testid="admin-failed">{{ status.error.value }}</Message>

    <template v-else-if="status.data.value">
      <section class="desk-card admin-card" data-testid="mail-status">
        <h3>{{ t.admin.mail.status }}</h3>
        <Message :severity="status.data.value.simulated ? 'warn' : status.data.value.reachable === false ? 'error' : 'success'" class="banner-error" data-testid="mail-mode">
          {{ status.data.value.simulated ? t.admin.mail.simulated : t.admin.mail.real }}
        </Message>
        <table class="admin-table">
          <tbody>
            <tr><th>{{ t.admin.mail.server }}</th><td data-testid="mail-server">{{ status.data.value.configured ? `${status.data.value.smtp_host}:${status.data.value.smtp_port}${status.data.value.use_tls ? ' (TLS)' : ''}` : '—' }}</td></tr>
            <tr><th>{{ t.admin.mail.from }}</th><td>{{ status.data.value.from || '—' }}</td></tr>
            <tr v-if="status.data.value.configured"><th>{{ t.admin.mail.reachable }}</th><td data-testid="mail-reachable">{{ status.data.value.reachable ? t.admin.yes : t.admin.no }}<span v-if="status.data.value.detail" class="desk-muted"> · {{ status.data.value.detail }}</span></td></tr>
            <tr><th /><td data-testid="mail-counters">{{ t.admin.mail.counters(status.data.value.sent, status.data.value.failed, status.data.value.queued) }}<span v-if="status.data.value.last_sent_at" class="desk-muted"> · {{ relativeTime(new Date(status.data.value.last_sent_at * 1000).toISOString()) }}</span></td></tr>
          </tbody>
        </table>
        <p v-if="!status.data.value.configured" class="desk-muted admin-hint" data-testid="mail-configure">{{ t.admin.mail.configure }}</p>
      </section>

      <section class="desk-card admin-card">
        <h3>{{ t.admin.mail.test }}</h3>
        <form class="admin-form" @submit.prevent="test">
          <label class="bf-field"><span>{{ t.admin.mail.to }}</span><InputText v-model="to" type="email" data-testid="mail-to" /></label>
          <div><Button type="submit" :label="t.admin.mail.send" icon="pi pi-send" size="small" :loading="sending" :disabled="!to.trim()" data-testid="mail-send" /></div>
        </form>
      </section>
    </template>
  </div>
</template>
