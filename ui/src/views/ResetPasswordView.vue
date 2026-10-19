<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import Password from 'primevue/password'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t } from '@/i18n'
import { useSession } from '@/stores/session'

/** Réinitialisation (`/password/reset`) : le jeton vient du lien d'e-mail (`#/reset?token=…`), jamais d'un deep-link. */
const session = useSession()
const route = useRoute()
const router = useRouter()

const token = computed(() => (typeof route.query.token === 'string' ? route.query.token : ''))
const password = ref('')
const confirm = ref('')
const error = ref<string | null>(null)
const done = ref(false)
const busy = ref(false)

async function submit(): Promise<void> {
  error.value = null
  if (password.value !== confirm.value) {
    error.value = t.login.reset.mismatch
    return
  }
  busy.value = true
  try {
    const out = await session.resetPassword(token.value, password.value)
    if (out.status === 'ok') done.value = true
    else error.value = out.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card">
      <h1>{{ t.login.reset.title }}</h1>
      <p class="hint">{{ t.login.reset.intro }}</p>
      <Message v-if="!token" severity="warn" class="banner-error" data-testid="reset-missing">{{ t.login.reset.missing }}</Message>
      <Message v-if="error" severity="error" class="banner-error" data-testid="reset-error">{{ error }}</Message>
      <Message v-if="done" severity="success" data-testid="reset-done">{{ t.login.reset.done }}</Message>

      <form v-if="token && !done" @submit.prevent="submit">
        <div class="form-field">
          <label for="reset-password">{{ t.login.reset.newPassword }}</label>
          <Password input-id="reset-password" v-model="password" :feedback="false" toggle-mask fluid autocomplete="new-password" required autofocus />
          <span class="hint">{{ t.login.passwordRules }}</span>
        </div>
        <div class="form-field">
          <label for="reset-confirm">{{ t.login.reset.confirm }}</label>
          <Password input-id="reset-confirm" v-model="confirm" :feedback="false" toggle-mask fluid autocomplete="new-password" required />
        </div>
        <Button type="submit" :label="t.login.reset.submit" :loading="busy" fluid />
      </form>

      <div v-if="done" style="margin-top: 1rem; text-align: center">
        <Button link size="small" :label="t.login.forgot.back" @click="router.replace({ name: 'login' })" />
      </div>
    </div>
  </div>
</template>
