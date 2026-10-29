<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed } from 'vue'
import { t } from '@/i18n'
import {
  KIND_LABELS,
  columnFields,
  fieldColumns,
  groupableColumns,
  printableFields,
  tableColumns,
  totalFields,
  type Block,
  type Column,
} from '@/lib/printedit'
import type { DocTypeMeta } from '@/lib/types'

/**
 * Les propriétés du bloc choisi : c'est ICI qu'on choisit les champs à imprimer, le tableau, ses colonnes et
 * ses sous-totaux — les trois choses qui manquaient et qui obligeaient à écrire du YAML.
 *
 * Le panneau ne décide rien : il appelle le modèle pur (`lib/printedit`) et n'offre que ce que le serveur
 * accepterait (un secret, un fichier et une table ne sont pas proposés ; un sous-total exige un regroupement).
 */
const props = defineProps<{ block: Block | null; meta: DocTypeMeta; editable: boolean }>()
const emit = defineEmits<{
  update: [patch: Partial<Block>]
  toggleField: [field: string]
  moveField: [field: string, how: 'up' | 'down']
  addColumn: [field: string]
  removeColumn: [key: string]
  moveColumn: [key: string, how: 'left' | 'right']
  updateColumn: [key: string, patch: Partial<Column>]
  setGroupBy: [field: string | null]
  toggleSubtotal: [field: string]
}>()

const kind = computed(() => (props.block ? KIND_LABELS[props.block.type] : ''))
/** Les champs qu'un bloc `fields` peut imprimer ; un bloc `totals` n'offre que des nombres. */
const available = computed(() => (props.block?.type === 'totals' ? totalFields(props.meta) : printableFields(props.meta)))
const chosen = computed(() => props.block?.fields ?? [])
const tables = computed(() => props.meta.fields.filter((f) => f.fieldtype === 'Table'))
const columns = computed(() => columnFields(props.meta, props.block?.field))
const groupable = computed(() => groupableColumns(props.meta, props.block?.field))
const used = computed(() => new Set(props.block ? tableColumns(props.block).map((c) => c.field) : []))
const labelOf = (name: string): string => props.meta.fields.find((f) => f.fieldname === name)?.label || name
const columnLabelOf = (name: string): string => columns.value.find((f) => f.fieldname === name)?.label || name
// Les listes de choix vivent ICI et non dans le gabarit : un `.map()` en ligne dans un template brouille
// l'analyse de portée du compilateur Vue (il a pris la clé `value` pour une propriété de `t`).
const alignOptions = [
  { value: 'left', label: t.printBuilder.align.left },
  { value: 'right', label: t.printBuilder.align.right },
  { value: 'center', label: t.printBuilder.align.center },
]
const styleOptions = (['bordered', 'ruled', 'plain'] as const).map((v) => ({ value: v, label: t.printBuilder.styles[v] }))
const densityOptions = (['compact', 'normal'] as const).map((v) => ({ value: v, label: t.printBuilder.densities[v] }))
const tableOptions = computed(() => tables.value.map((f) => ({ value: f.fieldname, label: f.label || f.fieldname })))
const groupOptions = computed(() => [
  { value: null as string | null, label: t.printBuilder.noGroup },
  ...groupable.value.map((f) => ({ value: f.fieldname as string | null, label: f.label || f.fieldname })),
])
</script>

