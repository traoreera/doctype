<script setup lang="ts">
import { computed, useId } from 'vue'
import DatePicker from 'primevue/datepicker'
import { dateFromUtc, utcFromDate } from '@/lib/datetime'
import { datePattern } from '@/lib/format'

/**
 * Un champ date + heure, celui des formulaires, utilisable seul (SDK : `DatetimeInput`). L'utilisateur lit et saisit l'heure LOCALE ;
 * la valeur est l'instant en UTC, `AAAA-MM-JJThh:mm:ssZ`, à la minute (lib/datetime.ts).
 */
const props = withDefaults(
  defineProps<{
    modelValue: unknown
    inputId?: string
    invalid?: boolean
    disabled?: boolean
    /** Bornes incluses, des instants UTC `AAAA-MM-JJThh:mm:ssZ` ; une borne illisible ne borne rien. */
    min?: string | null
    max?: string | null
    clearable?: boolean
    label?: string
  }>(),
  { clearable: true },
)
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const ownId = useId()
const id = computed(() => props.inputId ?? ownId)
const value = computed(() => dateFromUtc(typeof props.modelValue === 'string' ? props.modelValue : null))
const set = (v: unknown) => emit('update:modelValue', v instanceof Date ? utcFromDate(new Date(new Date(v).setSeconds(0, 0))) : null)
</script>

<template>
  <label v-if="label" :for="id" class="date-input-label">{{ label }}</label>
  <DatePicker
    :input-id="id"
    :model-value="value"
    :invalid="invalid"
    :disabled="disabled"
    :min-date="dateFromUtc(min) ?? undefined"
    :max-date="dateFromUtc(max) ?? undefined"
    :date-format="datePattern()"
    show-time
    hour-format="24"
    show-icon
    icon-display="input"
    :show-button-bar="clearable"
    fluid
    @update:model-value="set"
  />
</template>

<style scoped>
.date-input-label {
  display: block;
  margin-bottom: 0.25rem;
  font-size: 0.875rem;
}
</style>
