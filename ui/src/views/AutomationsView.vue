<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Select from 'primevue/select'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import LinkField from '@/components/LinkField.vue'
import { DOCUMENT_KEYS, fixedDoctypeOf, readFlows, readRuns, statusTone, triggerKeysUsed, type FlowSummary, type RunSummary } from '@/lib/automations'
import { formatTimestamp } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocTypeSummary } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * Automatisations : les flux xflow de l'organisation et leurs dernières exécutions (`lib/automations.ts`).
 *
 * Cette page dit ce qui EXISTE, ce qui a tourné et comment ça s'est terminé ; elle lance un flux « à la demande » et supprime celui
 * qu'on ne veut plus. Le tracé d'un flux, lui, est une page à part entière (`FlowBuilderView`, `#/automations/<nom>`) : un graphe ne
 * tient pas dans une boîte de dialogue. xflow juge chaque appel : un 403 se lit « réservé », jamais une panne.
 */

const { automations, api } = useServices()
const metaStore = useMeta()
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

/**
 * Lancer un flux à la main. Un flux qui lit « Du déclencheur » un DOCUMENT (son numéro, son type…) n'a rien à lire si on le lance
 * sans : l'étape échouait sur « champ obligatoire manquant : 'name' » (constaté). On demande donc d'abord sur quel document agir, et
 * on envoie ce que lui enverrait une transition (type, numéro, version, état du document).
 */
const asking = ref<FlowSummary | null>(null)
const pick = ref<{ doctype: string; name: string | null }>({ doctype: '', name: null })
const doctypes = ref<DocTypeSummary[]>([])

async function start(flow: FlowSummary): Promise<void> {
  if (!automations) return
  running.value = flow.name
  try {
    const body = (await automations.get(`/flows/${encodeURIComponent(flow.name)}`)) as { workflow?: unknown }
    const keys = triggerKeysUsed(body?.workflow)
    if (keys.some((k) => (DOCUMENT_KEYS as readonly string[]).includes(k))) {
      doctypes.value = (await metaStore.loadSummaries().catch(() => [])).filter((d) => !d.single)
      pick.value = { doctype: fixedDoctypeOf(body?.workflow) ?? '', name: null }
      asking.value = flow
      return
    }
    await launch(flow, {})
  } catch (e) {
    notify.error(e)
  } finally {
    running.value = null
  }
}

async function startOn(): Promise<void> {
  const flow = asking.value
  if (!flow || !pick.value.doctype || !pick.value.name) return
  running.value = flow.name
  try {
    const doc = await api.get(pick.value.doctype, pick.value.name)
    asking.value = null
    await launch(flow, { doctype: pick.value.doctype, name: doc.name, version: doc.version, docstatus: doc.docstatus ?? 0 })
  } catch (e) {
    notify.error(e)
  } finally {
    running.value = null
  }
}

