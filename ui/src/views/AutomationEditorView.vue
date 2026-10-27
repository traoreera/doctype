<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Textarea from 'primevue/textarea'
import { Background } from '@vue-flow/background'
import { MarkerType, VueFlow, useVueFlow, type Connection, type NodeDragEvent } from '@vue-flow/core'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import FlowEdge from '@/components/automation/FlowEdge.vue'
import FlowNode from '@/components/automation/FlowNode.vue'
import RunsPanel from '@/components/automation/RunsPanel.vue'
import StepPanel from '@/components/automation/StepPanel.vue'
import TriggerPanel from '@/components/automation/TriggerPanel.vue'
import CanvasControls from '@/components/workflow/CanvasControls.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import {
  DOC_ACTIONS, TRIGGER_ID, addStep, arrange, blankStep, connect, disconnect, docActionOf, docStep, edgesOf, emptyFlow, forDeploy, moveNode,
  parseDocEvent, patchPayload, patchStep, payloadOf, portsOf, positions, problems, removeStep, renameStep, setCase, stepIcon, triggerIcon,
  variablesFor, type DocAction, type FlowDefinition, type FlowStep, type NewStep, type Problem, type StepType,
} from '@/lib/automation'
import { useServices } from '@/lib/services'
import type { DocTypeMeta } from '@/lib/types'
import type { CatalogAction, CatalogEvent, FlowRun } from '@/lib/xflow'
import { useAutomations } from '@/stores/automations'
import { useMeta } from '@/stores/meta'
import { useSession } from '@/stores/session'
import { useUsers } from '@/stores/users'

/**
 * L'éditeur d'une automatisation xflow, à la manière de n8n : à gauche la palette (des nœuds Document propres au Desk, la logique, les
 * données, les actions des autres plugins), au centre le canevas — le déclencheur puis les étapes, reliées en tirant d'une sortie vers
 * une carte —, à droite les propriétés de ce qu'on a choisi, ou les exécutions. Ce qu'on édite EST la définition de xflow
 * (`lib/automation`) : l'enregistrer la déploie, et xflow retient qui l'a enregistrée — ses étapes agiront avec ses droits du moment.
 *
 * Les droits sont ceux de xflow (`xflow:workflows:write` pour modifier, `xflow:runs:write` pour exécuter) ; le Desk ne fait que griser
 * ce qu'il sait refusé, xflow reste juge.
 */
const props = defineProps<{ flow: string }>()
const { flows } = useServices()
const route = useRoute()
const router = useRouter()
const notify = useNotify()
const metaStore = useMeta()
const session = useSession()
const users = useUsers()
const automations = useAutomations()
const FLOW_ID = 'automation'
const { project, vueFlowRef, fitView } = useVueFlow(FLOW_ID)

const def = ref<FlowDefinition | null>(null)
const savedJson = ref('')
const isNew = ref(false)
const failure = ref('')
const notFound = ref(false)
const saveProblem = ref<string | null>(null)
const saving = ref(false)
const loading = ref(true)
const selected = ref<string | null>(null)
const selectedEdge = ref<string | null>(null)
const tab = ref<'properties' | 'runs'>('properties')
const doctypes = ref<{ name: string; label: string }[]>([])
const catalog = ref<CatalogAction[]>([])
const events = ref<CatalogEvent[]>([])
const metas = ref<Record<string, DocTypeMeta | null>>({})

const runs = ref<FlowRun[] | null>(null)
const runsLoading = ref(false)
const viewing = ref<FlowRun | null>(null)
const running = ref(false)
const runDialog = ref(false)
const runPayload = ref('{}')
let poll: ReturnType<typeof setTimeout> | null = null

const perms = computed(() => session.permissions)
const has = (grant: string) => perms.value === null || perms.value.includes(grant) || perms.value.includes('admin:*')
const canEdit = computed(() => has('xflow:workflows:write') && !viewing.value)
const canRun = computed(() => has('xflow:runs:write'))
const dirty = computed(() => !!def.value && (isNew.value || JSON.stringify(def.value) !== savedJson.value))
const issues = computed<Problem[]>(() => (def.value ? problems(def.value) : []))
const step = computed<FlowStep | null>(() => def.value?.steps.find((s) => s.id === selected.value) ?? null)
const triggerDoctype = computed(() => parseDocEvent(def.value?.trigger.event_name)?.doctype ?? '')

