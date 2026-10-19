<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { describeError, useNotify } from '@/composables/useNotify'
import { PROFILE_DOCTYPE, useProfile } from '@/stores/profile'
import { useSession } from '@/stores/session'

/** « Mon profil » : mène à MA fiche (un document par utilisateur, nommé comme son compte), ou à sa création s'il n'existe pas encore. */
const router = useRouter()
const profile = useProfile()
const session = useSession()
const notify = useNotify()

onMounted(async () => {
  const me = session.claims?.sub
  try {
    if (me && (await profile.exists())) await router.replace({ name: 'form', params: { doctype: PROFILE_DOCTYPE, name: me } })
    else await router.replace({ name: 'new', params: { doctype: PROFILE_DOCTYPE } })
  } catch (e) {
    notify.error(describeError(e))
    await router.replace({ name: 'home' })
  }
})
</script>

<template>
  <div class="desk-muted" style="padding: 2rem" data-testid="profile-loading"><i class="pi pi-spin pi-spinner" /></div>
</template>
