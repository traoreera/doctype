<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t } from '@/i18n'
import { safeNext } from '@/router'
import { useSession, type LoginOutcome, type TenantChoice } from '@/stores/session'

const session = useSession()
const route = useRoute()
const router = useRouter()

const mode = ref<'password' | 'token'>(session.canPasswordLogin ? 'password' : 'token')
// Le retour d'un OAuth peut laisser une double authentification en attente : on reprend directement à cette étape.
const step = ref<'credentials' | 'mfa'>(session.mfaToken ? 'mfa' : 'credentials')
const email = ref('')
const password = ref('')
const code = ref('')
const rawToken = ref('')
const tenants = ref<TenantChoice[]>([])
const tenantId = ref<string | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)
const providers = ref<string[]>([])
const registered = route.query.registered === '1'

onMounted(async () => {
  providers.value = await session.oauthProviders()
})

function providerLabel(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1)
}

async function oauth(provider: string): Promise<void> {
  error.value = null
  const url = await session.oauthAuthorizeUrl(provider)
  if (url) globalThis.location.assign(url)
  else error.value = t.login.oauthFailed
}

async function done(): Promise<void> {
  await router.replace(safeNext(route.query.next))
}

/** Une réponse d'authentification : on aboutit, ou on avance d'une étape (organisation, double authentification, organisation à créer). */
async function handle(out: LoginOutcome): Promise<void> {
  if (out.status === 'ok') return await done()
  if (out.status === 'tenant') {
    tenants.value = out.tenants
    error.value = t.login.chooseTenant
    return
  }
  if (out.status === 'mfa') {
    step.value = 'mfa'
    error.value = null
    return
  }
  if (out.status === 'onboarding') return void router.replace({ name: 'onboarding' })
  error.value = out.message
}

async function submitPassword(): Promise<void> {
  error.value = null
  busy.value = true
  try {
    // Une organisation a déjà été demandée : on échange le jeton de la connexion en cours, on ne redemande pas le mot de passe.
    if (tenants.value.length) {
      if (!tenantId.value) {
        error.value = t.login.chooseTenant
        return
      }
      return await handle(await session.selectTenant(tenantId.value))
    }
    await handle(await session.login(email.value.trim(), password.value))
  } finally {
    busy.value = false
  }
}

async function submitMfa(): Promise<void> {
  error.value = null
  busy.value = true
  try {
    const out = await session.verifyMfa(code.value.trim())
    if (out.status === 'ok') return await done()
    error.value = out.status === 'error' ? out.message : t.login.mfaInvalid
  } finally {
    busy.value = false
  }
}

async function submitToken(): Promise<void> {
  error.value = null
  if (!session.useToken(rawToken.value)) {
    error.value = t.login.tokenInvalid
    return
  }
  await done()
}
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card">
      <h1>{{ t.login.title }}</h1>
      <Message v-if="session.notice" severity="warn" class="banner-error" data-testid="login-notice">{{ session.notice }}</Message>
      <Message v-if="registered && step === 'credentials'" severity="success" data-testid="login-registered">{{ t.login.register.done }}</Message>
      <Message v-if="error" severity="error" class="banner-error" data-testid="login-error">{{ error }}</Message>

      <form v-if="mode === 'password' && step === 'credentials'" @submit.prevent="submitPassword">
        <div class="form-field">
          <label for="login-email">{{ t.login.identifier }}</label>
          <InputText id="login-email" v-model="email" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus :disabled="tenants.length > 0" />
        </div>
        <div class="form-field">
          <label for="login-password">{{ t.login.password }}</label>
          <Password input-id="login-password" v-model="password" :feedback="false" toggle-mask fluid autocomplete="current-password" required :disabled="tenants.length > 0" />
        </div>
        <div v-if="tenants.length" class="form-field">
          <label for="login-tenant">{{ t.login.chooseTenant }}</label>
          <Select input-id="login-tenant" v-model="tenantId" :options="tenants" option-label="name" option-value="id" fluid />
        </div>
        <Button type="submit" :label="t.login.submit" :loading="busy" fluid />
      </form>

      <form v-else-if="mode === 'password'" @submit.prevent="submitMfa">
        <p class="hint">{{ t.login.mfaHelp }}</p>
        <div class="form-field">
          <label for="login-mfa">{{ t.login.mfaCode }}</label>
          <InputText id="login-mfa" v-model="code" inputmode="numeric" autocomplete="one-time-code" autofocus />
        </div>
        <Button type="submit" :label="t.login.mfaSubmit" :loading="busy" fluid />
      </form>

      <form v-else @submit.prevent="submitToken">
        <div class="form-field">
          <label for="login-token">{{ t.login.token }}</label>
          <Textarea id="login-token" v-model="rawToken" rows="4" autofocus spellcheck="false" autocomplete="off" />
          <span class="hint">{{ t.login.tokenHelp }}</span>
        </div>
        <Button type="submit" :label="t.login.submit" fluid />
      </form>

      <div v-if="session.canPasswordLogin && step === 'credentials'" style="margin-top: 1rem; text-align: center">
        <Button
          link
          size="small"
          :label="mode === 'password' ? t.login.useToken : t.login.usePassword"
          @click="mode = mode === 'password' ? 'token' : 'password'"
        />
      </div>

      <div v-if="step === 'credentials'" class="login-links">
        <RouterLink :to="{ name: 'forgot', query: route.query.next ? { next: route.query.next } : {} }">{{ t.login.forgotLink }}</RouterLink>
        <RouterLink :to="{ name: 'register', query: route.query.next ? { next: route.query.next } : {} }">{{ t.login.registerLink }}</RouterLink>
      </div>

      <div v-if="providers.length && step === 'credentials'" class="login-oauth">
        <div class="login-separator"><span>{{ t.login.orContinue }}</span></div>
        <Button
          v-for="provider in providers"
          :key="provider"
          :label="providerLabel(provider)"
          icon="pi pi-sign-in"
          outlined
          fluid
          @click="oauth(provider)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-links {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  margin-top: 1rem;
  font-size: 0.9rem;
}
.login-oauth {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 1.25rem;
}
.login-separator {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.25rem;
  color: var(--p-text-muted-color);
  font-size: 0.85rem;
}
.login-separator::before,
.login-separator::after {
  content: '';
  flex: 1;
  border-top: 1px solid var(--p-content-border-color);
}
</style>
