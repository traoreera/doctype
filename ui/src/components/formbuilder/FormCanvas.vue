<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { ref } from 'vue'
import FieldCard from '@/components/formbuilder/FieldCard.vue'
import { t } from '@/i18n'
import { DRAG_FIELD, addSection, addTab, isTabbed, moveField, moveSection, removeTab, renameTab, type Board } from '@/lib/formbuilder'

/**
 * Le formulaire en construction — onglets, sections, colonnes, cartes de champ — partagé par les deux constructeurs : « Personnaliser le
 * formulaire » (l'habillage d'une organisation) et le constructeur de DocTypes du mode développeur. Il ne connaît que le tableau
 * (`lib/formbuilder`) : ce qu'une carte affiche lui est donné (`display`), ce qu'on choisit est rendu au parent.
 *
 * Glisser-déposer natif : une carte sur une colonne (avant la carte où elle tombe, sinon à la fin), sur le TITRE d'un onglet (à la fin de
 * sa dernière section : on ne voit pas ses colonnes d'ici) ; une section par sa poignée, sur une autre section ou un onglet. Le dépôt dans
 * la palette se fait chez le parent (type `DRAG_FIELD`).
 */
export interface CardView {
  label: string
  fieldtype: string
  mandatory?: boolean
  readOnly?: boolean
  hidden?: boolean
  custom?: boolean
}
export type Selection = { kind: 'field'; name: string } | { kind: 'section'; tab: number; section: number } | null

const props = defineProps<{ board: Board; display: (name: string) => CardView; selected: Selection; canEdit: boolean }>()
const emit = defineEmits<{ (e: 'update:board', value: Board): void; (e: 'update:selected', value: Selection): void }>()
const activeTab = defineModel<number>('activeTab', { required: true })

const FIELD = DRAG_FIELD
const SECTION = 'text/x-xdt-section'
const over = ref<string | null>(null)
const accepts = (e: DragEvent, kind: string): boolean => props.canEdit && !!e.dataTransfer?.types.includes(kind)
const chosenField = (): string | null => (props.selected?.kind === 'field' ? props.selected.name : null)

function set(board: Board): void {
  emit('update:board', board)
}
function chooseField(name: string): void {
  emit('update:selected', { kind: 'field', name }) // un clic choisit, toujours (jamais un interrupteur qui déchoisit par surprise)
}

