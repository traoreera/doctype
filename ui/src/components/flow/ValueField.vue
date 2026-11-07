<script setup lang="ts">
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { computed, inject } from 'vue'
import { t } from '@/i18n'
import { FLOW_VALUE_CONTEXT } from '@/lib/flowcatalog'
import { fixedText, fixedValue, readValue, TRIGGER_KEYS, writeValue, type FlowValue } from '@/lib/flowvalues'

/**
 * Une valeur d'automatisation : « une valeur fixe », « du document déclencheur » ou « du document d'une étape précédente ».
 *
 * C'est ce qui remplace les accolades : l'utilisateur CHOISIT dans une liste, le jeton `{{ … }}` est écrit par la machine
 * (`lib/flowvalues`). Une expression venue d'ailleurs est montrée telle quelle, en lecture seule : on ne l'abîme pas.
 */
const props = defineProps<{
  modelValue: unknown
  label: string
  /** Le type du champ visé, s'il est connu : une valeur fixe part alors dans le bon type (le moteur n'en convertit aucune). */
  fieldtype?: string
  /** Les valeurs possibles d'un Select. */
  options?: string[]
  /** Les étapes dont on peut reprendre le document (celles qui lisent ou créent, avant celle-ci). */
  steps?: string[]
  /** Les champs du document de chaque étape, quand son type est connu : on choisit « le total de la facture lue ». */
  stepFields?: Record<string, { value: string; label: string }[]>
  /** Les clés du déclencheur : celles d'un événement de PLUGIN quand le flux en écoute un (`lib/flowcatalog`), sinon celles du moteur. */
  triggerKeys?: { value: string; label: string }[]
  /** Ce que les nœuds de plugin AVANT celui-ci rendent (`message_id`…) : « le résultat de l'étape X ». */
  outputs?: { step: string; path: string; label: string }[]
  hint?: string
  disabled?: boolean
  testid?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: unknown): void }>()

const value = computed<FlowValue>(() => readValue(props.modelValue))
const context = inject(FLOW_VALUE_CONTEXT, null)
const outputs = computed(() => props.outputs ?? context?.outputs.value ?? [])

const sources = computed(() => [
  { value: 'fixed', label: t.flowBuilder.values.fixed },
  { value: 'trigger', label: t.flowBuilder.values.trigger },
  ...(props.steps?.length ? [{ value: 'step', label: t.flowBuilder.values.step }] : []),
  ...(outputs.value.length || value.value.kind === 'output' ? [{ value: 'output', label: t.flowBuilder.values.output }] : []),
  ...(value.value.kind === 'raw' ? [{ value: 'raw', label: t.flowBuilder.values.raw }] : []),
])

const triggerOptions = computed(() => {
  const keys = props.triggerKeys ?? context?.triggerKeys.value ?? TRIGGER_KEYS.map((key) => ({ value: key, label: t.flowBuilder.values.triggerKeys[key] }))
  const v = value.value
  // Une clé citée que la liste ne propose plus (le déclencheur a changé) reste montrée, pour qu'on la voie et la remplace.
  return v.kind === 'trigger' && !keys.some((k) => k.value === v.key) ? [...keys, { value: v.key, label: v.key }] : keys
})
const outputOptions = computed(() => {
  const known = outputs.value.map((o) => ({ value: `${o.step}|${o.path}`, label: `${o.step} › ${o.label}` }))
  const v = value.value
  // Un résultat cité mais qui n'est plus proposé (l'étape a changé) reste montré : on ne le perd pas en ouvrant le panneau.
  if (v.kind === 'output' && !known.some((o) => o.value === `${v.step}|${v.path}`)) known.push({ value: `${v.step}|${v.path}`, label: `${v.step} › ${v.path}` })
  return known
})

function setSource(kind: string): void {
  if (kind === value.value.kind) return
  if (kind === 'trigger') return emit('update:modelValue', writeValue({ kind: 'trigger', key: triggerOptions.value[0]?.value ?? 'name' }))
  if (kind === 'output') {
    const first = outputs.value[0]
    return emit('update:modelValue', writeValue({ kind: 'output', step: first?.step ?? '', path: first?.path ?? '' }))
  }
  if (kind === 'step') return emit('update:modelValue', writeValue({ kind: 'step', step: props.steps?.[0] ?? '' }))
  emit('update:modelValue', fixedValue('', props.fieldtype))
}
const set = (v: FlowValue) => emit('update:modelValue', writeValue(v))
</script>