// ── Les types de document cités : leurs champs nourrissent les variables et le panneau des nœuds Document ──
const citedDoctypes = computed(() => {
  const out = new Set<string>()
  if (triggerDoctype.value) out.add(triggerDoctype.value)
  for (const s of def.value?.steps ?? []) if (docActionOf(s) && typeof payloadOf(s).doctype === 'string' && payloadOf(s).doctype) out.add(payloadOf(s).doctype as string)
  return [...out]
})
watch(citedDoctypes, (list) => {
  for (const dt of list) {
    if (dt in metas.value) continue
    metas.value = { ...metas.value, [dt]: null }
    metaStore.get(dt).then((m) => (metas.value = { ...metas.value, [dt]: m }), () => {})
  }
}, { immediate: true })
const fieldsOf = (dt: string) => metas.value[dt]?.fields.filter((f) => f.fieldtype !== 'Password').map((f) => f.fieldname) ?? null
const variables = computed(() => (def.value ? variablesFor(def.value, selected.value === TRIGGER_ID ? null : selected.value, fieldsOf) : []))
const stepMeta = computed(() => {
  const dt = step.value ? payloadOf(step.value).doctype : null
  return typeof dt === 'string' ? metas.value[dt] ?? null : null
})

// ── Le canevas ──
function subtitle(s: FlowStep): string {
  const doc = docActionOf(s)
  if (doc) return [t.automations.docActions[doc], payloadOf(s).doctype].filter(Boolean).join(' · ')
  if (s.type === 'action') return [s.plugin, s.action].filter(Boolean).join('.') || t.automations.incomplete
  return t.automations.steps[s.type] ?? s.type
}
function triggerTitle(d: FlowDefinition): string {
  const doc = parseDocEvent(d.trigger.event_name)
  if (d.trigger.type === 'event' && doc) return t.automations.whenDoc(metaLabel(doc.doctype), t.automations.docEvents[doc.event] ?? doc.event)
  if (d.trigger.type === 'event') return d.trigger.event_name || t.automations.incomplete
  if (d.trigger.type === 'schedule') return d.trigger.cron || (d.trigger.interval_seconds ? `${d.trigger.interval_seconds} s` : t.automations.incomplete)
  return t.automations.trigger[d.trigger.type] ?? d.trigger.type
}
const metaLabel = (dt: string) => doctypes.value.find((d) => d.name === dt)?.label ?? dt
const flawed = computed(() => new Set(issues.value.flatMap((p) => ('step' in p ? [p.step] : p.code === 'event' || p.code === 'schedule' ? [TRIGGER_ID] : []))))

const nodes = computed(() => {
  const d = def.value
  if (!d) return []
  const at = positions(d)
  const status = (id: string) => (viewing.value ? viewing.value.steps[id]?.status ?? 'skipped' : null)
  return [
    {
      id: TRIGGER_ID, type: 'step', position: at[TRIGGER_ID]!, selected: selected.value === TRIGGER_ID,
      data: { title: triggerTitle(d), subtitle: t.automations.trigger[d.trigger.type], icon: triggerIcon(d.trigger), ports: [{ id: 'start', label: '', tone: 'neutral' as const }], trigger: true, status: null, problem: flawed.value.has(TRIGGER_ID), canEdit: canEdit.value },
    },
    ...d.steps.map((s) => ({
      id: s.id, type: 'step', position: at[s.id]!, selected: selected.value === s.id,
      data: { title: s.description || s.id, subtitle: subtitle(s), icon: stepIcon(s), ports: portsOf(s), status: status(s.id), problem: flawed.value.has(s.id), canEdit: canEdit.value },
    })),
  ]
})
const edges = computed(() =>
  def.value
    ? edgesOf(def.value).map((e) => ({
        id: e.id, source: e.source, target: e.target, sourceHandle: e.port, targetHandle: e.source === TRIGGER_ID ? 'in' : 'in', type: 'flow',
        markerEnd: MarkerType.ArrowClosed, selected: selectedEdge.value === e.id, data: { label: e.label, tone: e.tone, canEdit: canEdit.value },
      }))
    : [],
)