function overColumn(e: DragEvent, id: string): void {
  if (!accepts(e, FIELD)) return
  e.preventDefault()
  over.value = id
}
function dropOnColumn(e: DragEvent, tab: number, section: number, column: number): void {
  over.value = null
  const name = e.dataTransfer?.getData(FIELD)
  if (!name) return
  e.preventDefault()
  const card = (e.target as HTMLElement | null)?.closest<HTMLElement>('[data-field]')
  const col = props.board.tabs[tab]!.sections[section]!.columns[column]!
  const index = card?.dataset.field ? col.indexOf(card.dataset.field) : col.length
  set(moveField(props.board, name, { tab, section, column, index: index < 0 ? col.length : index }))
  emit('update:selected', { kind: 'field', name })
}
function startSection(e: DragEvent, tab: number, section: number): void {
  e.dataTransfer?.setData(SECTION, `${tab}:${section}`)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
function dropSection(e: DragEvent, tab: number, index: number): void {
  over.value = null
  const raw = e.dataTransfer?.getData(SECTION)
  if (!raw) return
  e.preventDefault()
  const [ft, fs] = raw.split(':').map(Number) as [number, number]
  set(moveSection(props.board, { tab: ft, section: fs }, { tab, index }))
  activeTab.value = tab
}
/** Sur le titre d'un onglet : une section y passe à la fin ; un champ, à la fin de sa dernière section. */
function dropOnTab(e: DragEvent, tab: number): void {
  const name = e.dataTransfer?.getData(FIELD)
  if (!name) return dropSection(e, tab, props.board.tabs[tab]!.sections.length)
  e.preventDefault()
  const last = props.board.tabs[tab]!.sections.length - 1
  set(moveField(props.board, name, { tab, section: last, column: 0, index: Infinity }))
  activeTab.value = tab
  emit('update:selected', { kind: 'field', name })
}

function newTab(): void {
  const next = addTab(props.board, t.formBuilder.newTab(props.board.tabs.length + (isTabbed(props.board) ? 1 : 2)), t.formBuilder.firstTab)
  set(next)
  activeTab.value = next.tabs.length - 1
}
function dropTab(index: number): void {
  const next = removeTab(props.board, index)
  set(next)
  activeTab.value = Math.max(0, Math.min(activeTab.value, next.tabs.length - 1))
  emit('update:selected', null)
}
function newSection(): void {
  const next = addSection(props.board, activeTab.value)
  set(next)
  emit('update:selected', { kind: 'section', tab: activeTab.value, section: next.tabs[activeTab.value]!.sections.length - 1 })
}
</script>

<template>
  <section class="fb-canvas" :aria-label="t.formBuilder.canvas" data-testid="fb-canvas">
    <div class="fb-tabs" role="tablist" :aria-label="t.formBuilder.tabs">
      <template v-if="isTabbed(board)">
        <button
          v-for="(tab, ti) in board.tabs"
          :key="tab.key"
          type="button"
          role="tab"
          class="fb-tab"
          :class="{ 'fb-tab-active': ti === activeTab }"
          :aria-selected="ti === activeTab ? 'true' : 'false'"
          :data-testid="`fb-tab-${ti}`"
          @click="activeTab = ti"
          @dragover="(accepts($event, SECTION) || accepts($event, FIELD)) && $event.preventDefault()"
          @drop="dropOnTab($event, ti)"
        >
          {{ tab.name || t.formBuilder.unnamed }}
        </button>
      </template>
      <span class="desk-spacer" />
      <Button v-if="canEdit" :label="t.formBuilder.addTab" icon="pi pi-plus" text size="small" data-testid="fb-add-tab" @click="newTab" />
    </div>

    <div v-if="isTabbed(board) && canEdit" class="fb-tab-edit">
      <label for="fb-tab-name" class="visually-hidden">{{ t.formBuilder.tabName }}</label>
      <InputText
        id="fb-tab-name"
        :model-value="board.tabs[activeTab]?.name ?? ''"
        size="small"
        :placeholder="t.formBuilder.tabName"
        data-testid="fb-tab-name"
        @update:model-value="set(renameTab(board, activeTab, $event ?? ''))"
      />
      <Button :label="t.formBuilder.removeTab" icon="pi pi-trash" text severity="secondary" size="small" data-testid="fb-remove-tab" @click="dropTab(activeTab)" />
    </div>

    <div
      v-for="(sec, si) in board.tabs[activeTab]?.sections ?? []"
      :key="sec.key"
      class="fb-section"
      :class="{ 'fb-section-selected': selected?.kind === 'section' && selected.tab === activeTab && selected.section === si }"
      :data-testid="`fb-section-${si}`"
      @dragover="accepts($event, SECTION) && $event.preventDefault()"
      @drop="dropSection($event, activeTab, si)"
    >
      <div class="fb-section-head">
        <span v-if="canEdit" class="pi pi-arrows-alt fb-grip" draggable="true" :title="t.formBuilder.dragSection" aria-hidden="true" @dragstart="startSection($event, activeTab, si)" />
        <button type="button" class="fb-section-title" :data-testid="`fb-section-title-${si}`" @click="emit('update:selected', { kind: 'section', tab: activeTab, section: si })">
          {{ sec.section || t.formBuilder.untitled }}
          <span class="desk-muted fb-section-meta">· {{ t.formBuilder.columnsCount(sec.columns.length) }}<template v-if="sec.collapsible"> · {{ t.formBuilder.collapsible }}</template></span>
        </button>
      </div>
      <div class="fb-columns" :style="{ gridTemplateColumns: `repeat(${sec.columns.length}, minmax(0, 1fr))` }">
        <div
          v-for="(col, ci) in sec.columns"
          :key="ci"
          class="fb-column"
          :class="{ 'fb-drop': over === `${sec.key}:${ci}` }"
          :data-testid="`fb-column-${si}-${ci}`"
          @dragover="overColumn($event, `${sec.key}:${ci}`)"
          @dragleave="over = null"
          @drop="dropOnColumn($event, activeTab, si, ci)"
        >
          <FieldCard v-for="name in col" :key="name" v-bind="display(name)" :name="name" :selected="chosenField() === name" :draggable="canEdit" @choose="chooseField" />
          <p v-if="!col.length" class="fb-empty">{{ t.formBuilder.dropHere }}</p>
        </div>
      </div>
    </div>
    <Button v-if="canEdit" :label="t.formBuilder.addSection" icon="pi pi-plus" text size="small" class="fb-add-section" data-testid="fb-add-section" @click="newSection" />
  </section>
</template>
