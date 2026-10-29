<script setup lang="ts">
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import Message from 'primevue/message'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import BlockPanel from '@/components/printbuilder/BlockPanel.vue'
import PrintCanvas from '@/components/printbuilder/PrintCanvas.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import {
  DRAG_KIND,
  KIND_LABELS,
  addBlock,
  addColumn,
  draftProblems,
  fromTemplate,
  insertField,
  moveBlockToSlot,
  moveColumn,
  moveColumnToSlot,
  moveFieldInBlock,
  moveFieldTo,
  newBlock,
  nudgeBlock,
  removeBlock,
  removeColumn,
  setColumnWidth,
  setGroupBy,
  toTemplate,
  toggleField,
  toggleSubtotal,
  totalFields,
  updateBlock,
  updateColumn,
  type Block,
  type BlockKind,
  type Draft,
} from '@/lib/printedit'
import { useServices } from '@/lib/services'
import type { DocTypeMeta } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'

/**
 * « Personnaliser l'impression » (`process/impression.md` §10 ter) : un éditeur de FACTURE. Au centre, la
 * feuille (`PrintCanvas`) dessine les blocs avec les valeurs d'un document réel ; on y glisse les blocs de la
 * palette, on y déplace blocs, champs et colonnes, on tire la largeur d'une colonne. À droite, les propriétés du
 * bloc choisi. La bascule « Aperçu imprimé » montre ce que le SERVEUR imprime vraiment.
 *
 * L'aperçu imprimé vient du SERVEUR (`POST /print-preview`), qui applique au brouillon les mêmes contrôles qu'à
 * un enregistrement — un gabarit non enregistré n'est donc pas une porte. La feuille, elle, n'est qu'un rendu
 * d'édition : quand les deux diffèrent, l'aperçu imprimé fait foi.
 *
 * Une pile, pas une toile : les blocs se suivent. Ce n'est pas une limite de l'éditeur mais du rendu (le moteur
 * d'impression ne connaît pas CSS grid), et c'est elle qui permet de garantir ce qui sortira.
 *
 * Droits : tout lecteur du type ouvre la page ; seul qui peut modifier TOUS ses documents enregistre, parce
 * qu'un gabarit rend des documents que son éditeur n'a pas forcément le droit de lire. La page le DIT et grise
 * le reste, au lieu de laisser découvrir un refus.
 */
const props = defineProps<{ doctype: string }>()
const { api } = useServices()
const notify = useNotify()
const metaStore = useMeta()

const links = useLinks()

const meta = ref<DocTypeMeta | null>(null)
const draft = ref<Draft>(fromTemplate(null))
const source = ref<string>('')
const editable = ref(false)
const initial = ref('')
const loading = ref(true)
const failure = ref('')
const problem = ref<string | null>(null)
const saving = ref(false)
const chosen = ref<string | null>(null)
const example = ref<string | null>(null)
/** Les valeurs du document d'exemple, que la feuille dessine. */
const exampleData = ref<Record<string, unknown> | null>(null)
/** La colonne de tableau choisie sur la feuille (ses réglages s'ouvrent dans le panneau). */
const chosenColumn = ref<string | null>(null)
/** « Mise en page » (la feuille qu'on édite) ou « Aperçu imprimé » (le rendu du serveur). */
const mode = ref<'layout' | 'preview'>('layout')
const modes = [
  { value: 'layout', label: t.printBuilder.layoutMode },
  { value: 'preview', label: t.printBuilder.previewMode },
]
const preview = ref<string>('')
const previewing = ref(false)
const previewFailure = ref('')
/** Un jeton par aperçu demandé : une réponse en retard ne doit pas écraser un rendu plus récent. */
let ticket = 0

const label = computed(() => meta.value?.label || props.doctype)
const block = computed(() => draft.value.blocks.find((b) => b.key === chosen.value) ?? null)
const problems = computed(() => (meta.value ? draftProblems(draft.value, meta.value) : []))
const payload = computed(() => toTemplate(draft.value))
const dirty = computed(() => !!initial.value && JSON.stringify(payload.value) !== initial.value)
const kinds: BlockKind[] = ['title', 'fields', 'table', 'totals', 'text', 'spacer', 'page_break']
const kindIcons: Record<BlockKind, string> = {
  title: 'pi pi-bookmark', // PrimeIcons 7 n'a pas de « titre » : une classe inconnue laisserait un trou
  fields: 'pi pi-th-large',
  table: 'pi pi-table',
  totals: 'pi pi-calculator',
  text: 'pi pi-align-left',
  spacer: 'pi pi-arrows-v',
  page_break: 'pi pi-minus',
}
const numeric = computed(() => new Set(meta.value ? totalFields(meta.value).map((f) => f.fieldname) : []))
const sizes = ['A3', 'A4', 'A5', 'B4', 'B5', 'Letter', 'Legal', 'Ledger']

