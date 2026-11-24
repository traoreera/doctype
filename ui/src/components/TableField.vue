<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { computed, nextTick, ref, watch } from 'vue'
import TableCell from '@/components/TableCell.vue'
import LinkField from '@/components/LinkField.vue'
import { t } from '@/i18n'
import { allowedDocstatus } from '@/lib/docstatus'
import { cellKey, cellStates, isEmpty, newRow, resolveLinkFilters, type Row, type Values } from '@/lib/form'
import { isNumeric } from '@/lib/format'
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
  /** Les champs de ce document : une ligne qui lit sa devise sur le parent (`parent_company.default_currency`) suit SES liens. */
  parentFields?: readonly FieldMeta[]
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
const visibleColumns = computed(() =>
  props.child.fields.filter((c) => {
    if (c.hidden) return false
    if (!c.depends_on) return true
    if (!rows.value.length) return cellStates(props.child, {}, props.parentValues)[c.fieldname]!.visible
    return states.value.some((s) => s[c.fieldname]!.visible)
  }),
)
/**
 * La GRILLE ne montre que les colonnes que la DÉFINITION désigne (`in_list_view`), sinon les premières — la règle du moteur
 * pour la liste d'un type (`schema.list_columns`). Mesuré sur le jeu ERP : une ligne de facture a 70 champs visibles et en
 * désigne 5 ; les rendre tous donnait une table illisible et inéditable, et 287 des 293 tables enfants déclarent déjà leurs
 * colonnes. Le RESTE n'est pas perdu : chaque ligne s'ouvre en fiche, où tous ses champs se lisent et se saisissent.
 */
const GRID_COLUMNS = 4
const columns = computed(() => {
  const flagged = visibleColumns.value.filter((c) => c.in_list_view)
  return flagged.length ? flagged : visibleColumns.value.slice(0, GRID_COLUMNS)
})
/** Combien de champs la grille ne montre pas : ce que le bouton de la ligne promet d'ouvrir. */
const foldedCount = computed(() => visibleColumns.value.length - columns.value.length)

// ── La fiche d'une LIGNE : tous ses champs, quand la grille n'en montre qu'une poignée ──────
const editing = ref<number | null>(null)
const editingRow = computed(() => (editing.value === null ? null : (rows.value[editing.value] ?? null)))
/**
 * En LECTURE, un champ vide n'apprend rien : la fiche ne montre que ce qui est renseigné. Sur une ligne de bon de
 * livraison, cela passe de 64 entrées à une vingtaine. On compte ce qu'on cache et on offre de le montrer : cacher en
 * silence ferait chercher un champ qu'on croirait disparu. En SAISIE on ne cache rien — un champ vide est justement
 * celui qu'on vient remplir.
 */
const showEmpty = ref(false)
const sheetColumns = computed(() => {
  const row = editingRow.value
  if (!props.readOnly || showEmpty.value || !row) return visibleColumns.value
  const i = editing.value ?? 0
  // Une cellule que la nature du document écarte (`depends_on`) n'affiche qu'un tiret : elle est vide elle aussi.
  return visibleColumns.value.filter((c) => !isEmpty(row[c.fieldname]) && cellState(i, c)?.visible)
})
const emptyCount = computed(() => visibleColumns.value.length - sheetColumns.value.length)
function openRow(i: number): void {
  showEmpty.value = false // chaque ouverture repart de la fiche courte
  editing.value = i
}
/** Supprimer la ligne ouverte depuis sa fiche : on ferme d'abord, sinon la fiche montrerait la ligne suivante. */
function removeOpenRow(): void {
  const i = editing.value
  editing.value = null
  if (i !== null) remove(i)
}
/** L'étoile de l'en-tête : obligatoire d'office, ou rendue obligatoire par la nature du document pour au moins une ligne. */
const columnRequired = (c: FieldMeta) =>
  c.required || (!!c.mandatory_depends_on && (rows.value.length ? states.value.some((s) => s[c.fieldname]!.required) : cellStates(props.child, {}, props.parentValues)[c.fieldname]!.required))

