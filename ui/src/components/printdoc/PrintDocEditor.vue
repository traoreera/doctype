<script setup lang="ts">
import { EditorContent, useEditor } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import { TableCell, TableHeader, TableRow } from '@tiptap/extension-table'
import { TextAlign } from '@tiptap/extension-text-align'
import { TextStyleKit } from '@tiptap/extension-text-style'
import { NodeSelection } from '@tiptap/pm/state'
import Button from 'primevue/button'
import Select from 'primevue/select'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { t } from '@/i18n'
import { PX_PER_MM, normalizeHtml, pageMm, type PageGeometry } from '@/lib/printdoc'
import type { DocTypeMeta } from '@/lib/types'
import { EmbeddedBlock, LayoutTable, PageBreak, PrintDocStorage, PrintImage, ValueToken, docContext, type PrintDocContext } from './extensions'

/**
 * L'éditeur du document d'impression, façon traitement de texte (`process/impression.md` §12) : une FEUILLE aux
 * dimensions du format, marges comprises, dessinée à l'échelle de l'écran, sur laquelle on écrit et met en forme
 * comme dans Word — styles de paragraphe, gras, couleur, taille, alignement, listes, tableaux de mise en page —
 * avec les valeurs du document insérées dans le texte, des images qu'on place où l'on veut, et le tableau des
 * lignes. Les traits pointillés marquent où le contenu passera à la page suivante.
 *
 * Ce que l'éditeur enregistre est son HTML, NETTOYÉ des styles qu'un collage apporte et que le serveur refuserait
 * (`normalizeHtml`) ; le serveur revalide tout (`print_document`) et reste l'autorité.
 */
type Page = { size: string; orientation: string; top: number; right: number; bottom: number; left: number }
export interface Selected {
  type: 'printImage' | 'embeddedBlock'
  pos: number
  attrs: Record<string, unknown>
}

const props = defineProps<{
  modelValue: string
  page: Page
  meta: DocTypeMeta
  example: Record<string, unknown> | null
  name: string | null
  editable: boolean
  linkTitle: (target: string, name: string) => string
  imageUrl: (fileId: string) => string | null
}>()
const emit = defineEmits<{ 'update:modelValue': [html: string]; select: [node: Selected | null] }>()

const editor = useEditor({
  content: props.modelValue,
  editable: props.editable,
  extensions: [
    // Pas de lien (un gabarit n'a pas d'URL), pas de code : ce que le serveur n'accepte pas n'existe pas ici.
    StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: false, code: false, codeBlock: false }),
    TextStyleKit.configure({ fontFamily: false }),
    TextAlign.configure({ types: ['heading', 'paragraph'] }),
    LayoutTable.configure({ resizable: true }),
    TableRow,
    TableHeader,
    TableCell,
    PrintDocStorage,
    ValueToken,
    PrintImage,
    EmbeddedBlock,
    PageBreak,
  ],
  editorProps: { attributes: { class: 'pd-content', 'data-testid': 'pd-content', 'aria-label': t.printDoc.content } },
  onUpdate: ({ editor: e }) => emit('update:modelValue', normalizeHtml(e.getHTML())),
  onSelectionUpdate: ({ editor: e }) => emit('select', selectedNode(e.state.selection)),
  onTransaction: ({ editor: e }) => emit('select', selectedNode(e.state.selection)),
})

function selectedNode(sel: unknown): Selected | null {
  if (!(sel instanceof NodeSelection)) return null
  const name = sel.node.type.name
  return name === 'printImage' || name === 'embeddedBlock' ? { type: name, pos: sel.from, attrs: { ...sel.node.attrs } } : null
}

// ── Le contexte partagé avec les vues des nœuds ──
const ctx = computed<PrintDocContext | undefined>(() => (editor.value ? docContext(editor.value) : undefined))
watch(
  [() => props.meta, () => props.example, () => props.name, () => props.editable, () => props.linkTitle, () => props.imageUrl, ctx],
  () => {
    const c = ctx.value
    if (!c) return
    Object.assign(c, { meta: props.meta, example: props.example, name: props.name, editable: props.editable, linkTitle: props.linkTitle, imageUrl: props.imageUrl })
  },
  { immediate: true },
)
watch(() => props.editable, (on) => editor.value?.setEditable(on))
// Un contenu remplacé de l'extérieur (rechargement, « Revenir à l'original ») : on recharge sans rejouer `onUpdate`.
watch(
  () => props.modelValue,
  (html) => {
    const e = editor.value
    if (e && html !== normalizeHtml(e.getHTML())) e.commands.setContent(html, { emitUpdate: false })
  },
)
onBeforeUnmount(() => editor.value?.destroy())