onMounted(load)
watch(() => props.doctype, load)
// L'aperçu suit le brouillon, sans le demander à chaque frappe : un court répit, puis une seule requête.
let timer: ReturnType<typeof setTimeout> | undefined
watch(payload, () => {
  clearTimeout(timer)
  timer = setTimeout(() => void refreshPreview(), 400)
})

async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  try {
    meta.value = await metaStore.refresh(props.doctype)
    const current = await api.printOverride(props.doctype)
    draft.value = fromTemplate(current.template)
    source.value = current.source
    editable.value = current.editable
    chosen.value = draft.value.blocks[0]?.key ?? null
    initial.value = JSON.stringify(toTemplate(draft.value))
    example.value = (await api.printExample(props.doctype)).name
    await loadExample()
    await refreshPreview()
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    loading.value = false
  }
}

/**
 * Le document d'exemple, et les TITRES de ses liens : une facture imprime « Durand », pas « CUST-0001 ». Un
 * échec ici ne bloque rien — la feuille montre alors des tirets, l'aperçu imprimé reste juste.
 */
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
  if (!example.value) return
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

// ── La feuille : ajouter, déplacer, retirer ──
function add(kind: BlockKind, slot?: number): void {
  const made = newBlock(kind, meta.value ?? undefined)
  const after = chosen.value ? draft.value.blocks.findIndex((b) => b.key === chosen.value) + 1 : null
  draft.value = addBlock(draft.value, made, slot ?? after)
  select(made.key)
}
function select(key: string): void {
  if (chosen.value !== key) chosenColumn.value = null
  chosen.value = key
}
function selectColumn(key: string, column: string): void {
  chosen.value = key
  chosenColumn.value = column
}
function remove(key: string): void {
  draft.value = removeBlock(draft.value, key)
  if (chosen.value === key) {
    chosen.value = null
    chosenColumn.value = null
  }
}
function moveField(from: string, field: string, to: string, slot: number): void {
  draft.value = moveFieldTo(draft.value, from, field, to, slot, numeric.value)
  chosen.value = to
}
/** La palette se GLISSE sur la feuille (et se clique : le bloc va après celui qui est choisi). */
function paletteStart(e: DragEvent, kind: BlockKind): void {
  if (!e.dataTransfer || !editable.value) return
  e.dataTransfer.effectAllowed = 'copy'
  e.dataTransfer.setData(DRAG_KIND, kind)
}
const patch = (p: Partial<Block>) => chosen.value && (draft.value = updateBlock(draft.value, chosen.value, p))
const setPage = (p: Partial<Draft['page']>) => (draft.value = { ...draft.value, page: { ...draft.value.page, ...p } })
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
          :disabled="!editable || !dirty || !!problems.length"
          data-testid="pb-save"
          @click="save"
        />
      </div>
    </header>

    <Message v-if="failure" severity="error" data-testid="pb-error">{{ failure }}</Message>
    <Message v-else-if="!editable && !loading" severity="info" size="small" data-testid="pb-readonly">{{ t.printBuilder.readOnly }}</Message>
    <Message v-if="problem" severity="error" data-testid="pb-refused">{{ problem }}</Message>
    <Message v-for="p in problems" :key="p" severity="warn" size="small" data-testid="pb-problem">{{ p }}</Message>

    <!-- La page : format, orientation, marges, et la bascule entre la feuille et l'aperçu du serveur -->
    <div v-if="meta" class="pb-toolbar">
      <label class="pb-inline">
        <span>{{ t.printBuilder.pageSize }}</span>
        <Select
          :model-value="draft.page.size"
          :options="sizes.map((s) => ({ value: s, label: s }))"
          option-label="label"
          option-value="value"
          :disabled="!editable"
          size="small"
          data-testid="pb-page-size"
          @update:model-value="setPage({ size: $event })"
        />
      </label>
      <label class="pb-inline">
        <span>{{ t.printBuilder.orientation }}</span>
        <Select
          :model-value="draft.page.orientation"
          :options="[{ value: 'portrait', label: t.printBuilder.portrait }, { value: 'landscape', label: t.printBuilder.landscape }]"
          option-label="label"
          option-value="value"
          :disabled="!editable"
          size="small"
          data-testid="pb-orientation"
          @update:model-value="setPage({ orientation: $event })"
        />
      </label>
      <fieldset class="pb-inline pb-margins">
        <legend>{{ t.printBuilder.margins }}</legend>
        <label v-for="side in (['top', 'right', 'bottom', 'left'] as const)" :key="side" class="pb-margin">
          <span>{{ t.printBuilder.sides[side] }}</span>
          <InputNumber
            :model-value="draft.page[side]"
            :min="0"
            :max="100"
            :disabled="!editable"
            size="small"
            input-class="pb-margin-input"
            :data-testid="`pb-margin-${side}`"
            @update:model-value="setPage({ [side]: $event ?? 0 })"
          />
        </label>
      </fieldset>
      <SelectButton v-model="mode" :options="modes" option-label="label" option-value="value" :allow-empty="false" size="small" class="pb-mode" data-testid="pb-mode" />
    </div>

    <div v-if="meta" class="pb-grid">
      <!-- La palette : on glisse un bloc sur la feuille, ou on le clique -->
      <aside class="pb-palette" :aria-label="t.printBuilder.palette">
        <h2 class="pb-panel-title">{{ t.printBuilder.palette }}</h2>
        <p class="desk-muted pb-hint">{{ t.printBuilder.paletteHint }}</p>
        <button
          v-for="k in kinds"
          :key="k"
          type="button"
          class="pb-palette-item"
          :draggable="editable"
          :disabled="!editable"
          :data-testid="`pb-new-${k}`"
          @dragstart="paletteStart($event, k)"
          @click="add(k)"
        >
          <i :class="kindIcons[k]" aria-hidden="true" />
          <span>{{ KIND_LABELS[k] }}</span>
        </button>
      </aside>

      <!-- La feuille, ou l'aperçu imprimé par le serveur -->
      <div class="pb-center">
        <PrintCanvas
          v-show="mode === 'layout'"
          :draft="draft"
          :meta="meta"
          :example="exampleData"
          :name="example"
          :chosen="chosen"
          :chosen-column="chosenColumn"
          :editable="editable"
          :link-title="links.title"
          @select="select"
          @select-column="selectColumn"
          @insert="(kind, slot) => add(kind, slot)"
          @move-block="(key, slot) => (draft = moveBlockToSlot(draft, key, slot))"
          @move-field="moveField"
          @move-column="(key, column, slot) => (draft = moveColumnToSlot(draft, key, column, slot))"
          @resize-column="(key, column, width) => (draft = setColumnWidth(draft, key, column, width))"
          @nudge="(key, how) => (draft = nudgeBlock(draft, key, how))"
          @remove="remove"
        />
        <div v-show="mode === 'preview'" class="pb-preview">
          <p v-if="!example" class="desk-muted" data-testid="pb-no-example">{{ t.printBuilder.noPreview }}</p>
          <Message v-else-if="previewFailure" severity="warn" size="small" data-testid="pb-preview-error">{{ previewFailure }}</Message>
          <span v-if="previewing" class="desk-muted" data-testid="pb-previewing">{{ t.printBuilder.previewing }}</span>
          <!-- Même bac à sable que le dialogue d'impression : aucun script, et `allow-same-origin` seulement pour
               que la page parente puisse atteindre le cadre (mesuré dans un vrai navigateur). -->
          <iframe
            v-if="example && preview"
            class="pb-frame"
            sandbox="allow-same-origin allow-modals"
            :title="t.printBuilder.preview"
            :srcdoc="preview"
            data-testid="pb-frame"
          />
        </div>
        <p v-if="mode === 'layout' && !example" class="desk-muted pb-hint" data-testid="pb-no-example">{{ t.printBuilder.noExample }}</p>
      </div>

      <!-- Les propriétés du bloc choisi -->
      <BlockPanel
        class="pb-props"
        :block="block"
        :meta="meta"
        :editable="editable"
        :chosen-column="chosenColumn"
        @update="patch"
        @select-column="(c) => (chosenColumn = c)"
        @toggle-field="chosen && (draft = toggleField(draft, chosen, $event))"
        @insert-field="(f) => chosen && (draft = insertField(draft, chosen, f, block?.fields?.length ?? 0))"
        @move-field="(f, how) => chosen && (draft = moveFieldInBlock(draft, chosen, f, how))"
        @add-column="chosen && (draft = addColumn(draft, chosen, $event))"
        @remove-column="chosen && (draft = removeColumn(draft, chosen, $event))"
        @move-column="(k, how) => chosen && (draft = moveColumn(draft, chosen, k, how))"
        @update-column="(k, p) => chosen && (draft = updateColumn(draft, chosen, k, p))"
        @set-column-width="(k, w) => chosen && (draft = setColumnWidth(draft, chosen, k, w))"
        @set-group-by="chosen && (draft = setGroupBy(draft, chosen, $event))"
        @toggle-subtotal="chosen && (draft = toggleSubtotal(draft, chosen, $event))"
      />
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

