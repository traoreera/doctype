<script setup lang="ts">
import Button from 'primevue/button'
import { computed, ref } from 'vue'
import { t } from '@/i18n'
import { formatCell } from '@/lib/format'
import {
  DRAG_BLOCK,
  DRAG_COLUMN,
  DRAG_FIELD,
  DRAG_KIND,
  KIND_LABELS,
  fieldColumns,
  fillText,
  tableColumns,
  type Block,
  type BlockKind,
  type Draft,
} from '@/lib/printedit'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'

/**
 * La FEUILLE de l'éditeur d'impression : les blocs dessinés comme ils sortiront (titre, champs avec leurs
 * valeurs, tableau avec ses lignes, totaux à droite), sur une page aux proportions du format choisi, marges
 * comprises. C'est ici qu'on compose la facture : glisser un bloc de la palette à sa place, déplacer un bloc,
 * déplacer un champ d'un bloc à l'autre, réordonner les colonnes d'un tableau et tirer leur largeur.
 *
 * Le composant ne décide rien : il dit OÙ l'on a lâché (un emplacement, 0 = avant le premier) et la page
 * appelle le modèle pur (`lib/printedit`). Le rendu est une approximation d'édition — l'aperçu imprimé, rendu
 * par le serveur, fait foi (bascule « Aperçu imprimé » de la page).
 *
 * Pendant un `dragover` le navigateur ne laisse lire que les TYPES du transfert, jamais son contenu : chaque
 * zone décide donc par le type (`DRAG_*`) si elle accepte, et lit la donnée au dépôt seulement.
 */
const props = defineProps<{
  draft: Draft
  meta: DocTypeMeta
  /** Les valeurs du document d'exemple, ou `null` quand le type n'en a aucun. */
  example: Record<string, unknown> | null
  name: string | null
  chosen: string | null
  chosenColumn: string | null
  editable: boolean
  /** Le titre d'un document lié (« CUST-0001 » → « Durand »), comme l'impression le montre. */
  linkTitle: (target: string, name: string) => string
}>()
const emit = defineEmits<{
  select: [key: string]
  selectColumn: [key: string, column: string]
  insert: [kind: BlockKind, slot: number]
  moveBlock: [key: string, slot: number]
  moveField: [from: string, field: string, to: string, slot: number]
  moveColumn: [key: string, column: string, slot: number]
  resizeColumn: [key: string, column: string, width: number]
  nudge: [key: string, how: 'up' | 'down']
  remove: [key: string]
}>()

// ── La page : proportions du format, marges à l'échelle ──
const SIZES: Record<string, [number, number]> = {
  A3: [297, 420], A4: [210, 297], A5: [148, 210], B4: [250, 353], B5: [176, 250], Letter: [216, 279], Legal: [216, 356], Ledger: [279, 432],
}
const dims = computed(() => {
  const [w, h] = SIZES[props.draft.page.size] ?? SIZES.A4!
  return props.draft.page.orientation === 'landscape' ? [h, w] : [w, h]
})
const paperStyle = computed(() => {
  const [w, h] = dims.value as [number, number]
  const pct = (mm: number) => `${(mm / w) * 100}%` // un padding en % se rapporte à la LARGEUR, même en haut
  const p = props.draft.page
  return { aspectRatio: `${w} / ${h}`, padding: `${pct(p.top)} ${pct(p.right)} ${pct(p.bottom)} ${pct(p.left)}` }
})

// ── Les valeurs du document d'exemple ──
const fieldOf = (name: string): FieldMeta | undefined => props.meta.fields.find((f) => f.fieldname === name)
const labelOf = (name: string): string => fieldOf(name)?.label || name
function show(f: FieldMeta | undefined, value: unknown): string {
  if (!f) return value === null || value === undefined ? '' : String(value)
  if ((f.fieldtype === 'Link') && typeof f.options === 'string' && typeof value === 'string' && value) return props.linkTitle(f.options, value)
  if (f.fieldtype === 'Boolean') return value === true ? 'Oui' : value === false ? 'Non' : ''
  return formatCell(f, value)
}
const valueOf = (name: string): string => show(fieldOf(name), props.example?.[name])
const text = (b: Block): string =>
  fillText(b.text ?? '', { name: props.name, doctype: props.meta.label || props.meta.name, value: valueOf })

