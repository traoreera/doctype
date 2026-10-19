<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { t } from '@/i18n'
import { useSession, type LoginOutcome } from '@/stores/session'

/**
 * Un compte sans organisation : le Desk s'arrête ici (le jeton d'accès n'a pas de tenant). On crée une organisation, ou on rejoint celle
 * qu'une invitation a proposée. Le jeton de renouvellement de la connexion représente l'utilisateur et autorise l'opération.
 */
const session = useSession()
const router = useRouter()

const tab = ref<'create' | 'join'>('create')
const name = ref('')
const slug = ref('')
const invite = ref('')
const error = ref<string | null>(null)
const busy = ref(false)

async function submit(): Promise<void> {
  error.value = null
  busy.value = true
  try {
    const out: LoginOutcome =
      tab.value === 'create' ? await session.setupCreate(name.value.trim(), slug.value.trim()) : await session.setupJoin(invite.value.trim())
    if (out.status === 'ok') return void router.replace('/')
    error.value = out.status === 'error' ? out.message : t.login.onboarding.failed
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card">
      <h1>{{ t.login.onboarding.title }}</h1>
      <p class="hint">{{ t.login.onboarding.intro }}</p>
      <Message v-if="error" severity="error" class="banner-error" data-testid="onboarding-error">{{ error }}</Message>

      <div style="display: flex; gap: 0.5rem; margin-bottom: 1rem">
        <Button :severity="tab === 'create' ? 'primary' : 'secondary'" :label="t.login.onboarding.createTab" size="small" @click="tab = 'create'" />
        <Button :severity="tab === 'join' ? 'primary' : 'secondary'" :label="t.login.onboarding.joinTab" size="small" @click="tab = 'join'" />
      </div>

      <form @submit.prevent="submit">
        <template v-if="tab === 'create'">
          <div class="form-field">
            <label for="onboarding-name">{{ t.login.onboarding.name }}</label>
            <InputText id="onboarding-name" v-model="name" autofocus required />
          </div>
          <div class="form-field">
            <label for="onboarding-slug">{{ t.login.onboarding.slug }}</label>
            <InputText id="onboarding-slug" v-model="slug" pattern="[a-z0-9-]+" required />
          </div>
        </template>
        <div v-else class="form-field">
          <label for="onboarding-invite">{{ t.login.onboarding.invite }}</label>
          <InputText id="onboarding-invite" v-model="invite" autofocus required />
        </div>
        <Button type="submit" :label="tab === 'create' ? t.login.onboarding.create : t.login.onboarding.join" :loading="busy" fluid />
      </form>

      <div style="margin-top: 1rem; text-align: center">
        <Button link size="small" :label="t.nav.logout" @click="session.logout()" />
      </div>
    </div>
  </div>
</template>