function set(next: FlowDefinition): void {
  def.value = next
  saveProblem.value = null
}
function choose(id: string | null): void {
  selected.value = id
  selectedEdge.value = null
  if (id && !viewing.value) tab.value = 'properties'
}
function onConnect(c: Connection): void {
  if (!def.value || !canEdit.value || !c.sourceHandle) return
  set(connect(def.value, c.source, c.sourceHandle, c.target))
}
function unlink(edgeId: string): void {
  const e = def.value && edgesOf(def.value).find((x) => x.id === edgeId)
  if (!e || !def.value) return
  set(disconnect(def.value, e.source, e.port))
  selectedEdge.value = null
}
function onDragStop(e: NodeDragEvent): void {
  if (!def.value || !canEdit.value) return
  let next = def.value
  for (const n of e.nodes ?? [e.node]) next = moveNode(next, n.id, n.position.x, n.position.y)
  set(next)
}

// ── La palette : glisser sur le canevas, ou cliquer (ajouté à droite de l'étape choisie, et relié à elle) ──
type PaletteItem = { key: string; label: string; icon: string; make: () => NewStep }
const palette = computed<{ group: string; items: PaletteItem[] }[]>(() => [
  {
    group: 'documents',
    items: DOC_ACTIONS.map((a: DocAction) => ({ key: a, label: t.automations.docActions[a] ?? a, icon: stepIcon(docStep(a, '') as FlowStep), make: () => docStep(a, triggerDoctype.value) })),
  },
  { group: 'logic', items: (['condition', 'switch', 'foreach', 'parallel', 'wait'] as StepType[]).map(stepItem) },
  { group: 'data', items: (['transform', 'template', 'ai'] as StepType[]).map(stepItem) },
  { group: 'plugins', items: [stepItem('action')] },
])
function stepItem(type: StepType): PaletteItem {
  return { key: type, label: t.automations.steps[type] ?? type, icon: stepIcon({ id: type, type }), make: () => blankStep(type, type) }
}
const PALETTE_MIME = 'text/x-xdt-flow-step'
const byKey = (key: string) => palette.value.flatMap((g) => g.items).find((i) => i.key === key)

function place(item: PaletteItem, at?: { x: number; y: number }): void {
  if (!def.value || !canEdit.value) return
  const from = selected.value
  const origin = from ? positions(def.value)[from] : null
  const where = at ?? (origin ? { x: origin.x + 280, y: origin.y } : { x: 280, y: 40 + def.value.steps.length * 40 })
  const added = addStep(def.value, item.make(), where)
  let next = added.def
  // Cliqué avec une étape choisie : la nouvelle s'y accroche (sa première sortie libre), comme le « + » d'un nœud n8n.
  if (!at && from) {
    const src = from === TRIGGER_ID ? null : next.steps.find((s) => s.id === from)
    if (from === TRIGGER_ID && !next.entry_step && next.steps.length === 1) next = connect(next, TRIGGER_ID, 'start', added.id)
    else if (src) {
      const free = portsOf(src).find((p) => !edgesOf(next).some((e) => e.source === src.id && e.port === p.id))
      if (free) next = connect(next, src.id, free.id, added.id)
    }
  }
  set(next)
  choose(added.id)
}
function dragItem(e: DragEvent, item: PaletteItem): void {
  e.dataTransfer?.setData(PALETTE_MIME, item.key)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
}
function overCanvas(e: DragEvent): void {
  if (canEdit.value && e.dataTransfer?.types.includes(PALETTE_MIME)) e.preventDefault()
}
function dropOnCanvas(e: DragEvent): void {
  const key = e.dataTransfer?.getData(PALETTE_MIME)
  const item = key ? byKey(key) : null
  const box = vueFlowRef.value?.getBoundingClientRect()
  if (!item || !box) return
  e.preventDefault()
  place(item, project({ x: e.clientX - box.left - 110, y: e.clientY - box.top - 30 }))
}