// Les unités (« kg ») viennent de l'article de chaque ligne : on lit ces articles d'un coup, pas un par un.
// La devise d'un montant peut venir du parent (`parent_company.default_currency`) : on lit aussi ce lien-là.
const parentSide = computed(() => (props.parentFields ? { fields: props.parentFields, values: props.parentValues } : undefined))
watch(
  [rows, parentSide],
  ([list, side]) => {
    for (const [target, names] of unitLinks(props.child.fields, list, side)) void links.resolve(target, names, { data: true })
  },
  { immediate: true },
)
const unit = (c: FieldMeta, row: Row) => unitOf(c, props.child.fields, row, links.field, parentSide.value)

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
    <LinkField v-if="!readOnly" :model-value="pending" :target="tagTarget" :input-id="`${formId}-${field.fieldname}`" :statuses="tagField ? allowedDocstatus(tagField) : null" data-testid="tag-add" @update:model-value="addTag" />
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
            <th class="act" />
          </tr>
        </thead>
        <tbody>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 2" class="desk-muted empty" data-testid="table-empty">{{ t.table.empty }}</td>
          </tr>
          <tr v-for="(row, i) in rows" :key="keys[i] ?? i" :data-row="i" :class="{ bad: rowsWithErrors.has(i) }">
            <td class="idx">{{ i + 1 }}</td>
            <td v-for="c in columns" :key="c.fieldname" :data-col="c.fieldname" :data-label="c.label ?? c.fieldname" :class="{ num: isNumeric(c) }">
              <TableCell
                :column="c"
                :row="row"
                :index="i"
                :visible="!!cellState(i, c)?.visible"
                :read-only="readOnly"
                :input-id="`${formId}-${field.fieldname}-${i}-${c.fieldname}`"
                :error="cellError(i, c)"
                :error-testid="`error-${cellKey(field.fieldname, i, c.fieldname)}`"
                :unit="unit(c, row)"
                :dynamic-target="dynamicOf(c, row)"
                :link-filters="resolveLinkFilters(c, row)"
                @update="emit('cell', i, c.fieldname, $event)"
              />
            </td>
            <td class="act">
              <!-- Ouvrir la LIGNE : tous ses champs, ceux que la grille ne montre pas compris. -->
              <Button
                icon="pi pi-pen-to-square"
                text
                severity="secondary"
                size="small"
                :aria-label="t.table.openRow(i + 1)"
                :title="foldedCount ? t.table.foldedFields(foldedCount) : t.table.openRow(i + 1)"
                :data-testid="`open-row-${i}`"
                @click="openRow(i)"
              />
              <Button v-if="!readOnly" icon="pi pi-trash" text severity="secondary" size="small" :aria-label="`${t.table.removeRow} ${i + 1}`" :data-testid="`remove-row-${i}`" @click="remove(i)" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Dialog
      :visible="editing !== null"
      modal
      :header="t.table.rowOf(label, (editing ?? 0) + 1)"
      :style="{ width: '42rem', maxWidth: '95vw' }"
      data-testid="row-sheet"
      @update:visible="editing = null"
    >
      <dl v-if="editingRow" class="row-sheet">
        <!-- TOUS les champs de la ligne, dans l'ordre de la définition : la grille n'en montrait qu'une poignée.
             DEUX COLONNES, libellé à gauche : empilés, 64 champs faisaient 128 lignes à faire défiler, la moitié
             droite vide. Une paire nom/valeur est une liste de description, ce qu'un lecteur d'écran annonce comme
             telle ; en saisie, le nom reste un vrai `label for`, qui donne le champ au clic. -->
        <div v-for="c in sheetColumns" :key="c.fieldname" class="row-sheet-field" :data-field="c.fieldname">
          <dt>
            <label v-if="!readOnly" :for="`${formId}-sheet-${field.fieldname}-${editing}-${c.fieldname}`">
              {{ c.label ?? c.fieldname }}<span v-if="columnRequired(c)" class="req" aria-hidden="true">*</span>
            </label>
            <template v-else>{{ c.label ?? c.fieldname }}</template>
          </dt>
          <dd>
          <TableCell
            :column="c"
            :row="editingRow"
            :index="editing ?? 0"
            :visible="!!cellState(editing ?? 0, c)?.visible"
            :read-only="readOnly"
            :input-id="`${formId}-sheet-${field.fieldname}-${editing}-${c.fieldname}`"
            :error="cellError(editing ?? 0, c)"
            :error-testid="`sheet-error-${c.fieldname}`"
            :unit="unit(c, editingRow)"
            :dynamic-target="dynamicOf(c, editingRow)"
            :link-filters="resolveLinkFilters(c, editingRow)"
            @update="emit('cell', editing ?? 0, c.fieldname, $event)"
          />
            <span v-if="c.description" class="hint">{{ c.description }}</span>
          </dd>
        </div>
      </dl>
      <template #footer>
        <Button
          v-if="readOnly && (emptyCount > 0 || showEmpty)"
          :label="showEmpty ? t.table.hideEmpty : t.table.showEmpty(emptyCount)"
          :icon="showEmpty ? 'pi pi-eye-slash' : 'pi pi-eye'"
          text
          severity="secondary"
          size="small"
          data-testid="row-sheet-empty"
          @click="showEmpty = !showEmpty"
        />
        <Button v-if="!readOnly" :label="t.table.removeRow" icon="pi pi-trash" text severity="danger" size="small" data-testid="row-sheet-remove" @click="removeOpenRow" />
        <Button :label="t.close" icon="pi pi-check" size="small" data-testid="row-sheet-close" @click="editing = null" />
      </template>
    </Dialog>

    <span v-if="tableError" class="error" role="alert" :data-testid="`error-${field.fieldname}`">{{ tableError }}</span>
    <div v-if="!readOnly" class="form-table-foot">
      <Button :label="t.table.addRow" icon="pi pi-plus" size="small" severity="secondary" text :data-testid="`add-row-${field.fieldname}`" @click="add" />
    </div>
  </div>
</template>
