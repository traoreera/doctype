<script setup lang="ts">
import Button from 'primevue/button'
import Select from 'primevue/select'
import { computed, ref } from 'vue'
import ChartCard from '@/components/blocks/ChartCard.vue'
import LinksCard from '@/components/blocks/LinksCard.vue'
import NumberCard from '@/components/blocks/NumberCard.vue'
import QuickList from '@/components/blocks/QuickList.vue'
import ShortcutGrid from '@/components/blocks/ShortcutGrid.vue'
import ExtensionSlot from '@/extensions/ExtensionSlot.vue'
import { useExtensions } from '@/extensions/host'
import { t } from '@/i18n'
import { colsOf, hasSize, isEditable, SIZES } from '@/lib/blocksedit'
import type { ChartBlock, ExtensionBlock, LinksCardBlock, NumberCardBlock, QuickListBlock, WorkspaceBlock, WorkspaceShortcut } from '@/lib/types'

/**
 * Les blocs d'un espace ou d'un tableau de bord, posés sur une grille de 12 colonnes (comme Frappe) : un titre, des raccourcis en rangée,
 * des cartes de chiffres côte à côte, des graphiques à la demi-largeur. Sur un petit écran tout passe en pleine largeur (les chiffres par deux).
 */
const props = defineProps<{
  blocks: readonly WorkspaceBlock[]
  /** Mode édition : chaque bloc devient une tuile qu'on déplace, redimensionne, modifie ou retire (les cartes restent VIVANTES, mais ne se cliquent pas). */
  editing?: boolean
  /** Un seul bloc dans une tuile : il prend toute la largeur de la tuile. */
  fill?: boolean
}>()
const emit = defineEmits<{
  move: [from: number, to: number]
  resize: [index: number, cols: number]
  edit: [index: number]
  remove: [index: number]
}>()

type Item =
  | { kind: 'title'; key: string; text: string }
  | { kind: 'text'; key: string; text: string }
  | { kind: 'spacer'; key: string }
  | { kind: 'shortcuts'; key: string; list: WorkspaceShortcut[] }
  | { kind: 'number'; key: string; block: NumberCardBlock; span: number }
  | { kind: 'chart'; key: string; block: ChartBlock; span: number }
  | { kind: 'quick'; key: string; block: QuickListBlock; span: number }
  | { kind: 'links'; key: string; block: LinksCardBlock; span: number }
  | { kind: 'extension'; key: string; block: ExtensionBlock; span: number }

const extensions = useExtensions()

const items = computed<Item[]>(() => {
  const out: Item[] = []
  props.blocks.forEach((b, i) => {
    const key = `${i}`
    switch (b.type) {
      case 'header':
        out.push({ kind: 'title', key, text: b.text })
        break
      case 'paragraph':
        out.push({ kind: 'text', key, text: b.text })
        break
      case 'spacer':
        out.push({ kind: 'spacer', key })
        break
      case 'shortcut': {
        const last = out.at(-1)
        if (last?.kind === 'shortcuts') last.list.push(b)
        else out.push({ kind: 'shortcuts', key, list: [b] })
        break
      }
      case 'number_card':
        out.push({ kind: 'number', key, block: b, span: b.cols ?? 3 })
        break
      case 'chart':
        out.push({ kind: 'chart', key, block: b, span: b.cols ?? 6 })
        break
      case 'quick_list':
        out.push({ kind: 'quick', key, block: b, span: b.cols ?? 6 })
        break
      case 'card':
        out.push({ kind: 'links', key, block: b, span: b.cols ?? 4 })
        break
      case 'extension':
        out.push({ kind: 'extension', key, block: b, span: b.cols ?? 6 })
        break
    }
  })
  return out
})

// ── Édition ─────────────────────────────────────────────────
// Un bloc = une tuile (même un raccourci : en édition on déplace chacun, pas des groupes). Glisser une tuile sur une autre lui prend sa place ;
// les flèches font la même chose au clavier, et sont la seule façon sur un écran tactile.
// Une tuile garde SA carte quand on la déplace : la clé est le contenu du bloc (et son rang parmi les identiques), jamais sa place. Sinon la
// carte de la place n°2 montrerait un instant les chiffres de celle qui s'y trouvait avant.
const tileKeys = computed(() => {
  const seen = new Map<string, number>()
  return props.blocks.map((b) => {
    const { cols: _cols, ...rest } = b as WorkspaceBlock & { cols?: number } // redimensionner ne change pas ce que la carte calcule
    const sig = JSON.stringify(rest)
    const n = (seen.get(sig) ?? 0) + 1
    seen.set(sig, n)
    return `${n}:${sig}`
  })
})
const dragging = ref<number | null>(null)
const over = ref<number | null>(null)
const sizeChoices = SIZES.map((n) => ({ value: n, label: t.editor.sizes[n] ?? `${n}/12` }))
const tileCols = (b: WorkspaceBlock): number => colsOf(b as never)
const label = (b: WorkspaceBlock): string => t.editor.kinds[b.type] ?? b.type

function dragStart(i: number, e: DragEvent): void {
  dragging.value = i
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', String(i)) // Firefox ne démarre pas un glisser sans donnée
  }
}