// ── La feuille, à l'échelle ──
const geom = computed<PageGeometry>(() => pageMm(props.page))
const stage = ref<HTMLElement | null>(null)
const paper = ref<HTMLElement | null>(null)
const scale = ref(1)
const paperHeight = ref(0)
let observer: ResizeObserver | null = null
function measure(): void {
  const avail = (stage.value?.clientWidth ?? 0) - 32
  const full = geom.value.width * PX_PER_MM
  scale.value = avail > 0 ? Math.min(1, avail / full) : 1
  paperHeight.value = paper.value?.offsetHeight ?? 0
  if (ctx.value) ctx.value.scale = scale.value
}
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(measure)
    if (stage.value) observer.observe(stage.value)
    if (paper.value) observer.observe(paper.value)
  }
})
onBeforeUnmount(() => observer?.disconnect())
watch(geom, () => requestAnimationFrame(measure))
watch(
  [geom, ctx],
  ([g, c]) => {
    if (c) c.area = { width: g.width - g.left - g.right, height: g.height - g.top - g.bottom, left: g.left, top: g.top, right: g.right, bottom: g.bottom }
  },
  { immediate: true },
)

const paperStyle = computed(() => {
  const g = geom.value
  const body = g.height - g.top - g.bottom // la hauteur de contenu d'une page : un trait pointillé à chaque saut
  return {
    width: `${g.width}mm`,
    minHeight: `${g.height}mm`,
    padding: `${g.top}mm ${g.right}mm ${g.bottom}mm ${g.left}mm`,
    transform: `scale(${scale.value})`,
    '--pd-page': `${body}mm`,
  }
})
const stageStyle = computed(() => ({ height: `${paperHeight.value * scale.value + 32}px` }))

// ── La barre d'outils ──
type Chain = ReturnType<NonNullable<typeof editor.value>['chain']>
const run = (fn: (c: Chain) => Chain) => {
  const e = editor.value
  if (e) fn(e.chain().focus()).run()
}
const active = (name: string, attrs?: Record<string, unknown>) => !!editor.value?.isActive(name, attrs)
const styleChoices = [
  { value: 'p', label: t.printDoc.styles.p },
  { value: 'h1', label: t.printDoc.styles.h1 },
  { value: 'h2', label: t.printDoc.styles.h2 },
  { value: 'h3', label: t.printDoc.styles.h3 },
]
const paragraphStyle = computed(() => {
  for (const level of [1, 2, 3] as const) if (active('heading', { level })) return `h${level}`
  return 'p'
})
function setParagraphStyle(v: string): void {
  if (v === 'p') run((c) => c.setParagraph())
  else run((c) => c.setHeading({ level: Number(v.slice(1)) as 1 | 2 | 3 }))
}
const sizes = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32].map((n) => ({ value: `${n}pt`, label: `${n}` }))
const fontSize = computed(() => (editor.value?.getAttributes('textStyle').fontSize as string | undefined) ?? null)
function setFontSize(v: string | null): void {
  if (v) run((c) => c.setFontSize(v))
  else run((c) => c.unsetFontSize())
}
const color = computed(() => (editor.value?.getAttributes('textStyle').color as string | undefined) ?? '#000000')
const highlight = computed(() => (editor.value?.getAttributes('textStyle').backgroundColor as string | undefined) ?? '#ffffff')
const inTable = computed(() => active('table'))
const tableBorders = computed(() => (editor.value?.getAttributes('table').borders as string | undefined) ?? 'all')

defineExpose({ editor })
</script>

