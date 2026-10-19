<script setup lang="ts">
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'
import Select from 'primevue/select'
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t } from '@/i18n'
import { safeNext } from '@/router'
import { useSession, type TenantChoice } from '@/stores/session'

/**
 * Retour du fournisseur OAuth (`#/oauth?access_token=…`) : c'est le Desk qui reçoit la session, puis avance d'une étape
 * s'il en reste une (double authentification, choix d'organisation, organisation à créer). Aucun deep-link n'est ouvert.
 */
const session = useSession()
const route = useRoute()
const router = useRouter()

const error = ref<string | null>(null)
const tenants = ref<TenantChoice[]>([])
const tenantId = ref<string | null>(null)
const busy = ref(false)

async function finish(): Promise<void> {
  await router.replace(safeNext(route.query.next))
}

async function chooseTenant(): Promise<void> {
  if (!tenantId.value) return
  busy.value = true
  error.value = null
  try {
    const out = await session.selectTenant(tenantId.value)
    if (out.status === 'ok') await finish()
    else if (out.status === 'error') error.value = out.message
    else error.value = t.login.refused
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  const out = session.completeOAuth(route.query)
  if (out.status === 'ok') return await finish()
  if (out.status === 'onboarding') return void router.replace({ name: 'onboarding' })
  if (out.status === 'mfa') return void router.replace({ name: 'login', query: route.query.next ? { next: route.query.next } : {} })
  if (out.status === 'tenant') {
    tenants.value = out.tenants
    return
  }
  error.value = out.status === 'error' ? out.message : t.login.oauthFailed
})
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card" :style="tenants.length ? '' : 'text-align: center'">
      <template v-if="error">
        <h1>{{ t.login.title }}</h1>
        <p class="hint" data-testid="oauth-error">{{ error }}</p>
        <RouterLink :to="{ name: 'login' }">{{ t.login.forgot.back }}</RouterLink>
      </template>
      <template v-else-if="tenants.length">
        <h1>{{ t.login.chooseTenant }}</h1>
        <div class="form-field">
          <label for="oauth-tenant">{{ t.login.chooseTenant }}</label>
          <Select input-id="oauth-tenant" v-model="tenantId" :options="tenants" option-label="name" option-value="id" fluid />
        </div>
        <Button :label="t.login.submit" :loading="busy" fluid @click="chooseTenant" />
      </template>
      <ProgressSpinner v-else stroke-width="3" style="width: 3rem; height: 3rem" />
    </div>
  </div>
</template>
