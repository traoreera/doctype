<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { Background } from '@vue-flow/background'
import { MarkerType, VueFlow, type NodeDragEvent } from '@vue-flow/core'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'
import CanvasControls from '@/components/workflow/CanvasControls.vue'
import StateNode from '@/components/workflow/StateNode.vue'
import TransitionEdge from '@/components/workflow/TransitionEdge.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { readFlows, type FlowSummary } from '@/lib/automations'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, WorkflowDef, WorkflowOverride } from '@/lib/types'
import { around, workflowGraph } from '@/lib/workflowgraph'
import {
  addState,
  addTransition,
  additionProblems,
  deadEnds,
  flowRows,
  hasTweaks,
  isAddedState,
  merged,
  patchAdded,
  removeAdded,
  removeState,
  setStateDocstatus,
  withTweak,
  type Docstatus,
  type TweakKey,
} from '@/lib/workflowedit'
import { useMeta } from '@/stores/meta'

/**
 * L'éditeur du circuit d'approbation d'un type, à la manière du Workflow Builder de Frappe : le circuit en graphe (états en cartes,
 * transitions en flèches) et, à droite, les propriétés de ce qu'on a choisi — ou le formulaire de ce qu'on ajoute.
 *
 * Le circuit vient de l'APPLICATION (le YAML du plugin). Une organisation peut l'ADAPTER (process/programme.md §3.6) :
 * - y ajouter des états et des transitions, rangés pour elle seule. Une transition ajoutée s'ouvre par un droit que le type déclare
 *   déjà (une liste) : un droit inventé ici, personne ne l'aurait chez xauth ;
 * - resserrer celles de l'application : fermer, ajouter une condition, interdire à l'auteur de la faire.
 * Les cartes se déplacent pour lire (positions propres à ce navigateur) ; on ne relie rien à la souris : une transition se crée par
 * le formulaire, avec son nom et son droit.
 *
 * Tout le monde qui LIT le type voit son circuit ; seul qui MODIFIE tous ses documents l'adapte (le serveur répond sinon 422
 * `workflow_reserved`).
 */
const props = defineProps<{ doctype: string }>()
const { api, automations } = useServices()
const notify = useNotify()
const metaStore = useMeta()

const meta = ref<DocTypeMeta | null>(null)
const definition = ref<WorkflowDef | null>(null)
const grants = ref<Record<string, string | null>>({})
const grantChoices = ref<Record<string, string>>({})
const submittable = ref(false)
const saved = ref<WorkflowOverride>({})
const draft = ref<WorkflowOverride>({})
const failure = ref('')
const problem = ref<string | null>(null)
const saving = ref(false)
const loading = ref(true)
const selected = ref<{ kind: 'state' | 'transition'; id: string } | null>(null)
/** Ce qu'on est en train d'AJOUTER : un état, ou une transition depuis un état. Le panneau montre alors son formulaire. */
const creating = ref<{ kind: 'state'; from: string | null } | { kind: 'transition'; from: string } | null>(null)

const label = computed(() => meta.value?.label || props.doctype)