<template>
  <aside class="pb-panel" :aria-label="t.printBuilder.properties">
    <h2 class="pb-panel-title" data-testid="pb-panel-title">{{ block ? kind : t.printBuilder.properties }}</h2>
    <p v-if="!block" class="desk-muted">{{ t.printBuilder.nothing }}</p>

    <template v-else>
      <!-- Titre, texte -->
      <label v-if="block.type === 'title' || block.type === 'text'" class="pb-field">
        <span>{{ t.printBuilder.text }}</span>
        <InputText
          :model-value="block.text ?? ''"
          :disabled="!editable"
          maxlength="2000"
          fluid
          data-testid="pb-text"
          @update:model-value="emit('update', { text: $event ?? '' })"
        />
        <small class="desk-muted">{{ t.printBuilder.textHint }}</small>
      </label>

      <label v-if="block.type === 'spacer'" class="pb-field">
        <span>{{ t.printBuilder.size }}</span>
        <InputNumber :model-value="block.size ?? 5" :min="1" :max="100" :disabled="!editable" data-testid="pb-size" @update:model-value="emit('update', { size: $event ?? 5 })" />
      </label>

      <!-- Les champs imprimés : le choix qui manquait -->
      <template v-if="block.type === 'fields' || block.type === 'totals'">
        <label v-if="block.type === 'fields'" class="pb-field">
          <span>{{ t.printBuilder.columnsCount }}</span>
          <InputNumber :model-value="fieldColumns(block)" :min="1" :max="4" :disabled="!editable" data-testid="pb-cols" @update:model-value="emit('update', { columns: $event ?? 2 })" />
        </label>
        <fieldset class="pb-field">
          <legend>{{ t.printBuilder.fields }}</legend>
          <ol class="pb-chosen" data-testid="pb-chosen">
            <li v-for="(name, i) in chosen" :key="name" :data-testid="`pb-chosen-${name}`">
              <span>{{ labelOf(name) }}</span>
              <span class="pb-row-actions">
                <Button icon="pi pi-arrow-up" text size="small" :disabled="!editable || i === 0" :aria-label="t.printBuilder.moveUp" @click="emit('moveField', name, 'up')" />
                <Button icon="pi pi-arrow-down" text size="small" :disabled="!editable || i === chosen.length - 1" :aria-label="t.printBuilder.moveDown" @click="emit('moveField', name, 'down')" />
                <Button icon="pi pi-times" text severity="danger" size="small" :disabled="!editable" :aria-label="t.delete" :data-testid="`pb-drop-${name}`" @click="emit('toggleField', name)" />
              </span>
            </li>
          </ol>
          <div class="pb-add">
            <Button
              v-for="f in available.filter((x) => !chosen.includes(x.fieldname))"
              :key="f.fieldname"
              :label="f.label || f.fieldname"
              icon="pi pi-plus"
              text
              size="small"
              :disabled="!editable"
              :data-testid="`pb-add-${f.fieldname}`"
              @click="emit('toggleField', f.fieldname)"
            />
          </div>
        </fieldset>
      </template>

      <!-- Le tableau : le choix qui manquait aussi -->
      <template v-if="block.type === 'table'">
        <label class="pb-field">
          <span>{{ t.printBuilder.table }}</span>
          <Select
            :model-value="block.field"
            :options="tableOptions"
            option-label="label"
            option-value="value"
            :disabled="!editable"
            size="small"
            data-testid="pb-table"
            @update:model-value="emit('update', { field: $event, columns: [], group_by: null, subtotal: [] })"
          />
        </label>
        <fieldset class="pb-field">
          <legend>{{ t.printBuilder.tableColumns }}</legend>
          <ol class="pb-chosen" data-testid="pb-columns">
            <li v-for="(c, i) in tableColumns(block)" :key="c.key" :data-testid="`pb-col-${c.field}`">
              <span>{{ c.label || columnLabelOf(c.field) }}</span>
              <span class="pb-row-actions">
                <Button icon="pi pi-arrow-left" text size="small" :disabled="!editable || i === 0" :aria-label="t.printBuilder.moveUp" @click="emit('moveColumn', c.key, 'left')" />
                <Button icon="pi pi-arrow-right" text size="small" :disabled="!editable || i === tableColumns(block).length - 1" :aria-label="t.printBuilder.moveDown" @click="emit('moveColumn', c.key, 'right')" />
                <Button icon="pi pi-times" text severity="danger" size="small" :disabled="!editable" :aria-label="t.delete" :data-testid="`pb-col-drop-${c.field}`" @click="emit('removeColumn', c.key)" />
              </span>
              <div class="pb-col-props">
                <InputText :model-value="c.label ?? ''" :placeholder="t.printBuilder.columnLabel" :disabled="!editable" size="small" @update:model-value="emit('updateColumn', c.key, { label: $event || null })" />
                <Select :model-value="c.align ?? 'left'" :options="alignOptions" option-label="label" option-value="value" :disabled="!editable" size="small" @update:model-value="emit('updateColumn', c.key, { align: $event })" />
                <InputNumber :model-value="c.width ?? null" :min="1" :max="100" :placeholder="t.printBuilder.columnWidth" :disabled="!editable" size="small" @update:model-value="emit('updateColumn', c.key, { width: $event ?? null })" />
              </div>
            </li>
          </ol>
          <div class="pb-add">
            <Button
              v-for="f in columns.filter((x) => !used.has(x.fieldname))"
              :key="f.fieldname"
              :label="f.label || f.fieldname"
              icon="pi pi-plus"
              text
              size="small"
              :disabled="!editable"
              :data-testid="`pb-addcol-${f.fieldname}`"
              @click="emit('addColumn', f.fieldname)"
            />
          </div>
        </fieldset>
        <label class="pb-field">
          <span>{{ t.printBuilder.style }}</span>
          <Select :model-value="block.style ?? 'bordered'" :options="styleOptions" option-label="label" option-value="value" :disabled="!editable" size="small" data-testid="pb-style" @update:model-value="emit('update', { style: $event })" />
        </label>
        <label class="pb-field">
          <span>{{ t.printBuilder.density }}</span>
          <Select :model-value="block.density ?? 'normal'" :options="densityOptions" option-label="label" option-value="value" :disabled="!editable" size="small" @update:model-value="emit('update', { density: $event })" />
        </label>
        <label class="pb-check">
          <Checkbox :model-value="block.repeat_header !== false" binary :disabled="!editable" @update:model-value="emit('update', { repeat_header: !!$event })" />
          <span>{{ t.printBuilder.repeatHeader }}</span>
        </label>
        <label class="pb-field">
          <span>{{ t.printBuilder.groupBy }}</span>
          <Select
            :model-value="block.group_by ?? null"
            :options="groupOptions"
            option-label="label"
            option-value="value"
            :disabled="!editable"
            size="small"
            data-testid="pb-groupby"
            @update:model-value="emit('setGroupBy', $event)"
          />
        </label>
        <fieldset class="pb-field">
          <legend>{{ t.printBuilder.subtotal }}</legend>
          <p v-if="!block.group_by" class="desk-muted" data-testid="pb-subtotal-hint">{{ t.printBuilder.subtotalHint }}</p>
          <div v-else class="pb-add">
            <Button
              v-for="c in tableColumns(block)"
              :key="c.key"
              :label="c.label || columnLabelOf(c.field)"
              :icon="(block.subtotal ?? []).includes(c.field) ? 'pi pi-check-square' : 'pi pi-stop'"
              text
              size="small"
              :disabled="!editable"
              :data-testid="`pb-sub-${c.field}`"
              @click="emit('toggleSubtotal', c.field)"
            />
          </div>
        </fieldset>
      </template>

      <label v-if="block.type !== 'spacer' && block.type !== 'page_break' && block.type !== 'title' && block.type !== 'text'" class="pb-field">
        <span>{{ t.printBuilder.blockLabel }}</span>
        <InputText :model-value="block.label ?? ''" :disabled="!editable" maxlength="140" fluid data-testid="pb-label" @update:model-value="emit('update', { label: $event || null })" />
      </label>

      <Message v-if="block.type === 'page_break'" severity="secondary" size="small">{{ KIND_LABELS.page_break }}</Message>
    </template>
  </aside>
</template>

<style scoped>
.pb-panel {
  display: grid;
  gap: 0.875rem;
  align-content: start;
}

.pb-panel-title {
  font-size: 0.95rem;
  margin: 0;
}

.pb-field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
  border: 0;
  padding: 0;
  margin: 0;
}

.pb-field > span,
.pb-field > legend {
  color: var(--p-text-muted-color);
  padding: 0;
}

.pb-check {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.85rem;
}

.pb-chosen {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}

.pb-chosen li {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.4rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-sm, 4px);
}

.pb-row-actions {
  display: flex;
}

.pb-col-props {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 0.25rem;
}

.pb-add {
  display: flex;
  flex-wrap: wrap;
  gap: 0.15rem;
}
</style>