function childFields(b: Block): FieldMeta[] {
  const table = props.meta.fields.find((f) => f.fieldname === b.field)
  return (table && typeof table.options === 'string' ? props.meta.children?.[table.options]?.fields : undefined) ?? []
}
function rows(b: Block): Record<string, unknown>[] {
  const all = props.example?.[b.field ?? '']
  return Array.isArray(all) && all.length ? (all.slice(0, 6) as Record<string, unknown>[]) : [{}, {}]
}
const cell = (b: Block, column: string, row: Record<string, unknown>): string =>
  show(childFields(b).find((f) => f.fieldname === column), row[column])
const columnLabel = (b: Block, column: string): string => childFields(b).find((f) => f.fieldname === column)?.label || column

// ── Glisser : les blocs sur la page ──
const has = (e: DragEvent, type: string): boolean => !!e.dataTransfer?.types.includes(type)
/** L'emplacement survolé par un BLOC (neuf ou déplacé) : la page y trace une ligne. */
const slot = ref<number | null>(null)
/** Le bloc qu'on survole avec un CHAMP, et l'emplacement dans ce bloc. */
const fieldSlot = ref<{ block: string; at: number } | null>(null)
/** La colonne qu'on survole avec une COLONNE. */
const columnSlot = ref<{ block: string; at: number } | null>(null)

function blockStart(e: DragEvent, b: Block): void {
  // Un glisser lancé depuis un champ ou une colonne n'est PAS celui du bloc qui les contient.
  if (!props.editable || !e.dataTransfer || (e.target as HTMLElement).closest('[data-drag-inner]')) return
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData(DRAG_BLOCK, b.key)
  emit('select', b.key)
}
function blockOver(e: DragEvent, i: number): void {
  if (!props.editable || !(has(e, DRAG_BLOCK) || has(e, DRAG_KIND))) return
  e.preventDefault()
  e.stopPropagation()
  if (e.dataTransfer) e.dataTransfer.dropEffect = has(e, DRAG_KIND) ? 'copy' : 'move'
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  slot.value = e.clientY < r.top + r.height / 2 ? i : i + 1
}
function pageOver(e: DragEvent): void {
  if (!props.editable || !(has(e, DRAG_BLOCK) || has(e, DRAG_KIND))) return
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = has(e, DRAG_KIND) ? 'copy' : 'move'
  // Sous le dernier bloc (ou sur une page vide) : à la fin.
  if (slot.value === null) slot.value = props.draft.blocks.length
}
function pageDrop(e: DragEvent): void {
  const at = slot.value ?? props.draft.blocks.length
  clear()
  if (!props.editable || !e.dataTransfer) return
  const kind = e.dataTransfer.getData(DRAG_KIND) as BlockKind
  const moved = e.dataTransfer.getData(DRAG_BLOCK)
  if (kind && kind in KIND_LABELS) {
    e.preventDefault()
    emit('insert', kind, at)
  } else if (moved) {
    e.preventDefault()
    emit('moveBlock', moved, at)
  }
}
function pageLeave(e: DragEvent): void {
  // `dragleave` part aussi vers chaque enfant : on n'efface que quand le pointeur QUITTE la feuille.
  const to = e.relatedTarget as Node | null
  if (!to || !(e.currentTarget as HTMLElement).contains(to)) clear()
}
function clear(): void {
  slot.value = null
  fieldSlot.value = null
  columnSlot.value = null
}

// ── Glisser : les champs d'un bloc ──
function fieldStart(e: DragEvent, b: Block, field: string): void {
  if (!props.editable || !e.dataTransfer) return
  e.stopPropagation()
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData(DRAG_FIELD, `${b.key}|${field}`)
  emit('select', b.key)
}
function fieldOver(e: DragEvent, b: Block, i: number | null): void {
  if (!props.editable || !has(e, DRAG_FIELD)) return
  e.preventDefault()
  e.stopPropagation()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  let at = b.fields?.length ?? 0
  if (i !== null) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    at = e.clientX < r.left + r.width / 2 ? i : i + 1
  }
  fieldSlot.value = { block: b.key, at }
}
function fieldDrop(e: DragEvent, b: Block): void {
  const data = e.dataTransfer?.getData(DRAG_FIELD)
  const at = fieldSlot.value?.block === b.key ? fieldSlot.value.at : (b.fields?.length ?? 0)
  clear()
  if (!data) return
  e.preventDefault()
  e.stopPropagation()
  const [from, field] = data.split('|')
  if (from && field) emit('moveField', from, field, b.key, at)
}