// ── Les automatisations de l'organisation (xflow) : ce qu'une transition peut lancer après elle ──
const flows = ref<FlowSummary[]>([])
async function loadFlows(): Promise<void> {
  if (!automations) return
  try {
    flows.value = readFlows(await automations.get('/flows'))
  } catch {
    flows.value = [] // pas le droit de les lire, ou xflow injoignable : la liste reste vide, le panneau le dit
  }
}
/** La liste à choisir : « aucune », puis les automatisations ; une automatisation nommée mais introuvable reste montrée (on ne la perd pas). */
function flowOptions(current: string | null): { value: string; label: string }[] {
  const out = [{ value: '', label: t.workflowBuilder.noFlow }, ...flows.value.map((f) => ({ value: f.name, label: f.description ? `${f.name} — ${f.description}` : f.name }))]
  if (current && !flows.value.some((f) => f.name === current)) out.push({ value: current, label: t.workflowBuilder.missingFlow(current) })
  return out
}
/** Adapter le circuit : modifier TOUS les documents du type (`rights.write === 'all'`, ni restreint ni partagé seulement). */
const allowed = computed(() => meta.value?.rights.write === 'all')
const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(saved.value))
const all = computed(() => merged(definition.value, draft.value))
const stuck = computed(() => deadEnds(definition.value, draft.value))
const pending = computed(() => additionProblems(definition.value, draft.value, t.workflowBuilder.problems))
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
const chosenState = computed(() => (selected.value?.kind === 'state' ? all.value?.states.find((s) => s.name === selected.value!.id) ?? null : null))
const chosenAround = computed(() => (chosenState.value ? around(all.value, chosenState.value.name) : null))
const description = computed(() => all.value?.transitions.find((x) => x.action === chosenRow.value?.action)?.description ?? null)
const editable = computed(() => allowed.value && !saving.value)

/** Les droits proposés pour une transition ajoutée : ceux du type, sous leur libellé (le code reste visible, en petit). */
const grantOptions = computed(() => Object.entries(grantChoices.value).map(([value, text]) => ({ value, label: text, code: value })))
const docstatusOptions = computed(() =>
  (['draft', 'submitted', 'cancelled'] as Docstatus[])
    .filter((d) => d === 'draft' || submittable.value)
    .map((value) => ({ value, label: t.workflowBuilder.docstatus[value] })),
)
const grantLabel = (grant: string | null | undefined) => (grant ? grantChoices.value[grant] ?? null : null)

function choose(kind: 'state' | 'transition', id: string): void {
  creating.value = null
  selected.value = selected.value?.kind === kind && selected.value.id === id ? null : { kind, id }
}

// ── Ajouter un état ──
const newState = ref({ name: '', docstatus: 'draft' as Docstatus })
const newStateProblem = computed(() => {
  const name = newState.value.name.trim()
  if (!name) return t.workflowBuilder.nameRequired
  if (all.value?.states.some((s) => s.name === name)) return t.workflowBuilder.problems.duplicateState(name)
  return null
})
function startState(): void {
  newState.value = { name: '', docstatus: 'draft' }
  // L'état choisi AVANT d'ouvrir le formulaire : c'est de lui que partira la transition proposée ensuite.
  const from = selected.value?.kind === 'state' ? selected.value.id : null
  selected.value = null
  creating.value = { kind: 'state', from }
}
function confirmState(): void {
  if (newStateProblem.value || creating.value?.kind !== 'state') return
  const name = newState.value.name.trim()
  const from = creating.value.from ?? definition.value?.states[0]?.name ?? name
  draft.value = addState(draft.value, name, newState.value.docstatus)
  problem.value = null
  // Un état que rien n'atteint ne s'enregistre pas : on enchaîne sur la transition qui y mène, depuis l'état d'où l'on venait.
  startTransition(from, name)
}

// ── Ajouter une transition ──
const newTransition = ref({ action: '', to: '', grant: '', not_owner: false })
const newTransitionProblem = computed(() => {
  const n = newTransition.value
  if (!n.action.trim()) return t.workflowBuilder.nameRequired
  if (all.value?.transitions.some((x) => x.action === n.action.trim())) return t.workflowBuilder.problems.duplicateAction(n.action.trim())
  if (!n.to) return t.workflowBuilder.targetRequired
  if (!n.grant) return t.workflowBuilder.problems.noGrant(n.action.trim())
  return null
})
const targetOptions = computed(() => {
  const from = creating.value?.kind === 'transition' ? creating.value.from : null
  return (all.value?.states ?? []).filter((s) => s.name !== from).map((s) => ({ value: s.name, label: s.name }))
})
function startTransition(from: string, to = ''): void {
  newTransition.value = { action: to ? t.workflowBuilder.towards(to) : '', to, grant: '', not_owner: false }
  selected.value = null
  creating.value = { kind: 'transition', from }
}
function confirmTransition(): void {
  if (newTransitionProblem.value || creating.value?.kind !== 'transition') return
  const n = newTransition.value
  draft.value = addTransition(draft.value, { action: n.action, from: creating.value.from, to: n.to, grant: n.grant, not_owner: n.not_owner })
  problem.value = null
  creating.value = null
  selected.value = { kind: 'transition', id: n.action.trim() }
}

