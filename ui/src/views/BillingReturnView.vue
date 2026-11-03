<script setup lang="ts">
/**
 * Le retour du prestataire de paiement (`#/billing?module=…&session_id=…`).
 *
 * Pourquoi une page à soi plutôt qu'un bout de code dans la liste des modules : le prestataire renvoie à une
 * adresse que le SERVEUR a construite, et le retour doit être confirmé (l'encaissement se vérifie chez le
 * prestataire, jamais sur la foi du navigateur). Une page nommée rend ce moment lisible — et c'est aussi là que
 * vit l'accès au portail de facturation.
 *
 * L'identifiant de session n'est pas une preuve : `confirm` le fait revérifier, et le serveur refuse si la
 * session ne désigne pas cette organisation et ce module.
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { describeError, useNotify } from '@/composables/useNotify'
import { useServices } from '@/lib/services'

const route = useRoute()
const { license } = useServices()
const notify = useNotify()

type State = 'idle' | 'working' | 'done' | 'cancelled' | 'failed'
const state = ref<State>('idle')
const detail = ref('')

const moduleCode = computed(() => String(route.query.module ?? '').trim())
const sessionId = computed(() => String(route.query.session_id ?? '').trim())
const cancelled = computed(() => route.query.cancelled === '1')

onMounted(async () => {
  if (!license) return void (state.value = 'failed', (detail.value = t.billing.unavailable))
  if (cancelled.value) return void (state.value = 'cancelled')
  if (!moduleCode.value || !sessionId.value) return void (state.value = 'failed', (detail.value = t.billing.noSession))
  state.value = 'working'
  try {
    await license.confirm(moduleCode.value, sessionId.value)
    state.value = 'done'
  } catch (e) {
    state.value = 'failed'
    // Sur CETTE page, la raison du serveur est ce que l'utilisateur doit lire : « cette session n'est pas la
    // vôtre » et « réservé aux administrateurs » appellent deux gestes différents. `describeError` remplace
    // tout 403 par un texte générique — bon réflexe ailleurs, trop vague au retour d'un paiement.
    detail.value = e instanceof ApiError && e.message ? e.message : describeError(e)
  }
})

async function openPortal(): Promise<void> {
  if (!license) return
  try {
    window.location.assign(await license.portal())
  } catch (e) {
    notify.error(e)
  }
}
</script>

<template>
  <section class="desk-page">
    <h1>{{ t.billing.returnTitle }}</h1>

    <div v-if="state === 'working'" class="billing-wait">
      <ProgressSpinner style="width: 2.5rem; height: 2.5rem" :aria-label="t.billing.confirming" />
      <p>{{ t.billing.confirming }}</p>
    </div>

    <Message v-else-if="state === 'done'" severity="success" :closable="false" data-testid="billing-done">
      {{ t.billing.confirmed(moduleCode) }}
    </Message>
    <Message v-else-if="state === 'cancelled'" severity="warn" :closable="false" data-testid="billing-cancelled">
      {{ t.billing.cancelled }}
    </Message>
    <Message v-else-if="state === 'failed'" severity="error" :closable="false" data-testid="billing-failed">
      {{ detail || t.billing.failed }}
    </Message>

    <div class="billing-actions">
      <Button as="router-link" :to="{ name: 'settings', params: { doctype: '~modules' } }" :label="t.billing.myModules" icon="pi pi-th-large" outlined />
      <Button
        v-if="license"
        :label="t.billing.portal"
        icon="pi pi-credit-card"
        severity="secondary"
        outlined
        data-testid="billing-portal"
        @click="openPortal"
      />
    </div>
  </section>
</template>

<style scoped>
.billing-wait {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.billing-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 1.25rem;
}
</style>