.pb-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: flex-end;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: var(--p-content-background);
}

.pb-inline {
  display: grid;
  gap: 0.2rem;
  font-size: 0.8rem;
  border: 0;
  margin: 0;
  padding: 0;
}

.pb-inline > span,
.pb-inline > legend {
  color: var(--p-text-muted-color);
  padding: 0;
}

.pb-margins {
  display: flex;
  gap: 0.35rem;
  align-items: flex-end;
}

.pb-margins > legend {
  float: left;
  width: 100%;
  margin-bottom: 0.2rem;
}

.pb-margin {
  display: grid;
  gap: 0.1rem;
  font-size: 0.7rem;
  color: var(--p-text-muted-color);
}

.pb-margin :deep(.pb-margin-input) {
  width: 3.5rem;
}

.pb-mode {
  margin-left: auto;
}

/* Trois colonnes de largeur FIXE autour de la feuille : la palette et le panneau ne mangent plus la page
   (`max-content` laissait la palette s'étendre sur toute la largeur et écrasait la feuille et l'aperçu). */
.pb-grid {
  display: grid;
  grid-template-columns: 10rem minmax(0, 1fr) 20rem;
  gap: 1rem;
  align-items: start;
}

.pb-grid > * {
  min-width: 0;
}

/* Sous 1100 px : la palette passe en bande au-dessus, le panneau sous la feuille. */
@media (max-width: 1100px) {
  .pb-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .pb-palette {
    display: flex !important;
    flex-wrap: wrap;
  }

  .pb-palette .pb-panel-title,
  .pb-palette .pb-hint {
    width: 100%;
  }
}

