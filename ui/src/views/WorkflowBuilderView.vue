<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { Background } from '@vue-flow/background'
import { MarkerType, VueFlow, type NodeDragEvent } from '@vue-flow/core'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'
import CanvasControls from '@/components/workflow/CanvasControls.vue'
import StateNode from '@/components/workflow/StateNode.vue'
import TransitionEdge from '@/components/workflow/TransitionEdge.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, WorkflowDef, WorkflowOverride } from '@/lib/types'
import { around, workflowGraph } from '@/lib/workflowgraph'
import { deadEnds, flowRows, hasTweaks, withTweak, type TweakKey } from '@/lib/workflowedit'
import { useMeta } from '@/stores/meta'

/**
 * L'éditeur du circuit d'approbation d'un type, à la manière du Workflow Builder de Frappe : le circuit en graphe (états en cartes,
 * transitions en flèches) et, à droite, les propriétés de ce qu'on a choisi.
 *
 * Ce qu'on peut y faire est ce que le serveur permet, rien de plus (doc/programme.md §3.6) : l'automate vient de l'APPLICATION (le
 * YAML du plugin), une organisation le RESSERRE — fermer une transition, lui ajouter une condition, interdire à l'auteur de la faire.
 * Pas d'état ni de transition à ajouter : une transition inventée ici porterait un droit qu'xauth n'a jamais entendu, personne ne
 * pourrait la faire. Les cartes se déplacent (pour lire), jamais ne se relient.
 *
 * Tout le monde qui LIT le type voit son circuit ; seul qui MODIFIE tous ses documents l'adapte (le serveur répond sinon 422
 * `workflow_reserved`).
 */
const props = defineProps<{ doctype: string }>()
const { api } = useServices()
const notify = useNotify()
const metaStore = useMeta()

const meta = ref<DocTypeMeta | null>(null)
const definition = ref<WorkflowDef | null>(null)
const grants = ref<Record<string, string | null>>({})
const saved = ref<WorkflowOverride>({})
const draft = ref<WorkflowOverride>({})
const failure = ref('')
const problem = ref<string | null>(null)
const saving = ref(false)
const loading = ref(true)
const selected = ref<{ kind: 'state' | 'transition'; id: string } | null>(null)

const label = computed(() => meta.value?.label || props.doctype)
/** Adapter le circuit : modifier TOUS les documents du type (`rights.write === 'all'`, ni restreint ni partagé seulement). */
const allowed = computed(() => meta.value?.rights.write === 'all')
const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(saved.value))
const stuck = computed(() => deadEnds(definition.value, draft.value))
const rows = computed(() => flowRows(definition.value, draft.value))

// ── Positions : une commodité de lecture, propre à ce navigateur (jamais une donnée du circuit) ──
const positionsKey = computed(() => `xdt.workflow.positions.${props.doctype}`)
const positions = ref<Record<string, { x: number; y: number }>>({})
function readPositions(): void {
  try {
    const raw = localStorage.getItem(positionsKey.value)
    positions.value = raw ? (JSON.parse(raw) as Record<string, { x: number; y: number }>) : {}
  } catch {
    positions.value = {}
  }
}
function keepPosition(e: NodeDragEvent): void {
  positions.value = { ...positions.value, [e.node.id]: { x: e.node.position.x, y: e.node.position.y } }
  try {
    localStorage.setItem(positionsKey.value, JSON.stringify(positions.value))
  } catch {
    /* navigation privée, stockage plein : la position vaut pour la session */
  }
}
function arrange(): void {
  positions.value = {}
  try {
    localStorage.removeItem(positionsKey.value)
  } catch {
    /* rien à retirer */
  }
}

const graph = computed(() => workflowGraph(definition.value, draft.value, positions.value))
const nodes = computed(() => graph.value.nodes.map((n) => ({ ...n, selected: selected.value?.kind === 'state' && selected.value.id === n.id })))
const edges = computed(() =>
  graph.value.edges.map((e) => ({ ...e, markerEnd: MarkerType.ArrowClosed, selected: selected.value?.kind === 'transition' && selected.value.id === e.id })),
)

const chosenRow = computed(() => (selected.value?.kind === 'transition' ? rows.value.find((r) => r.action === selected.value!.id) ?? null : null))
const chosenState = computed(() => (selected.value?.kind === 'state' ? definition.value?.states.find((s) => s.name === selected.value!.id) ?? null : null))
const chosenAround = computed(() => (chosenState.value ? around(definition.value, chosenState.value.name) : null))
const description = computed(() => definition.value?.transitions.find((x) => x.action === chosenRow.value?.action)?.description ?? null)

function choose(kind: 'state' | 'transition', id: string): void {
  selected.value = selected.value?.kind === kind && selected.value.id === id ? null : { kind, id }
}

function set(action: string, key: TweakKey, value: boolean | string): void {
  draft.value = withTweak(draft.value, action, key, value)
  problem.value = null
}

