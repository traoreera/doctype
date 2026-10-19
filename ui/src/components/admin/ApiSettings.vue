<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { computed, ref } from 'vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { useServices } from '@/lib/services'
import { authBase } from '@/lib/xauth'
import { useSession } from '@/stores/session'

/**
 * Accès à l'API : tout ce que le Desk fait passe par elle, un script peut l'appeler avec le même jeton. On montre les adresses, l'organisation à envoyer
 * en `X-Tenant-ID`, le lien vers la documentation interactive et le jeton de CETTE session (masqué, copiable : il vaut tous vos droits jusqu'à son expiration).
 */
const { config } = useServices()
const session = useSession()
const notify = useNotify()
const origin = typeof location !== 'undefined' ? location.origin : ''
const absolute = (path: string): string => (/^https?:/.test(path) ? path : `${origin}${path}`)

const docsUrl = computed(() => `${absolute(config.baseUrl).replace(/\/app\/[^/]+$/, '')}/docs`)
const authRoot = computed(() => absolute(authBase(config.loginUrl) ?? ''))
const shown = ref(false)
const token = computed(() => session.token ?? '')
const minutes = computed(() => {
  const exp = session.claims?.exp
  return typeof exp === 'number' ? Math.max(0, Math.round((exp * 1000 - Date.now()) / 60000)) : null
})
const example = computed(
  () => `curl -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: ${session.tenant ?? '<organisation>'}" ${absolute(config.baseUrl)}/meta`,
)

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    notify.success(t.admin.api.copied)
  } catch (e) {
    notify.error(e)
  }
}
</script>

<template>
  <div class="admin-page" data-testid="api-settings">
    <section class="desk-card admin-card">
      <h3>{{ t.admin.api.title }}</h3>
      <p class="desk-muted">{{ t.admin.api.intro }}</p>
      <div class="admin-form" style="max-width: none">
        <div class="bf-field"><span class="desk-muted">{{ t.admin.api.base }}</span><div class="admin-copy"><InputText :model-value="absolute(config.baseUrl)" readonly data-testid="api-base" /><Button icon="pi pi-copy" text rounded size="small" severity="secondary" :aria-label="t.admin.api.copy" @click="copy(absolute(config.baseUrl))" /></div></div>
        <div v-if="authRoot" class="bf-field"><span class="desk-muted">{{ t.admin.api.auth }}</span><div class="admin-copy"><InputText :model-value="authRoot" readonly data-testid="api-auth" /><Button icon="pi pi-copy" text rounded size="small" severity="secondary" :aria-label="t.admin.api.copy" @click="copy(authRoot)" /></div></div>
        <div class="bf-field">
          <span class="desk-muted">{{ t.admin.api.tenant }}</span>
          <div class="admin-copy"><InputText :model-value="session.tenant ?? ''" readonly data-testid="api-tenant" /><Button icon="pi pi-copy" text rounded size="small" severity="secondary" :aria-label="t.admin.api.copy" @click="copy(session.tenant ?? '')" /></div>
          <small class="desk-muted">{{ t.admin.api.tenantHint }}</small>
        </div>
        <div class="bf-field"><span class="desk-muted">{{ t.admin.api.docs }}</span><a class="desk-link" :href="docsUrl" target="_blank" rel="noopener noreferrer" data-testid="api-docs">{{ docsUrl }}</a></div>
      </div>
    </section>

    <section class="desk-card admin-card" data-testid="api-token">
      <h3>{{ t.admin.api.token }}</h3>
      <p class="desk-muted admin-hint">{{ t.admin.api.tokenHint }}<template v-if="minutes !== null"> · {{ t.admin.api.expiresIn(minutes) }}</template></p>
      <div class="admin-copy">
        <InputText :model-value="shown ? token : '•'.repeat(24)" readonly data-testid="api-token-value" />
        <Button :label="shown ? t.admin.api.hide : t.admin.api.reveal" text size="small" severity="secondary" data-testid="api-token-toggle" @click="shown = !shown" />
        <Button :label="t.admin.api.copy" icon="pi pi-copy" text size="small" severity="secondary" data-testid="api-token-copy" @click="copy(token)" />
      </div>
      <div class="bf-field" style="margin-top: 0.75rem"><span class="desk-muted">{{ t.admin.api.example }}</span><code class="admin-codes" data-testid="api-example">{{ example }}</code></div>
    </section>
  </div>
</template>