async function launch(flow: FlowSummary, payload: Record<string, unknown>): Promise<void> {
  if (!automations) return
  running.value = flow.name
  try {
    const answer = await automations.post(`/run/${encodeURIComponent(flow.name)}`, payload)
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
const flowCount = computed(() => flows.value.length)
const runCount = computed(() => runs.value.length)
// Le contenu reste affiché pendant qu'on RELIT (actualiser, après un lancement ou un déploiement) : seule la première lecture le cache.
const loaded = ref(false)
const empty = computed(() => loaded.value && !failure.value && !reserved.value)
</script>

<template>
  <div class="desk-page automations" data-testid="automations">
    <header class="auto-hero">
      <div class="auto-hero-copy">
        <span class="auto-eyebrow"><i class="pi pi-bolt" /> {{ t.automations.group }}</span>
        <h1>{{ t.automations.title }}</h1>
        <p>{{ t.automations.intro }}</p>
      </div>
      <div class="auto-toolbar">
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
    </header>

    <Message v-if="reserved" severity="secondary" :closable="false" data-testid="automations-reserved">{{ t.automations.reserved }}</Message>
    <Message v-else-if="failure" severity="error" :closable="false" data-testid="automations-failure">{{ failure }}</Message>

    <template v-if="empty">
      <section class="desk-card auto-section">
        <div class="auto-section-head"><div><i class="pi pi-sitemap" /><h2>{{ t.automations.flows }}</h2></div><span class="auto-count">{{ flowCount }}</span></div>
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
        <div class="auto-section-head"><div><i class="pi pi-history" /><h2>{{ t.automations.runs }}</h2></div><span class="auto-count">{{ runCount }}</span></div>
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

    <!-- Lancer sur un document : celui que le flux lit « Du déclencheur ». -->
    <Dialog
      :visible="!!asking"
      modal
      :header="asking ? t.automations.runOnTitle(asking.name) : ''"
      :style="{ width: 'min(28rem, 92vw)' }"
      data-testid="run-on"
      @update:visible="asking = $event ? asking : null"
    >
      <p class="desk-muted">{{ t.automations.runOnHint }}</p>
      <label class="wf-field" for="run-on-doctype">
        <span>{{ t.automations.runOnDoctype }}</span>
        <Select
          input-id="run-on-doctype"
          :model-value="pick.doctype"
          :options="doctypes.map((d) => ({ value: d.name, label: d.label }))"
          option-label="label"
          option-value="value"
          filter
          size="small"
          data-testid="run-on-doctype"
          @update:model-value="pick = { doctype: String($event ?? ''), name: null }"
        />
      </label>
      <label v-if="pick.doctype" class="wf-field" for="run-on-name">
        <span>{{ t.automations.runOnDocument }}</span>
        <LinkField :key="pick.doctype" v-model="pick.name" :target="pick.doctype" input-id="run-on-name" data-testid="run-on-name" />
      </label>
      <template #footer>
        <Button :label="t.cancel" text severity="secondary" size="small" @click="asking = null" />
        <Button :label="t.automations.run" icon="pi pi-play" size="small" :disabled="!pick.doctype || !pick.name" :loading="!!running" data-testid="run-on-go" @click="startOn" />
      </template>
    </Dialog>
  </div>
</template>

<style scoped>
.automations {
  display: grid;
  gap: 1.25rem;
  max-width: 76rem;
  margin-inline: auto;
  padding-block: 1.5rem 3rem;
}

.auto-hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 1.5rem;
  padding: 1.35rem 1.5rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 14px;
  background: linear-gradient(120deg, color-mix(in srgb, var(--p-primary-color) 7%, var(--p-content-background)), var(--p-content-background) 62%);
}

.auto-hero-copy { min-width: 0; }
.auto-eyebrow { display: inline-flex; align-items: center; gap: .4rem; color: var(--p-primary-color); font-size: .75rem; font-weight: 650; letter-spacing: .04em; text-transform: uppercase; }
.auto-hero h1 { margin: .4rem 0 .35rem; font-size: clamp(1.5rem, 3vw, 1.9rem); letter-spacing: -.025em; }
.auto-hero p { max-width: 48rem; margin: 0; color: var(--p-text-muted-color); line-height: 1.55; }
.auto-toolbar { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: .35rem; }

.automations :deep(.desk-card) {
  padding: 0;
  overflow: hidden;
  border-radius: 12px;
}

.auto-section {
  display: grid;
  gap: 0;
  min-width: 0;
}

.auto-section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: .9rem 1rem;
  border-bottom: 1px solid var(--p-content-border-color);
}

.auto-section-head > div { display: flex; align-items: center; gap: .65rem; }
.auto-section-head > div > i { color: var(--p-primary-color); }
.auto-section h2 { font-size: .98rem; margin: 0; }
.auto-count { min-width: 1.75rem; padding: .15rem .45rem; border: 1px solid var(--p-content-border-color); border-radius: 999px; color: var(--p-text-muted-color); text-align: center; font-size: .75rem; font-variant-numeric: tabular-nums; }
.auto-section > .desk-muted { margin: 1rem; }
.auto-table tbody tr:hover { background: var(--p-highlight-background); }
.auto-table tr:last-child td { border-bottom: 0; }
.auto-table th { font-size: .74rem; letter-spacing: .035em; text-transform: uppercase; background: var(--p-highlight-background); }
.auto-table a { text-decoration: none; }
.auto-table a:hover { text-decoration: underline; }
.auto-error { color: var(--desk-danger); font-size: .82rem; }

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
  .automations { padding-block: .75rem 2rem; }
  .auto-hero { align-items: stretch; flex-direction: column; gap: 1rem; padding: 1rem; }
  .auto-toolbar { justify-content: flex-start; }

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
