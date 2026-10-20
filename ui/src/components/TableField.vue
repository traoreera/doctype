<script setup lang="ts">
import Button from 'primevue/button'
import { computed, nextTick, ref, watch } from 'vue'
import FieldInput from '@/components/FieldInput.vue'
import LinkField from '@/components/LinkField.vue'
import { t } from '@/i18n'
import { cellKey, cellStates, newRow, type Row, type Values } from '@/lib/form'
import { formatCell, isNumeric } from '@/lib/format'
import { unitLinks, unitOf } from '@/lib/units'
import type { ChildMeta, FieldMeta } from '@/lib/types'
import { useLinks } from '@/stores/links'

const props = defineProps<{
  field: FieldMeta
  child: ChildMeta
  modelValue: Row[]
  required: boolean
  readOnly: boolean
  /** Toutes les erreurs du formulaire : on n'y lit que les cellules de ce champ (`items.0.qty`) et l'erreur du champ lui-même. */
  errors: Record<string, string>
  formId: string
  /** Les valeurs du document qui porte la table : les conditions d'une cellule les lisent (`parent_purpose`). */
  parentValues: Values
}>()
const emit = defineEmits<{
  /** Ligne ajoutée ou supprimée : les numéros des lignes changent, les erreurs de cellules n'ont plus de sens. */
  'update:modelValue': [rows: Row[]]
  /** Une cellule modifiée : seule SON erreur doit disparaître. */
  cell: [row: number, field: string, value: unknown]
}>()

const label = computed(() => props.field.label ?? props.field.fieldname)
const rows = computed(() => (Array.isArray(props.modelValue) ? props.modelValue : []))
const links = useLinks()

/** L'état de chaque cellule, ligne par ligne : une condition peut dépendre de la ligne ET du document (`parent_purpose`). */
const states = computed(() => rows.value.map((r) => cellStates(props.child, r, props.parentValues)))
const cellState = (i: number, c: FieldMeta) => states.value[i]?.[c.fieldname]
/** DynamicLink : le DocType que le champ de type de CETTE ligne désigne. */
const dynamicOf = (c: FieldMeta, row: Row): string | null => (c.fieldtype === 'DynamicLink' && typeof c.options === 'string' && typeof row[c.options] === 'string' && row[c.options] ? (row[c.options] as string) : null)

/**
 * Une colonne que PERSONNE ne peut remplir (l'entrepôt source d'une réception) disparaît : elle n'a pas de sens pour cette nature. Sans ligne,
 * on juge d'après le seul document (une ligne vide), pour que les bonnes colonnes soient déjà là quand on ajoute la première.
 */
const columns = computed(() =>
  props.child.fields.filter((c) => {
    if (c.hidden) return false
    if (!c.depends_on) return true
    if (!rows.value.length) return cellStates(props.child, {}, props.parentValues)[c.fieldname]!.visible
    return states.value.some((s) => s[c.fieldname]!.visible)
  }),
)
/** L'étoile de l'en-tête : obligatoire d'office, ou rendue obligatoire par la nature du document pour au moins une ligne. */
const columnRequired = (c: FieldMeta) =>
  c.required || (!!c.mandatory_depends_on && (rows.value.length ? states.value.some((s) => s[c.fieldname]!.required) : cellStates(props.child, {}, props.parentValues)[c.fieldname]!.required))

// Les unités (« kg ») viennent de l'article de chaque ligne : on lit ces articles d'un coup, pas un par un.
watch(
  rows,
  (list) => {
    for (const [target, names] of unitLinks(props.child.fields, list)) void links.resolve(target, names, { data: true })
  },
  { immediate: true },
)
const unit = (c: FieldMeta, row: Row) => unitOf(c, props.child.fields, row, links.field)