// ── Les retouches du panneau ──
function patch(values: Record<string, unknown>): void {
  if (def.value && step.value) set(patchStep(def.value, step.value.id, values))
}
function patchData(values: Record<string, unknown>): void {
  if (def.value && step.value) set(patchPayload(def.value, step.value.id, values))
}
function rename(to: string): void {
  if (!def.value || !step.value) return
  const next = renameStep(def.value, step.value.id, to)
  if (next) {
    set(next)
    selected.value = to
  }
}
function remove(): void {
  if (!def.value || !step.value) return
  set(removeStep(def.value, step.value.id))
  choose(null)
}
function link(port: string, target: string | null): void {
  if (!def.value || !step.value) return
  set(target ? connect(def.value, step.value.id, port, target) : disconnect(def.value, step.value.id, port))
}
function editCase(from: string | null, to: string | null): void {
  if (def.value && step.value) set(setCase(def.value, step.value.id, from, to))
}
const targets = computed(() => (def.value?.steps ?? []).filter((s) => s.id !== selected.value).map((s) => ({ id: s.id, label: s.description ? `${s.description} (${s.id})` : s.id })))
const problemText = (p: Problem): string =>
  p.code === 'step_id' ? t.automations.problems.step_id(p.step)
  : p.code === 'missing' ? t.automations.problems.missing(p.step, p.field)
  : p.code === 'unreachable' ? t.automations.problems.unreachable(p.step)
  : t.automations.problems[p.code]

// ── Charger, enregistrer, supprimer ──
async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  notFound.value = false
  await automations.probe()
  if (automations.availability !== 'yes' || !flows) {
    loading.value = false
    return
  }
  try {
    const [summaries] = await Promise.all([
      metaStore.loadSummaries(),
      session.permissions === null ? session.loadPermissions().catch(() => {}) : Promise.resolve(),
    ])
    doctypes.value = summaries.filter((d) => !d.single).map((d) => ({ name: d.name, label: d.label || d.name })).sort((a, b) => a.label.localeCompare(b.label))
    void flows.catalog().then((c) => (catalog.value = c.filter((a) => a.plugin !== 'xcore-doctype')), () => {})
    void flows.events().then((e) => (events.value = e), () => {})
    try {
      def.value = await flows.flow(props.flow)
      isNew.value = false
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e
      if (route.query.new !== '1') {
        notFound.value = true
        return
      }
      def.value = emptyFlow(props.flow)
      isNew.value = true
    }
    savedJson.value = JSON.stringify(def.value)
    if (def.value.deployed_by) users.want([def.value.deployed_by])
    void loadRuns()
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!def.value || !flows || issues.value.length) return
  saving.value = true
  saveProblem.value = null
  try {
    await flows.deploy(forDeploy(def.value))
    def.value = await flows.flow(def.value.name) // xflow a posé qui l'a déployée
    savedJson.value = JSON.stringify(def.value)
    if (isNew.value) {
      isNew.value = false
      void router.replace({ name: 'automation', params: { flow: def.value.name } })
    }
    if (def.value.deployed_by) users.want([def.value.deployed_by])
    notify.success(t.automations.saved)
  } catch (e) {
    saveProblem.value = describeError(e) // le refus de xflow (validation pydantic, droits) reste sous les yeux
  } finally {
    saving.value = false
  }
}

async function removeFlow(): Promise<void> {
  if (!def.value || !flows || !window.confirm(t.automations.removeConfirm(def.value.name))) return
  try {
    await flows.remove(def.value.name)
    savedJson.value = JSON.stringify(def.value)
    isNew.value = false
    notify.success(t.automations.removed)
    void router.push({ name: 'automations' })
  } catch (e) {
    notify.error(e)
  }
}

// ── Exécuter, regarder les exécutions ──
async function loadRuns(): Promise<void> {
  if (!flows || isNew.value) return
  runsLoading.value = true
  try {
    runs.value = await flows.runs(props.flow)
    if (viewing.value) viewing.value = await flows.runDetail(viewing.value.run_id)
  } catch {
    runs.value = runs.value ?? []
  } finally {
    runsLoading.value = false
  }
}
const payloadBad = computed(() => {
  try {
    const v = JSON.parse(runPayload.value) as unknown
    return !v || typeof v !== 'object' || Array.isArray(v)
  } catch {
    return true
  }
})
async function runNow(): Promise<void> {
  if (!flows || !def.value || payloadBad.value) return
  running.value = true
  try {
    const id = await flows.run(def.value.name, JSON.parse(runPayload.value) as Record<string, unknown>)
    runDialog.value = false
    notify.success(t.automations.runStarted)
    tab.value = 'runs'
    void follow(id, 0)
  } catch (e) {
    notify.error(e)
  } finally {
    running.value = false
  }
}
/** Suivre une exécution jusqu'à sa fin (au plus une minute) : le canevas s'allume étape par étape. */
async function follow(id: string, round: number): Promise<void> {
  if (!flows) return
  try {
    viewing.value = await flows.runDetail(id)
  } catch {
    return
  }
  if (['pending', 'running'].includes(viewing.value.status) && round < 40) poll = setTimeout(() => void follow(id, round + 1), 1500)
  else void loadRuns()
}
async function show(run: FlowRun | null): Promise<void> {
  if (poll) clearTimeout(poll)
  viewing.value = null
  if (run && flows) {
    try {
      viewing.value = await flows.runDetail(run.run_id)
    } catch (e) {
      notify.error(e)
    }
  }
}
async function cancelRun(id: string): Promise<void> {
  try {
    await flows?.cancelRun(id)
    await loadRuns()
  } catch (e) {
    notify.error(e)
  }
}
const viewedStep = computed(() => (viewing.value && selected.value && selected.value !== TRIGGER_ID ? viewing.value.steps[selected.value] ?? null : null))