function set(action: string, key: TweakKey, value: boolean | string): void {
  draft.value = withTweak(draft.value, action, key, value)
  problem.value = null
}
function patch(action: string, change: Parameters<typeof patchAdded>[2]): void {
  draft.value = patchAdded(draft.value, action, change)
  problem.value = null
}
function dropTransition(action: string): void {
  draft.value = removeAdded(draft.value, action)
  selected.value = null
}
function dropState(name: string): void {
  draft.value = removeState(draft.value, name)
  selected.value = null
}

async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  definition.value = null
  try {
    meta.value = await metaStore.get(props.doctype)
    if (!meta.value.workflow) return // le panneau le dit : un circuit se déclare d'abord dans la définition du type
    // L'automate du YAML, pas celui de `/meta` : une transition que l'organisation a fermée n'y figure plus, on ne pourrait plus la rouvrir.
    const current = await api.workflowOverride(props.doctype)
    definition.value = current.definition
    grants.value = current.grants ?? {}
    grantChoices.value = current.grant_choices ?? {}
    submittable.value = current.submittable === true
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
    problem.value = describeError(e) // le refus du serveur (cul-de-sac, état occupé, droit inconnu…) reste sous les yeux
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  if (!window.confirm(t.workflowBuilder.resetConfirm)) return
  saving.value = true
  problem.value = null
  try {
    await api.removeWorkflowOverride(props.doctype)
    saved.value = {}
    draft.value = {}
    selected.value = null
    creating.value = null
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
  creating.value = null
  void load()
})
onMounted(() => {
  void load()
  void loadFlows()
})
</script>