// ── Glisser et tirer : les colonnes d'un tableau ──
function columnStart(e: DragEvent, b: Block, column: string): void {
  if (!props.editable || !e.dataTransfer) return
  e.stopPropagation()
  if ((e.target as HTMLElement).closest('.pc-resize')) return e.preventDefault()
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData(DRAG_COLUMN, `${b.key}|${column}`)
  emit('selectColumn', b.key, column)
}
function columnOver(e: DragEvent, b: Block, i: number): void {
  if (!props.editable || !has(e, DRAG_COLUMN)) return
  e.preventDefault()
  e.stopPropagation()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
  columnSlot.value = { block: b.key, at: e.clientX < r.left + r.width / 2 ? i : i + 1 }
}
function columnDrop(e: DragEvent, b: Block): void {
  const data = e.dataTransfer?.getData(DRAG_COLUMN)
  const at = columnSlot.value?.block === b.key ? columnSlot.value.at : tableColumns(b).length
  clear()
  if (!data) return
  e.preventDefault()
  e.stopPropagation()
  const [from, column] = data.split('|')
  if (from === b.key && column) emit('moveColumn', b.key, column, at) // une colonne reste dans SON tableau
}

/**
 * Tirer le bord droit d'un en-tête : la largeur suit le pointeur, en % de la largeur du tableau.
 *
 * Pendant ce geste l'en-tête n'est plus `draggable` (`resizing`) : sinon Chromium lance un glisser NATIF de la
 * colonne au premier déplacement et n'envoie plus aucun `pointermove` — mesuré, la largeur ne bougeait pas.
 * Le suivi se fait sur la fenêtre, pour ne pas perdre le pointeur quand il sort de la poignée.
 */