async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  try {
    meta.value = await metaStore.get(props.doctype)
    if (!meta.value.workflow) {
      definition.value = null
      return
    }
    // L'automate du YAML, pas celui de `/meta` : une transition que l'organisation a fermée n'y figure plus, on ne pourrait plus la rouvrir.
    const current = await api.workflowOverride(props.doctype)
    definition.value = current.definition
    grants.value = current.grants ?? {}
    saved.value = current.config ?? {}
    draft.value = current.config ?? {}
    readPositions()
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  problem.value = null
  try {
    if (hasTweaks(draft.value)) await api.saveWorkflowOverride(props.doctype, draft.value)
    else await api.removeWorkflowOverride(props.doctype) // un habillage vide se retire : l'application reprend la main
    saved.value = draft.value
    await metaStore.refresh(props.doctype) // `/meta` ne montre plus le même circuit (fiche, liste, filtres d'état)
    notify.success(t.flowOverride.savedToast)
  } catch (e) {
    problem.value = describeError(e) // le refus du serveur (cul-de-sac, condition fautive…) reste sous les yeux
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  saving.value = true
  problem.value = null
  try {
    await api.removeWorkflowOverride(props.doctype)
    saved.value = {}
    draft.value = {}
    await metaStore.refresh(props.doctype)
    notify.success(t.flowOverride.resetToast)
  } catch (e) {
    problem.value = describeError(e)
  } finally {
    saving.value = false
  }
}

onBeforeRouteLeave(() => !dirty.value || window.confirm(t.workflowBuilder.leave))
watch(() => props.doctype, () => {
  selected.value = null
  void load()
})
onMounted(load)
</script>

<template>
  <div class="desk-page wf-page">
    <div class="desk-page-head">
      <h1 data-testid="wf-title">{{ t.workflowBuilder.title(label) }}</h1>
      <span class="desk-spacer" />
      <Button :as="RouterLink" :to="{ name: 'list', params: { doctype } }" :label="t.workflowBuilder.toList" icon="pi pi-list" text severity="secondary" size="small" />
      <template v-if="definition && allowed">
        <Button :label="t.override.reset" severity="secondary" outlined size="small" :disabled="saving || !hasTweaks(saved)" data-testid="wf-reset" @click="reset" />
        <Button :label="t.save" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty || stuck.length > 0" data-testid="wf-save" @click="save" />
      </template>
    </div>

    <Message v-if="failure" severity="error" class="banner-error" data-testid="wf-error">{{ failure }}</Message>
    <p v-else-if="!loading && !definition" class="desk-muted" data-testid="wf-none">{{ t.flowOverride.empty }}</p>

    <template v-if="definition">
      <p class="desk-muted wf-hint">{{ t.flowOverride.hint }}</p>
      <Message v-if="!allowed" severity="info" class="banner-error" data-testid="wf-reserved">{{ t.flowOverride.reserved }}</Message>
      <Message v-if="problem" severity="error" class="banner-error" data-testid="wf-problem">{{ problem }}</Message>
      <Message v-if="stuck.length" severity="warn" class="banner-error" data-testid="wf-deadend">{{ t.flowOverride.deadEnd }}{{ stuck.join(', ') }}</Message>

      <div class="wf-body">
        <div class="wf-canvas" data-testid="wf-canvas">
          <VueFlow
            :nodes="nodes"
            :edges="edges"
            :nodes-connectable="false"
            :edges-updatable="false"
            :delete-key-code="null"
            :nodes-focusable="false"
            :edges-focusable="false"
            disable-keyboard-a11y
            :min-zoom="0.3"
            :max-zoom="2"
            fit-view-on-init
            :aria-label="t.workflowBuilder.canvas"
            @node-drag-stop="keepPosition"
            @pane-click="selected = null"
          >
            <template #node-state="node">
              <StateNode :id="node.id" :data="node.data" :selected="node.selected" @choose="choose('state', $event)" />
            </template>
            <template #edge-transition="edge">
              <TransitionEdge v-bind="edge" @choose="choose('transition', $event)" />
            </template>
            <Background :gap="18" :size="1.2" pattern-color="var(--desk-grid-dot)" />
            <CanvasControls @arrange="arrange" />
          </VueFlow>
        </div>

        <aside class="wf-panel" :aria-label="t.workflowBuilder.properties" data-testid="wf-panel">
          <!-- Une transition choisie : ce que l'application exige, puis ce que l'organisation y ajoute. -->
          <template v-if="chosenRow">
            <p class="wf-kicker">{{ t.workflowBuilder.transition }}</p>
            <h2 class="wf-panel-title" data-testid="wf-panel-title">{{ chosenRow.action }}</h2>
            <p class="wf-path">
              <button type="button" class="wf-link" @click="choose('state', chosenRow.from)">{{ chosenRow.from }}</button>
              <span class="pi pi-arrow-right" aria-hidden="true" />
              <button type="button" class="wf-link" @click="choose('state', chosenRow.to)">{{ chosenRow.to }}</button>
            </p>
            <p v-if="description" class="desk-muted">{{ description }}</p>

            <h3 class="wf-section">{{ t.workflowBuilder.fromApp }}</h3>
            <dl class="wf-facts">
              <dt>{{ t.workflowBuilder.grant }}</dt>
              <dd><code data-testid="wf-grant">{{ grants[chosenRow.action] ?? '—' }}</code></dd>
              <dt>{{ t.flowOverride.condition }}</dt>
              <dd><code v-if="chosenRow.yamlCondition">{{ chosenRow.yamlCondition }}</code><span v-else class="desk-muted">{{ t.workflowBuilder.none }}</span></dd>
              <dt>{{ t.flowOverride.notOwner }}</dt>
              <dd>{{ chosenRow.locked ? t.workflowBuilder.yes : t.workflowBuilder.no }}</dd>
            </dl>

            <h3 class="wf-section">{{ t.workflowBuilder.fromOrg }}</h3>
            <label class="wf-check">
              <Checkbox
                :model-value="chosenRow.closed"
                binary
                :disabled="!allowed || saving"
                input-id="wf-closed"
                data-testid="wf-closed"
                @update:model-value="set(chosenRow.action, 'closed', $event === true)"
              />
              <span>{{ t.flowOverride.closed }} <span class="desk-muted wf-help">{{ t.flowOverride.closedHint }}</span></span>
            </label>
            <label class="wf-check">
              <Checkbox
                :model-value="chosenRow.notOwner"
                binary
                :disabled="chosenRow.locked || !allowed || saving"
                input-id="wf-owner"
                data-testid="wf-owner"
                @update:model-value="set(chosenRow.action, 'not_owner', $event === true)"
              />
              <span>{{ t.flowOverride.notOwner }} <span class="desk-muted wf-help">{{ chosenRow.locked ? t.flowOverride.locked : t.flowOverride.notOwnerHint }}</span></span>
            </label>
            <label class="wf-field" for="wf-condition">
              <span>{{ t.flowOverride.condition }}</span>
              <InputText
                id="wf-condition"
                :model-value="chosenRow.condition"
                :disabled="chosenRow.closed || !allowed || saving"
                size="small"
                :placeholder="t.workflowBuilder.conditionPlaceholder"
                data-testid="wf-condition"
                @update:model-value="set(chosenRow.action, 'condition', $event ?? '')"
              />
              <span class="desk-muted wf-help">{{ t.flowOverride.conditionHint }}</span>
            </label>
          </template>

          <!-- Un état choisi : l'état de document qu'il impose, et ses transitions. -->
          <template v-else-if="chosenState && chosenAround">
            <p class="wf-kicker">{{ t.workflowBuilder.state }}</p>
            <h2 class="wf-panel-title" data-testid="wf-panel-title">{{ chosenState.name }}</h2>
            <dl class="wf-facts">
              <dt>{{ t.workflowBuilder.docstatusLabel }}</dt>
              <dd>{{ t.workflowBuilder.docstatus[chosenState.docstatus ?? 'draft'] }}</dd>
            </dl>
            <h3 class="wf-section">{{ t.workflowBuilder.leaving }}</h3>
            <ul v-if="chosenAround.leaving.length" class="wf-list">
              <li v-for="a in chosenAround.leaving" :key="a">
                <button type="button" class="wf-link" :data-testid="`wf-go-${a}`" @click="choose('transition', a)">{{ a }}</button>
              </li>
            </ul>
            <p v-else class="desk-muted">{{ t.workflowBuilder.finalHint }}</p>
            <h3 class="wf-section">{{ t.workflowBuilder.arriving }}</h3>
            <ul v-if="chosenAround.arriving.length" class="wf-list">
              <li v-for="a in chosenAround.arriving" :key="a"><button type="button" class="wf-link" @click="choose('transition', a)">{{ a }}</button></li>
            </ul>
            <p v-else class="desk-muted">{{ t.workflowBuilder.initialHint }}</p>
          </template>

          <!-- Rien de choisi : la légende, et toutes les transitions (le graphe sans la souris). -->
          <template v-else>
            <h2 class="wf-panel-title">{{ t.workflowBuilder.overview }}</h2>
            <p class="desk-muted">{{ t.workflowBuilder.pick }}</p>
            <ul class="wf-list" data-testid="wf-all">
              <li v-for="r in rows" :key="r.action">
                <button type="button" class="wf-link" :class="{ 'wf-link-closed': r.closed }" @click="choose('transition', r.action)">{{ r.action }}</button>
                <span class="desk-muted"> {{ r.from }} → {{ r.to }}</span>
              </li>
            </ul>
            <h3 class="wf-section">{{ t.workflowBuilder.legend }}</h3>
            <ul class="wf-legend">
              <li><span class="pi pi-filter" aria-hidden="true" /> {{ t.workflowBuilder.legendCondition }}</li>
              <li><span class="pi pi-lock" aria-hidden="true" /> {{ t.workflowBuilder.legendOwner }}</li>
              <li><span class="pi pi-ban" aria-hidden="true" /> {{ t.workflowBuilder.legendClosed }}</li>
            </ul>
          </template>
        </aside>
      </div>
    </template>
  </div>
</template>
