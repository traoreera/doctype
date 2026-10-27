<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { computed, ref, watch } from 'vue'
import VarInput from '@/components/automation/VarInput.vue'
import { t } from '@/i18n'
import { docActionOf, payloadOf, portsOf, shownValue, targetOf, typedValue, type FlowStep, type Variable } from '@/lib/automation'
import type { DocTypeMeta } from '@/lib/types'
import type { CatalogAction } from '@/lib/xflow'

/**
 * Les propriétés d'une étape, groupées comme dans n8n : ce qu'elle fait (un document et ses champs pour un nœud Document ; un plugin, une
 * action et ses données pour une action quelconque ; la comparaison d'une condition…), puis ses sorties et où elles mènent. Le panneau
 * n'écrit que des retouches que la page applique au modèle (`lib/automation`) ; xflow juge la définition entière à l'enregistrement.
 */
const props = defineProps<{
  step: FlowStep
  variables: Variable[]
  doctypes: { name: string; label: string }[]
  meta: DocTypeMeta | null
  targets: { id: string; label: string }[]
  catalog: CatalogAction[]
  canEdit: boolean
}>()
const emit = defineEmits<{
  (e: 'patch', value: Record<string, unknown>): void
  (e: 'payload', value: Record<string, unknown>): void
  (e: 'rename', value: string): void
  (e: 'remove'): void
  (e: 'link', port: string, target: string | null): void
  (e: 'case', from: string | null, to: string | null): void
}>()

const doc = computed(() => docActionOf(props.step))
const payload = computed(() => payloadOf(props.step))
const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number | null => (typeof v === 'number' ? v : null)
const ro = computed(() => !props.canEdit)

// ── Identifiant : appliqué à la validation, jamais à chaque frappe (renommer réécrit les références partout) ──
const idDraft = ref(props.step.id)
watch(() => props.step.id, (v) => (idDraft.value = v))
const idProblem = computed(() =>
  idDraft.value === props.step.id ? null
  : !/^[a-z][a-z0-9_]{0,63}$/.test(idDraft.value) ? t.automations.badStepId
  : props.targets.some((s) => s.id === idDraft.value) ? t.automations.takenStepId
  : null,
)
function applyId(): void {
  if (idDraft.value !== props.step.id && !idProblem.value) emit('rename', idDraft.value)
}

// ── JSON libre (données d'une action, filtres d'une recherche) : appliqué seulement quand il se lit ──
function jsonDraft(source: () => unknown) {
  const text = ref(JSON.stringify(source() ?? {}, null, 2))
  const bad = ref(false)
  watch(source, (v) => {
    try {
      if (JSON.stringify(JSON.parse(text.value)) === JSON.stringify(v ?? {})) return
    } catch {
      /* le brouillon illisible est remplacé par la valeur du modèle */
    }
    text.value = JSON.stringify(v ?? {}, null, 2)
    bad.value = false
  })
  return { text, bad }
}
const payloadJson = jsonDraft(() => props.step.payload)
const filtersJson = jsonDraft(() => payload.value.filters)
function applyJson(draft: { text: { value: string }; bad: { value: boolean } }, apply: (v: Record<string, unknown>) => void, value: string): void {
  draft.text.value = value
  try {
    const parsed = JSON.parse(value) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('objet attendu')
    draft.bad.value = false
    apply(parsed as Record<string, unknown>)
  } catch {
    draft.bad.value = true
  }
}

// ── Nœud Document : les champs du type, ses transitions ──
const named = computed(() => doc.value !== null && !['doc.list', 'doc.save'].includes(doc.value))
const writesData = computed(() => doc.value === 'doc.save' || doc.value === 'doc.patch')
const dataRows = computed(() => Object.entries((payload.value.data as Record<string, unknown> | undefined) ?? {}))
const writableFields = computed(() => (props.meta?.fields ?? []).filter((f) => !['Table', 'Password', 'Image', 'Attach'].includes(f.fieldtype) && !f.read_only))
const freeFields = computed(() => writableFields.value.filter((f) => !dataRows.value.some(([k]) => k === f.fieldname)))
const fieldtypeOf = (name: string): string => props.meta?.fields.find((f) => f.fieldname === name)?.fieldtype ?? 'Text'
const transitions = computed(() => [...new Set((props.meta?.workflow?.transitions ?? []).map((x) => x.action))])

function setData(next: [string, unknown][]): void {
  emit('payload', { data: Object.fromEntries(next) })
}
function addDataField(name: string | null): void {
  if (name) setData([...dataRows.value, [name, '']])
}

