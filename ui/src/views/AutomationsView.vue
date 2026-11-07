<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { readFlows, readRuns, statusTone, type FlowSummary, type RunSummary } from '@/lib/automations'
import { formatTimestamp } from '@/lib/format'
import { useServices } from '@/lib/services'

/**
 * Automatisations : les flux xflow de l'organisation et leurs dernières exécutions (`lib/automations.ts`).
 *
 * Cette page dit ce qui EXISTE, ce qui a tourné et comment ça s'est terminé ; elle lance un flux « à la demande » et supprime celui
 * qu'on ne veut plus. Le tracé d'un flux, lui, est une page à part entière (`FlowBuilderView`, `#/automations/<nom>`) : un graphe ne
 * tient pas dans une boîte de dialogue. xflow juge chaque appel : un 403 se lit « réservé », jamais une panne.
 */

const { automations } = useServices()
const notify = useNotify()
const confirm = useConfirm()

const flows = ref<FlowSummary[]>([])
const runs = ref<RunSummary[]>([])
const loading = ref(true)
const failure = ref<string | null>(null)
const reserved = ref(false)
const running = ref<string | null>(null)

/** xflow répond aussi ses refus dans un corps 200 (`{status: "error", msg}`) : son message, tel quel. */
class Refused extends Error {}

function refused(body: unknown): string | null {
  const b = body as { status?: unknown; message?: unknown; msg?: unknown } | null
  return b?.status === 'error' ? String(b.msg ?? b.message ?? t.automations.loadFailed) : null
}

async function load(): Promise<void> {
  if (!automations) return void ((failure.value = t.automations.loadFailed), (loading.value = false))
  loading.value = true
  try {
    const [f, r] = await Promise.all([automations.get('/flows'), automations.get('/executions', { limit: 20 })])
    const problem = refused(f) ?? refused(r)
    if (problem) throw new Refused(problem)
    flows.value = readFlows(f)
    runs.value = readRuns(r)
    failure.value = null
    reserved.value = false
  } catch (e) {
    reserved.value = e instanceof ApiError && e.status === 403
    failure.value = reserved.value ? null : e instanceof Refused ? e.message : describeError(e) || t.automations.loadFailed
  } finally {
    loading.value = false
    loaded.value = true
  }
}
onMounted(load)

async function start(flow: FlowSummary): Promise<void> {
  if (!automations) return
  running.value = flow.name
  try {
    const answer = await automations.post(`/run/${encodeURIComponent(flow.name)}`, {})
    const problem = refused(answer)
    if (problem) throw new Refused(problem)
    notify.success(t.automations.started_(flow.name))
    await load()
  } catch (e) {
    if (e instanceof Refused) notify.warn(e.message)
    else notify.error(e)
  } finally {
    running.value = null
  }
}

function remove(flow: FlowSummary): void {
  confirm.require({
    header: t.automations.remove,
    message: t.automations.removeConfirm(flow.name),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.automations.remove,
    rejectLabel: t.automations.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      if (!automations) return
      try {
        const answer = await automations.delete(`/flows/${encodeURIComponent(flow.name)}`)
        const problem = refused(answer)
        if (problem) throw new Refused(problem)
        notify.success(t.automations.removed(flow.name))
        await load()
      } catch (e) {
        if (e instanceof Refused) notify.warn(e.message)
        else notify.error(e)
      }
    },
  })
}

const label = (map: Record<string, string>, key: string | null) => (key ? map[key] ?? key : '—')
// Le contenu reste affiché pendant qu'on RELIT (actualiser, après un lancement ou un déploiement) : seule la première lecture le cache.
const loaded = ref(false)
const empty = computed(() => loaded.value && !failure.value && !reserved.value)
</script>

