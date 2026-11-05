<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import Textarea from 'primevue/textarea'
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import {
  definitionOf,
  definitionText,
  draftOf,
  emptyDraft,
  emptyStep,
  TRIGGER_KINDS,
  VARIABLES,
  type FlowDraft,
} from '@/lib/automations'

/**
 * Créer ou modifier une automatisation (un flux xflow). Deux faces du même flux : le FORMULAIRE (un déclencheur, des étapes « action »
 * qui s'enchaînent dans l'ordre) et le JSON brut, pour tout le reste du langage d'xflow. Une définition que le formulaire ne sait pas
 * montrer s'ouvre en JSON, jamais tronquée (`lib/automations.draftOf`). Présentation seulement : la page déploie (`deploy`).
 */
const props = defineProps<{
  visible: boolean
  /** La définition stockée (modification), ou null (création). */
  definition: Record<string, unknown> | null
  /** `plugin.action` du catalogue d'xflow (`GET /registry`), pour l'autocomplétion. */
  actions: { name: string; description: string }[]
  /** Les événements proposés (`eventSuggestions`). */
  events: string[]
  saving: boolean
  /** Ce que le serveur a refusé au dernier déploiement. */
  serverError: string | null
}>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; deploy: [definition: Record<string, unknown>] }>()

const mode = ref<'form' | 'json'>('form')
const draft = ref<FlowDraft>(emptyDraft())
const json = ref('')
const jsonOnly = ref<string | null>(null)
const problems = ref<string[]>([])
const editing = computed(() => props.definition !== null)

watch(
  () => [props.visible, props.definition] as const,
  ([open]) => {
    if (!open) return
    problems.value = []
    if (!props.definition) {
      draft.value = emptyDraft()
      json.value = ''
      jsonOnly.value = null
      mode.value = 'form'
      return
    }
    json.value = definitionText(props.definition)
    const read = draftOf(props.definition)
    jsonOnly.value = read.draft ? null : read.reason
    if (read.draft) draft.value = read.draft
    mode.value = read.draft ? 'form' : 'json'
  },
  { immediate: true },
)

const modes = computed(() => [
  { value: 'form', label: t.automations.form, disabled: !!jsonOnly.value },
  { value: 'json', label: t.automations.json },
])
const triggerOptions = TRIGGER_KINDS.map((k) => ({ value: k, label: t.automations.triggers[k] ?? k }))
const triggerHint = computed(() => ({
  manual: t.automations.manualHint, event: t.automations.eventHint, schedule: t.automations.cronHint, webhook: t.automations.webhookHint,
})[draft.value.trigger])

/** Passer d'une face à l'autre garde ce qui est saisi : le formulaire s'écrit en JSON, et un JSON lisible redevient formulaire. */
function switchTo(next: 'form' | 'json'): void {
  problems.value = []
  if (next === mode.value) return
  if (next === 'json') {
    const made = definitionOf(draft.value)
    json.value = made.definition ? definitionText(made.definition) : json.value || definitionText({ name: draft.value.name })
    mode.value = 'json'
    return
  }
  const parsed = parseJson()
  if (!parsed) return
  const read = draftOf(parsed)
  if (!read.draft) return void (problems.value = [t.automations.jsonOnly(read.reason)])
  draft.value = read.draft
  mode.value = 'form'
}

function parseJson(): Record<string, unknown> | null {
  try {
    const value = JSON.parse(json.value)
    if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  } catch {
    // signalé ci-dessous
  }
  problems.value = [t.automations.jsonInvalid]
  return null
}

/** Le texte d'une puce de variable (écrit en script : des accolades doubles dans le gabarit seraient lues par Vue). */
const chip = (v: string) => `{{ ${v} }}`

function addStep(): void {
  draft.value.steps.push(emptyStep(draft.value.steps.length))
}
function move(i: number, by: -1 | 1): void {
  const steps = draft.value.steps
  const j = i + by
  if (j < 0 || j >= steps.length) return
  ;[steps[i], steps[j]] = [steps[j]!, steps[i]!]
}
function insertVariable(i: number, variable: string): void {
  const step = draft.value.steps[i]
  if (step) step.payload = `${step.payload}{{ ${variable} }}`
}