function dragOver(i: number, e: DragEvent): void {
  if (dragging.value === null) return
  e.preventDefault() // autorise le dépôt
  over.value = i
}

function drop(i: number, e: DragEvent): void {
  e.preventDefault()
  const from = dragging.value
  dragging.value = over.value = null
  if (from !== null && from !== i) emit('move', from, i)
}

function dragEnd(): void {
  dragging.value = over.value = null
}
</script>

<template>
  <div v-if="editing" class="block-grid is-editing" data-testid="block-grid-edit">
    <p v-if="!blocks.length" class="desk-muted block-wide block-empty" data-testid="block-empty">{{ t.editor.empty }}</p>
    <div
      v-for="(b, i) in blocks"
      :key="tileKeys[i]"
      :class="['block-tile', { dragging: dragging === i, over: over === i && dragging !== i }]"
      :style="{ '--span': tileCols(b) }"
      draggable="true"
      :data-testid="`tile-${i}`"
      @dragstart="dragStart(i, $event)"
      @dragover="dragOver(i, $event)"
      @drop="drop(i, $event)"
      @dragend="dragEnd"
    >
      <div class="tile-bar" role="toolbar" :aria-label="label(b)">
        <i class="pi pi-arrows-alt tile-grip" :title="t.editor.drag" aria-hidden="true" />
        <span class="tile-kind">{{ label(b) }}</span>
        <span class="desk-spacer" />
        <Button icon="pi pi-angle-left" text rounded size="small" severity="secondary" :disabled="i === 0" :aria-label="t.editor.earlier" :title="t.editor.earlier" :data-testid="`tile-earlier-${i}`" @click="emit('move', i, i - 1)" />
        <Button icon="pi pi-angle-right" text rounded size="small" severity="secondary" :disabled="i === blocks.length - 1" :aria-label="t.editor.later" :title="t.editor.later" :data-testid="`tile-later-${i}`" @click="emit('move', i, i + 1)" />
        <Select
          v-if="hasSize(b as never)"
          :model-value="tileCols(b)"
          :options="sizeChoices"
          option-label="label"
          option-value="value"
          size="small"
          class="tile-size"
          :aria-label="t.editor.width"
          :data-testid="`tile-size-${i}`"
          @update:model-value="emit('resize', i, $event)"
        />
        <Button v-if="isEditable(b as never)" icon="pi pi-pencil" text rounded size="small" severity="secondary" :aria-label="t.editor.change" :title="t.editor.change" :data-testid="`tile-edit-${i}`" @click="emit('edit', i)" />
        <Button icon="pi pi-trash" text rounded size="small" severity="danger" :aria-label="t.editor.remove" :title="t.editor.remove" :data-testid="`tile-remove-${i}`" @click="emit('remove', i)" />
      </div>
      <div class="tile-body"><BlockGrid :blocks="[b]" fill /></div>
    </div>
  </div>

  <div v-else class="block-grid" :class="{ 'is-fill': fill }" data-testid="block-grid">
    <template v-for="it in items" :key="it.key">
      <h3 v-if="it.kind === 'title'" class="home-title block-wide">{{ it.text }}</h3>
      <p v-else-if="it.kind === 'text'" class="desk-muted block-text block-wide" data-testid="block-text">{{ it.text }}</p>
      <div v-else-if="it.kind === 'spacer'" class="block-spacer block-wide" aria-hidden="true" />
      <div v-else-if="it.kind === 'shortcuts'" class="block-wide"><ShortcutGrid :shortcuts="it.list" /></div>
      <div v-else-if="it.kind === 'number'" class="block-item is-number" :style="{ '--span': fill ? 12 : it.span }"><NumberCard :block="it.block" /></div>
      <div v-else-if="it.kind === 'chart'" class="block-item" :style="{ '--span': fill ? 12 : it.span }"><ChartCard :block="it.block" /></div>
      <div v-else-if="it.kind === 'quick'" class="block-item" :style="{ '--span': fill ? 12 : it.span }"><QuickList :block="it.block" /></div>
      <div v-else-if="it.kind === 'links'" class="block-item" :style="{ '--span': fill ? 12 : it.span }"><LinksCard :block="it.block" /></div>
      <!-- D28 : un bloc dessiné par une extension ; absent (extension non chargée), un encart discret plutôt qu'un trou. Ses réglages
           arrivent dans UNE prop (`config`) : jamais étalés, sinon une clé non déclarée (`innerHTML`, `style`) tomberait sur l'élément racine. -->
      <div v-else-if="it.kind === 'extension'" class="block-item" :style="{ '--span': fill ? 12 : it.span }" :data-component="it.block.component" data-testid="block-extension">
        <ExtensionSlot
          v-if="extensions.block(it.block.component)"
          :extension="extensions.block(it.block.component)!.extension"
          :component="extensions.block(it.block.component)!.component"
          :props="{ config: it.block.props ?? {}, label: it.block.label ?? null }"
        />
        <div v-else class="desk-card desk-muted ext-missing" role="status" data-testid="ext-missing">{{ t.extensions.missingBlock(it.block.component) }}</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ext-missing {
  padding: 0.75rem 1rem;
  font-size: 0.875rem;
}
</style>