const resizing = ref(false)
function resizeStart(e: PointerEvent, b: Block, column: string): void {
  if (!props.editable) return
  e.preventDefault()
  e.stopPropagation()
  const handle = e.currentTarget as HTMLElement
  const th = handle.closest('th') as HTMLElement
  const table = handle.closest('table') as HTMLElement
  resizing.value = true
  emit('selectColumn', b.key, column)
  const move = (m: PointerEvent) => {
    m.preventDefault()
    const width = ((m.clientX - th.getBoundingClientRect().left) / table.getBoundingClientRect().width) * 100
    emit('resizeColumn', b.key, column, width)
  }
  const up = () => {
    resizing.value = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    window.removeEventListener('pointercancel', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
  window.addEventListener('pointercancel', up)
}

const gridStyle = (b: Block) => ({ gridTemplateColumns: `repeat(${fieldColumns(b)}, minmax(0, 1fr))` })
const showFieldMark = (b: Block, at: number): boolean => fieldSlot.value?.block === b.key && fieldSlot.value.at === at
const showColumnMark = (b: Block, at: number): boolean => columnSlot.value?.block === b.key && columnSlot.value.at === at
</script>

<template>
  <div class="pc-desk" data-testid="pc-desk">
    <div
      class="pc-paper"
      :style="paperStyle"
      data-testid="pc-paper"
      @dragover="pageOver"
      @drop="pageDrop"
      @dragleave="pageLeave"
      @dragend="clear"
    >
      <p v-if="!draft.blocks.length" class="pc-empty" data-testid="pb-empty">{{ t.printBuilder.empty }}</p>
      <template v-for="(b, i) in draft.blocks" :key="b.key">
        <div v-if="slot === i" class="pc-mark" data-testid="pc-mark" />
        <section
          :class="['pc-block', `pc-${b.type}`, { chosen: b.key === chosen }]"
          :draggable="editable && !resizing"
          :data-testid="`pb-block-${i}`"
          :data-kind="b.type"
          @dragstart="blockStart($event, b)"
          @dragover="blockOver($event, i)"
          @click="emit('select', b.key)"
        >
          <header class="pc-tools">
            <span class="pc-grip" aria-hidden="true"><i class="pi pi-bars" /></span>
            <button type="button" class="pc-kind" :data-testid="`pb-pick-${i}`" @click.stop="emit('select', b.key)">{{ KIND_LABELS[b.type] }}</button>
            <span class="pc-actions">
              <Button icon="pi pi-arrow-up" text rounded size="small" :disabled="!editable || i === 0" :aria-label="t.printBuilder.moveUp" :data-testid="`pb-up-${i}`" @click.stop="emit('nudge', b.key, 'up')" />
              <Button icon="pi pi-arrow-down" text rounded size="small" :disabled="!editable || i === draft.blocks.length - 1" :aria-label="t.printBuilder.moveDown" :data-testid="`pb-down-${i}`" @click.stop="emit('nudge', b.key, 'down')" />
              <Button icon="pi pi-trash" text rounded severity="danger" size="small" :disabled="!editable" :aria-label="t.printBuilder.remove" :data-testid="`pb-remove-${i}`" @click.stop="emit('remove', b.key)" />
            </span>
          </header>

          <!-- Titre, texte -->
          <h1 v-if="b.type === 'title'" class="pc-title">{{ text(b) || t.printBuilder.emptyText }}</h1>
          <p v-else-if="b.type === 'text'" :class="['pc-text', { 'pc-placeholder': !b.text?.trim() }]">{{ text(b) || t.printBuilder.emptyText }}</p>

          <!-- Champs, totaux : chaque champ se déplace, y compris vers un autre bloc -->
          <template v-else-if="b.type === 'fields' || b.type === 'totals'">
            <h2 v-if="b.label" class="pc-label">{{ b.label }}</h2>
            <div
              :class="b.type === 'fields' ? 'pc-fields' : 'pc-totals'"
              :style="b.type === 'fields' ? gridStyle(b) : undefined"
              data-drag-inner
              @dragover="fieldOver($event, b, null)"
              @drop="fieldDrop($event, b)"
            >
              <p v-if="!b.fields?.length" class="pc-placeholder pc-drop-hint">{{ t.printBuilder.dropFields }}</p>
              <div
                v-for="(f, j) in b.fields ?? []"
                :key="f"
                :class="['pc-field', { 'mark-before': showFieldMark(b, j), 'mark-after': showFieldMark(b, j + 1) && j === (b.fields?.length ?? 0) - 1 }]"
                :draggable="editable"
                data-drag-inner
                :data-testid="`pc-field-${f}`"
                @dragstart="fieldStart($event, b, f)"
                @dragover="fieldOver($event, b, j)"
                @drop="fieldDrop($event, b)"
              >
                <span class="pc-field-label">{{ labelOf(f) }}</span>
                <span class="pc-field-value">{{ valueOf(f) || '—' }}</span>
              </div>
            </div>
          </template>

          <!-- Le tableau : en-têtes qu'on glisse et qu'on élargit, lignes du document d'exemple -->
          <template v-else-if="b.type === 'table'">
            <h2 v-if="b.label" class="pc-label">{{ b.label }}</h2>
            <p v-if="!b.field" class="pc-placeholder">{{ t.printBuilder.chooseTable }}</p>
            <p v-else-if="!tableColumns(b).length" class="pc-placeholder pc-drop-hint">{{ t.printBuilder.addColumnsHint }}</p>
            <table v-else :class="['pc-table', `style-${b.style ?? 'bordered'}`, `density-${b.density ?? 'normal'}`]" data-drag-inner @drop="columnDrop($event, b)">
              <thead>
                <tr>
                  <th
                    v-for="(c, j) in tableColumns(b)"
                    :key="c.key"
                    :class="[`align-${c.align ?? 'left'}`, { chosen: chosenColumn === c.key, 'mark-before': showColumnMark(b, j), 'mark-after': showColumnMark(b, j + 1) && j === tableColumns(b).length - 1 }]"
                    :style="c.width ? { width: `${c.width}%` } : undefined"
                    :draggable="editable && !resizing"
                    :data-testid="`pc-col-${c.field}`"
                    @dragstart="columnStart($event, b, c.key)"
                    @dragover="columnOver($event, b, j)"
                    @click.stop="emit('selectColumn', b.key, c.key)"
                  >
                    {{ c.label || columnLabel(b, c.field) }}
                    <span v-if="c.width && chosenColumn === c.key" class="pc-width">{{ c.width }} %</span>
                    <span
                      v-if="editable"
                      class="pc-resize"
                      :title="t.printBuilder.resizeHint"
                      :data-testid="`pc-resize-${c.field}`"
                      @pointerdown="resizeStart($event, b, c.key)"
                      @mousedown.prevent
                      @click.stop
                    />
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, r) in rows(b)" :key="r">
                  <td v-for="c in tableColumns(b)" :key="c.key" :class="`align-${c.align ?? 'left'}`">{{ cell(b, c.field, row) || ' ' }}</td>
                </tr>
              </tbody>
            </table>
          </template>

          <div v-else-if="b.type === 'spacer'" class="pc-spacer" :style="{ height: `${Math.min(b.size ?? 5, 60) * 3}px` }">{{ b.size ?? 5 }} mm</div>
          <div v-else-if="b.type === 'page_break'" class="pc-break">{{ KIND_LABELS.page_break }}</div>
        </section>
      </template>
      <div v-if="slot === draft.blocks.length" class="pc-mark" data-testid="pc-mark" />
      <div v-if="editable" class="pc-tail" data-testid="pc-tail">{{ t.printBuilder.dropHere }}</div>
    </div>
  </div>
</template>

<style scoped>
/* Le bureau gris et la feuille blanche : la feuille reste BLANCHE en mode sombre, comme elle s'imprimera. */
.pc-desk {
  background: var(--desk-subtle);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  padding: 1.25rem;
  overflow: auto;
  max-height: calc(100vh - 12rem);
}

.pc-paper {
  /* Couleurs d'une page imprimée, volontairement fixes : la feuille ne suit pas le thème du Desk. */
  --paper-ink: var(--desk-paper-ink);
  --paper-muted: var(--desk-paper-muted);
  --paper-rule: var(--desk-paper-rule);
  --paper-accent: var(--p-primary-color);
  background: var(--desk-paper);
  color: var(--paper-ink);
  width: 100%;
  max-width: 52rem;
  margin: 0 auto;
  box-shadow: 0 1px 3px color-mix(in srgb, var(--desk-paper-ink) 18%, transparent), 0 8px 24px color-mix(in srgb, var(--desk-paper-ink) 8%, transparent);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.82rem;
}

.pc-empty {
  color: var(--paper-muted);
  text-align: center;
  padding: 3rem 1rem;
}

.pc-block {
  position: relative;
  border: 1px dashed transparent;
  border-radius: 4px;
  padding: 0.35rem 0.45rem;
  cursor: pointer;
}

.pc-block:hover {
  border-color: var(--paper-rule);
}

.pc-block.chosen {
  border: 1px solid var(--paper-accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--paper-accent) 18%, transparent);
}

