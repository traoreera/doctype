<script setup lang="ts">
import { computed } from 'vue'
import FieldInput from '@/components/FieldInput.vue'
import CellValue from '@/components/CellValue.vue'
import LinkField from '@/components/LinkField.vue'
import { t } from '@/i18n'
import { isWide } from '@/lib/form'
import { formatCell } from '@/lib/format'
import { sanitizeRich } from '@/lib/richtext'
import type { FieldMeta } from '@/lib/types'

const props = defineProps<{
  field: FieldMeta
  modelValue: unknown
  required: boolean
  readOnly: boolean
  error?: string | undefined
  formId: string
  /** Le type du document (un fichier se téléverse POUR un champ d'un type). */
  doctype?: string
  /** L'unité à afficher à côté de la valeur (« kg »), si le champ en a une. */
  unit?: string | null
  /** DynamicLink : le DocType que le champ de type désigne en ce moment. */
  dynamicTarget?: string | null
}>()
const emit = defineEmits<{ 'update:modelValue': [value: unknown] }>()

const id = computed(() => `${props.formId}-${props.field.fieldname}`)
const label = computed(() => props.field.label ?? props.field.fieldname)
const target = computed(() => (typeof props.field.options === 'string' ? props.field.options : ''))
</script>

<template>
  <div class="form-field" :data-field="field.fieldname" :data-wide="isWide(field) || undefined">
    <label :for="id" :class="{ 'form-checkbox-label': field.fieldtype === 'Boolean' }">
      {{ label }}<span v-if="required && !readOnly" class="req" aria-hidden="true">*</span>
    </label>

    <template v-if="readOnly">
      <LinkField v-if="field.fieldtype === 'Link'" :model-value="modelValue" :target="target" readonly />
      <LinkField v-else-if="field.fieldtype === 'DynamicLink' && dynamicTarget && modelValue" :model-value="modelValue" :target="dynamicTarget" readonly />
      <CellValue v-else-if="field.fieldtype === 'Image' || field.fieldtype === 'Attach'" :field="field" :value="modelValue" />
      <!-- Le HTML d'un texte enrichi ne s'insère qu'APRÈS le filtre qui ne garde que les balises permises (lib/richtext.ts) : jamais le texte brut d'un autre. -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div v-else-if="field.fieldtype === 'TextEditor' && modelValue" class="value rich-value" data-testid="rich-value" v-html="sanitizeRich(modelValue)" />
      <pre v-else-if="(field.fieldtype === 'Code' || field.fieldtype === 'JSON') && modelValue" class="value code-value" :data-testid="`value-${field.fieldname}`">{{ modelValue }}</pre>
      <span v-else :id="id" class="value" :data-testid="`value-${field.fieldname}`">
        {{ formatCell(field, modelValue) || t.form.noValue }}<span v-if="unit && modelValue !== null && modelValue !== ''" class="unit">{{ unit }}</span>
      </span>
    </template>
    <div v-else-if="unit" class="cell-input">
      <FieldInput :field="field" :model-value="modelValue" :input-id="id" :invalid="!!error" :aria-required="required" :doctype="doctype" :dynamic-target="dynamicTarget" @update:model-value="emit('update:modelValue', $event)" />
      <span class="unit" :data-testid="`unit-${field.fieldname}`">{{ unit }}</span>
    </div>
    <FieldInput
      v-else
      :field="field"
      :model-value="modelValue"
      :input-id="id"
      :invalid="!!error"
      :aria-required="required"
      :doctype="doctype"
      :dynamic-target="dynamicTarget"
      @update:model-value="emit('update:modelValue', $event)"
    />

    <span v-if="error" class="error" role="alert" :data-testid="`error-${field.fieldname}`">{{ error }}</span>
    <span v-else-if="field.description" class="hint">{{ field.description }}</span>
  </div>
</template>
