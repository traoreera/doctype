<script setup lang="ts">
import { NodeSelection, TextSelection } from '@tiptap/pm/state'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import Message from 'primevue/message'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import PagePanel from '@/components/printdoc/PagePanel.vue'
import TablePanel from '@/components/printdoc/TablePanel.vue'
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import BlockPanel from '@/components/printbuilder/BlockPanel.vue'
import PrintDocEditor, { type Selected } from '@/components/printdoc/PrintDocEditor.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { downloadBytes } from '@/lib/download'
import type { TableCommand, TableStyle } from '@/lib/tablestyles'
import { t } from '@/i18n'
import { PX_PER_MM, blocksToContent, newEmbedded, round1, tokenHtml } from '@/lib/printdoc'
import { docContext } from '@/components/printdoc/extensions'
import {
  addColumn,
  fromTemplate,
  insertField,
  moveColumn,
  moveFieldInBlock,
  printableFields,
  rawBlock,
  removeColumn,
  setColumnWidth,
  setGroupBy,
  toggleField,
  toggleSubtotal,
  updateBlock,
  updateColumn,
  type Block,
  type Draft,
  type Letterhead,
} from '@/lib/printedit'
import { useServices } from '@/lib/services'
import type { DocTypeMeta } from '@/lib/types'
import { useFiles } from '@/stores/files'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'

/**
 * « Personnaliser l'impression » (`process/impression.md` §12) : un éditeur de document FAÇON WORD. On écrit et
 * met en forme la pièce sur une feuille à l'échelle ; on y insère les VALEURS du document (cliquées ou glissées
 * depuis le panneau « Insérer »), le tableau des lignes, les totaux, des images qu'on place librement, des
 * tableaux de mise en page. Le panneau de droite règle ce qu'on a cliqué (une image, un tableau des lignes).
 * La bascule « Aperçu imprimé » montre ce que le SERVEUR imprime, et c'est lui qui fait foi.
 *
 * Le panneau de droite règle ce qui a le curseur ou la sélection : une image, un tableau des lignes, un tableau
 * de mise en page (style et outils, `TablePanel`) — et sinon la PAGE (format, marges, fond PDF : `PagePanel`).
 * Allégé le 4 nov. : plus de barre de page au-dessus de la feuille, une barre de mise en forme réduite.
 *
 * Un gabarit à BLOCS (celui d'un plugin, ou le fabriqué) s'ouvre CONVERTI en document : on part de ce qui
 * s'imprime aujourd'hui, et l'enregistrement le remplace par un document libre (le format livré ne change pas).
 *
 * Droits : tout lecteur du type ouvre la page ; seul qui peut modifier TOUS ses documents enregistre, parce
 * qu'un gabarit rend des documents que son éditeur n'a pas forcément le droit de lire.
 */
const props = defineProps<{ doctype: string }>()
const { api } = useServices()
const notify = useNotify()
const metaStore = useMeta()
const links = useLinks()
const files = useFiles()

const meta = ref<DocTypeMeta | null>(null)
/** Le nom, le libellé, la page et le papier à en-tête du gabarit (ses blocs éventuels ne servent qu'à l'ouverture). */
const draft = ref<Draft>(fromTemplate(null))
const content = ref('')
const source = ref<string>('')
const editable = ref(false)
const initial = ref('')
const loading = ref(true)
const failure = ref('')
const problem = ref<string | null>(null)
const saving = ref(false)
const example = ref<string | null>(null)
const exampleData = ref<Record<string, unknown> | null>(null)
const mode = ref<'layout' | 'preview'>('layout')
const modes = [
  { value: 'layout', label: t.printBuilder.layoutMode },
  { value: 'preview', label: t.printBuilder.previewMode },
]
const preview = ref('')
const previewing = ref(false)
const previewFailure = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const docEditor = shallowRef<InstanceType<typeof PrintDocEditor> | null>(null)
const selected = ref<Selected | null>(null)
/** Le style du tableau de mise en page où est le curseur, ou `null` hors d'un tableau. */
const tableStyle = ref<TableStyle | null>(null)
const backgroundBusy = ref(false)
/**
 * La PAGE demandée explicitement (« Page et fond PDF ») alors que quelque chose est choisi : à l'ouverture, TipTap
 * sélectionne souvent le dernier bloc (le tableau des totaux), et la page n'était plus atteignable qu'en cliquant
 * du texte. Le prochain choix sur la feuille rend la main au panneau de ce choix.
 */