onBeforeRouteLeave(() => !dirty.value || window.confirm(t.automations.leave))
watch(() => props.flow, () => {
  choose(null)
  void show(null)
  void load()
})
onMounted(load)
onBeforeUnmount(() => {
  if (poll) clearTimeout(poll)
})
</script>

<template>
  <div class="desk-page af-page">
    <div class="desk-page-head">
      <h1 data-testid="af-title">{{ t.automations.editorTitle(flow) }}</h1>
      <span class="desk-spacer" />
      <Button :as="RouterLink" :to="{ name: 'automations' }" :label="t.automations.back" icon="pi pi-list" text severity="secondary" size="small" />
      <template v-if="def">
        <Button v-if="!isNew && has('xflow:workflows:write')" icon="pi pi-trash" text severity="danger" size="small" :aria-label="t.automations.remove" :title="t.automations.remove" data-testid="af-delete" @click="removeFlow" />
        <Button v-if="canRun" :label="t.automations.run" icon="pi pi-play" severity="secondary" outlined size="small" :disabled="isNew || dirty" data-testid="af-run" @click="runDialog = true" />
        <Button v-if="has('xflow:workflows:write')" :label="t.automations.save" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty || issues.length > 0 || !!viewing" data-testid="af-save" @click="save" />
      </template>
    </div>

    <Message v-if="automations.availability === 'absent'" severity="info" class="banner-error">{{ t.automations.absent }}</Message>
    <Message v-else-if="automations.availability === 'reserved'" severity="info" class="banner-error">{{ t.automations.reserved }}</Message>
    <Message v-else-if="failure" severity="error" class="banner-error" data-testid="af-error">{{ failure }}</Message>
    <p v-else-if="notFound" class="desk-muted" data-testid="af-not-found">{{ t.automations.notFound }}</p>

    <template v-if="def">
      <Message v-if="!has('xflow:workflows:write')" severity="info" class="banner-error" data-testid="af-readonly">{{ t.automations.readOnly }}</Message>
      <Message v-if="saveProblem" severity="error" class="banner-error" data-testid="af-save-problem">{{ saveProblem }}</Message>
      <p v-if="def.deployed_by" class="desk-muted af-signed">{{ t.automations.signedBy(users.label(def.deployed_by)) }}</p>

      <div class="af-body">
        <aside class="af-palette" :aria-label="t.automations.palette" data-testid="af-palette">
          <p class="desk-muted wf-help">{{ t.automations.paletteHint }}</p>
          <section v-for="g in palette" :key="g.group" class="af-palette-group">
            <h3 class="wf-section">{{ t.automations.groups[g.group] }}</h3>
            <button
              v-for="item in g.items"
              :key="item.key"
              type="button"
              class="af-palette-item"
              :draggable="canEdit"
              :disabled="!canEdit"
              :data-testid="`af-add-${item.key}`"
              @dragstart="dragItem($event, item)"
              @click="place(item)"
            >
              <span :class="item.icon" aria-hidden="true" />{{ item.label }}
            </button>
          </section>
        </aside>

        <div class="af-canvas" data-testid="af-canvas" @dragover="overCanvas" @drop="dropOnCanvas">
          <VueFlow
            :id="FLOW_ID"
            :nodes="nodes"
            :edges="edges"
            :nodes-connectable="canEdit"
            :nodes-draggable="canEdit"
            :edges-updatable="false"
            :delete-key-code="null"
            :nodes-focusable="false"
            :edges-focusable="false"
            disable-keyboard-a11y
            :min-zoom="0.25"
            :max-zoom="2"
            fit-view-on-init
            :aria-label="t.automations.canvas"
            @connect="onConnect"
            @node-drag-stop="onDragStop"
            @edge-click="(e) => ((selectedEdge = e.edge.id), (selected = null))"
            @pane-click="choose(null)"
          >
            <template #node-step="node">
              <FlowNode :id="node.id" :data="node.data" :selected="node.selected" @choose="choose" />
            </template>
            <template #edge-flow="edge">
              <FlowEdge v-bind="edge" @unlink="unlink" />
            </template>
            <Background :gap="18" :size="1.2" pattern-color="var(--desk-grid-dot)" />
            <CanvasControls @arrange="def && set(arrange(def)), fitView({ padding: 0.2 })" />
          </VueFlow>
        </div>

        <aside class="af-panel wf-panel" :aria-label="t.automations.properties" data-testid="af-panel">
          <div class="af-tabs" role="tablist">
            <button v-for="k in (['properties', 'runs'] as const)" :key="k" type="button" role="tab" class="fb-tab" :class="{ 'fb-tab-active': tab === k }" :aria-selected="tab === k ? 'true' : 'false'" :data-testid="`af-tab-${k}`" @click="tab = k">
              {{ t.automations.tabs[k] }}
            </button>
          </div>

          <RunsPanel
            v-if="tab === 'runs' || viewing"
            :runs="runs"
            :current="viewing"
            :step="viewedStep"
            :step-label="selected"
            :loading="runsLoading"
            :can-run="canRun"
            @show="show"
            @refresh="loadRuns"
            @cancel="cancelRun"
          />
          <TriggerPanel
            v-else-if="selected === TRIGGER_ID"
            :trigger="def.trigger"
            :doctypes="doctypes"
            :events="events"
            :can-edit="canEdit"
            @patch="def && set({ ...def, trigger: { ...def.trigger, ...$event } })"
          />
          <StepPanel
            v-else-if="step"
            :key="step.id"
            :step="step"
            :variables="variables"
            :doctypes="doctypes"
            :meta="stepMeta"
            :targets="targets"
            :catalog="catalog"
            :can-edit="canEdit"
            @patch="patch"
            @payload="patchData"
            @rename="rename"
            @remove="remove"
            @link="link"
            @case="editCase"
          />
          <div v-else class="af-panel-body">
            <h2 class="wf-panel-title">{{ t.automations.overview }}</h2>
            <label class="wf-field" for="af-flow-desc">
              <span>{{ t.automations.description }}</span>
              <InputText id="af-flow-desc" :model-value="def.description ?? ''" size="small" :disabled="!canEdit" data-testid="af-flow-desc" @update:model-value="def && set({ ...def, description: $event || null })" />
            </label>
            <p class="desk-muted">{{ t.automations.pick }}</p>
            <ul v-if="issues.length" class="af-issues" data-testid="af-issues">
              <li v-for="(p, i) in issues" :key="i">
                <button v-if="'step' in p" type="button" class="wf-link" @click="choose(p.step)">{{ problemText(p) }}</button>
                <button v-else-if="p.code === 'event' || p.code === 'schedule'" type="button" class="wf-link" @click="choose(TRIGGER_ID)">{{ problemText(p) }}</button>
                <span v-else>{{ problemText(p) }}</span>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </template>

    <Dialog v-model:visible="runDialog" modal :header="t.automations.run" :style="{ width: 'min(30rem, 94vw)' }">
      <label class="wf-field" for="af-run-payload">
        <span>{{ t.automations.runPayload }}</span>
        <Textarea id="af-run-payload" v-model="runPayload" rows="5" auto-resize class="af-code" :invalid="payloadBad" data-testid="af-run-payload" />
        <span v-if="payloadBad" class="wf-help fb-error">{{ t.automations.badJson }}</span>
      </label>
      <template #footer>
        <Button :label="t.cancel" text severity="secondary" @click="runDialog = false" />
        <Button :label="t.automations.run" icon="pi pi-play" :loading="running" :disabled="payloadBad" data-testid="af-run-ok" @click="runNow" />
      </template>
    </Dialog>
  </div>
</template>
