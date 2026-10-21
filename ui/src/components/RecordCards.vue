<script setup lang="ts">
import Checkbox from 'primevue/checkbox'
import { computed } from 'vue'
import CellValue from '@/components/CellValue.vue'
import FileThumb from '@/components/FileThumb.vue'
import { t } from '@/i18n'
import { statusLabel, statusTone } from '@/lib/docstatus'
import { relativeTime } from '@/lib/format'
import type { DocRecord, DocTypeMeta, FieldMeta } from '@/lib/types'

/**
 * La liste sur un téléphone : chaque document est une CARTE (son titre, quelques champs avec leur libellé, son état), plutôt qu'un tableau qu'il faut faire défiler
 * de côté. La carte entière ouvre le document ; la case à cocher (si une action en lot est possible) ne l'ouvre pas.
 */
const props = defineProps<{
  doctype: string
  meta: DocTypeMeta
  rows: readonly DocRecord[]
  columns: readonly FieldMeta[]
  selection: readonly DocRecord[]
  selectable: boolean
  /** Le texte d'une cellule (unité comprise), calculé par la liste. */
  cell: (field: FieldMeta, row: DocRecord) => string
  title: (row: DocRecord) => string
}>()
const emit = defineEmits<{ 'update:selection': [value: DocRecord[]]; open: [row: DocRecord] }>()

const MAX_FIELDS = 3
/** Les champs montrés sous le titre : les premières colonnes après la première (qui sert de titre), jamais plus de trois. */
const details = computed(() => props.columns.slice(1, 1 + MAX_FIELDS))
const picked = (row: DocRecord) => props.selection.some((r) => r.name === row.name)
function toggle(row: DocRecord, on: boolean): void {
  emit('update:selection', on ? [...props.selection, row] : props.selection.filter((r) => r.name !== row.name))
}
function onCard(e: Event, row: DocRecord): void {
  if ((e.target as HTMLElement).closest('a, button, input, label, .p-checkbox')) return
  emit('open', row)
}
</script>

<template>
  <ul class="record-cards" data-testid="record-cards">
    <li
      v-for="r in rows"
      :key="r.name"
      :class="['record-card', { picked: picked(r) }]"
      :data-testid="`card-${r.name}`"
      @click="onCard($event, r)"
    >
      <div class="card-head">
        <Checkbox v-if="selectable" :model-value="picked(r)" binary :aria-label="t.list.selectRow(title(r))" :data-testid="`pick-${r.name}`" @update:model-value="toggle(r, $event as boolean)" />
        <FileThumb v-if="meta.image_field && r.data[meta.image_field]" :id="r.data[meta.image_field] as string" :size="32" round />
        <RouterLink class="card-title" :to="{ name: 'form', params: { doctype, name: r.name } }" :data-testid="`open-${r.name}`">{{ columns[0] ? cell(columns[0], r) || title(r) : title(r) }}</RouterLink>
        <span v-if="meta.submittable" :class="['status-pill', statusTone(r.docstatus)]">{{ statusLabel(r.docstatus) }}</span>
      </div>
      <dl v-if="details.length" class="card-fields">
        <template v-for="f in details" :key="f.fieldname">
          <template v-if="r.data[f.fieldname] !== undefined && r.data[f.fieldname] !== null && r.data[f.fieldname] !== ''">
            <dt>{{ f.label ?? f.fieldname }}</dt>
            <dd><CellValue :field="f" :value="r.data[f.fieldname]" :text="cell(f, r)" /></dd>
          </template>
        </template>
      </dl>
      <small v-if="!meta.virtual" class="desk-muted card-time">{{ relativeTime(r.modified) }}</small>
    </li>
  </ul>
</template>

<style scoped>
.record-cards {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.5rem;
}

.record-card {
  padding: 0.7rem 0.8rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--desk-radius);
  background: var(--p-content-background);
  cursor: pointer;
}

.record-card.picked {
  border-color: var(--p-text-muted-color);
  background: var(--p-highlight-background);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.card-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  overflow-wrap: anywhere;
  text-decoration: none;
}

.card-fields {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.2rem 0.9rem;
  margin: 0.5rem 0 0;
  font-size: 0.9rem;
}

.card-fields dt {
  color: var(--p-text-muted-color);
}

.card-fields dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.card-time {
  display: block;
  margin-top: 0.4rem;
}
</style>