const pageOpen = ref(false)
const choice = computed(() => `${selected.value?.type ?? ''}:${selected.value?.pos ?? ''}:${tableStyle.value ?? ''}`)
watch(choice, () => (pageOpen.value = false))
const somethingChosen = computed(() => !!imageAttrs.value || !!embedded.value || !!tableStyle.value)
const pdfPreviewing = ref(false)
/** Le bloc incorporé choisi, dans la forme d'édition de `printedit` (clés de colonnes stables le temps du choix). */
const blockDraft = ref<Draft | null>(null)
const chosenColumn = ref<string | null>(null)
let ticket = 0

const label = computed(() => meta.value?.label || props.doctype)
const payload = computed(() => {
  const out: Record<string, unknown> = { name: draft.value.name, page: { ...draft.value.page }, content: content.value }
  if (draft.value.label) out.label = draft.value.label
  const lh = draft.value.letterhead
  if (lh && (lh.name || lh.pdf)) out.letterhead = Object.fromEntries(Object.entries(lh).filter(([, v]) => v !== null && v !== undefined))
  return out
})
const dirty = computed(() => !!initial.value && JSON.stringify(payload.value) !== initial.value)
const empty = computed(() => !content.value.replace(/<p><\/p>/g, '').trim())
const values = computed(() => {
  const m = meta.value
  if (!m) return []
  return [
    { token: 'name', label: t.printDoc.docNumber },
    { token: 'doctype', label: t.printDoc.docType },
    ...printableFields(m).filter((f) => !f.hidden).map((f) => ({ token: `doc.${f.fieldname}`, label: f.label || f.fieldname })),
  ]
})
const tables = computed(() => meta.value?.fields.filter((f) => f.fieldtype === 'Table') ?? [])

onMounted(load)
watch(() => props.doctype, load)
let timer: ReturnType<typeof setTimeout> | undefined
watch(payload, () => {
  clearTimeout(timer)
  timer = setTimeout(() => void refreshPreview(), 500)
})

async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  selected.value = null
  try {
    meta.value = await metaStore.refresh(props.doctype)
    const current = await api.printOverride(props.doctype)
    draft.value = fromTemplate(current.template)
    const raw = current.template as { content?: string; blocks?: Record<string, unknown>[] }
    content.value = typeof raw.content === 'string' ? raw.content : blocksToContent(raw.blocks ?? [], meta.value)
    const background = draft.value.letterhead?.pdf
    void files.resolve([...[...content.value.matchAll(/data-file="([0-9a-f]{32})"/g)].map((m) => m[1]!), ...(background ? [background] : [])])
    source.value = current.source
    editable.value = current.editable
    initial.value = JSON.stringify(payload.value)
    example.value = (await api.printExample(props.doctype)).name
    await loadExample()
    await refreshPreview()
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    loading.value = false
  }
}

