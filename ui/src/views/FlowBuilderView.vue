<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import { Background } from '@vue-flow/background'
import { MarkerType, VueFlow, useVueFlow, type Connection, type NodeDragEvent } from '@vue-flow/core'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRouter } from 'vue-router'
import FlowNode from '@/components/flow/FlowNode.vue'
import FlowPalette from '@/components/flow/FlowPalette.vue'
import StepPanel from '@/components/flow/StepPanel.vue'
import TriggerNode from '@/components/flow/TriggerNode.vue'
import TriggerPanel from '@/components/flow/TriggerPanel.vue'
import CanvasControls from '@/components/workflow/CanvasControls.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { type TriggerKind } from '@/lib/automations'
import { actionOf } from '@/lib/flowactions'
import {
  addBranch,
  addCase,
  addStep,
  connect as connectOutlet,
  disconnect,
  edgeParts,
  forgetPositions,
  graphOf,
  moveNode,
  outletLabel,
  patchStep,
  readFlow,
  removeBranch,
  removeCase,
  removeStep,
  renameCase,
  renameStep,
  STEP_MIME,
  TRIGGER_NODE,
  writeFlow,
  type FlowModel,
  type StepKind,
} from '@/lib/flowgraph'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, DocTypeSummary } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * L'éditeur d'une automatisation, sur TOUTE la page : la palette des étapes à gauche, le flux en graphe au milieu, les propriétés de
 * ce qu'on a choisi à droite — façon n8n pour le canevas, façon Frappe pour la page.
 *
 * **Écrit pour quelqu'un qui ne programme pas** : aucun JSON, aucune accolade, aucune expression cron. Une étape se règle par un
 * GESTE pris dans une liste (`lib/flowactions`) et ses valeurs se choisissent (`lib/flowvalues` : « du document déclencheur »…) ;
 * le déclencheur se dit « quand une facture est soumise » ou « chaque lundi à 9 h ». Ce que l'écran ne sait pas régler — une action
 * d'un autre plugin, une expression écrite ailleurs — est MONTRÉ et gardé tel quel, jamais réécrit de travers.
 *
 * On pose une étape en la glissant, on la relie en tirant une poignée (au clavier : les listes « Va vers » du panneau), et le dessin
 * lui-même vit dans la définition (`_x`/`_y`). xflow juge le déploiement et retient qui l'a déployé : un refus reste sous les yeux,
 * dans la page, sans rien perdre de ce qui a été tracé.
 */
const props = defineProps<{ name?: string }>()
const { automations } = useServices()
const notify = useNotify()
const router = useRouter()
const metaStore = useMeta()

/** xflow répond aussi ses refus dans un corps 200 (`{status: "error", msg}`) : son message, tel quel. */
class Refused extends Error {}
function refused(body: unknown): string | null {
  const b = body as { status?: unknown; message?: unknown; msg?: unknown } | null
  return b?.status === 'error' ? String(b.msg ?? b.message ?? t.automations.loadFailed) : null
}

const editing = computed(() => !!props.name)
const model = ref<FlowModel>(readFlow({ name: '', trigger: { type: 'manual' }, steps: [] }))
const saved = ref('')
const loading = ref(true)
const saving = ref(false)
const failure = ref<string | null>(null)
const reserved = ref(false)
const problems = ref<string[]>([])
const serverError = ref<string | null>(null)
const secret = ref<string | null>(null)
const selected = ref<string | null>(TRIGGER_NODE)
const selectedEdge = ref<string | null>(null)
const doctypes = ref<DocTypeSummary[]>([])
const targetMeta = ref<DocTypeMeta | null>(null)