// Saisie au clavier : Tab dans la dernière cellule de la dernière ligne ajoute une ligne et s'y place (pas besoin de la souris).
const root = ref<HTMLElement | null>(null)
async function onTab(e: KeyboardEvent): Promise<void> {
  if (e.key !== 'Tab' || e.shiftKey || props.readOnly || !root.value) return
  const cells = root.value.querySelectorAll<HTMLElement>('tbody tr:last-child td:not(.act) :is(input, [role="combobox"])')
  if (!cells.length || e.target !== cells[cells.length - 1]) return
  e.preventDefault()
  add()
  await nextTick()
  root.value.querySelector<HTMLElement>('tbody tr:last-child td:not(.idx) :is(input, [role="combobox"])')?.focus()
}

// Une clé stable par ligne : sans elle, supprimer la ligne 1 ferait « glisser » la saisie en cours de la ligne 2 dans la 1.
let seq = 0
const keys = ref<number[]>(rows.value.map(() => ++seq))
watch(
  () => rows.value.length,
  (n) => {
    if (keys.value.length !== n) keys.value = rows.value.map(() => ++seq) // la liste a été remplacée de l'extérieur (rechargement)
  },
)

const cellError = (i: number, c: FieldMeta) => props.errors[cellKey(props.field.fieldname, i, c.fieldname)]
const tableError = computed(() => props.errors[props.field.fieldname])
const rowsWithErrors = computed(() => {
  const prefix = `${props.field.fieldname}.`
  return new Set(Object.keys(props.errors).filter((k) => k.startsWith(prefix)).map((k) => Number(k.split('.')[1])))
})

// ── Sélection multiple (`multiselect`) : les lignes sont des étiquettes, une par valeur du Link unique de leur DocType ──────
const tagField = computed(() => props.child.fields[0])
const tagTarget = computed(() => (typeof tagField.value?.options === 'string' ? tagField.value.options : ''))
const tags = computed(() => rows.value.map((r) => r[tagField.value?.fieldname ?? ''] as string | null).filter((v): v is string => typeof v === 'string' && !!v))
const pending = ref<string | null>(null)
watch(
  tags,
  (list) => {
    if (tagTarget.value && list.length) void links.resolve(tagTarget.value, list)
  },
  { immediate: true },
)
function addTag(value: string | null): void {
  pending.value = null
  const name = tagField.value?.fieldname
  if (!value || !name || tags.value.includes(value)) return
  emit('update:modelValue', [...rows.value, { ...newRow(props.child), [name]: value }])
}
function removeTag(value: string): void {
  const name = tagField.value?.fieldname ?? ''
  emit('update:modelValue', rows.value.filter((r) => r[name] !== value))
}

function add(): void {
  keys.value = [...keys.value, ++seq]
  emit('update:modelValue', [...rows.value, newRow(props.child)])
}
function remove(i: number): void {
  keys.value = keys.value.filter((_, j) => j !== i)
  emit('update:modelValue', rows.value.filter((_, j) => j !== i))
}
</script>

