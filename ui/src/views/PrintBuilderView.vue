<script setup lang="ts">
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import BlockPanel from '@/components/printbuilder/BlockPanel.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import {
  DRAG_BLOCK,
  KIND_LABELS,
  addBlock,
  addColumn,
  blockLabel,
  draftProblems,
  fromTemplate,
  moveBlock,
  moveColumn,
  moveFieldInBlock,
  newBlock,
  nudgeBlock,
  removeBlock,
  removeColumn,
  setGroupBy,
  toTemplate,
  toggleField,
  toggleSubtotal,
  updateBlock,
  updateColumn,
  type Block,
  type BlockKind,
  type Draft,
} from '@/lib/printedit'
import { useServices } from '@/lib/services'
import type { DocTypeMeta } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * « Personnaliser l'impression » (`process/impression.md` §10) : le gabarit d'un document comme une PILE de
 * blocs qu'on réarrange, avec à gauche les blocs à ajouter, au milieu la pile, à droite les propriétés — et
 * l'APERÇU d'un document réel.
 *
 * L'aperçu est ce qui distingue cet éditeur des autres du Desk : on ne règle pas une mise en page abstraite, on
 * regarde la facture. Il vient du SERVEUR (`POST /print-preview`), qui applique au brouillon les mêmes contrôles
 * qu'à un enregistrement — un gabarit non enregistré n'est donc pas une porte.
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
    await refreshPreview()
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    loading.value = false
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

// ── La pile : ajouter, déplacer, retirer ──
function add(kind: BlockKind): void {
  const made = newBlock(kind, meta.value ?? undefined)
  const at = chosen.value ? draft.value.blocks.findIndex((b) => b.key === chosen.value) + 1 : null
  draft.value = addBlock(draft.value, made, at)
  chosen.value = made.key
}
/** Le rang survolé pendant un glisser : la pile y montre une ligne de dépôt. `null` quand on ne glisse pas. */
const over = ref<number | null>(null)

function dragStart(e: DragEvent, key: string): void {
  // Sans `effectAllowed`/`dropEffect`, le navigateur affiche le curseur « interdit » et le dépôt n'aboutit pas.
  if (!e.dataTransfer) return
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData(DRAG_BLOCK, key)
}

function dragOver(e: DragEvent, at: number): void {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  over.value = at
}

function drop(e: DragEvent, at: number): void {
  e.preventDefault()
  over.value = null
  const key = e.dataTransfer?.getData(DRAG_BLOCK)
  if (key) draft.value = moveBlock(draft.value, key, at)
}
const patch = (p: Partial<Block>) => chosen.value && (draft.value = updateBlock(draft.value, chosen.value, p))
</script>