.pb-panel-title {
  font-size: 0.9rem;
  margin: 0 0 0.25rem;
}

.pb-hint {
  font-size: 0.75rem;
  margin: 0 0 0.25rem;
}

.pb-palette {
  display: grid;
  gap: 0.35rem;
  align-content: start;
  position: sticky;
  top: 0.5rem;
}

.pb-palette-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.25rem;
  padding: 0.35rem 0.6rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-sm, 4px);
  background: var(--p-content-background);
  color: var(--p-text-color);
  font: inherit;
  font-size: 0.85rem;
  cursor: grab;
  text-align: left;
}

.pb-palette-item:hover:not(:disabled) {
  border-color: var(--p-primary-color);
}

.pb-palette-item:disabled {
  cursor: default;
  opacity: 0.6;
}

.pb-palette-item i {
  color: var(--p-primary-color);
}

.pb-center {
  display: grid;
  gap: 0.5rem;
}

.pb-preview {
  display: grid;
  gap: 0.5rem;
}

.pb-props {
  position: sticky;
  top: 0.5rem;
  max-height: calc(100vh - 6rem);
  overflow: auto;
  padding: 0.75rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: var(--p-content-background);
}

@media (max-width: 1100px) {
  .pb-palette,
  .pb-props {
    position: static;
    max-height: none;
  }
}

/* L'aperçu est une page de papier : fond blanc quel que soit le thème, comme elle sortira. */
.pb-frame {
  width: 100%;
  height: calc(100vh - 14rem);
  min-height: 28rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: var(--desk-paper);
}
</style>