<template>
  <div class="pd-editor">
    <div v-if="editor && editable" class="pd-toolbar" role="toolbar" :aria-label="t.printDoc.toolbar" data-testid="pd-toolbar" @mousedown.prevent>
      <Select :model-value="paragraphStyle" :options="styleChoices" option-label="label" option-value="value" size="small" class="pd-style" :aria-label="t.printDoc.paragraphStyle" data-testid="pd-style" @update:model-value="setParagraphStyle" />
      <Select :model-value="fontSize" :options="sizes" option-label="label" option-value="value" size="small" show-clear :placeholder="t.printDoc.size" class="pd-size" :aria-label="t.printDoc.size" data-testid="pd-size" @update:model-value="setFontSize" />
      <span class="pd-sep" />
      <Button text size="small" :severity="active('bold') ? 'primary' : 'secondary'" :aria-label="t.printDoc.bold" :title="t.printDoc.bold" data-testid="pd-bold" @click="run((c) => c.toggleBold())"><b>G</b></Button>
      <Button text size="small" :severity="active('italic') ? 'primary' : 'secondary'" :aria-label="t.printDoc.italic" :title="t.printDoc.italic" @click="run((c) => c.toggleItalic())"><i>I</i></Button>
      <Button text size="small" :severity="active('underline') ? 'primary' : 'secondary'" :aria-label="t.printDoc.underline" :title="t.printDoc.underline" @click="run((c) => c.toggleUnderline())"><u>S</u></Button>
      <Button text size="small" :severity="active('strike') ? 'primary' : 'secondary'" :aria-label="t.printDoc.strike" :title="t.printDoc.strike" @click="run((c) => c.toggleStrike())"><s>ab</s></Button>
      <label class="pd-color" :title="t.printDoc.color">
        <span class="pd-color-a" :style="{ borderBottomColor: color }">A</span>
        <input type="color" :value="color" :aria-label="t.printDoc.color" data-testid="pd-color" @input="run((c) => c.setColor(($event.target as HTMLInputElement).value))" />
      </label>
      <label class="pd-color" :title="t.printDoc.highlight">
        <i class="pi pi-palette" aria-hidden="true" />
        <input type="color" :value="highlight" :aria-label="t.printDoc.highlight" @input="run((c) => c.setBackgroundColor(($event.target as HTMLInputElement).value))" />
      </label>
      <span class="pd-sep" />
      <Button icon="pi pi-align-left" text size="small" :aria-label="t.printDoc.alignLeft" @click="run((c) => c.setTextAlign('left'))" />
      <Button icon="pi pi-align-center" text size="small" :aria-label="t.printDoc.alignCenter" data-testid="pd-center" @click="run((c) => c.setTextAlign('center'))" />
      <Button icon="pi pi-align-right" text size="small" :aria-label="t.printDoc.alignRight" data-testid="pd-right" @click="run((c) => c.setTextAlign('right'))" />
      <Button icon="pi pi-align-justify" text size="small" :aria-label="t.printDoc.justify" @click="run((c) => c.setTextAlign('justify'))" />
      <span class="pd-sep" />
      <Button icon="pi pi-list" text size="small" :severity="active('bulletList') ? 'primary' : 'secondary'" :aria-label="t.printDoc.bullets" @click="run((c) => c.toggleBulletList())" />
      <Button label="1." text size="small" :severity="active('orderedList') ? 'primary' : 'secondary'" :aria-label="t.printDoc.numbers" @click="run((c) => c.toggleOrderedList())" />
      <Button icon="pi pi-minus" text size="small" :aria-label="t.printDoc.rule" @click="run((c) => c.setHorizontalRule())" />
      <span class="pd-sep" />
      <Button icon="pi pi-undo" text size="small" :aria-label="t.printDoc.undo" :disabled="!editor.can().undo()" @click="run((c) => c.undo())" />
      <Button icon="pi pi-refresh" text size="small" :aria-label="t.printDoc.redo" :disabled="!editor.can().redo()" @click="run((c) => c.redo())" />
      <!-- Les outils du tableau, quand le curseur y est -->
      <template v-if="inTable">
        <span class="pd-sep" />
        <Button :label="t.printDoc.addRow" icon="pi pi-plus" text size="small" data-testid="pd-add-row" @click="run((c) => c.addRowAfter())" />
        <Button :label="t.printDoc.addColumn" icon="pi pi-plus" text size="small" data-testid="pd-add-col" @click="run((c) => c.addColumnAfter())" />
        <Button icon="pi pi-arrows-h" text size="small" :aria-label="t.printDoc.mergeCells" :title="t.printDoc.mergeCells" @click="run((c) => c.mergeOrSplit())" />
        <Button
          :icon="tableBorders === 'none' ? 'pi pi-table' : 'pi pi-stop'"
          text
          size="small"
          :aria-label="tableBorders === 'none' ? t.printDoc.showBorders : t.printDoc.hideBorders"
          :title="tableBorders === 'none' ? t.printDoc.showBorders : t.printDoc.hideBorders"
          data-testid="pd-borders"
          @click="run((c) => c.updateAttributes('table', { borders: tableBorders === 'none' ? 'all' : 'none' }))"
        />
        <Button icon="pi pi-trash" text size="small" severity="danger" :aria-label="t.printDoc.deleteRow" :title="t.printDoc.deleteRow" @click="run((c) => c.deleteRow())" />
        <Button :label="t.printDoc.deleteTable" text size="small" severity="danger" @click="run((c) => c.deleteTable())" />
      </template>
    </div>

    <div ref="stage" class="pd-stage" :style="stageStyle" data-testid="pd-stage">
      <div ref="paper" class="pd-paper" :style="paperStyle" data-testid="pd-paper">
        <EditorContent v-if="editor" :editor="editor" class="pd-body" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.pd-editor {
  display: grid;
  gap: 0.5rem;
  min-width: 0;
}