/** Le document d'exemple et les TITRES de ses liens : la feuille montre « Durand », pas « CUST-0001 ». */
async function loadExample(): Promise<void> {
  exampleData.value = null
  if (!example.value || !meta.value) return
  try {
    const record = await api.get(props.doctype, example.value)
    exampleData.value = record.data
    const wanted = new Map<string, Set<string>>()
    const want = (target: unknown, value: unknown) => {
      if (typeof target !== 'string' || typeof value !== 'string' || !value) return
      if (!wanted.has(target)) wanted.set(target, new Set())
      wanted.get(target)!.add(value)
    }
    for (const f of meta.value.fields) {
      if (f.fieldtype === 'Link') want(f.options, record.data[f.fieldname])
      if (f.fieldtype === 'Table' && typeof f.options === 'string' && Array.isArray(record.data[f.fieldname])) {
        for (const c of meta.value.children?.[f.options]?.fields ?? []) {
          if (c.fieldtype !== 'Link') continue
          for (const row of record.data[f.fieldname] as Record<string, unknown>[]) want(c.options, row[c.fieldname])
        }
      }
    }
    await Promise.all([...wanted].map(([target, names]) => links.resolve(target, names)))
  } catch {
    exampleData.value = null
  }
}

async function refreshPreview(): Promise<void> {
  if (!example.value || empty.value) return
  const mine = ++ticket
  previewing.value = true
  previewFailure.value = ''
  try {
    const out = await api.printPreview(props.doctype, example.value, payload.value)
    if (mine === ticket) preview.value = out.html
  } catch (e) {
    if (mine === ticket) previewFailure.value = describeError(e)
  } finally {
    if (mine === ticket) previewing.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  problem.value = null
  try {
    await api.savePrintOverride(props.doctype, payload.value)
    initial.value = JSON.stringify(payload.value)
    notify.success(t.printBuilder.savedToast)
    source.value = 'organisation'
  } catch (e) {
    problem.value = describeError(e) // le refus du serveur reste sous les yeux, rien n'est perdu
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  saving.value = true
  problem.value = null
  try {
    await api.removePrintOverride(props.doctype, draft.value.name)
    await load()
    notify.success(t.printBuilder.resetToast)
  } catch (e) {
    problem.value = describeError(e)
  } finally {
    saving.value = false
  }
}

const setPage = (p: Partial<Draft['page']>) => (draft.value = { ...draft.value, page: { ...draft.value.page, ...p } })
const setLetterhead = (lh: Letterhead | null) => (draft.value = { ...draft.value, letterhead: lh })

// ── Le fond PDF (§5) : déposé pour CE type, imprimé sous le contenu ──
const backgroundName = computed(() => files.info(draft.value.letterhead?.pdf)?.filename ?? null)
async function uploadBackground(file: File): Promise<void> {
  backgroundBusy.value = true
  try {
    const made = await api.printBackground(props.doctype, file)
    await files.resolve([made.id], { force: true })
    const was = draft.value.letterhead
    // Un papier à en-tête est un `Letter Head` OU un fond PDF (le serveur refuse les deux) : le fond remplace.
    setLetterhead({ pdf: made.id, pages: was?.pages ?? 'all', offset_top: was?.offset_top ?? 0, offset_bottom: was?.offset_bottom ?? 0 })
  } catch (e) {
    notify.error(describeError(e))
  } finally {
    backgroundBusy.value = false
  }
}
/** Le fond n'existe qu'au PDF : on télécharge le PDF du BROUILLON, rendu par le serveur sur le document d'exemple. */
async function previewPdf(): Promise<void> {
  if (!example.value) return
  pdfPreviewing.value = true
  try {
    const file = await api.printPreviewPdf(props.doctype, example.value, payload.value)
    downloadBytes(file.filename, file.bytes, 'application/pdf')
  } catch (e) {
    notify.error(describeError(e))
  } finally {
    pdfPreviewing.value = false
  }
}

// ── Le tableau de mise en page sous le curseur ──
const setTableStyle = (style: TableStyle) => editor()?.chain().focus().updateAttributes('table', { tableStyle: style }).run()
const tableCommand = (name: TableCommand) => editor()?.chain().focus()[name]().run()

// ── Insérer ──
const editor = () => docEditor.value?.editor ?? null
/** Le curseur a-t-il été posé par l'utilisateur ? Sinon, un élément inséré va à la FIN du document, pas avant le titre. */
let placed = false
watch(docEditor, (c) => c?.editor?.on('focus', () => (placed = true)))
/**
 * Insère au curseur. Un bloc ou une image SÉLECTIONNÉS ne sont jamais remplacés (le comportement de base d'un
 * éditeur, qui surprend ici : cliquer « Totaux » faisait disparaître le tableau des lignes qu'on venait de régler) —
 * on insère juste après. Un TEXTE sélectionné non plus : on insère après lui, il ne disparaît pas.
 */
function insert(html: string | Record<string, unknown>): void {
  const e = editor()
  if (!e || !editable.value) return
  const sel = e.state.selection
  if (sel instanceof NodeSelection || (!sel.empty && placed)) e.chain().focus().insertContentAt(sel.to, html).run()
  else if (!placed) e.chain().focus().insertContentAt(e.state.doc.content.size, html).run()
  else e.chain().focus().insertContent(html).run()
}
const insertValue = (token: string) => meta.value && insert(tokenHtml(token, meta.value) + ' ')
/** Un champ se GLISSE aussi dans le texte : l'éditeur reçoit le HTML du champ inséré là où on le lâche. */
function valueDrag(e: DragEvent, token: string): void {
  if (!e.dataTransfer || !meta.value || !editable.value) return
  e.dataTransfer.effectAllowed = 'copy'
  e.dataTransfer.setData('text/html', tokenHtml(token, meta.value))
  e.dataTransfer.setData('text/plain', values.value.find((v) => v.token === token)?.label ?? token)
}
function insertBlock(type: 'table' | 'totals' | 'fields', tableField?: string): void {
  if (!meta.value) return
  insert({ type: 'embeddedBlock', attrs: { block: newEmbedded(type, meta.value, tableField) } })
}
/**
 * Un tableau de MISE EN PAGE (le logo à gauche, l'adresse à droite), deux sur deux, né SANS bordure ; la galerie
 * l'habille. Inséré par `insert`, comme tout le reste : `insertTable` de TipTap REMPLAÇAIT la sélection (le texte
 * qu'on venait de mettre en gras disparaissait, vu dans Chromium).
 */
function insertTable(): void {
  const e = editor()
  if (!e) return
  const known = new Set<number>()
  e.state.doc.descendants((node, pos) => void (node.type.name === 'table' && known.add(pos)))
  const cell = { type: 'tableCell', content: [{ type: 'paragraph' }] }
  const row = { type: 'tableRow', content: [cell, cell] }
  insert({ type: 'table', attrs: { tableStyle: 'plain' }, content: [row, row] })
  // Le curseur dans la PREMIÈRE cellule du nouveau tableau, comme `insertTable` : on y écrit, et le panneau de droite
  // montre aussitôt son style et ses outils. Le nouveau est le tableau dont la position n'existait pas avant.
  let made: number | null = null
  e.state.doc.descendants((node, pos) => {
    if (made === null && node.type.name === 'table' && !known.has(pos)) made = pos
    return made === null
  })
  if (made !== null) {
    const at: number = made
    e.chain().focus().command(({ tr }) => (tr.setSelection(TextSelection.near(tr.doc.resolve(at + 1))), true)).run()
  }
}
const insertBreak = () => insert({ type: 'pageBreak' })

async function uploadImage(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploading.value = true
  try {
    const made = await api.printImage(props.doctype, file)
    await files.resolve([made.id], { force: true })
    insert({ type: 'printImage', attrs: { file: made.id, wrap: 'inline', width: 40, left: 0, top: 0 } })
  } catch (e) {
    notify.error(describeError(e))
  } finally {
    uploading.value = false
  }
}

// ── Ce qu'on a cliqué : une image, un bloc incorporé ──
function onSelect(node: Selected | null): void {
  const was = selected.value
  selected.value = node
  if (node?.type !== 'embeddedBlock') {
    blockDraft.value = null
    chosenColumn.value = null
    return
  }
  // Le même bloc qu'avant (une transaction l'a seulement mis à jour) : on garde la forme d'édition et ses clés.
  const same = was?.type === 'embeddedBlock' && was.pos === node.pos && blockDraft.value
  if (same && JSON.stringify(rawBlock(blockDraft.value!.blocks[0]!)) === JSON.stringify(node.attrs.block)) return
  blockDraft.value = fromTemplate({ blocks: [node.attrs.block] })
  if (!same) chosenColumn.value = null
}
function setNodeAttrs(attrs: Record<string, unknown>): void {
  const e = editor()
  const node = selected.value
  if (!e || !node) return
  e.chain()
    .command(({ tr }) => {
      const current = tr.doc.nodeAt(node.pos)
      if (!current) return false
      tr.setNodeMarkup(node.pos, undefined, { ...current.attrs, ...attrs })
      tr.setSelection(NodeSelection.create(tr.doc, node.pos))
      return true
    })
    .run()
}
const imageAttrs = computed(() => (selected.value?.type === 'printImage' ? (selected.value.attrs as { wrap: string; width: number; left: number; top: number }) : null))
const wraps = computed(() => [
  { value: 'inline', label: t.printDoc.wraps.inline },
  { value: 'left', label: t.printDoc.wraps.left },
  { value: 'right', label: t.printDoc.wraps.right },
  { value: 'front', label: t.printDoc.wraps.front },
])
/**
 * Changer l'habillage d'une image. Passée « devant le texte », elle reste OÙ ELLE ÉTAIT à l'écran : sa position
 * se calcule depuis sa place dans le texte (sinon elle sautait au coin de la page, souvent hors de la vue).
 */
function setWrap(wrap: string): void {
  const e = editor()
  const node = selected.value
  if (!e || !node) return
  if (wrap === 'front' && node.attrs.wrap !== 'front') {
    const dom = e.view.nodeDOM(node.pos) as HTMLElement | null
    if (dom) {
      const at = dom.getBoundingClientRect()
      const root = e.view.dom.getBoundingClientRect()
      const perMm = PX_PER_MM * (docContext(e).scale || 1)
      setNodeAttrs({ wrap, left: round1((at.left - root.left) / perMm), top: round1((at.top - root.top) / perMm) })
      return
    }
  }
  setNodeAttrs({ wrap })
}
function removeSelected(): void {
  const e = editor()
  if (e && selected.value) e.chain().focus().deleteSelection().run()
}

/** Une opération de `printedit` sur le bloc incorporé choisi, réécrite dans le document. */
function onBlock(fn: (d: Draft, key: string) => Draft): void {
  const d = blockDraft.value
  const b = d?.blocks[0]
  if (!d || !b) return
  const next = fn(d, b.key)
  blockDraft.value = next
  setNodeAttrs({ block: rawBlock(next.blocks[0]!) })
}
const embedded = computed<Block | null>(() => blockDraft.value?.blocks[0] ?? null)
</script>

<template>
  <section class="pb-page">
    <header class="pb-head">
      <div class="pb-head-text">
        <h1 data-testid="pb-title">{{ t.printBuilder.title(label) }}</h1>
        <p class="desk-muted">
          {{ t.printBuilder.intro }}
          <span data-testid="pb-source">({{ t.printBuilder.source(source) }})</span>
        </p>
      </div>
      <div class="pb-actions">
        <SelectButton v-if="meta" v-model="mode" :options="modes" option-label="label" option-value="value" :allow-empty="false" size="small" data-testid="pb-mode" />
        <RouterLink :to="`/app/${encodeURIComponent(doctype)}`"><Button :label="t.printBuilder.backToList" icon="pi pi-arrow-left" text severity="secondary" size="small" /></RouterLink>
        <Button
          v-if="source === 'organisation'"
          :label="t.printBuilder.reset"
          icon="pi pi-undo"
          text
          severity="secondary"
          size="small"
          :disabled="!editable || saving"
          data-testid="pb-reset"
          @click="reset"
        />
        <Button
          :label="t.printBuilder.save"
          icon="pi pi-check"
          size="small"
          :loading="saving"
          :disabled="!editable || !dirty || empty"
          data-testid="pb-save"
          @click="save"
        />
      </div>
    </header>

    <Message v-if="failure" severity="error" data-testid="pb-error">{{ failure }}</Message>
    <Message v-else-if="!editable && !loading" severity="info" size="small" data-testid="pb-readonly">{{ t.printBuilder.readOnly }}</Message>
    <Message v-if="problem" severity="error" data-testid="pb-refused">{{ problem }}</Message>

    <div v-if="meta" class="pb-grid">
      <!-- Insérer : cliquer place au curseur ; une valeur se glisse aussi dans le texte -->
      <aside class="pb-insert" :aria-label="t.printDoc.insert">
        <h2 class="pb-panel-title">{{ t.printDoc.insert }}</h2>
        <h3 class="pb-group">{{ t.printDoc.elements }}</h3>
        <button type="button" class="pb-item" :disabled="!editable || uploading" data-testid="pd-insert-image" @click="fileInput?.click()">
          <i class="pi pi-image" aria-hidden="true" />{{ uploading ? t.printDoc.uploading : t.printDoc.image }}
        </button>
        <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/gif,image/webp" class="pb-file" data-testid="pd-image-file" @change="uploadImage" />
        <button v-for="tb in tables" :key="tb.fieldname" type="button" class="pb-item" :disabled="!editable" :data-testid="`pd-insert-lines-${tb.fieldname}`" @click="insertBlock('table', tb.fieldname)">
          <i class="pi pi-table" aria-hidden="true" />{{ t.printDoc.lines(tb.label || tb.fieldname) }}
        </button>
        <button type="button" class="pb-item" :disabled="!editable" data-testid="pd-insert-totals" @click="insertBlock('totals')"><i class="pi pi-calculator" aria-hidden="true" />{{ t.printDoc.totals }}</button>
        <button type="button" class="pb-item" :disabled="!editable" data-testid="pd-insert-fields" @click="insertBlock('fields')"><i class="pi pi-th-large" aria-hidden="true" />{{ t.printDoc.fieldsGrid }}</button>
        <button type="button" class="pb-item" :disabled="!editable" data-testid="pd-insert-table" @click="insertTable"><i class="pi pi-objects-column" aria-hidden="true" />{{ t.printDoc.layoutTable }}</button>
        <button type="button" class="pb-item" :disabled="!editable" data-testid="pd-insert-break" @click="insertBreak"><i class="pi pi-minus" aria-hidden="true" />{{ t.printDoc.pageBreak }}</button>
        <h3 class="pb-group">{{ t.printDoc.values }}</h3>
        <p class="desk-muted pb-hint">{{ t.printDoc.valuesHint }}</p>
        <button
          v-for="v in values"
          :key="v.token"
          type="button"
          class="pb-item pb-value"
          :draggable="editable"
          :disabled="!editable"
          :data-testid="`pd-value-${v.token}`"
          @dragstart="valueDrag($event, v.token)"
          @click="insertValue(v.token)"
        >
          <i class="pi pi-tag" aria-hidden="true" />{{ v.label }}
        </button>
      </aside>

      <div class="pb-center">
        <PrintDocEditor
          v-show="mode === 'layout'"
          ref="docEditor"
          v-model="content"
          :page="draft.page"
          :meta="meta"
          :example="exampleData"
          :name="example"
          :editable="editable"
          :link-title="links.title"
          :image-url="files.url"
          @select="onSelect"
          @table="(s) => (tableStyle = s)"
        />
        <div v-show="mode === 'preview'" class="pb-preview">
          <p v-if="!example" class="desk-muted" data-testid="pb-no-example">{{ t.printBuilder.noPreview }}</p>
          <Message v-else-if="previewFailure" severity="warn" size="small" data-testid="pb-preview-error">{{ previewFailure }}</Message>
          <span v-if="previewing" class="desk-muted" data-testid="pb-previewing">{{ t.printBuilder.previewing }}</span>
          <!-- Aucun script dans ce cadre ; `allow-same-origin` seulement pour que la page atteigne le cadre (mesuré). -->
          <iframe v-if="example && preview" class="pb-frame" sandbox="allow-same-origin allow-modals" :title="t.printBuilder.preview" :srcdoc="preview" data-testid="pb-frame" />
        </div>
        <p v-if="mode === 'layout' && !example" class="desk-muted pb-hint" data-testid="pb-no-example">{{ t.printBuilder.noExample }}</p>
      </div>

      <!-- Les propriétés de ce qu'on a cliqué -->
      <aside class="pb-props" :aria-label="t.printBuilder.properties">
        <Button
          v-if="somethingChosen && !pageOpen"
          :label="t.printBuilder.pageAndBackground"
          icon="pi pi-file"
          text
          size="small"
          class="pb-to-page"
          data-testid="pb-open-page"
          @click="pageOpen = true"
        />
        <template v-if="pageOpen" />
        <template v-else-if="imageAttrs">
          <h2 class="pb-panel-title" data-testid="pd-props-title">{{ t.printDoc.image }}</h2>
          <label class="pb-field">
            <span>{{ t.printDoc.wrap }}</span>
            <Select :model-value="imageAttrs.wrap" :options="wraps" option-label="label" option-value="value" :disabled="!editable" size="small" fluid data-testid="pd-wrap" @update:model-value="setWrap($event)" />
          </label>
          <p v-if="imageAttrs.wrap === 'front'" class="desk-muted pb-hint">{{ t.printDoc.frontHint }}</p>
          <label class="pb-field">
            <span>{{ t.printDoc.width }}</span>
            <InputNumber :model-value="imageAttrs.width" :min="5" :max="500" :max-fraction-digits="1" :disabled="!editable" size="small" fluid data-testid="pd-width" @update:model-value="setNodeAttrs({ width: $event ?? 40 })" />
          </label>
          <div v-if="imageAttrs.wrap === 'front'" class="pb-pair">
            <label class="pb-field">
              <span>{{ t.printDoc.left }}</span>
              <InputNumber :model-value="imageAttrs.left" :min="-50" :max="500" :max-fraction-digits="1" :disabled="!editable" size="small" fluid data-testid="pd-left" @update:model-value="setNodeAttrs({ left: $event ?? 0 })" />
            </label>
            <label class="pb-field">
              <span>{{ t.printDoc.top }}</span>
              <InputNumber :model-value="imageAttrs.top" :min="-50" :max="500" :max-fraction-digits="1" :disabled="!editable" size="small" fluid data-testid="pd-top" @update:model-value="setNodeAttrs({ top: $event ?? 0 })" />
            </label>
          </div>
          <Button :label="t.printDoc.removeImage" icon="pi pi-trash" text severity="danger" size="small" :disabled="!editable" data-testid="pd-remove" @click="removeSelected" />
        </template>

        <template v-else-if="embedded">
          <BlockPanel
            :block="embedded"
            :meta="meta"
            :editable="editable"
            :chosen-column="chosenColumn"
            @update="(p) => onBlock((d, k) => updateBlock(d, k, p))"
            @select-column="(c) => (chosenColumn = c)"
            @toggle-field="(f) => onBlock((d, k) => toggleField(d, k, f))"
            @insert-field="(f) => onBlock((d, k) => insertField(d, k, f, embedded?.fields?.length ?? 0))"
            @move-field="(f, how) => onBlock((d, k) => moveFieldInBlock(d, k, f, how))"
            @add-column="(f) => onBlock((d, k) => addColumn(d, k, f))"
            @remove-column="(c) => onBlock((d, k) => removeColumn(d, k, c))"
            @move-column="(c, how) => onBlock((d, k) => moveColumn(d, k, c, how))"
            @update-column="(c, p) => onBlock((d, k) => updateColumn(d, k, c, p))"
            @set-column-width="(c, w) => onBlock((d, k) => setColumnWidth(d, k, c, w))"
            @set-group-by="(f) => onBlock((d, k) => setGroupBy(d, k, f))"
            @toggle-subtotal="(f) => onBlock((d, k) => toggleSubtotal(d, k, f))"
          />
          <Button :label="t.printDoc.removeBlock" icon="pi pi-trash" text severity="danger" size="small" :disabled="!editable" data-testid="pd-remove" @click="removeSelected" />
        </template>

        <TablePanel v-else-if="tableStyle" :table-style="tableStyle" :editable="editable" @style="setTableStyle" @command="tableCommand" />

        <PagePanel
          v-if="pageOpen || !somethingChosen"
          :page="draft.page"
          :letterhead="draft.letterhead"
          :editable="editable"
          :background-name="backgroundName"
          :uploading="backgroundBusy"
          :can-preview="!!example && !empty"
          :previewing="pdfPreviewing"
          @page="setPage"
          @letterhead="setLetterhead"
          @upload="uploadBackground"
          @preview="previewPdf"
        />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.pb-page {
  display: grid;
  gap: 0.75rem;
}

.pb-head {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: flex-start;
  justify-content: space-between;
}

.pb-head-text {
  display: grid;
  gap: 0.25rem;
  min-width: 0;
}

.pb-head h1 {
  margin: 0;
  font-size: 1.2rem;
}

.pb-head p {
  margin: 0;
}

.pb-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.pb-grid {
  display: grid;
  grid-template-columns: 12rem minmax(0, 1fr) 18rem;
  gap: 1rem;
  align-items: start;
}

.pb-grid > * {
  min-width: 0;
}

@media (max-width: 1100px) {
  .pb-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .pb-insert {
    position: static !important;
    max-height: none !important;
    display: flex !important;
    flex-wrap: wrap;
  }

  .pb-insert .pb-panel-title,
  .pb-insert .pb-group,
  .pb-insert .pb-hint {
    width: 100%;
  }

  .pb-props {
    position: static !important;
    max-height: none !important;
  }
}

.pb-panel-title {
  font-size: 0.9rem;
  margin: 0 0 0.25rem;
}

.pb-group {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--p-text-muted-color);
  margin: 0.5rem 0 0.1rem;
}

.pb-hint {
  font-size: 0.75rem;
  margin: 0 0 0.25rem;
}

.pb-insert,
.pb-props {
  display: grid;
  gap: 0.35rem;
  align-content: start;
  position: sticky;
  top: 0.5rem;
  max-height: calc(100vh - 6rem);
  overflow: auto;
}

.pb-props {
  gap: 0.6rem;
  padding: 0.75rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: var(--p-content-background);
}

.pb-to-page {
  justify-self: start;
}

.pb-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.1rem;
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-sm, 4px);
  background: var(--p-content-background);
  color: var(--p-text-color);
  font: inherit;
  font-size: 0.85rem;
  text-align: left;
  cursor: pointer;
}

.pb-item:hover:not(:disabled) {
  border-color: var(--p-primary-color);
}

.pb-item:disabled {
  cursor: default;
  opacity: 0.6;
}

.pb-item i {
  color: var(--p-primary-color);
}

.pb-value {
  cursor: grab;
}

.pb-file {
  display: none;
}

.pb-field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.pb-field > span {
  color: var(--p-text-muted-color);
}

.pb-pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.5rem;
}

.pb-center,
.pb-preview {
  display: grid;
  gap: 0.5rem;
}

.pb-frame {
  width: 100%;
  height: calc(100vh - 14rem);
  min-height: 28rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: var(--desk-paper);
}
</style>