// ── Le graphe ────────────────────────────────────────────────────────────────────────────────────────────────────
const graph = computed(() => graphOf(model.value))
/** Le flux tel qu'il est tracé, positions comprises : ce qu'on compare pour savoir si quelque chose n'est pas enregistré. */
const current = computed(() => JSON.stringify(rawDefinition()))
const dirty = computed(() => current.value !== saved.value)
const chosenStep = computed(() => (selected.value && selected.value !== TRIGGER_NODE ? model.value.steps.find((s) => s.id === selected.value) ?? null : null))
const chosenEdge = computed(() => {
  if (!selectedEdge.value) return null
  const parts = edgeParts(selectedEdge.value)
  if (!parts) return null
  const edge = graph.value.edges.find((e) => e.id === selectedEdge.value)
  return edge ? { ...parts, label: outletLabel(edge.data.outlet), target: edge.target } : null
})
const others = computed(() => model.value.steps.filter((s) => s.id !== chosenStep.value?.id).map((s) => s.id))
/** Les étapes dont une valeur peut reprendre le document : celles qui lisent ou créent, avant celle qu'on règle. */
const sourceSteps = computed(() => {
  const here = model.value.steps.findIndex((s) => s.id === chosenStep.value?.id)
  const before = here < 0 ? model.value.steps : model.value.steps.slice(0, here)
  return before.filter((s) => s.kind === 'action' && ['read', 'create'].includes(actionOf(s.raw) ?? ''))
})
const sources = computed(() => sourceSteps.value.map((s) => s.id))
/** Le type de document de chaque étape source, quand elle en nomme un : c'est lui qui donne les champs à proposer. */
const sourceTypes = computed(() => {
  const out: Record<string, string> = {}
  for (const s of sourceSteps.value) {
    const payload = s.raw.payload && typeof s.raw.payload === 'object' ? (s.raw.payload as Record<string, unknown>) : {}
    const doctype = payload.doctype
    if (typeof doctype === 'string' && doctype && !doctype.includes('{{')) out[s.id] = doctype
  }
  return out
})
/** Les champs qu'une valeur peut reprendre d'une étape source (« le total de la facture lue »). */
const stepFields = computed(() => {
  const out: Record<string, { value: string; label: string }[]> = {}
  for (const [step, doctype] of Object.entries(sourceTypes.value)) {
    const fields = metaStore.metas[doctype]?.fields
    if (fields) out[step] = fields.filter((f) => f.fieldtype !== 'Table' && f.fieldtype !== 'Password').map((f) => ({ value: f.fieldname, label: f.label ?? f.fieldname }))
  }
  return out
})
// Les définitions des types visés par les étapes sources : lues une fois, gardées par le cache du magasin.
watch(sourceTypes, (types) => {
  for (const doctype of Object.values(types)) void metaStore.get(doctype).catch(() => undefined)
}, { immediate: true })

const nodes = computed(() => graph.value.nodes.map((n) => ({ ...n, selected: selected.value === n.id })))
const edges = computed(() =>
  graph.value.edges.map((e) => ({
    ...e,
    type: 'smoothstep',
    markerEnd: MarkerType.ArrowClosed,
    selected: selectedEdge.value === e.id,
    class: `fl-edge fl-edge-${e.data.outlet.tone}${selectedEdge.value === e.id ? ' fl-edge-selected' : ''}`,
  })),
)

const { screenToFlowCoordinate, fitView, onNodesInitialized, setCenter, viewport, dimensions } = useVueFlow()
/**
 * Le cadrage du graphe. `fit-view-on-init` de Vue Flow appelle `fitView()` SANS options (mesuré dans son code) : un graphe d'un seul
 * nœud — l'éditeur s'ouvre sur son seul déclencheur — était alors cadré jusqu'au zoom maximum, et la carte remplissait l'écran
 * (constaté dans Chromium). On cadre donc nous-mêmes, une fois les nœuds mesurés, sans jamais grossir au-delà de la taille réelle.
 */
const FIT = { padding: 0.25, maxZoom: 1 }
/** Cadrer n'est qu'un confort : un canevas pas encore mesuré (onglet caché, test) ne doit jamais faire échouer la page. */
const fit = () => void fitView(FIT).catch(() => undefined)
let toFit = true
onNodesInitialized(() => {
  if (!toFit) return
  toFit = false
  fit()
})