.pc-tools {
  position: absolute;
  top: -0.85rem;
  right: 0.35rem;
  display: none;
  align-items: center;
  gap: 0.15rem;
  background: var(--p-content-background);
  color: var(--p-text-color);
  border: 1px solid var(--p-content-border-color);
  border-radius: 999px;
  padding: 0 0.25rem 0 0.5rem;
  z-index: 2;
  font-size: 0.75rem;
}

/* Les outils restent visibles sur le bloc choisi : sur un écran tactile il n'y a pas de survol. */
.pc-block:hover > .pc-tools,
.pc-block.chosen > .pc-tools,
.pc-block:focus-within > .pc-tools {
  display: flex;
}

.pc-grip {
  cursor: grab;
  color: var(--p-text-muted-color);
}

.pc-kind {
  background: none;
  border: 0;
  color: inherit;
  font: inherit;
  font-weight: 600;
  padding: 0.25rem 0.15rem;
  cursor: pointer;
  min-height: 24px;
}

.pc-actions {
  display: flex;
}

.pc-mark {
  height: 3px;
  border-radius: 2px;
  background: var(--paper-accent);
  margin: 0.1rem 0;
}

.pc-tail {
  margin-top: auto;
  padding: 0.75rem;
  border: 1px dashed var(--paper-rule);
  border-radius: 4px;
  color: var(--paper-muted);
  text-align: center;
  font-size: 0.75rem;
}

