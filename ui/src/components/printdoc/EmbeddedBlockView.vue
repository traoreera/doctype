<script setup lang="ts">
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import { computed } from 'vue'
import { t } from '@/i18n'
import { formatCell } from '@/lib/format'
import { KIND_LABELS } from '@/lib/printedit'
import type { FieldMeta } from '@/lib/types'
import { docContext } from './extensions'

/**
 * Un bloc incorporé dans le document : le tableau des LIGNES (colonnes, regroupement, sous-totaux), les TOTAUX,
 * ou une GRILLE de champs. Dessiné avec les valeurs du document d'exemple et les classes du rendu du serveur
 * (`table.lines`, `table.totaux`, `table.fields`), pour que la page ressemble à ce qui sortira. On le règle dans
 * le panneau de droite après l'avoir cliqué ; on le déplace en le glissant par sa poignée.
 */
interface Column { field: string; label?: string | null; align?: string | null; width?: number | null }
interface Block { type: 'table' | 'totals' | 'fields'; field?: string; fields?: string[]; columns?: Column[] | number; label?: string | null; style?: string; density?: string; group_by?: string | null; subtotal?: string[] }

const props = defineProps(nodeViewProps)
const ctx = computed(() => docContext(props.editor))
const block = computed(() => props.node.attrs.block as Block)
const meta = computed(() => ctx.value.meta)

function show(f: FieldMeta | undefined, value: unknown): string {
  if (!f || value === null || value === undefined || value === '') return ''
  if (f.fieldtype === 'Link' && typeof f.options === 'string' && typeof value === 'string') return ctx.value.linkTitle(f.options, value)
  if (f.fieldtype === 'Boolean') return value ? 'Oui' : 'Non'
  return formatCell(f, value)
}
const fieldOf = (name: string) => meta.value?.fields.find((f) => f.fieldname === name)
const labelOf = (name: string) => fieldOf(name)?.label || name
const valueOf = (name: string) => show(fieldOf(name), ctx.value.example?.[name])

const child = computed<FieldMeta[]>(() => {
  const table = meta.value?.fields.find((f) => f.fieldname === block.value.field)
  return (table && typeof table.options === 'string' ? meta.value?.children?.[table.options]?.fields : undefined) ?? []
})
const columns = computed<Column[]>(() => (Array.isArray(block.value.columns) ? block.value.columns : []))
const rows = computed<Record<string, unknown>[]>(() => {
  const all = ctx.value.example?.[block.value.field ?? '']
  return Array.isArray(all) && all.length ? (all.slice(0, 8) as Record<string, unknown>[]) : [{}, {}]
})
const cell = (c: Column, row: Record<string, unknown>) => show(child.value.find((f) => f.fieldname === c.field), row[c.field])
const heading = (c: Column) => c.label || child.value.find((f) => f.fieldname === c.field)?.label || c.field
const grid = computed(() => {
  const n = typeof block.value.columns === 'number' ? block.value.columns : 2
  const names = block.value.fields ?? []
  const out: string[][] = []
  for (let i = 0; i < names.length; i += n) out.push(names.slice(i, i + n))
  return { n, rows: out }
})
const fields = computed(() => block.value.fields ?? [])
</script>

<template>
  <NodeViewWrapper :class="['pd-block', { selected }]" contenteditable="false" :data-kind="block.type" data-testid="pd-block">
    <span class="pd-block-grip" data-drag-handle :title="t.printDoc.dragBlock"><i class="pi pi-bars" aria-hidden="true" /> {{ KIND_LABELS[block.type] }}</span>
    <h2 v-if="block.label">{{ block.label }}</h2>

    <template v-if="block.type === 'table'">
      <p v-if="!columns.length" class="pd-hint">{{ t.printDoc.noColumns }}</p>
      <table v-else :class="['lines', block.style ?? 'bordered', block.density ?? 'normal']">
        <thead>
          <tr>
            <th v-for="c in columns" :key="c.field" :class="c.align ?? 'left'" :style="c.width ? { width: `${c.width}%` } : undefined">{{ heading(c) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in rows" :key="r">
            <td v-for="c in columns" :key="c.field" :class="c.align ?? 'left'">{{ cell(c, row) || ' ' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <table v-else-if="block.type === 'totals'" class="totaux">
      <tbody>
        <tr v-for="(f, i) in fields" :key="f" :class="{ grand: i === fields.length - 1 }">
          <td>{{ labelOf(f) }}</td>
          <td class="r">{{ valueOf(f) || '—' }}</td>
        </tr>
      </tbody>
    </table>

    <table v-else class="fields">
      <tbody>
        <tr v-for="(r, i) in grid.rows" :key="i">
          <td v-for="f in r" :key="f"><span class="k">{{ labelOf(f) }}</span><br />{{ valueOf(f) || '—' }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="block.type !== 'table' && !fields.length" class="pd-hint">{{ t.printDoc.noFields }}</p>
  </NodeViewWrapper>
</template>