function choose(id: string | null): void {
  selected.value = id
  selectedEdge.value = null
}

/**
 * Deux cartes lâchées au même endroit se cacheraient l'une l'autre : on décale la nouvelle vers le bas à droite jusqu'à une place
 * libre. L'encombrement est celui d'une carte (`.fl-node` : 200 px de large, une centaine de haut), pas un point.
 */
const CARD = { w: 210, h: 110 }
function free(at: { x: number; y: number }): { x: number; y: number } {
  const taken = graph.value.nodes.map((n) => n.position)
  let spot = { ...at }
  for (let i = 0; i < 20 && taken.some((p) => Math.abs(p.x - spot.x) < CARD.w && Math.abs(p.y - spot.y) < CARD.h); i++) {
    spot = { x: spot.x + 60, y: spot.y + 60 }
  }
  return spot
}

/**
 * Montrer un nœud qui vient d'être posé : s'il sort de la partie visible du canevas, on y déplace la vue (même zoom). Avant, un clic
 * sur la palette posait l'étape là où la mise en page la rangeait — souvent HORS du cadre — et le recadrage partait avant que Vue Flow
 * ait mesuré le nouveau nœud : rien ne bougeait, et l'on croyait que le clic n'avait rien ajouté (constaté dans Chromium).
 */
function reveal(at: { x: number; y: number }): void {
  const v = viewport.value
  const d = dimensions.value
  if (!d.width || !d.height || !v.zoom) return
  const left = -v.x / v.zoom
  const top = -v.y / v.zoom
  const inside = at.x >= left && at.y >= top && at.x + CARD.w <= left + d.width / v.zoom && at.y + CARD.h <= top + d.height / v.zoom
  if (!inside) void setCenter(at.x + CARD.w / 2, at.y + CARD.h / 2, { zoom: v.zoom }).catch(() => undefined)
}

function add(kind: StepKind, position?: { x: number; y: number }): void {
  // Posée au bout de ce qui est choisi : sans flèche, xflow ne l'exécuterait jamais (`addStep` relie la première sortie libre).
  const after = selected.value === TRIGGER_NODE ? model.value.steps[model.value.steps.length - 1]?.id ?? null : selected.value
  // Au clic (pas de position), la nouvelle carte se pose à DROITE de celle à laquelle elle se relie (le déclencheur pour la première),
  // jamais là où la mise en page automatique l'enverrait : on la voit naître à côté de sa flèche.
  const anchorId = after ?? (model.value.steps.length ? model.value.steps[model.value.steps.length - 1]!.id : TRIGGER_NODE)
  const anchor = graph.value.nodes.find((n) => n.id === anchorId)?.position
  const spot = free(position ?? (anchor ? { x: anchor.x + CARD.w + 60, y: anchor.y } : { x: 0, y: 0 }))
  const made = addStep(model.value, kind, spot, after)
  model.value = made.model
  choose(made.id)
  void nextTick(() => reveal(spot))
}

/** Lâcher une étape de la palette sur le canevas : elle se pose là où le curseur l'a laissée. */
function drop(event: DragEvent): void {
  const kind = (event.dataTransfer?.getData(STEP_MIME) || '') as StepKind
  if (!kind) return
  event.preventDefault()
  // Le curseur montre le COIN du nœud chez Vue Flow : on recentre la carte sous la souris. Tant que le canevas n'est pas mesuré
  // (premier affichage, fenêtre cachée), on laisse le placement calculé faire son travail.
  const at = screenToFlowCoordinate({ x: event.clientX, y: event.clientY })
  const placed = Number.isFinite(at.x) && Number.isFinite(at.y) ? { x: at.x - 100, y: at.y - 36 } : undefined
  add(kind, placed)
}