<template>
  <section class="pb-page">
    <header class="pb-head">
      <h1 data-testid="pb-title">{{ t.printBuilder.title(label) }}</h1>
      <p class="desk-muted">
        {{ t.printBuilder.intro }}
        <span data-testid="pb-source">({{ t.printBuilder.source(source) }})</span>
      </p>
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

    <div v-if="meta" class="pb-grid">
      <!-- La palette -->
      <aside class="pb-palette" :aria-label="t.printBuilder.palette">
        <h2 class="pb-panel-title">{{ t.printBuilder.palette }}</h2>
        <Button
          v-for="k in kinds"
          :key="k"
          :label="KIND_LABELS[k]"
          icon="pi pi-plus"
          text
          size="small"
          class="pb-palette-item"
          :disabled="!editable"
          :data-testid="`pb-new-${k}`"
          @click="add(k)"
        />
        <h2 class="pb-panel-title">{{ t.printBuilder.page }}</h2>
        <label class="pb-field">
          <span>{{ t.printBuilder.pageSize }}</span>
          <Select
            :model-value="draft.page.size"
            :options="sizes.map((s) => ({ value: s, label: s }))"
            option-label="label"
            option-value="value"
            :disabled="!editable"
            size="small"
            data-testid="pb-page-size"
            @update:model-value="draft = { ...draft, page: { ...draft.page, size: $event } }"
          />
        </label>
        <label class="pb-field">
          <span>{{ t.printBuilder.orientation }}</span>
          <Select
            :model-value="draft.page.orientation"
            :options="[{ value: 'portrait', label: t.printBuilder.portrait }, { value: 'landscape', label: t.printBuilder.landscape }]"
            option-label="label"
            option-value="value"
            :disabled="!editable"
            size="small"
            @update:model-value="draft = { ...draft, page: { ...draft.page, orientation: $event } }"
          />
        </label>
        <fieldset class="pb-field">
          <legend>{{ t.printBuilder.margins }}</legend>
          <div class="pb-margins">
            <InputNumber v-for="side in (['top', 'right', 'bottom', 'left'] as const)" :key="side" :model-value="draft.page[side]" :min="0" :max="100" :disabled="!editable" size="small" @update:model-value="draft = { ...draft, page: { ...draft.page, [side]: $event ?? 0 } }" />
          </div>
        </fieldset>
      </aside>

      <!-- La pile -->
      <div class="pb-stack" :aria-label="t.printBuilder.stack">
        <h2 class="pb-panel-title">{{ t.printBuilder.stack }}</h2>
        <p v-if="!draft.blocks.length" class="desk-muted" data-testid="pb-empty">{{ t.printBuilder.empty }}</p>
        <ol class="pb-blocks">
          <li
            v-for="(b, i) in draft.blocks"
            :key="b.key"
            :class="['pb-block', { chosen: b.key === chosen }]"
            :draggable="editable"
            :data-testid="`pb-block-${i}`"
            @dragstart="$event.dataTransfer?.setData(DRAG_BLOCK, b.key)"
            @dragover.prevent
            @drop.prevent="drop($event, i)"
          >
            <button type="button" class="pb-block-pick" :data-testid="`pb-pick-${i}`" @click="chosen = b.key">{{ blockLabel(b, meta) }}</button>
            <span class="pb-block-actions">
              <Button icon="pi pi-arrow-up" text size="small" :disabled="!editable || i === 0" :aria-label="t.printBuilder.moveUp" :data-testid="`pb-up-${i}`" @click="draft = nudgeBlock(draft, b.key, 'up')" />
              <Button icon="pi pi-arrow-down" text size="small" :disabled="!editable || i === draft.blocks.length - 1" :aria-label="t.printBuilder.moveDown" :data-testid="`pb-down-${i}`" @click="draft = nudgeBlock(draft, b.key, 'down')" />
              <Button icon="pi pi-trash" text severity="danger" size="small" :disabled="!editable" :aria-label="t.printBuilder.remove" :data-testid="`pb-remove-${i}`" @click="draft = removeBlock(draft, b.key); chosen = null" />
            </span>
          </li>
        </ol>
      </div>

      <!-- Les propriétés du bloc choisi -->
      <BlockPanel
        :block="block"
        :meta="meta"
        :editable="editable"
        @update="patch"
        @toggle-field="chosen && (draft = toggleField(draft, chosen, $event))"
        @move-field="(f, how) => chosen && (draft = moveFieldInBlock(draft, chosen, f, how))"
        @add-column="chosen && (draft = addColumn(draft, chosen, $event))"
        @remove-column="chosen && (draft = removeColumn(draft, chosen, $event))"
        @move-column="(k, how) => chosen && (draft = moveColumn(draft, chosen, k, how))"
        @update-column="(k, p) => chosen && (draft = updateColumn(draft, chosen, k, p))"
        @set-group-by="chosen && (draft = setGroupBy(draft, chosen, $event))"
        @toggle-subtotal="chosen && (draft = toggleSubtotal(draft, chosen, $event))"
      />

      <!-- L'aperçu : un document réel, rendu par le serveur -->
      <div class="pb-preview">
        <h2 class="pb-panel-title">
          {{ t.printBuilder.preview }}
          <span v-if="previewing" class="desk-muted" data-testid="pb-previewing">· {{ t.printBuilder.previewing }}</span>
        </h2>
        <p v-if="!example" class="desk-muted" data-testid="pb-no-example">{{ t.printBuilder.noPreview }}</p>
        <Message v-else-if="previewFailure" severity="warn" size="small" data-testid="pb-preview-error">{{ previewFailure }}</Message>
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
    </div>
  </section>
</template>

<style scoped>
.pb-page {
  display: grid;
  gap: 0.875rem;
}

.pb-head {
  display: grid;
  gap: 0.35rem;
}

.pb-head h1 {
  margin: 0;
  font-size: 1.2rem;
}

.pb-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.pb-grid {
  display: grid;
  /* `max-content` pour la palette et `minmax(0, …)` pour le reste : sans cela un bouton de palette plus large
     que sa piste DÉBORDE par-dessus la pile — mesuré dans Chromium, où le clic sur un bloc atteignait un
     bouton de palette. Un enfant de grille a `min-width: auto`, donc son contenu ne se laisse pas rétrécir et
     sort de la colonne au lieu de la contraindre. */
  grid-template-columns: minmax(11rem, max-content) minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1.6fr);
  gap: 1rem;
  align-items: start;
}

/* La même raison : chaque colonne doit pouvoir rétrécir, sinon elle empiète sur sa voisine. */
.pb-grid > * {
  min-width: 0;
}

/* Sous 1200 px tout s'empile : l'aperçu passe en dernier, après les réglages. */
@media (max-width: 1200px) {
  .pb-grid {
    grid-template-columns: 1fr;
  }
}

.pb-panel-title {
  font-size: 0.95rem;
  margin: 0 0 0.35rem;
}

.pb-palette,
.pb-stack,
.pb-preview {
  display: grid;
  gap: 0.5rem;
  align-content: start;
}

.pb-palette-item {
  justify-content: flex-start;
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

.pb-margins {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.25rem;
}

.pb-blocks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}

.pb-block {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-sm, 4px);
  background: var(--p-content-background);
}

.pb-block.chosen {
  border-color: var(--p-primary-color);
}

.pb-block-pick {
  background: none;
  border: 0;
  color: inherit;
  font: inherit;
  text-align: left;
  padding: 0.5rem;
  cursor: pointer;
  min-height: 2.25rem;
}

.pb-block-actions {
  display: flex;
}

/* L'aperçu est une page de papier : fond blanc quel que soit le thème, comme elle sortira. */
.pb-frame {
  width: 100%;
  height: min(70vh, 48rem);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: #fff;
}
</style>
