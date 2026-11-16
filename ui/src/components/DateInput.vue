<script setup lang="ts">
import { computed, useId } from 'vue'
import DatePicker from 'primevue/datepicker'
import { dateFromIso, datePattern, isoFromDate } from '@/lib/format'

/**
 * Un champ de date, celui des formulaires, utilisable seul (SDK : `DateInput`). La valeur est `AAAA-MM-JJ`, le jour LOCAL choisi
 * (jamais décalé par l'UTC) ; l'affichage suit le format et la langue du Desk, pas ceux du navigateur comme `<input type="date">`.
 */
const props = withDefaults(
  defineProps<{
    modelValue: unknown
    inputId?: string
    invalid?: boolean
    disabled?: boolean
    /** Bornes incluses, `AAAA-MM-JJ` ; une borne illisible ne borne rien. */
    min?: string | null
    max?: string | null
    /** Le bouton « Effacer » (et « Aujourd'hui ») du calendrier. */
    clearable?: boolean
    label?: string
  }>(),
  { clearable: true },
)
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const ownId = useId()
const id = computed(() => props.inputId ?? ownId)
const value = computed(() => dateFromIso(typeof props.modelValue === 'string' ? props.modelValue : null))
const set = (v: unknown) => emit('update:modelValue', v instanceof Date ? isoFromDate(v) : null)
</script>

<template>
  <label v-if="label" :for="id" class="date-input-label">{{ label }}</label>
  <DatePicker
    :input-id="id"
    :model-value="value"
    :invalid="invalid"
    :disabled="disabled"
    :min-date="dateFromIso(min) ?? undefined"
    :max-date="dateFromIso(max) ?? undefined"
    :date-format="datePattern()"
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
