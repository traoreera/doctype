<script setup lang="ts">
import Button from 'primevue/button'
import DatePicker from 'primevue/datepicker'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { computed } from 'vue'
import { t } from '@/i18n'
import { defaultOp, opsFor, type FilterRow } from '@/lib/filters'
import { dateFromUtc, utcFromDate } from '@/lib/datetime'
import { datePattern, dateFromIso, isoFromDate } from '@/lib/format'
import { isDecimalType, type FieldMeta } from '@/lib/types'

const props = defineProps<{ fields: FieldMeta[]; rows: FilterRow[] }>()
const emit = defineEmits<{ 'update:rows': [rows: FilterRow[]]; apply: [] }>()

const choices = computed(() => props.fields.filter((f) => !f.hidden && opsFor(f.fieldtype).length > 0).map((f) => ({ value: f.fieldname, label: f.label ?? f.fieldname })))
const fieldOf = (name: string) => props.fields.find((f) => f.fieldname === name)
const opChoices = (f: FieldMeta | undefined) => (f ? opsFor(f.fieldtype) : []).map((op) => ({ value: op, label: t.ops[op] ?? op }))
let seq = Date.now()

function patch(row: FilterRow, change: Partial<FilterRow>): void {
  emit('update:rows', props.rows.map((r) => (r.id === row.id ? { ...r, ...change } : r)))
}

function add(): void {
  const first = props.fields.find((f) => !f.hidden && opsFor(f.fieldtype).length > 0 && !props.rows.some((r) => r.field === f.fieldname))
  if (!first) return
  emit('update:rows', [...props.rows, { id: ++seq, field: first.fieldname, op: defaultOp(first.fieldtype), value: first.fieldtype === 'Boolean' ? true : null }])
}

function changeField(row: FilterRow, name: string): void {
  const f = fieldOf(name)
  if (f) patch(row, { field: name, op: defaultOp(f.fieldtype), value: f.fieldtype === 'Boolean' ? true : null })
}

function remove(row: FilterRow): void {
  emit('update:rows', props.rows.filter((r) => r.id !== row.id))
  emit('apply')
}

function clear(): void {
  emit('update:rows', [])
  emit('apply')
}
</script>

<template>
  <div class="desk-card filter-bar" data-testid="filter-bar" style="padding: 0.75rem 1rem; margin-bottom: 0.75rem">
    <div v-for="row in rows" :key="row.id" class="filter-row" :data-testid="`filter-${row.field}`">
      <Select :model-value="row.field" :options="choices" option-label="label" option-value="value" size="small" :aria-label="t.list.field" @update:model-value="changeField(row, $event)" />
      <Select :model-value="row.op" :options="opChoices(fieldOf(row.field))" option-label="label" option-value="value" size="small" :aria-label="t.list.operator" @update:model-value="patch(row, { op: $event })" />

      <template v-if="fieldOf(row.field)">
        <Select
          v-if="fieldOf(row.field)!.fieldtype === 'Boolean'"
          :model-value="row.value"
          :options="[{ v: true, l: t.yes }, { v: false, l: t.no }]"
          option-label="l"
          option-value="v"
          size="small"
          :aria-label="t.list.value"
          @update:model-value="patch(row, { value: $event })"
        />
        <InputText
          v-else-if="row.op === 'in'"
          :model-value="typeof row.value === 'string' ? row.value : ''"
          size="small"
          :placeholder="isDecimalType(fieldOf(row.field)!.fieldtype) ? t.list.inHelpDecimal : t.list.inHelp"
          :aria-label="t.list.value"
          @update:model-value="patch(row, { value: $event })"
          @keydown.enter="emit('apply')"
        />
        <Select
          v-else-if="fieldOf(row.field)!.fieldtype === 'Select'"
          :model-value="row.value"
          :options="(fieldOf(row.field)!.options as string[]) ?? []"
          size="small"
          :aria-label="t.list.value"
          @update:model-value="patch(row, { value: $event })"
        />
        <InputNumber
          v-else-if="['Number', 'Rating', 'Int', 'Percent'].includes(fieldOf(row.field)!.fieldtype)"
          :model-value="typeof row.value === 'number' ? row.value : null"
          :use-grouping="false"
          :max-fraction-digits="10"
          locale="fr-FR"
          size="small"
          :aria-label="t.list.value"
          @input="patch(row, { value: $event.value ?? null })"
          @update:model-value="patch(row, { value: $event })"
          @keydown.enter="emit('apply')"
        />
        <DatePicker
          v-else-if="fieldOf(row.field)!.fieldtype === 'Date'"
          :model-value="dateFromIso(typeof row.value === 'string' ? row.value : null)"
          :date-format="datePattern()"
          size="small"
          :aria-label="t.list.value"
          @update:model-value="patch(row, { value: $event instanceof Date ? isoFromDate($event) : null })"
        />
        <DatePicker
          v-else-if="fieldOf(row.field)!.fieldtype === 'Datetime'"
          :model-value="dateFromUtc(typeof row.value === 'string' ? row.value : null)"
          :date-format="datePattern()"
          show-time
          hour-format="24"
          size="small"
          :aria-label="t.list.value"
          @update:model-value="patch(row, { value: $event instanceof Date ? utcFromDate(new Date(new Date($event).setSeconds(0, 0))) : null })"
        />
        <InputText
          v-else
          :model-value="typeof row.value === 'string' ? row.value : ''"
          size="small"
          :aria-label="t.list.value"
          @update:model-value="patch(row, { value: $event })"
          @keydown.enter="emit('apply')"
        />
      </template>

      <Button icon="pi pi-times" text severity="secondary" size="small" :aria-label="t.delete" @click="remove(row)" />
    </div>

    <div class="filter-actions">
      <Button :label="t.list.addFilter" icon="pi pi-plus" text size="small" data-testid="add-filter" @click="add" />
      <span class="desk-spacer" />
      <Button v-if="rows.length" :label="t.list.clearFilters" text severity="secondary" size="small" @click="clear" />
      <Button v-if="rows.length" :label="t.apply" size="small" data-testid="apply-filters" @click="emit('apply')" />
    </div>
  </div>
</template>

<style scoped>
.filter-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  flex-wrap: wrap;
}
.filter-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
</style>
