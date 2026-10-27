<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import { statusTone, xflowTime, type FlowRun, type FlowSummary } from '@/lib/xflow'
import { useAutomations } from '@/stores/automations'

/**
 * Les automatisations de l'organisation (xflow), comme la liste des workflows de n8n : chacune avec son déclencheur et ses dernières
 * exécutions. xflow juge chaque droit : sans lui, la page dit qu'il manque ; sans le droit de lire, qu'elle est réservée.
 */
const { flows } = useServices()
const router = useRouter()
const automations = useAutomations()

const items = ref<FlowSummary[] | null>(null)
const runs = ref<FlowRun[]>([])
const failure = ref<string | null>(null)
const creating = ref(false)
const newName = ref('')

const nameProblem = computed(() =>
  !newName.value ? null
  : !/^[a-z][a-z0-9_]{0,99}$/.test(newName.value) ? t.automations.badName
  : items.value?.some((f) => f.name === newName.value) ? t.automations.takenName
  : null,
)
const lastRun = (name: string) => runs.value.find((r) => r.workflow_name === name) ?? null

async function load(): Promise<void> {
  failure.value = null
  await automations.probe()
  if (automations.availability !== 'yes' || !flows) return
  try {
    ;[items.value, runs.value] = await Promise.all([flows.flows(), flows.runs(undefined, 100).catch(() => [])])
  } catch (e) {
    failure.value = describeError(e)
  }
}

function create(): void {
  if (!newName.value || nameProblem.value) return
  creating.value = false
  void router.push({ name: 'automation', params: { flow: newName.value }, query: { new: '1' } })
}

onMounted(load)
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head">
      <h1>{{ t.automations.title }}</h1>
      <span class="desk-spacer" />
      <Button v-if="automations.availability === 'yes'" :label="t.automations.create" icon="pi pi-plus" size="small" data-testid="flow-create" @click="creating = true" />
    </div>
    <Message v-if="automations.availability === 'absent'" severity="info" class="banner-error" data-testid="flows-absent">{{ t.automations.absent }}</Message>
    <Message v-else-if="automations.availability === 'reserved'" severity="info" class="banner-error" data-testid="flows-reserved">{{ t.automations.reserved }}</Message>
    <Message v-else-if="failure" severity="error" class="banner-error" data-testid="flows-failed">{{ failure }}</Message>
    <template v-else-if="items">
      <p class="desk-muted">{{ t.automations.hint }}</p>
      <p v-if="!items.length" class="desk-muted" data-testid="flows-empty">{{ t.automations.empty }}</p>
      <ul v-else class="desk-card flow-list" data-testid="flows-list">
        <li v-for="f in items" :key="f.name" :data-testid="`flow-${f.name}`">
          <RouterLink class="desk-link flow-title" :to="{ name: 'automation', params: { flow: f.name } }">{{ f.name }}</RouterLink>
          <span class="status-pill">{{ t.automations.trigger[f.trigger] ?? f.trigger }}</span>
          <span v-if="f.description" class="desk-muted flow-desc">{{ f.description }}</span>
          <span class="flow-last">
            <template v-if="lastRun(f.name)">
              <span class="flow-pill" :data-tone="statusTone(lastRun(f.name)!.status)">{{ t.automations.status[lastRun(f.name)!.status] ?? lastRun(f.name)!.status }}</span>
              <span class="desk-muted">{{ relativeTime(xflowTime(lastRun(f.name)!.started_at) ?? '') }}</span>
            </template>
            <span v-else class="desk-muted">{{ t.automations.noRuns }}</span>
          </span>
        </li>
      </ul>
    </template>

    <Dialog v-model:visible="creating" modal :header="t.automations.create" :style="{ width: 'min(28rem, 94vw)' }">
      <form class="wf-field" @submit.prevent="create">
        <label for="flow-new-name">{{ t.automations.newName }}</label>
        <InputText id="flow-new-name" v-model.trim="newName" autofocus :invalid="!!nameProblem" data-testid="flow-new-name" />
        <span v-if="nameProblem" class="wf-help fb-error">{{ nameProblem }}</span>
        <span v-else class="desk-muted wf-help">{{ t.automations.newNameHint }}</span>
      </form>
      <template #footer>
        <Button :label="t.cancel" text severity="secondary" @click="creating = false" />
        <Button :label="t.automations.create" :disabled="!newName || !!nameProblem" data-testid="flow-new-ok" @click="create" />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.flow-list {
  list-style: none;
  margin: 0;
  padding: 0.5rem 1rem;
  display: grid;
}

.flow-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  padding: 0.7rem 0;
  border-bottom: 1px solid var(--p-content-border-color);
}

.flow-list li:last-child {
  border-bottom: none;
}

.flow-title {
  font-weight: 600;
}

.flow-desc {
  flex-basis: 100%;
  order: 3;
  font-size: 0.9rem;
}

.flow-last {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
}
</style>