.pd-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.1rem;
  padding: 0.25rem 0.4rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: var(--p-content-background);
  position: sticky;
  top: 0;
  z-index: 3;
}

.pd-style {
  width: 9rem;
}

.pd-size {
  width: 7rem;
}

.pd-sep {
  width: 1px;
  height: 1.4rem;
  background: var(--p-content-border-color);
  margin: 0 0.25rem;
}

.pd-color {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  cursor: pointer;
  border-radius: var(--p-border-radius-sm, 4px);
  color: var(--p-text-color);
}

.pd-color:hover {
  background: var(--desk-hover);
}

.pd-color input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.pd-color-a {
  font-weight: 700;
  border-bottom: 3px solid;
  line-height: 1.1;
}

/* Le bureau gris, la feuille blanche — blanche aussi en mode sombre : une facture ne s'imprime pas en sombre. */
.pd-stage {
  background: var(--desk-subtle);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  padding: 16px;
  overflow: hidden;
  box-sizing: border-box;
  /* Sa propre pile d'empilement : une image « devant le texte » (z-index 2) ne passe jamais par-dessus la barre
     d'outils collante (mesuré : elle la recouvrait en défilant). */
  isolation: isolate;
}

.pd-paper {
  background: var(--desk-paper);
  color: var(--desk-paper-ink);
  box-sizing: border-box;
  transform-origin: top left;
  box-shadow: 0 1px 3px color-mix(in srgb, var(--desk-paper-ink) 18%, transparent), 0 8px 24px color-mix(in srgb, var(--desk-paper-ink) 8%, transparent);
}

/* ── Le contenu : LES MÊMES règles que la feuille de l'impression (`print_document.STYLESHEET` et `printing._stylesheet`) ── */
.pd-body :deep(.pd-content) {
  position: relative;
  padding-top: 0.01mm;
  outline: none;
  min-height: var(--pd-page);
  font-family: sans-serif;
  font-size: 10pt;
  color: var(--desk-paper-ink);
  /* Un trait pointillé à chaque hauteur de page : là où le contenu passera à la page suivante. */
  background-image: repeating-linear-gradient(to bottom, transparent 0, transparent calc(var(--pd-page) - 1px), var(--desk-paper-rule) calc(var(--pd-page) - 1px), var(--desk-paper-rule) var(--pd-page));
}

.pd-body :deep(.pd-content p) {
  margin: 0 0 2mm;
}

.pd-body :deep(.pd-content h1) {
  font-size: 18pt;
  margin: 0 0 3mm;
}

.pd-body :deep(.pd-content h2) {
  font-size: 14pt;
  margin: 3mm 0 2mm;
}