function onConnect(connection: Connection): void {
  if (!connection.source || !connection.target || !connection.sourceHandle) return
  model.value = connectOutlet(model.value, connection.source, connection.sourceHandle, connection.target)
}

function detach(): void {
  const edge = chosenEdge.value
  if (!edge) return
  model.value = edge.source === TRIGGER_NODE ? { ...model.value, entry: null } : disconnect(model.value, edge.source, edge.key)
  selectedEdge.value = null
}

function keepPosition(e: NodeDragEvent): void {
  model.value = moveNode(model.value, e.node.id, e.node.position)
}

function arrange(): void {
  model.value = forgetPositions(model.value)
}

// ── Le flux ──────────────────────────────────────────────────────────────────────────────────────────────────────
function setTrigger(patch: Partial<{ kind: TriggerKind; event: string; cron: string }>): void {
  model.value = { ...model.value, trigger: { ...model.value.trigger, ...patch } }
}

/**
 * Le type de document que l'étape choisie vise : ses champs peuplent les listes du panneau. Un type nommé par un jeton
 * (« le même que le déclencheur ») n'est pas connu d'avance : le panneau le dit et laisse saisir les valeurs à la main.
 */
watch(
  () => {
    const p = chosenStep.value?.raw.payload
    const doctype = p && typeof p === 'object' ? (p as Record<string, unknown>).doctype : null
    return typeof doctype === 'string' && !doctype.includes('{{') ? doctype : null
  },
  async (doctype) => {
    targetMeta.value = null
    if (!doctype) return
    try {
      targetMeta.value = await metaStore.get(doctype)
    } catch {
      targetMeta.value = null // un type qu'on ne peut pas lire : les valeurs se saisissent à la main
    }
  },
  { immediate: true },
)

async function load(): Promise<void> {
  toFit = true
  loading.value = true
  failure.value = null
  reserved.value = false
  void metaStore.loadSummaries().then((list) => (doctypes.value = list)).catch(() => undefined)
  if (!props.name) {
    model.value = readFlow({ name: '', trigger: { type: 'manual' }, steps: [] })
    saved.value = current.value // un flux neuf qu'on n'a pas touché se quitte sans question
    loading.value = false
    return
  }
  if (!automations) {
    failure.value = t.automations.loadFailed
    loading.value = false
    return
  }
  try {
    const body = (await automations.get(`/flows/${encodeURIComponent(props.name)}`)) as { workflow?: unknown }
    const problem = refused(body)
    if (problem) throw new Refused(problem)
    model.value = readFlow(body.workflow)
    saved.value = current.value
    // On ouvre sur le DÉCLENCHEUR : c'est l'identité du flux (ce qui le met en route, sa description). Les étapes, elles, se
    // choisissent sur le graphe — ouvrir sur la première cachait la description du flux, qui n'était alors plus atteignable.
    choose(TRIGGER_NODE)
  } catch (e) {
    reserved.value = e instanceof ApiError && e.status === 403
    failure.value = reserved.value ? null : e instanceof Refused ? e.message : describeError(e) || t.automations.loadFailed
  } finally {
    loading.value = false
  }
}

/** Le flux tel qu'il est tracé — y compris incomplet : ce qui sert à savoir s'il a changé, jamais ce qu'on déploie. */
function rawDefinition(): Record<string, unknown> {
  const m = model.value
  const trigger: Record<string, unknown> = { ...m.trigger.raw, type: m.trigger.kind }
  if (m.trigger.kind === 'event') trigger.event_name = m.trigger.event
  if (m.trigger.kind === 'schedule') trigger.cron = m.trigger.cron
  return { ...m.extra, name: m.name, description: m.description, trigger, steps: m.steps.map((s) => s.raw), ...(m.entry ? { entry_step: m.entry } : {}) }
}