// ── Action quelconque : plugin et action du catalogue de xflow, ou tapés ──
const plugins = computed(() => [...new Set(props.catalog.map((a) => a.plugin))].sort())
const actionsOf = computed(() => props.catalog.filter((a) => a.plugin === props.step.plugin).map((a) => a.action))

// ── Sorties ──
const ports = computed(() => portsOf(props.step))
const linkOptions = computed(() => [{ id: '', label: `— ${t.automations.unlinked}` }, ...props.targets])
const caseDraft = ref('')

const OPERATORS = Object.keys(t.automations.operators).map((value) => ({ value, label: t.automations.operators[value] }))
const condition = computed(() => (props.step.condition as { left?: string; operator?: string; right?: string | null } | undefined) ?? {})
const SERVICES = Object.keys(t.automations.aiServices).map((value) => ({ value, label: t.automations.aiServices[value] }))
const BACKOFFS = Object.keys(t.automations.backoff).map((value) => ({ value, label: t.automations.backoff[value] }))
const retry = computed(() => (props.step.retry as { max_attempts?: number; delay_seconds?: number; backoff?: string } | undefined) ?? null)
</script>

<template>
  <div class="af-panel-body" data-testid="af-step-panel">
    <p class="wf-kicker">{{ doc ? t.automations.docActions[doc] : t.automations.steps[step.type] }}</p>
    <h2 class="wf-panel-title" data-testid="af-panel-title">{{ step.description || step.id }}</h2>

    <label class="wf-field" for="af-label">
      <span>{{ t.automations.label }}</span>
      <InputText id="af-label" :model-value="str(step.description)" size="small" :disabled="ro" data-testid="af-label" @update:model-value="emit('patch', { description: $event || undefined })" />
    </label>
    <label class="wf-field" for="af-id">
      <span>{{ t.automations.stepId }}</span>
      <InputText id="af-id" v-model="idDraft" size="small" :disabled="ro" :invalid="!!idProblem" data-testid="af-id" @blur="applyId" @keydown.enter.prevent="applyId" />
      <span v-if="idProblem" class="wf-help fb-error">{{ idProblem }}</span>
      <span v-else class="desk-muted wf-help">{{ t.automations.stepIdHint }}</span>
    </label>

    <!-- Nœud Document -->
    <template v-if="doc">
      <label class="wf-field" for="af-doctype">
        <span>{{ t.automations.doctype }}</span>
        <Select
          input-id="af-doctype"
          :model-value="str(payload.doctype) || null"
          :options="doctypes"
          option-label="label"
          option-value="name"
          filter
          size="small"
          :disabled="ro"
          data-testid="af-doctype"
          @update:model-value="emit('payload', { doctype: $event ?? '', ...(writesData ? { data: {} } : {}) })"
        />
      </label>
      <label v-if="named" class="wf-field" for="af-docname">
        <span>{{ t.automations.document }}</span>
        <VarInput id="af-docname" :model-value="str(payload.name)" :variables="variables" :disabled="ro" testid="af-docname" @update:model-value="emit('payload', { name: $event })" />
      </label>
      <label v-if="doc === 'doc.transition'" class="wf-field" for="af-transition">
        <span>{{ t.automations.transitionAction }}</span>
        <Select
          v-if="transitions.length"
          input-id="af-transition"
          :model-value="str(payload.action) || null"
          :options="transitions"
          size="small"
          :disabled="ro"
          data-testid="af-transition"
          @update:model-value="emit('payload', { action: $event ?? '' })"
        />
        <InputText v-else id="af-transition" :model-value="str(payload.action)" size="small" :disabled="ro" data-testid="af-transition" @update:model-value="emit('payload', { action: $event ?? '' })" />
      </label>
      <label v-if="['doc.transition', 'doc.submit', 'doc.cancel'].includes(doc)" class="wf-field" for="af-version">
        <span>{{ t.automations.version }}</span>
        <VarInput id="af-version" :model-value="shownValue(payload.version)" :variables="variables" :disabled="ro" testid="af-version" @update:model-value="emit('payload', { version: typedValue('Int', $event) })" />
        <span class="desk-muted wf-help">{{ t.automations.versionHint }}</span>
      </label>

      <template v-if="writesData">
        <h3 class="wf-section">{{ t.automations.fields }}</h3>
        <div v-for="([key, value], i) in dataRows" :key="key" class="af-data-row" :data-testid="`af-data-${key}`">
          <label class="af-data-key" :for="`af-data-${i}`">{{ meta?.fields.find((f) => f.fieldname === key)?.label || key }}</label>
          <VarInput
            :id="`af-data-${i}`"
            :model-value="shownValue(value)"
            :variables="variables"
            :disabled="ro"
            :testid="`af-data-value-${key}`"
            @update:model-value="setData(dataRows.map(([k, v]) => [k, k === key ? typedValue(fieldtypeOf(k), $event) : v]))"
          />
          <Button icon="pi pi-times" text rounded severity="secondary" size="small" :disabled="ro" :aria-label="t.automations.unlink(key)" @click="setData(dataRows.filter(([k]) => k !== key))" />
        </div>
        <Select
          :model-value="null"
          :options="freeFields"
          :option-label="(f: { label?: string; fieldname: string }) => f.label || f.fieldname"
          option-value="fieldname"
          :placeholder="t.automations.addField"
          filter
          size="small"
          :disabled="ro || !freeFields.length"
          data-testid="af-add-field"
          @update:model-value="addDataField"
        />
      </template>

      <template v-if="doc === 'doc.list'">
        <label class="wf-field" for="af-filters">
          <span>{{ t.automations.filters }}</span>
          <Textarea id="af-filters" :model-value="filtersJson.text.value" rows="3" auto-resize :disabled="ro" :invalid="filtersJson.bad.value" @update:model-value="applyJson(filtersJson, (v) => emit('payload', { filters: v }), $event ?? '')" />
          <span v-if="filtersJson.bad.value" class="wf-help fb-error">{{ t.automations.badJson }}</span>
        </label>
        <label class="wf-field" for="af-limit">
          <span>{{ t.automations.limit }}</span>
          <InputNumber input-id="af-limit" :model-value="num(payload.limit)" :min="1" :max="500" size="small" :disabled="ro" @update:model-value="emit('payload', { limit: $event ?? undefined })" />
        </label>
      </template>
      <label v-if="doc === 'doc.comment.add'" class="wf-field" for="af-comment">
        <span>{{ t.automations.comment }}</span>
        <VarInput id="af-comment" multiline :model-value="str(payload.content)" :variables="variables" :disabled="ro" testid="af-comment" @update:model-value="emit('payload', { content: $event })" />
      </label>
      <template v-if="doc === 'doc.assign'">
        <label class="wf-field" for="af-assignee">
          <span>{{ t.automations.assignee }}</span>
          <VarInput id="af-assignee" :model-value="str(payload.assignee)" :variables="variables" :disabled="ro" @update:model-value="emit('payload', { assignee: $event })" />
        </label>
        <label class="wf-field" for="af-task">
          <span>{{ t.automations.taskDescription }}</span>
          <VarInput id="af-task" :model-value="str(payload.description)" :variables="variables" :disabled="ro" @update:model-value="emit('payload', { description: $event })" />
        </label>
      </template>
    </template>

    <!-- Action d'un autre plugin -->
    <template v-else-if="step.type === 'action'">
      <label class="wf-field" for="af-plugin">
        <span>{{ t.automations.plugin }}</span>
        <InputText id="af-plugin" :model-value="str(step.plugin)" size="small" list="af-plugins" :disabled="ro" data-testid="af-plugin" @update:model-value="emit('patch', { plugin: $event ?? '' })" />
        <datalist id="af-plugins"><option v-for="p in plugins" :key="p" :value="p" /></datalist>
      </label>
      <label class="wf-field" for="af-action">
        <span>{{ t.automations.action }}</span>
        <InputText id="af-action" :model-value="str(step.action)" size="small" list="af-actions" :disabled="ro" data-testid="af-action" @update:model-value="emit('patch', { action: $event ?? '' })" />
        <datalist id="af-actions"><option v-for="a in actionsOf" :key="a" :value="a" /></datalist>
      </label>
      <label class="wf-field" for="af-payload">
        <span>{{ t.automations.payload }}</span>
        <Textarea id="af-payload" :model-value="payloadJson.text.value" rows="5" auto-resize class="af-code" :disabled="ro" :invalid="payloadJson.bad.value" data-testid="af-payload" @update:model-value="applyJson(payloadJson, (v) => emit('patch', { payload: v }), $event ?? '')" />
        <span v-if="payloadJson.bad.value" class="wf-help fb-error">{{ t.automations.badJson }}</span>
      </label>
    </template>

    <!-- Condition -->
    <template v-else-if="step.type === 'condition'">
      <label class="wf-field" for="af-left">
        <span>{{ t.automations.left }}</span>
        <VarInput id="af-left" :model-value="str(condition.left)" :variables="variables" :disabled="ro" testid="af-left" @update:model-value="emit('patch', { condition: { ...condition, left: $event } })" />
      </label>
      <label class="wf-field" for="af-operator">
        <span>{{ t.automations.operator }}</span>
        <Select input-id="af-operator" :model-value="condition.operator ?? '=='" :options="OPERATORS" option-label="label" option-value="value" size="small" :disabled="ro" data-testid="af-operator" @update:model-value="emit('patch', { condition: { ...condition, operator: $event } })" />
      </label>
      <label v-if="!['is_null', 'is_not_null'].includes(condition.operator ?? '')" class="wf-field" for="af-right">
        <span>{{ t.automations.right }}</span>
        <VarInput id="af-right" :model-value="str(condition.right)" :variables="variables" :disabled="ro" testid="af-right" @update:model-value="emit('patch', { condition: { ...condition, right: $event } })" />
      </label>
    </template>

    <!-- Aiguillage -->
    <template v-else-if="step.type === 'switch'">
      <label class="wf-field" for="af-expression">
        <span>{{ t.automations.expression }}</span>
        <VarInput id="af-expression" :model-value="str(step.expression)" :variables="variables" :disabled="ro" @update:model-value="emit('patch', { expression: $event })" />
      </label>
      <form class="af-inline" @submit.prevent="caseDraft.trim() && (emit('case', null, caseDraft.trim()), (caseDraft = ''))">
        <InputText v-model="caseDraft" size="small" :placeholder="t.automations.caseValue" :disabled="ro" :aria-label="t.automations.caseValue" data-testid="af-case-new" />
        <Button type="submit" :label="t.automations.addCase" icon="pi pi-plus" text size="small" :disabled="ro || !caseDraft.trim()" />
      </form>
    </template>

    <!-- Boucle, parallèle, attente, données, IA -->
    <template v-else-if="step.type === 'foreach'">
      <label class="wf-field" for="af-items">
        <span>{{ t.automations.items }}</span>
        <VarInput id="af-items" :model-value="str(step.items)" :variables="variables" :disabled="ro" @update:model-value="emit('patch', { items: $event })" />
      </label>
      <label class="wf-check">
        <Checkbox :model-value="step.parallel === true" binary input-id="af-par" :disabled="ro" @update:model-value="emit('patch', { parallel: $event === true || undefined })" />
        <span>{{ t.automations.inParallel }}</span>
      </label>
      <label v-if="step.parallel" class="wf-field" for="af-maxpar">
        <span>{{ t.automations.maxParallel }}</span>
        <InputNumber input-id="af-maxpar" :model-value="num(step.max_parallel) ?? 5" :min="1" :max="50" size="small" :disabled="ro" @update:model-value="emit('patch', { max_parallel: $event ?? undefined })" />
      </label>
    </template>
    <label v-else-if="step.type === 'parallel'" class="wf-check">
      <Checkbox :model-value="step.wait_all !== false" binary input-id="af-waitall" :disabled="ro" @update:model-value="emit('patch', { wait_all: $event === true })" />
      <span>{{ t.automations.waitAll }}</span>
    </label>
    <template v-else-if="step.type === 'wait'">
      <label class="wf-field" for="af-delay">
        <span>{{ t.automations.waitSeconds }}</span>
        <InputNumber input-id="af-delay" :model-value="num(step.delay_seconds)" :min="0" size="small" :disabled="ro" @update:model-value="emit('patch', { delay_seconds: $event ?? undefined })" />
      </label>
      <label class="wf-field" for="af-waitevent">
        <span>{{ t.automations.waitEvent }}</span>
        <InputText id="af-waitevent" :model-value="str(step.wait_for_event)" size="small" :disabled="ro" @update:model-value="emit('patch', { wait_for_event: $event || undefined })" />
      </label>
    </template>
    <template v-else-if="step.type === 'transform'">
      <label class="wf-field" for="af-input">
        <span>{{ t.automations.input }}</span>
        <VarInput id="af-input" :model-value="str(step.input)" :variables="variables" :disabled="ro" @update:model-value="emit('patch', { input: $event || undefined })" />
      </label>
      <label class="wf-field" for="af-query">
        <span>{{ t.automations.query }}</span>
        <InputText id="af-query" :model-value="str(step.query)" size="small" class="af-code" :disabled="ro" placeholder="items[?jours > `2`].name" @update:model-value="emit('patch', { query: $event ?? '' })" />
      </label>
    </template>
    <template v-else-if="step.type === 'template'">
      <label class="wf-field" for="af-template">
        <span>{{ t.automations.template }}</span>
        <VarInput id="af-template" multiline :model-value="str(step.template)" :variables="variables" :disabled="ro" testid="af-template" @update:model-value="emit('patch', { template: $event })" />
      </label>
      <label class="wf-field" for="af-outkey">
        <span>{{ t.automations.outputKey }}</span>
        <InputText id="af-outkey" :model-value="str(step.output_key)" size="small" :disabled="ro" @update:model-value="emit('patch', { output_key: $event || 'rendered' })" />
      </label>
    </template>
    <template v-else-if="step.type === 'ai'">
      <label class="wf-field" for="af-service">
        <span>{{ t.automations.aiService }}</span>
        <Select input-id="af-service" :model-value="str(step.service) || 'summarize'" :options="SERVICES" option-label="label" option-value="value" size="small" :disabled="ro" @update:model-value="emit('patch', { service: $event })" />
      </label>
      <label class="wf-field" for="af-prompt">
        <span>{{ t.automations.prompt }}</span>
        <VarInput id="af-prompt" multiline :model-value="str(step.prompt)" :variables="variables" :disabled="ro" @update:model-value="emit('patch', { prompt: $event })" />
      </label>
      <label class="wf-field" for="af-context">
        <span>{{ t.automations.context }}</span>
        <VarInput id="af-context" :model-value="str(step.context)" :variables="variables" :disabled="ro" @update:model-value="emit('patch', { context: $event || undefined })" />
      </label>
    </template>

    <!-- Nouvel essai (actions) -->
    <template v-if="step.type === 'action'">
      <label class="wf-check">
        <Checkbox :model-value="!!retry" binary input-id="af-retry" :disabled="ro" data-testid="af-retry" @update:model-value="emit('patch', { retry: $event ? { max_attempts: 3, delay_seconds: 5, backoff: 'exponential' } : undefined })" />
        <span>{{ t.automations.retry }}</span>
      </label>
      <div v-if="retry" class="af-inline">
        <label class="wf-field" for="af-attempts">
          <span>{{ t.automations.attempts }}</span>
          <InputNumber input-id="af-attempts" :model-value="retry.max_attempts ?? 3" :min="1" :max="20" size="small" :disabled="ro" @update:model-value="emit('patch', { retry: { ...retry, max_attempts: $event ?? 3 } })" />
        </label>
        <label class="wf-field" for="af-delay-retry">
          <span>{{ t.automations.delay }}</span>
          <InputNumber input-id="af-delay-retry" :model-value="retry.delay_seconds ?? 5" :min="0.1" :max-fraction-digits="1" size="small" :disabled="ro" @update:model-value="emit('patch', { retry: { ...retry, delay_seconds: $event ?? 5 } })" />
        </label>
        <label class="wf-field" for="af-backoff">
          <span>&nbsp;</span>
          <Select input-id="af-backoff" :model-value="retry.backoff ?? 'exponential'" :options="BACKOFFS" option-label="label" option-value="value" size="small" :disabled="ro" :aria-label="t.automations.retry" @update:model-value="emit('patch', { retry: { ...retry, backoff: $event } })" />
        </label>
      </div>
    </template>

    <!-- Sorties : où chacune mène (le canevas fait pareil en tirant d'une poignée) -->
    <h3 class="wf-section">{{ t.automations.outputs }}</h3>
    <div v-for="p in ports" :key="p.id" class="af-port-row" :data-tone="p.tone">
      <label class="af-port-name" :for="`af-link-${p.id}`">{{ p.label }}</label>
      <Select
        :input-id="`af-link-${p.id}`"
        :model-value="targetOf(step, p.id) ?? ''"
        :options="linkOptions"
        option-label="label"
        option-value="id"
        size="small"
        :disabled="ro"
        :data-testid="`af-link-${p.id}`"
        @update:model-value="emit('link', p.id, $event || null)"
      />
      <Button
        v-if="p.id.startsWith('case:')"
        icon="pi pi-trash"
        text
        rounded
        severity="secondary"
        size="small"
        :disabled="ro"
        :aria-label="t.automations.unlink(p.label)"
        @click="emit('case', p.id.slice(5), null)"
      />
    </div>

    <Button :label="t.automations.removeStep" icon="pi pi-trash" severity="danger" text size="small" class="af-remove" :disabled="ro" data-testid="af-remove" @click="emit('remove')" />
  </div>
</template>