.pc-title {
  font-size: 1.5rem;
  margin: 0;
  font-weight: 700;
}

.pc-text {
  margin: 0;
  white-space: pre-wrap;
}

.pc-placeholder {
  color: var(--paper-muted);
  font-style: italic;
  margin: 0;
}

.pc-drop-hint {
  padding: 0.6rem;
  border: 1px dashed var(--paper-rule);
  border-radius: 4px;
  text-align: center;
}

.pc-label {
  font-size: 0.9rem;
  margin: 0 0 0.3rem;
  font-weight: 600;
}

.pc-fields {
  display: grid;
  gap: 0.4rem 1rem;
}

.pc-totals {
  display: grid;
  gap: 0.2rem;
  margin-left: auto;
  min-width: 45%;
}

.pc-field {
  display: grid;
  gap: 0.05rem;
  padding: 0.15rem 0.3rem;
  border-radius: 3px;
  cursor: grab;
  position: relative;
}

.pc-totals .pc-field {
  grid-template-columns: 1fr auto;
  gap: 1rem;
  border-bottom: 1px solid var(--paper-rule);
}

.pc-field:hover {
  background: color-mix(in srgb, var(--paper-accent) 8%, transparent);
}

.pc-field-label {
  color: var(--paper-muted);
  font-size: 0.7rem;
}

.pc-totals .pc-field-label {
  color: inherit;
  font-size: inherit;
  font-weight: 600;
}

.pc-totals .pc-field-value {
  text-align: right;
}

.mark-before {
  box-shadow: inset 3px 0 0 var(--paper-accent);
}

.mark-after {
  box-shadow: inset -3px 0 0 var(--paper-accent);
}

.pc-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.pc-table th,
.pc-table td {
  padding: 0.35rem 0.45rem;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pc-table.density-compact th,
.pc-table.density-compact td {
  padding: 0.15rem 0.35rem;
}

.pc-table th {
  position: relative;
  background: var(--desk-paper-head);
  text-align: left;
  font-weight: 600;
  cursor: grab;
  user-select: none;
}

.pc-table th.chosen {
  outline: 2px solid var(--paper-accent);
  outline-offset: -2px;
}

.pc-table.style-bordered th,
.pc-table.style-bordered td {
  border: 1px solid var(--paper-rule);
}

.pc-table.style-ruled tr {
  border-bottom: 1px solid var(--paper-rule);
}

.align-right {
  text-align: right !important;
}

.align-center {
  text-align: center !important;
}

.pc-width {
  display: block;
  font-size: 0.65rem;
  color: var(--paper-accent);
  font-weight: 500;
}

/* La poignée de largeur : une bande le long du bord droit, DANS son en-tête. À cheval sur le bord, la colonne
   suivante la recouvrait (et `overflow: hidden` la coupait) : le clic attrapait l'en-tête voisin et lançait un
   glisser de colonne — mesuré dans Chromium par `elementFromPoint`. */
.pc-resize {
  position: absolute;
  top: 0;
  right: 0;
  width: 8px;
  height: 100%;
  cursor: col-resize;
  z-index: 1;
  touch-action: none;
}

.pc-resize:hover {
  background: color-mix(in srgb, var(--paper-accent) 35%, transparent);
}

.pc-spacer {
  display: grid;
  place-items: center;
  min-height: 12px;
  color: var(--paper-muted);
  font-size: 0.65rem;
  background: repeating-linear-gradient(45deg, transparent 0 6px, var(--desk-paper-head) 6px 12px);
}

.pc-break {
  text-align: center;
  color: var(--paper-muted);
  font-size: 0.7rem;
  border-top: 2px dashed var(--paper-rule);
  padding-top: 0.2rem;
}

@media (max-width: 700px) {
  .pc-desk {
    padding: 0.5rem;
  }
}
</style>