<template>
  <div v-if="field.multiselect" class="form-tags" :data-field="field.fieldname" data-wide data-testid="multiselect">
    <span class="form-table-title">{{ label }}<span v-if="required && !readOnly" class="req" aria-hidden="true">*</span></span>
    <ul class="tag-list" :aria-label="label">
      <li v-for="v in tags" :key="v" class="tag" :data-testid="`tag-${v}`">
        <RouterLink class="tag-name" :to="{ name: 'form', params: { doctype: tagTarget, name: v } }">{{ links.title(tagTarget, v) }}</RouterLink>
        <button v-if="!readOnly" type="button" class="tag-remove" :aria-label="`${t.table.removeRow} ${links.title(tagTarget, v)}`" :data-testid="`tag-remove-${v}`" @click="removeTag(v)">✕</button>
      </li>
      <li v-if="!tags.length" class="desk-muted" data-testid="tags-empty">{{ t.table.empty }}</li>
    </ul>
    <LinkField v-if="!readOnly" :model-value="pending" :target="tagTarget" :input-id="`${formId}-${field.fieldname}`" data-testid="tag-add" @update:model-value="addTag" />
    <span v-if="tableError" class="error" role="alert" :data-testid="`error-${field.fieldname}`">{{ tableError }}</span>
    <span v-else-if="field.description" class="hint">{{ field.description }}</span>
  </div>
  <div v-else ref="root" class="form-table" :data-field="field.fieldname" data-wide @keydown="onTab">
    <div class="form-table-head">
      <span class="form-table-title">{{ label }}<span v-if="required && !readOnly" class="req" aria-hidden="true">*</span></span>
      <span v-if="field.description" class="hint">{{ field.description }}</span>
    </div>

    <div class="table-wrap">
      <table class="grid" :aria-label="label">
        <thead>
          <tr>
            <th class="idx">{{ t.table.rowNumber }}</th>
            <th v-for="c in columns" :key="c.fieldname">
              {{ c.label ?? c.fieldname }}<span v-if="columnRequired(c) && !readOnly" class="req" aria-hidden="true">*</span>
            </th>
            <th v-if="!readOnly" class="act" />
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + (readOnly ? 1 : 2)" class="desk-muted empty" data-testid="table-empty">{{ t.table.empty }}</td>
          </tr>
          <tr v-for="(row, i) in rows" :key="keys[i] ?? i" :data-row="i" :class="{ bad: rowsWithErrors.has(i) }">
            <td class="idx">{{ i + 1 }}</td>
            <td v-for="c in columns" :key="c.fieldname" :data-col="c.fieldname" :data-label="c.label ?? c.fieldname" :class="{ num: isNumeric(c) }">
              <!-- Une colonne qui existe pour d'autres lignes mais pas pour celle-ci : une cellule vide, pas un champ à remplir. -->
              <span v-if="!cellState(i, c)?.visible" class="desk-muted" :data-testid="`na-${i}-${c.fieldname}`">—</span>
              <template v-else-if="readOnly">
                <!-- Un document figé montre le TITRE de l'article, pas son identifiant. -->
                <LinkField v-if="c.fieldtype === 'Link' && typeof c.options === 'string' && row[c.fieldname]" :model-value="row[c.fieldname]" :target="c.options" readonly />
                <LinkField v-else-if="c.fieldtype === 'DynamicLink' && dynamicOf(c, row) && row[c.fieldname]" :model-value="row[c.fieldname]" :target="dynamicOf(c, row)!" readonly />
                <template v-else>{{ formatCell(c, row[c.fieldname]) || t.form.noValue }}</template>
                <span v-if="unit(c, row) && row[c.fieldname]" class="unit">{{ unit(c, row) }}</span>
              </template>
              <!-- Une cellule remplie par le serveur (`fetch_from`) se lit, elle ne se saisit pas. -->
              <span v-else-if="c.fetch_from" class="value" :data-testid="`fetched-${i}-${c.fieldname}`">{{ formatCell(c, row[c.fieldname]) || t.form.noValue }}</span>
              <template v-else>
                <div class="cell-input">
                  <FieldInput
                    :field="c"
                    :model-value="row[c.fieldname]"
                    :input-id="`${formId}-${field.fieldname}-${i}-${c.fieldname}`"
                    :invalid="!!cellError(i, c)"
                    :dynamic-target="dynamicOf(c, row)"
                    @update:model-value="emit('cell', i, c.fieldname, $event)"
                  />
                  <span v-if="unit(c, row)" class="unit" :data-testid="`unit-${i}-${c.fieldname}`">{{ unit(c, row) }}</span>
                </div>
                <span v-if="cellError(i, c)" class="error" role="alert" :data-testid="`error-${cellKey(field.fieldname, i, c.fieldname)}`">{{ cellError(i, c) }}</span>
              </template>
            </td>
            <td v-if="!readOnly" class="act">
              <Button icon="pi pi-trash" text severity="secondary" size="small" :aria-label="`${t.table.removeRow} ${i + 1}`" :data-testid="`remove-row-${i}`" @click="remove(i)" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <span v-if="tableError" class="error" role="alert" :data-testid="`error-${field.fieldname}`">{{ tableError }}</span>
    <div v-if="!readOnly" class="form-table-foot">
      <Button :label="t.table.addRow" icon="pi pi-plus" size="small" severity="secondary" text :data-testid="`add-row-${field.fieldname}`" @click="add" />
    </div>
  </div>
</template>
