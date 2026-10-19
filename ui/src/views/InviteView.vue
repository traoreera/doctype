<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { t } from '@/i18n'
import { useSession, type InviteInfo } from '@/stores/session'

/**
 * Acceptation d'une invitation (`#/invite?token=…`, lien d'e-mail vers le Desk, jamais un deep-link).
 * L'invitation se consulte sans être connecté ; l'accepter demande une session (le serveur vérifie que l'adresse correspond).
 */
const session = useSession()
const route = useRoute()
const router = useRouter()

const token = computed(() => (typeof route.query.token === 'string' ? route.query.token : ''))
const info = ref<InviteInfo | null>(null)
const loaded = ref(false)
const error = ref<string | null>(null)
const accepted = ref(false)
const busy = ref(false)

const intro = computed(() =>
  info.value?.tenantName ? t.login.invite.intro(info.value.tenantName) : t.login.invite.introGeneric,
)

onMounted(async () => {
  if (token.value) info.value = await session.fetchInvite(token.value)
  loaded.value = true
})

async function accept(): Promise<void> {
  error.value = null
  busy.value = true
  try {
    const out = await session.acceptInvite(token.value)
    if (out.status === 'ok') {
      accepted.value = true
      await session.loadTenants()
    } else {
      error.value = out.message
    }
  } finally {
    busy.value = false
  }
}

function login(): void {
  void router.push({ name: 'login', query: { next: route.fullPath } })
}

function home(): void {
  void router.replace({ name: 'home' })
}
</script>

<template>
  <div class="login-wrap">
    <div class="desk-card login-card">
      <h1>{{ t.login.invite.title }}</h1>
      <p v-if="loaded && token" class="hint">{{ intro }}</p>
      <Message v-if="!token || (loaded && !info)" severity="warn" class="banner-error" data-testid="invite-not-found">
        {{ t.login.invite.notFound }}
      </Message>
      <Message v-if="error" severity="error" class="banner-error" data-testid="invite-error">{{ error }}</Message>
      <Message v-if="accepted" severity="success" data-testid="invite-accepted">{{ t.login.invite.accepted }}</Message>

      <template v-if="loaded && token && info">
        <Button v-if="session.authenticated && !accepted" :label="t.login.invite.accept" :loading="busy" fluid @click="accept" />
        <Button v-if="accepted" :label="t.login.forgot.back" fluid @click="home" />
        <template v-if="!session.authenticated">
          <Message severity="info" class="banner-error">{{ t.login.invite.needsLogin }}</Message>
          <Button :label="t.login.submit" fluid @click="login" />
          <div style="margin-top: 1rem; text-align: center">
            <Button link size="small" :label="t.login.invite.createAccount" @click="router.push({ name: 'register', query: { next: route.fullPath } })" />
          </div>
        </template>
      </template>
    </div>
  </div>
</template>