<template>
  <div class="desk-page wf-page">
    <div class="desk-page-head">
      <h1 data-testid="wf-title">{{ t.workflowBuilder.title(label) }}</h1>
      <span class="desk-spacer" />
      <Button :as="RouterLink" :to="{ name: 'list', params: { doctype } }" :label="t.workflowBuilder.toList" icon="pi pi-list" text severity="secondary" size="small" />
      <template v-if="definition && allowed">
        <Button :label="t.workflowBuilder.addState" icon="pi pi-plus" severity="secondary" outlined size="small" :disabled="saving" data-testid="wf-add-state" @click="startState" />
        <Button :label="t.workflowBuilder.resetToApp" severity="secondary" text size="small" :disabled="saving || !hasTweaks(saved)" data-testid="wf-reset" @click="reset" />
        <Button :label="t.save" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty || stuck.length > 0 || pending.length > 0" data-testid="wf-save" @click="save" />
      </template>
    </div>

    <Message v-if="failure" severity="error" class="banner-error" data-testid="wf-error">{{ failure }}</Message>
    <div v-else-if="!loading && !definition" class="wf-none" data-testid="wf-none">
      <p>{{ t.flowOverride.empty }}</p>
      <p class="desk-muted">{{ t.workflowBuilder.noWorkflowHint }}</p>
    </div>

    <template v-if="definition">
      <p class="desk-muted wf-hint">{{ t.flowOverride.hint }}</p>
      <Message v-if="!allowed" severity="info" class="banner-error" data-testid="wf-reserved">{{ t.flowOverride.reserved }}</Message>
      <Message v-if="problem" severity="error" class="banner-error" data-testid="wf-problem">{{ problem }}</Message>
      <Message v-if="stuck.length" severity="warn" class="banner-error" data-testid="wf-deadend">{{ t.flowOverride.deadEnd }}{{ stuck.join(', ') }}</Message>
      <Message v-for="p in pending" :key="p" severity="warn" class="banner-error" data-testid="wf-pending">{{ p }}</Message>

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
          <!-- Un nouvel état : son nom et l'état de document qu'il impose ; on enchaîne sur la transition qui y mène. -->
          <template v-if="creating?.kind === 'state'">
            <p class="wf-kicker">{{ t.workflowBuilder.addedByOrg }}</p>
            <h2 class="wf-panel-title">{{ t.workflowBuilder.newState }}</h2>
            <label class="wf-field" for="wf-new-state">
              <span>{{ t.workflowBuilder.stateName }}</span>
              <InputText id="wf-new-state" v-model="newState.name" size="small" autofocus data-testid="wf-new-state-name" @keydown.enter="confirmState" />
            </label>
            <label class="wf-field" for="wf-new-docstatus">
              <span>{{ t.workflowBuilder.docstatusLabel }}</span>
              <Select
                input-id="wf-new-docstatus"
                v-model="newState.docstatus"
                :options="docstatusOptions"
                option-label="label"
                option-value="value"
                size="small"
                data-testid="wf-new-state-docstatus"
              />
              <span class="desk-muted wf-help">{{ submittable ? t.workflowBuilder.docstatusHint : t.workflowBuilder.draftOnlyHint }}</span>
            </label>
            <p v-if="newState.name && newStateProblem" class="wf-form-problem" data-testid="wf-new-state-problem">{{ newStateProblem }}</p>
            <div class="wf-form-actions">
              <Button :label="t.cancel" text severity="secondary" size="small" @click="creating = null" />
              <Button :label="t.workflowBuilder.nextTransition" icon="pi pi-arrow-right" size="small" :disabled="!!newStateProblem" data-testid="wf-new-state-ok" @click="confirmState" />
            </div>
          </template>

          <!-- Une nouvelle transition depuis un état : son nom, où elle mène, qui peut la faire. -->
          <template v-else-if="creating?.kind === 'transition'">
            <p class="wf-kicker">{{ t.workflowBuilder.addedByOrg }}</p>
            <h2 class="wf-panel-title">{{ t.workflowBuilder.newTransition }}</h2>
            <p class="wf-path"><strong>{{ creating.from }}</strong> <span class="pi pi-arrow-right" aria-hidden="true" /> {{ newTransition.to || '…' }}</p>
            <label class="wf-field" for="wf-new-action">
              <span>{{ t.workflowBuilder.transitionName }}</span>
              <InputText id="wf-new-action" v-model="newTransition.action" size="small" data-testid="wf-new-action" />
              <span class="desk-muted wf-help">{{ t.workflowBuilder.transitionNameHint }}</span>
            </label>
            <label class="wf-field" for="wf-new-to">
              <span>{{ t.workflowBuilder.transitionTo }}</span>
              <Select input-id="wf-new-to" v-model="newTransition.to" :options="targetOptions" option-label="label" option-value="value" size="small" data-testid="wf-new-to" />
            </label>
            <label class="wf-field" for="wf-new-grant">
              <span>{{ t.workflowBuilder.transitionGrant }}</span>
              <Select
                input-id="wf-new-grant"
                v-model="newTransition.grant"
                :options="grantOptions"
                option-label="label"
                option-value="value"
                size="small"
                filter
                :placeholder="t.workflowBuilder.pickGrant"
                data-testid="wf-new-grant"
              >
                <template #option="{ option }">
                  <span class="wf-grant-option"><span>{{ option.label }}</span><code>{{ option.code }}</code></span>
                </template>
              </Select>
              <span class="desk-muted wf-help">{{ t.workflowBuilder.transitionGrantHint }}</span>
            </label>
            <label class="wf-check">
              <Checkbox v-model="newTransition.not_owner" binary input-id="wf-new-owner" data-testid="wf-new-owner" />
              <span>{{ t.flowOverride.notOwner }} <span class="desk-muted wf-help">{{ t.flowOverride.notOwnerHint }}</span></span>
            </label>
            <p v-if="newTransition.action && newTransitionProblem" class="wf-form-problem" data-testid="wf-new-transition-problem">{{ newTransitionProblem }}</p>
            <div class="wf-form-actions">
              <Button :label="t.cancel" text severity="secondary" size="small" @click="creating = null" />
              <Button :label="t.workflowBuilder.addTransition" icon="pi pi-check" size="small" :disabled="!!newTransitionProblem" data-testid="wf-new-transition-ok" @click="confirmTransition" />
            </div>
          </template>

          <!-- Une transition choisie. -->
          <template v-else-if="chosenRow">
            <p class="wf-kicker">{{ chosenRow.added ? t.workflowBuilder.addedTransition : t.workflowBuilder.transition }}</p>
            <h2 class="wf-panel-title" data-testid="wf-panel-title">{{ chosenRow.action }}</h2>
            <p class="wf-path">
              <button type="button" class="wf-link" @click="choose('state', chosenRow.from)">{{ chosenRow.from }}</button>
              <span class="pi pi-arrow-right" aria-hidden="true" />
              <button type="button" class="wf-link" @click="choose('state', chosenRow.to)">{{ chosenRow.to }}</button>
            </p>
            <p v-if="description" class="desk-muted">{{ description }}</p>

            <!-- Ajoutée par l'organisation : elle se règle entièrement ici. -->
            <template v-if="chosenRow.added">
              <label class="wf-field" for="wf-added-to">
                <span>{{ t.workflowBuilder.transitionTo }}</span>
                <Select
                  input-id="wf-added-to"
                  :model-value="chosenRow.to"
                  :options="(all?.states ?? []).filter((s) => s.name !== chosenRow!.from).map((s) => ({ value: s.name, label: s.name }))"
                  option-label="label"
                  option-value="value"
                  :disabled="!editable"
                  size="small"
                  data-testid="wf-added-to"
                  @update:model-value="patch(chosenRow!.action, { to: $event })"
                />
              </label>
              <label class="wf-field" for="wf-added-grant">
                <span>{{ t.workflowBuilder.transitionGrant }}</span>
                <Select
                  input-id="wf-added-grant"
                  :model-value="chosenRow.grant"
                  :options="grantOptions"
                  option-label="label"
                  option-value="value"
                  :disabled="!editable"
                  size="small"
                  filter
                  data-testid="wf-added-grant"
                  @update:model-value="patch(chosenRow!.action, { grant: $event })"
                >
                  <template #option="{ option }">
                    <span class="wf-grant-option"><span>{{ option.label }}</span><code>{{ option.code }}</code></span>
                  </template>
                </Select>
                <code v-if="chosenRow.grant" class="wf-grant-code">{{ chosenRow.grant }}</code>
              </label>
              <label class="wf-check">
                <Checkbox :model-value="chosenRow.notOwner" binary :disabled="!editable" input-id="wf-added-owner" data-testid="wf-added-owner" @update:model-value="patch(chosenRow!.action, { not_owner: $event === true })" />
                <span>{{ t.flowOverride.notOwner }} <span class="desk-muted wf-help">{{ t.flowOverride.notOwnerHint }}</span></span>
              </label>
              <label class="wf-field" for="wf-added-condition">
                <span>{{ t.flowOverride.condition }}</span>
                <InputText
                  id="wf-added-condition"
                  :model-value="chosenRow.condition"
                  :disabled="!editable"
                  size="small"
                  :placeholder="t.workflowBuilder.conditionPlaceholder"
                  data-testid="wf-added-condition"
                  @update:model-value="patch(chosenRow!.action, { condition: $event ?? '' })"
                />
              </label>
              <div v-if="!chosenRow.yamlFlow" class="wf-field">
                <label for="wf-flow-pick">{{ t.workflowBuilder.flowPick }}</label>
                <Select
                  v-if="automations"
                  input-id="wf-flow-pick"
                  :model-value="chosenRow.flow ?? ''"
                  :options="flowOptions(chosenRow.flow)"
                  option-label="label"
                  option-value="value"
                  :disabled="!editable"
                  size="small"
                  filter
                  data-testid="wf-flow-pick"
                  @update:model-value="patch(chosenRow!.action, { flow: String($event ?? '') })"
                />
                <span v-if="automations" class="desk-muted wf-help">{{ t.workflowBuilder.flowPickHint }}</span>
                <span v-else class="desk-muted wf-help" data-testid="wf-flow-unavailable">{{ t.workflowBuilder.flowUnavailable }}</span>
                <span v-if="automations" class="wf-flow-links">
                  <RouterLink v-if="chosenRow.flow" :to="{ name: 'flow', params: { name: chosenRow.flow } }" data-testid="wf-flow-open">{{ t.workflowBuilder.flowOpen }}</RouterLink>
                  <RouterLink :to="{ name: 'flow-new' }" data-testid="wf-flow-new">{{ t.workflowBuilder.flowNew }}</RouterLink>
                </span>
              </div>
              <Button v-if="allowed" :label="t.workflowBuilder.removeTransition" icon="pi pi-trash" text severity="danger" size="small" :disabled="saving" data-testid="wf-remove-transition" @click="dropTransition(chosenRow.action)" />
            </template>

            <!-- Une transition de l'application : ce qu'elle exige, puis ce que l'organisation y ajoute. -->
            <template v-else>
              <h3 class="wf-section">{{ t.workflowBuilder.fromApp }}</h3>
              <dl class="wf-facts">
                <dt>{{ t.workflowBuilder.grant }}</dt>
                <dd>
                  <span v-if="grantLabel(grants[chosenRow.action])">{{ grantLabel(grants[chosenRow.action]) }}</span>
                  <code class="wf-grant-code" data-testid="wf-grant">{{ grants[chosenRow.action] ?? '—' }}</code>
                </dd>
                <dt>{{ t.flowOverride.condition }}</dt>
                <dd><code v-if="chosenRow.yamlCondition">{{ chosenRow.yamlCondition }}</code><span v-else class="desk-muted">{{ t.workflowBuilder.none }}</span></dd>
                <dt>{{ t.flowOverride.notOwner }}</dt>
                <dd>{{ chosenRow.locked ? t.workflowBuilder.yes : t.workflowBuilder.no }}</dd>
                <dt>{{ t.workflowBuilder.flow }}</dt>
                <dd data-testid="wf-flow">
                  <template v-if="chosenRow.yamlFlow"><code>{{ chosenRow.yamlFlow }}</code> <span class="desk-muted wf-help">{{ t.workflowBuilder.flowHint }}</span></template>
                  <span v-else class="desk-muted">{{ t.workflowBuilder.none }}</span>
                </dd>
              </dl>

              <h3 class="wf-section">{{ t.workflowBuilder.fromOrg }}</h3>
              <label class="wf-check">
                <Checkbox
                  :model-value="chosenRow.closed"
                  binary
                  :disabled="!editable"
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
                  :disabled="chosenRow.locked || !editable"
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
                  :disabled="chosenRow.closed || !editable"
                  size="small"
                  :placeholder="t.workflowBuilder.conditionPlaceholder"
                  data-testid="wf-condition"
                  @update:model-value="set(chosenRow.action, 'condition', $event ?? '')"
                />
                <span class="desk-muted wf-help">{{ t.flowOverride.conditionHint }}</span>
              </label>
              <div v-if="!chosenRow.yamlFlow" class="wf-field">
                <label for="wf-flow-pick">{{ t.workflowBuilder.flowPick }}</label>
                <Select
                  v-if="automations"
                  input-id="wf-flow-pick"
                  :model-value="chosenRow.flow ?? ''"
                  :options="flowOptions(chosenRow.flow)"
                  option-label="label"
                  option-value="value"
                  :disabled="!editable"
                  size="small"
                  filter
                  data-testid="wf-flow-pick"
                  @update:model-value="set(chosenRow!.action, 'flow', String($event ?? ''))"
                />
                <span v-if="automations" class="desk-muted wf-help">{{ t.workflowBuilder.flowPickHint }}</span>
                <span v-else class="desk-muted wf-help" data-testid="wf-flow-unavailable">{{ t.workflowBuilder.flowUnavailable }}</span>
                <span v-if="automations" class="wf-flow-links">
                  <RouterLink v-if="chosenRow.flow" :to="{ name: 'flow', params: { name: chosenRow.flow } }" data-testid="wf-flow-open">{{ t.workflowBuilder.flowOpen }}</RouterLink>
                  <RouterLink :to="{ name: 'flow-new' }" data-testid="wf-flow-new">{{ t.workflowBuilder.flowNew }}</RouterLink>
                </span>
              </div>
            </template>
          </template>

          <!-- Un état choisi : l'état de document qu'il impose, ses transitions, et de quoi en ajouter une. -->
          <template v-else-if="chosenState && chosenAround">
            <p class="wf-kicker">{{ isAddedState(draft, chosenState.name) ? t.workflowBuilder.addedState : t.workflowBuilder.state }}</p>
            <h2 class="wf-panel-title" data-testid="wf-panel-title">{{ chosenState.name }}</h2>
            <label v-if="isAddedState(draft, chosenState.name)" class="wf-field" for="wf-state-docstatus">
              <span>{{ t.workflowBuilder.docstatusLabel }}</span>
              <Select
                input-id="wf-state-docstatus"
                :model-value="chosenState.docstatus ?? 'draft'"
                :options="docstatusOptions"
                option-label="label"
                option-value="value"
                :disabled="!editable"
                size="small"
                data-testid="wf-state-docstatus"
                @update:model-value="draft = setStateDocstatus(draft, chosenState!.name, $event)"
              />
            </label>
            <dl v-else class="wf-facts">
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
            <Button v-if="allowed" :label="t.workflowBuilder.addTransitionFrom" icon="pi pi-plus" text size="small" :disabled="saving" data-testid="wf-add-transition" @click="startTransition(chosenState.name)" />
            <h3 class="wf-section">{{ t.workflowBuilder.arriving }}</h3>
            <ul v-if="chosenAround.arriving.length" class="wf-list">
              <li v-for="a in chosenAround.arriving" :key="a"><button type="button" class="wf-link" @click="choose('transition', a)">{{ a }}</button></li>
            </ul>
            <p v-else class="desk-muted">{{ t.workflowBuilder.initialHint }}</p>
            <Button
              v-if="allowed && isAddedState(draft, chosenState.name)"
              :label="t.workflowBuilder.removeState"
              icon="pi pi-trash"
              text
              severity="danger"
              size="small"
              :disabled="saving"
              data-testid="wf-remove-state"
              @click="dropState(chosenState.name)"
            />
          </template>

          <!-- Rien de choisi : la légende, et toutes les transitions (le graphe sans la souris). -->
          <template v-else>
            <h2 class="wf-panel-title">{{ t.workflowBuilder.overview }}</h2>
            <p class="desk-muted wf-help">{{ allowed ? t.workflowBuilder.pickEdit : t.workflowBuilder.pick }}</p>
            <ul class="wf-list" data-testid="wf-all">
              <li v-for="r in rows" :key="r.action">
                <button type="button" class="wf-link" :class="{ 'wf-link-closed': r.closed }" @click="choose('transition', r.action)">{{ r.action }}</button>
                <span class="desk-muted"> {{ r.from }} → {{ r.to }}</span>
                <span v-if="r.added" class="wf-added-tag">{{ t.workflowBuilder.added }}</span>
              </li>
            </ul>
            <h3 class="wf-section">{{ t.workflowBuilder.legend }}</h3>
            <ul class="wf-legend">
              <li><span class="pi pi-plus-circle" aria-hidden="true" /> {{ t.workflowBuilder.legendAdded }}</li>
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