async function deploy(): Promise<void> {
  problems.value = []
  serverError.value = null
  const made = writeFlow(model.value)
  if (!made.definition) return void (problems.value = made.problems)
  if (!automations) return void (serverError.value = t.automations.loadFailed)
  saving.value = true
  try {
    const answer = (await automations.post('/flows', made.definition)) as { webhook_secret?: unknown }
    const problem = refused(answer)
    if (problem) throw new Refused(problem)
    saved.value = current.value
    if (typeof answer.webhook_secret === 'string') secret.value = answer.webhook_secret
    notify.success(t.automations.saved(model.value.name))
    // Une création devient une modification : l'adresse porte le nom, et le nom ne change plus.
    if (!editing.value) await router.replace({ name: 'flow', params: { name: model.value.name } })
  } catch (e) {
    serverError.value = e instanceof Refused ? e.message : describeError(e)
  } finally {
    saving.value = false
  }
}

onMounted(load)
watch(() => props.name, load)
onBeforeRouteLeave(() => !dirty.value || window.confirm(t.flowBuilder.leave))
</script>

<template>
  <div class="fl-page">
    <!-- Une seule barre : le nom du flux, et ce qu'on peut en faire. Tout le reste du réglage est dans le panneau. -->
    <div class="desk-page-head fl-head">
      <h1 class="fl-title" data-testid="fl-title">{{ t.automations.editorTitle(props.name ?? null) }}</h1>
      <InputText
        :model-value="model.name"
        :disabled="editing || reserved"
        size="small"
        class="fl-name"
        :placeholder="t.flowBuilder.namePlaceholder"
        :aria-label="t.automations.name"
        :title="t.automations.nameHint"
        data-testid="fl-name"
        @update:model-value="model = { ...model, name: $event ?? '' }"
      />
      <span class="desk-spacer" />
      <Button :as="RouterLink" :to="{ name: 'automations' }" :label="t.flowBuilder.toList" icon="pi pi-list" text severity="secondary" size="small" data-testid="fl-to-list" />
      <Button :label="t.automations.deploy" icon="pi pi-cloud-upload" size="small" :loading="saving" :disabled="saving || reserved" data-testid="fl-deploy" @click="deploy" />
    </div>

    <Message v-if="reserved" severity="secondary" :closable="false" data-testid="fl-reserved">{{ t.automations.reserved }}</Message>
    <Message v-else-if="failure" severity="error" :closable="false" data-testid="fl-failure">{{ failure }}</Message>
    <Message v-if="secret" severity="warn" :closable="true" data-testid="fl-secret" @close="secret = null">
      {{ t.automations.secret }} : <code class="fl-secret">{{ secret }}</code>
    </Message>
    <Message v-for="p in problems" :key="p" severity="error" :closable="false" data-testid="fl-problem">{{ p }}</Message>
    <Message v-if="serverError" severity="error" :closable="false" data-testid="fl-server-error">{{ serverError }}</Message>

    <div v-if="!loading && !reserved" class="fl-body">
      <FlowPalette @add="add($event)" />

      <div class="fl-canvas" data-testid="fl-canvas" @dragover.prevent @drop="drop">
        <VueFlow
          :nodes="nodes"
          :edges="edges"
          :delete-key-code="null"
          :nodes-focusable="false"
          :edges-focusable="false"
          :connection-radius="30"
          disable-keyboard-a11y
          :min-zoom="0.3"
          :max-zoom="2"
          :default-viewport="{ x: 0, y: 0, zoom: 1 }"
          :aria-label="t.flowBuilder.canvas"
          @connect="onConnect"
          @node-drag-stop="keepPosition"
          @edge-click="selectedEdge = $event.edge.id; selected = null"
          @pane-click="choose(null)"
        >
          <template #node-trigger="node">
            <TriggerNode :id="node.id" :data="node.data" :selected="node.selected" @choose="choose" />
          </template>
          <template #node-step="node">
            <FlowNode :id="node.id" :data="node.data" :selected="node.selected" @choose="choose" />
          </template>
          <Background :gap="18" :size="1.2" pattern-color="var(--desk-grid-dot)" />
          <CanvasControls :max-zoom="FIT.maxZoom" @arrange="arrange" />
        </VueFlow>
        <p v-if="!model.steps.length" class="fl-empty" data-testid="fl-empty">{{ t.flowBuilder.empty }}</p>
      </div>

      <aside class="wf-panel fl-panel" :aria-label="t.flowBuilder.properties" data-testid="fl-panel">
        <!-- Une étape choisie : ce qu'elle fait, et où elle mène. -->
        <StepPanel
          v-if="chosenStep"
          :key="chosenStep.id"
          :step="chosenStep"
          :others="others"
          :sources="sources"
          :step-fields="stepFields"
          :doctypes="doctypes"
          :target="targetMeta"
          @patch="model = patchStep(model, chosenStep.id, $event)"
          @rename="model = renameStep(model, chosenStep.id, $event); selected = $event"
          @connect="model = connectOutlet(model, chosenStep.id, $event.key, $event.target)"
          @remove="model = removeStep(model, chosenStep.id); choose(TRIGGER_NODE)"
          @add-branch="model = addBranch(model, chosenStep.id)"
          @remove-branch="model = removeBranch(model, chosenStep.id, $event)"
          @add-case="model = addCase(model, chosenStep.id, $event)"
          @rename-case="model = renameCase(model, chosenStep.id, $event.from, $event.to)"
          @remove-case="model = removeCase(model, chosenStep.id, $event)"
        />

        <!-- Une flèche choisie : d'où elle part, où elle va, et le moyen de la détacher. -->
        <template v-else-if="chosenEdge">
          <p class="wf-kicker">{{ t.flowBuilder.edge }}</p>
          <h2 class="wf-panel-title" data-testid="fl-edge-title">{{ chosenEdge.label }}</h2>
          <p class="wf-path">
            <button type="button" class="wf-link" @click="choose(chosenEdge.source)">{{ chosenEdge.source === TRIGGER_NODE ? t.flowBuilder.trigger : chosenEdge.source }}</button>
            <span class="pi pi-arrow-right" aria-hidden="true" />
            <button type="button" class="wf-link" @click="choose(chosenEdge.target)">{{ chosenEdge.target }}</button>
          </p>
          <Button :label="t.flowBuilder.detach" icon="pi pi-times-circle" severity="danger" text size="small" data-testid="fl-detach" @click="detach" />
        </template>

        <!-- Rien de choisi, ou le déclencheur : ce qui met le flux en route. -->
        <template v-else>
          <TriggerPanel
            :kind="model.trigger.kind"
            :event="model.trigger.event"
            :cron="model.trigger.cron"
            :description="model.description"
            :doctypes="doctypes"
            @update="setTrigger($event)"
            @describe="model = { ...model, description: $event }"
          />
          <h3 class="wf-section">{{ t.flowBuilder.start }}</h3>
          <Select
            :model-value="model.entry ?? model.steps[0]?.id ?? null"
            :options="model.steps.map((s) => s.id)"
            size="small"
            :placeholder="t.flowBuilder.endOfFlow"
            :aria-label="t.flowBuilder.start"
            data-testid="fl-entry"
            @update:model-value="model = { ...model, entry: $event }"
          />
          <ul class="wf-list fl-steps" data-testid="fl-all-steps">
            <li v-for="s in model.steps" :key="s.id">
              <button type="button" class="wf-link" @click="choose(s.id)">{{ s.id }}</button>
              <span class="desk-muted">{{ s.kind ? t.flowBuilder.kinds[s.kind].label : t.flowBuilder.unknownKind }}</span>
            </li>
          </ul>
          <p v-if="!model.steps.length" class="desk-muted">{{ t.flowBuilder.empty }}</p>
        </template>
      </aside>
    </div>
  </div>
</template>