<template>
  <div class="desk-page automations" data-testid="automations">
    <div class="desk-page-head">
      <h1>{{ t.automations.title }}</h1>
      <span class="desk-spacer" />
      <Button :label="t.automations.refresh" icon="pi pi-refresh" text severity="secondary" size="small" data-testid="automations-refresh" @click="load" />
      <Button :as="RouterLink" :to="{ name: 'flow-nodes' }" :label="t.automations.customNodes" icon="pi pi-box" text severity="secondary" size="small" data-testid="automations-nodes" />
      <Button
        :as="RouterLink"
        :to="{ name: 'flow-new' }"
        :label="t.automations.create"
        icon="pi pi-plus"
        size="small"
        data-testid="automations-create"
      />
    </div>
    <p class="desk-muted">{{ t.automations.intro }}</p>

    <Message v-if="reserved" severity="secondary" :closable="false" data-testid="automations-reserved">{{ t.automations.reserved }}</Message>
    <Message v-else-if="failure" severity="error" :closable="false" data-testid="automations-failure">{{ failure }}</Message>

    <template v-if="empty">
      <section class="desk-card auto-section">
        <h2>{{ t.automations.flows }}</h2>
        <p v-if="!flows.length" class="desk-muted" data-testid="automations-no-flows">{{ t.automations.noFlows }}</p>
        <table v-else class="auto-table" data-testid="automations-flows">
          <thead>
            <tr><th>{{ t.automations.name }}</th><th>{{ t.automations.trigger }}</th><th>{{ t.automations.description }}</th><th /></tr>
          </thead>
          <tbody>
            <tr v-for="f in flows" :key="f.name" :data-testid="`flow-${f.name}`">
              <td :data-label="t.automations.name">
                <RouterLink :to="{ name: 'flow', params: { name: f.name } }" :data-testid="`flow-open-${f.name}`"><code>{{ f.name }}</code></RouterLink>
              </td>
              <td :data-label="t.automations.trigger">{{ label(t.automations.triggers, f.trigger) }}</td>
              <td :data-label="t.automations.description">{{ f.description || '—' }}</td>
              <td class="auto-actions">
                <Button
                  :as="RouterLink"
                  :to="{ name: 'flow', params: { name: f.name } }"
                  icon="pi pi-pencil"
                  text
                  rounded
                  size="small"
                  :aria-label="`${t.automations.edit} ${f.name}`"
                  :data-testid="`flow-edit-${f.name}`"
                />
                <Button icon="pi pi-trash" text rounded size="small" severity="danger" :aria-label="`${t.automations.remove} ${f.name}`" :data-testid="`flow-remove-${f.name}`" @click="remove(f)" />
                <Button
                  v-if="f.trigger === 'manual'"
                  :label="t.automations.run"
                  icon="pi pi-play"
                  size="small"
                  severity="secondary"
                  :loading="running === f.name"
                  :disabled="running !== null"
                  :data-testid="`flow-run-${f.name}`"
                  @click="start(f)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="desk-card auto-section">
        <h2>{{ t.automations.runs }}</h2>
        <p v-if="!runs.length" class="desk-muted" data-testid="automations-no-runs">{{ t.automations.noRuns }}</p>
        <table v-else class="auto-table" data-testid="automations-runs">
          <thead>
            <tr>
              <th>{{ t.automations.name }}</th><th>{{ t.automations.status }}</th><th>{{ t.automations.started }}</th>
              <th>{{ t.automations.origin }}</th><th>{{ t.automations.error }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in runs" :key="r.id" :data-testid="`run-${r.id}`">
              <td :data-label="t.automations.name"><code>{{ r.flow }}</code></td>
              <td :data-label="t.automations.status"><span class="status-pill" :class="statusTone(r.status)">{{ label(t.automations.statuses, r.status) }}</span></td>
              <td :data-label="t.automations.started">{{ r.startedAt ? formatTimestamp(r.startedAt) : '—' }}</td>
              <td :data-label="t.automations.origin">
                <RouterLink v-if="r.document" :to="{ name: 'form', params: { doctype: r.document.doctype, name: r.document.name } }">
                  {{ r.document.action ?? r.document.doctype }} · {{ r.document.name }}
                </RouterLink>
                <template v-else>{{ label(t.automations.origins, r.via) }}</template>
              </td>
              <td :data-label="t.automations.error" class="auto-error">{{ r.error || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.automations {
  display: grid;
  gap: 1rem;
}

.auto-section {
  display: grid;
  gap: 0.6rem;
  min-width: 0;
}

.auto-section h2 {
  font-size: 1rem;
  margin: 0;
}

.auto-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.auto-table th,
.auto-table td {
  text-align: left;
  padding: 0.45rem 0.5rem;
  border-bottom: 1px solid var(--p-content-border-color);
  vertical-align: top;
}

.auto-table th {
  color: var(--p-text-muted-color);
  font-weight: 500;
}

.auto-actions {
  text-align: right;
  white-space: nowrap;
}

.auto-error {
  overflow-wrap: anywhere;
  max-width: 24rem;
}

@media (max-width: 700px) {
  .auto-table thead {
    display: none;
  }

  .auto-table tr {
    display: grid;
    border-bottom: 1px solid var(--p-content-border-color);
    padding: 0.4rem 0;
  }

  .auto-table td {
    border: 0;
    padding: 0.15rem 0;
  }

  .auto-table td[data-label]::before {
    content: attr(data-label) ' : ';
    color: var(--p-text-muted-color);
  }

  .auto-actions {
    text-align: left;
  }
}
</style>