<template>
  <div class="wf-field fl-value">
    <span>{{ label }}</span>
    <div class="fl-value-row">
      <Select
        :model-value="value.kind"
        :options="sources"
        option-label="label"
        option-value="value"
        :disabled="disabled"
        size="small"
        class="fl-value-source"
        :aria-label="`${label} — ${t.flowBuilder.values.source}`"
        :data-testid="testid ? `${testid}-source` : undefined"
        @update:model-value="setSource(String($event))"
      />

      <!-- Une valeur fixe : saisie selon le type du champ visé (une liste pour un Select, oui/non pour un booléen). -->
      <Select
        v-if="value.kind === 'fixed' && fieldtype === 'Boolean'"
        :model-value="value.value === true"
        :options="[{ value: true, label: t.yes }, { value: false, label: t.no }]"
        option-label="label"
        option-value="value"
        :disabled="disabled"
        size="small"
        :data-testid="testid"
        @update:model-value="set({ kind: 'fixed', value: $event === true })"
      />
      <Select
        v-else-if="value.kind === 'fixed' && options?.length"
        :model-value="fixedText(value)"
        :options="options"
        :disabled="disabled"
        size="small"
        :data-testid="testid"
        @update:model-value="set({ kind: 'fixed', value: $event })"
      />
      <InputText
        v-else-if="value.kind === 'fixed'"
        :model-value="fixedText(value)"
        :disabled="disabled"
        size="small"
        :type="fieldtype === 'Date' ? 'date' : fieldtype === 'Number' || fieldtype === 'Int' ? 'number' : 'text'"
        :data-testid="testid"
        @update:model-value="set({ kind: 'fixed', value: fixedValue(String($event ?? ''), fieldtype) })"
      />

      <!-- Du document déclencheur : son numéro, son type, qui a agi… -->
      <Select
        v-else-if="value.kind === 'trigger'"
        :model-value="value.key"
        :options="triggerOptions"
        option-label="label"
        option-value="value"
        :disabled="disabled"
        size="small"
        :data-testid="testid"
        @update:model-value="set({ kind: 'trigger', key: $event })"
      />

      <!-- Le résultat d'un nœud de plugin précédent : ce qu'il a déclaré rendre (`message_id`…). -->
      <Select
        v-else-if="value.kind === 'output'"
        :model-value="`${value.step}|${value.path}`"
        :options="outputOptions"
        option-label="label"
        option-value="value"
        :disabled="disabled"
        size="small"
        :data-testid="testid"
        @update:model-value="set({ kind: 'output', step: String($event).split('|')[0]!, path: String($event).split('|')[1] ?? '' })"
      />

      <!-- D'une étape précédente : le document qu'elle a lu ou créé, ou l'un de ses champs. -->
      <template v-else-if="value.kind === 'step'">
        <Select
          :model-value="value.step"
          :options="steps ?? []"
          :disabled="disabled"
          size="small"
          :data-testid="testid"
          @update:model-value="set({ kind: 'step', step: String($event), field: value.kind === 'step' ? value.field : undefined })"
        />
        <!-- `$name` : le document lui-même (son numéro). Une valeur vide laisserait la liste muette — PrimeVue la lit comme « rien de choisi ». -->
        <Select
          :model-value="value.field ?? '$name'"
          :options="[{ value: '$name', label: t.flowBuilder.values.docName }, ...(stepFields?.[value.step] ?? [])]"
          option-label="label"
          option-value="value"
          :disabled="disabled"
          size="small"
          :aria-label="t.flowBuilder.values.whichValue"
          :data-testid="testid ? `${testid}-field` : undefined"
          @update:model-value="set({ kind: 'step', step: value.kind === 'step' ? value.step : '', field: $event === '$name' ? undefined : String($event) })"
        />
      </template>

      <!-- Une expression écrite ailleurs : montrée, gardée, jamais réécrite ici. -->
      <InputText v-else :model-value="value.text" disabled size="small" class="fe-code" :data-testid="testid" />
    </div>
    <span v-if="hint" class="desk-muted wf-help">{{ hint }}</span>
    <span v-else-if="value.kind === 'raw'" class="desk-muted wf-help">{{ t.flowBuilder.values.rawHint }}</span>
  </div>
</template>
