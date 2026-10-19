<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t } from '@/i18n'
import { useSession } from '@/stores/session'

/**
 * Inscription (xauth `/register`) : crée le compte, sans organisation. On renvoie ensuite à la connexion : c'est là que le compte
 * choisit (ou crée) son organisation. Aucun jeton n'est émis ici.
 */
const session = useSession()
const route = useRoute()
const router = useRouter()

const next = computed(() => (typeof route.query.next === 'string' ? route.query.next : null))

const email = ref('')
const username = ref('')
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
    const out = await session.register(email.value.trim(), password.value, username.value)
    if (out.status === 'ok') done.value = true
    else error.value = out.message
  } finally {
    busy.value = false
  }
}

function toLogin(): void {
  const query: Record<string, string> = done.value ? { registered: '1' } : {}
  if (next.value) query.next = next.value
  void router.replace({ name: 'login', query })
}
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card">
      <h1>{{ t.login.register.title }}</h1>
      <p class="hint">{{ t.login.register.intro }}</p>
      <Message v-if="error" severity="error" class="banner-error" data-testid="register-error">{{ error }}</Message>
      <Message v-if="done" severity="success" data-testid="register-done">{{ t.login.register.done }}</Message>

      <form v-if="!done" @submit.prevent="submit">
        <div class="form-field">
          <label for="register-email">{{ t.login.email }}</label>
          <InputText id="register-email" v-model="email" type="email" autocomplete="username" required autofocus />
        </div>
        <div class="form-field">
          <label for="register-username">{{ t.login.register.username }}</label>
          <InputText id="register-username" v-model="username" autocomplete="nickname" autocapitalize="none" autocorrect="off" spellcheck="false" maxlength="32" data-testid="register-username" />
          <span class="hint">{{ t.login.register.usernameHelp }}</span>
        </div>
        <div class="form-field">
          <label for="register-password">{{ t.login.password }}</label>
          <Password input-id="register-password" v-model="password" :feedback="false" toggle-mask fluid autocomplete="new-password" required />
          <span class="hint">{{ t.login.passwordRules }}</span>
        </div>
        <div class="form-field">
          <label for="register-confirm">{{ t.login.reset.confirm }}</label>
          <Password input-id="register-confirm" v-model="confirm" :feedback="false" toggle-mask fluid autocomplete="new-password" required />
        </div>
        <Button type="submit" :label="t.login.register.submit" :loading="busy" fluid />
      </form>

      <div style="margin-top: 1rem; text-align: center">
        <Button link size="small" :label="t.login.register.hasAccount" @click="toLogin" />
      </div>
    </div>
  </div>
</template>