.pd-body :deep(.pd-content h3) {
  font-size: 11pt;
  margin: 2mm 0 1mm;
}

.pd-body :deep(.pd-content ul),
.pd-body :deep(.pd-content ol) {
  margin: 0 0 2mm;
  padding-left: 6mm;
}

.pd-body :deep(.pd-content li p) {
  margin: 0;
}

.pd-body :deep(.pd-content blockquote) {
  margin: 0 0 2mm;
  padding-left: 3mm;
  border-left: 0.6mm solid var(--desk-paper-rule);
}

.pd-body :deep(.pd-content hr) {
  border: 0;
  border-top: 0.2mm solid var(--desk-paper-muted);
  margin: 3mm 0;
}

/* Les tableaux de mise en page (TipTap les enveloppe dans `.tableWrapper`) : sans bordure à l'impression si
   on le choisit, en pointillé ici pour qu'on les voie. Limité à `.tableWrapper` : sinon ces règles encadraient
   aussi la grille de champs et les totaux incorporés, que le PDF n'encadre pas (vu dans Chromium). */
.pd-body :deep(.pd-content .tableWrapper table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0 0 2mm;
  table-layout: fixed;
}

.pd-body :deep(.pd-content .tableWrapper td),
.pd-body :deep(.pd-content .tableWrapper th) {
  padding: 1mm 1.5mm;
  vertical-align: top;
  border: 0.2mm solid var(--desk-paper-muted);
  position: relative;
  min-width: 1em;
}

.pd-body :deep(.pd-content .tableWrapper table[data-borders='none'] td),
.pd-body :deep(.pd-content .tableWrapper table[data-borders='none'] th) {
  border: 1px dashed var(--desk-paper-rule);
}

.pd-body :deep(.pd-content .tableWrapper td p),
.pd-body :deep(.pd-content .tableWrapper th p) {
  margin: 0;
}

.pd-body :deep(.pd-content .selectedCell)::after {
  content: '';
  position: absolute;
  inset: 0;
  background: color-mix(in srgb, var(--p-primary-color) 15%, transparent);
  pointer-events: none;
}

.pd-body :deep(.pd-content .column-resize-handle) {
  position: absolute;
  right: -2px;
  top: 0;
  bottom: 0;
  width: 4px;
  background: var(--p-primary-color);
  pointer-events: none;
}

.pd-body :deep(.pd-content.resize-cursor) {
  cursor: col-resize;
}

/* Un champ inséré : la valeur du document, sur un fond léger qui dit « ceci sera remplacé ». */
.pd-body :deep(.pd-token) {
  background: color-mix(in srgb, var(--p-primary-color) 12%, transparent);
  border-radius: 3px;
  padding: 0 2px;
  box-decoration-break: clone;
}

.pd-body :deep(.pd-token.selected) {
  outline: 2px solid var(--p-primary-color);
}

/* Les images */
.pd-body :deep(.pd-img) {
  display: inline-block;
  position: relative;
  vertical-align: bottom;
  line-height: 0;
  max-width: 100%;
}

.pd-body :deep(.pd-img img) {
  width: 100%;
  height: auto;
  display: block;
}

.pd-body :deep(.pd-img.wrap-left) {
  float: left;
  margin: 0 3mm 2mm 0;
}

.pd-body :deep(.pd-img.wrap-right) {
  float: right;
  margin: 0 0 2mm 3mm;
}

.pd-body :deep(.pd-img.wrap-front) {
  position: absolute;
  max-width: none;
  z-index: 2;
  cursor: move;
}

.pd-body :deep(.pd-img.selected) {
  outline: 2px solid var(--p-primary-color);
  outline-offset: 1px;
}

.pd-body :deep(.pd-img-missing) {
  display: inline-block;
  line-height: 1.4;
  padding: 2mm;
  font-size: 8pt;
  color: var(--desk-paper-muted);
  border: 1px dashed var(--desk-paper-rule);
}

.pd-body :deep(.pd-img-handle) {
  position: absolute;
  right: -6px;
  bottom: -6px;
  width: 12px;
  height: 12px;
  border-radius: 2px;
  background: var(--p-primary-color);
  border: 2px solid var(--desk-paper);
  cursor: nwse-resize;
  touch-action: none;
}