function submit(): void {
  problems.value = []
  if (mode.value === 'json') {
    const parsed = parseJson()
    if (parsed) emit('deploy', parsed)
    return
  }
  const made = definitionOf(draft.value)
  if (!made.definition) return void (problems.value = made.problems)
  emit('deploy', made.definition)
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    :header="t.automations.editorTitle(definition ? String(definition.name ?? '') : null)"
    :style="{ width: 'min(54rem, 96vw)' }"
    :draggable="false"
    data-testid="flow-editor"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="fe">
      <SelectButton
        :model-value="mode"
        :options="modes"
        option-label="label"
        option-value="value"
        option-disabled="disabled"
        :allow-empty="false"
        size="small"
        data-testid="flow-mode"
        @update:model-value="switchTo($event)"
      />
      <Message v-if="jsonOnly" severity="secondary" :closable="false" data-testid="flow-json-only">{{ t.automations.jsonOnly(jsonOnly) }}</Message>

      <template v-if="mode === 'form'">
        <div class="fe-pair">
          <label class="fe-field">
            <span>{{ t.automations.name }}</span>
            <InputText v-model="draft.name" :disabled="editing" fluid data-testid="flow-name" />
            <small class="desk-muted">{{ t.automations.nameHint }}</small>
          </label>
          <label class="fe-field">
            <span>{{ t.automations.description }}</span>
            <InputText v-model="draft.description" fluid data-testid="flow-description" />
          </label>
        </div>

        <div class="fe-field">
          <span>{{ t.automations.trigger }}</span>
          <SelectButton v-model="draft.trigger" :options="triggerOptions" option-label="label" option-value="value" :allow-empty="false" size="small" data-testid="flow-trigger" />
          <label v-if="draft.trigger === 'event'" class="fe-field">
            <span class="fe-sub">{{ t.automations.eventLabel }}</span>
            <InputText v-model="draft.event" list="fe-events" fluid data-testid="flow-event" />
          </label>
          <label v-if="draft.trigger === 'schedule'" class="fe-field">
            <span class="fe-sub">{{ t.automations.cronLabel }}</span>
            <InputText v-model="draft.cron" placeholder="0 9 * * 1-5" fluid data-testid="flow-cron" />
          </label>
          <small class="desk-muted">{{ triggerHint }}</small>
        </div>

        <section class="fe-steps">
          <h3>{{ t.automations.steps }}</h3>
          <small class="desk-muted">{{ t.automations.stepsHint }}</small>
          <ol class="fe-list">
            <li v-for="(step, i) in draft.steps" :key="i" class="fe-step" :data-testid="`flow-step-${i}`">
              <div class="fe-step-head">
                <strong>{{ i + 1 }}.</strong>
                <label class="fe-field fe-id">
                  <span class="fe-sub">{{ t.automations.stepId }}</span>
                  <InputText v-model="step.id" size="small" fluid :data-testid="`flow-step-id-${i}`" />
                </label>
                <label class="fe-field fe-grow">
                  <span class="fe-sub">{{ t.automations.stepAction }}</span>
                  <InputText v-model="step.target" list="fe-actions" size="small" fluid :data-testid="`flow-step-action-${i}`" />
                </label>
                <div class="fe-tools">
                  <Button icon="pi pi-arrow-up" text rounded size="small" :aria-label="t.automations.up" :disabled="i === 0" @click="move(i, -1)" />
                  <Button icon="pi pi-arrow-down" text rounded size="small" :aria-label="t.automations.down" :disabled="i === draft.steps.length - 1" @click="move(i, 1)" />
                  <Button icon="pi pi-trash" text rounded size="small" severity="danger" :aria-label="t.automations.removeStep" :disabled="draft.steps.length === 1" :data-testid="`flow-step-remove-${i}`" @click="draft.steps.splice(i, 1)" />
                </div>
              </div>
              <label class="fe-field">
                <span class="fe-sub">{{ t.automations.stepPayload }}</span>
                <Textarea v-model="step.payload" rows="5" auto-resize class="fe-code" fluid :data-testid="`flow-step-payload-${i}`" />
              </label>
              <div class="fe-vars" :aria-label="t.automations.variables">
                <span class="fe-sub">{{ t.automations.variables }}</span>
                <button v-for="v in VARIABLES" :key="v" type="button" class="fe-var" @click="insertVariable(i, v)" v-text="chip(v)" />
              </div>
            </li>
          </ol>
          <small class="desk-muted">{{ t.automations.variablesHint }}</small>
          <Button :label="t.automations.addStep" icon="pi pi-plus" text size="small" class="fe-add" data-testid="flow-add-step" @click="addStep" />
        </section>
      </template>

      <Textarea v-else v-model="json" rows="20" class="fe-code" fluid data-testid="flow-json" />

      <Message v-for="p in problems" :key="p" severity="error" :closable="false" data-testid="flow-problem">{{ p }}</Message>
      <Message v-if="serverError" severity="error" :closable="false" data-testid="flow-server-error">{{ serverError }}</Message>
    </div>

    <datalist id="fe-actions">
      <option v-for="a in actions" :key="a.name" :value="a.name">{{ a.description }}</option>
    </datalist>
    <datalist id="fe-events">
      <option v-for="e in events" :key="e" :value="e" />
    </datalist>

    <template #footer>
      <Button :label="t.automations.cancel" text @click="emit('update:visible', false)" />
      <Button :label="t.automations.deploy" icon="pi pi-cloud-upload" :loading="saving" :disabled="saving" data-testid="flow-deploy" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.fe {
  display: grid;
  gap: 0.9rem;
}

.fe-pair {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 0.75rem;
}

.fe-field {
  display: grid;
  gap: 0.3rem;
  min-width: 0;
  font-size: 0.875rem;
}

.fe-field > span:first-child {
  color: var(--p-text-muted-color);
}

.fe-sub {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}

.fe-steps h3 {
  margin: 0;
  font-size: 0.95rem;
}

.fe-steps {
  display: grid;
  gap: 0.4rem;
}

.fe-list {
  display: grid;
  gap: 0.75rem;
  margin: 0.25rem 0 0;
  padding: 0;
  list-style: none;
}

.fe-step {
  display: grid;
  gap: 0.5rem;
  padding: 0.75rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 8px;
}

.fe-step-head {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.5rem;
}

.fe-id {
  width: 10rem;
}

.fe-grow {
  flex: 1 1 14rem;
}

.fe-tools {
  display: flex;
}

.fe-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem;
}

.fe-vars {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3rem;
}

.fe-var {
  font: inherit;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  padding: 0.15rem 0.45rem;
  min-height: 24px;
  border-radius: 999px;
  border: 1px solid var(--p-content-border-color);
  background: var(--p-content-background);
  color: var(--p-text-color);
  cursor: pointer;
}

.fe-var:hover {
  border-color: var(--p-primary-color);
}

.fe-add {
  justify-self: start;
}
</style>
