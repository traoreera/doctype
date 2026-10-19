<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { t } from '@/i18n'
import { useSession } from '@/stores/session'

/** Demande d'un lien de réinitialisation (`/password/forgot`) : la réponse est toujours neutre (pas d'énumération des adresses). */
const session = useSession()
const router = useRouter()

const email = ref('')
const error = ref<string | null>(null)
const sent = ref(false)
const busy = ref(false)

async function submit(): Promise<void> {
  error.value = null
  busy.value = true
  try {
    const out = await session.requestPasswordReset(email.value.trim())
    if (out.status === 'ok') sent.value = true
    else error.value = out.message
  } finally {
    busy.value = false
  }
}

function toLogin(): void {
  void router.replace({ name: 'login' })
}
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card">
      <h1>{{ t.login.forgot.title }}</h1>
      <p class="hint">{{ t.login.forgot.intro }}</p>
      <Message v-if="error" severity="error" class="banner-error" data-testid="forgot-error">{{ error }}</Message>
      <Message v-if="sent" severity="success" data-testid="forgot-sent">{{ t.login.forgot.sent }}</Message>

      <form v-if="!sent" @submit.prevent="submit">
        <div class="form-field">
          <label for="forgot-email">{{ t.login.email }}</label>
          <InputText id="forgot-email" v-model="email" type="email" autocomplete="username" required autofocus />
        </div>
        <Button type="submit" :label="t.login.forgot.submit" :loading="busy" fluid />
      </form>

      <div style="margin-top: 1rem; text-align: center">
        <Button link size="small" :label="t.login.forgot.back" @click="toLogin" />
      </div>
    </div>
  </div>
</template>