.pd-body :deep(.pd-img-size) {
  position: absolute;
  left: 0;
  top: -1.6rem;
  white-space: nowrap;
  line-height: 1.4;
  font-size: 0.7rem;
  padding: 0 0.3rem;
  border-radius: 3px;
  background: var(--p-primary-color);
  color: var(--p-primary-contrast-color);
}

/* Les blocs incorporés : les classes du rendu du serveur (`printing._stylesheet`) */
.pd-body :deep(.pd-block) {
  position: relative;
  margin: 0 0 2mm;
  border: 1px dashed transparent;
  border-radius: 3px;
}

.pd-body :deep(.pd-block:hover) {
  border-color: var(--desk-paper-rule);
}

.pd-body :deep(.pd-block.selected) {
  border: 1px solid var(--p-primary-color);
}

.pd-body :deep(.pd-block-grip) {
  position: absolute;
  top: -0.75rem;
  right: 0.25rem;
  display: none;
  gap: 0.3rem;
  align-items: center;
  font-size: 0.7rem;
  padding: 0 0.4rem;
  border-radius: 999px;
  background: var(--p-content-background);
  color: var(--p-text-color);
  border: 1px solid var(--p-content-border-color);
  cursor: grab;
  z-index: 2;
}

.pd-body :deep(.pd-block:hover .pd-block-grip),
.pd-body :deep(.pd-block.selected .pd-block-grip) {
  display: inline-flex;
}

.pd-body :deep(.pd-block h2) {
  font-size: 11pt;
  margin: 0 0 2mm;
}

.pd-body :deep(.pd-block table) {
  border-collapse: collapse;
  width: 100%;
}

.pd-body :deep(.pd-block table.lines.bordered th),
.pd-body :deep(.pd-block table.lines.bordered td) {
  border: 0.2mm solid var(--desk-paper-muted);
}

.pd-body :deep(.pd-block table.lines.ruled th),
.pd-body :deep(.pd-block table.lines.ruled td) {
  border-bottom: 0.2mm solid var(--desk-paper-rule);
}

.pd-body :deep(.pd-block table.lines.bordered th),
.pd-body :deep(.pd-block table.lines.ruled th) {
  background: var(--desk-paper-head);
}

.pd-body :deep(.pd-block table.lines th),
.pd-body :deep(.pd-block table.lines td) {
  padding: 1.2mm 2mm;
  text-align: left;
}

.pd-body :deep(.pd-block table.lines.compact th),
.pd-body :deep(.pd-block table.lines.compact td) {
  padding: 0.6mm 1.5mm;
  font-size: 9pt;
}

.pd-body :deep(.pd-block table.totaux) {
  margin: 0 0 0 auto;
  width: 70mm;
}

.pd-body :deep(.pd-block table.totaux td) {
  padding: 1mm 0;
}

.pd-body :deep(.pd-block table.totaux tr.grand td) {
  font-weight: 700;
  border-top: 0.3mm solid var(--desk-paper-ink);
}

.pd-body :deep(.pd-block table.fields td) {
  padding: 1mm 2mm 1mm 0;
  vertical-align: top;
}

.pd-body :deep(.pd-block .k) {
  font-size: 8pt;
  color: var(--desk-paper-muted);
}

.pd-body :deep(.pd-block .right),
.pd-body :deep(.pd-block td.r) {
  text-align: right;
}

.pd-body :deep(.pd-block .center) {
  text-align: center;
}

.pd-body :deep(.pd-hint) {
  margin: 0;
  padding: 2mm;
  font-size: 8pt;
  font-style: italic;
  color: var(--desk-paper-muted);
  border: 1px dashed var(--desk-paper-rule);
}

.pd-body :deep(.pd-break) {
  margin: 2mm 0;
  border-top: 2px dashed var(--desk-paper-rule);
  text-align: center;
  font-size: 8pt;
  color: var(--desk-paper-muted);
  cursor: grab;
}

.pd-body :deep(.pd-break.selected) {
  border-top-color: var(--p-primary-color);
}

.pd-body :deep(.ProseMirror-selectednode) {
  outline: none;
}
</style>
