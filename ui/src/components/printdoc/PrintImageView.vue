<script setup lang="ts">
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import { computed, ref } from 'vue'
import { t } from '@/i18n'
import { PX_PER_MM, round1 } from '@/lib/printdoc'
import { docContext } from './extensions'

/**
 * Une image du gabarit, comme dans Word : dans le texte, habillée à gauche ou à droite, ou « devant le texte ».
 *
 * Devant le texte, on la DÉPLACE à la souris où l'on veut sur la page ; dans tous les modes, la poignée du coin
 * la redimensionne. Les positions et la largeur s'écrivent en MILLIMÈTRES depuis le coin de la zone imprimable :
 * c'est l'unité du PDF, et la position est mesurée exacte dans le vrai moteur (`test_pdf_engine.py`).
 *
 * Pendant le geste, la position est locale (`live`) et ne s'écrit dans le document qu'au relâcher : une seule
 * étape d'annulation par déplacement, et pas une transaction par pixel.
 */
const props = defineProps(nodeViewProps)
const ctx = computed(() => docContext(props.editor))
const attrs = computed(() => props.node.attrs as { file: string; wrap: string; width: number; left: number; top: number })
const src = computed(() => ctx.value.imageUrl(attrs.value.file))
const live = ref<{ width: number; left: number; top: number } | null>(null)
const geom = computed(() => live.value ?? { width: attrs.value.width, left: attrs.value.left, top: attrs.value.top })
const style = computed(() => {
  const s: Record<string, string> = { width: `${geom.value.width}mm` }
  if (attrs.value.wrap === 'front') {
    s.left = `${geom.value.left}mm`
    s.top = `${geom.value.top}mm`
  }
  return s
})

/** Un pixel de l'écran en millimètres de papier, à l'échelle où la feuille est dessinée. */
const mmPerPx = (): number => 1 / (PX_PER_MM * (ctx.value.scale || 1))

function select(): void {
  const pos = props.getPos()
  if (typeof pos === 'number') props.editor.commands.setNodeSelection(pos)
}

function follow(e: PointerEvent, apply: (dx: number, dy: number) => void): void {
  const x0 = e.clientX
  const y0 = e.clientY
  const target = e.currentTarget as HTMLElement
  target.setPointerCapture(e.pointerId)
  const move = (m: PointerEvent) => apply((m.clientX - x0) * mmPerPx(), (m.clientY - y0) * mmPerPx())
  const up = () => {
    target.removeEventListener('pointermove', move)
    target.removeEventListener('pointerup', up)
    target.removeEventListener('pointercancel', up)
    if (live.value) props.updateAttributes({ ...live.value })
    live.value = null
  }
  target.addEventListener('pointermove', move)
  target.addEventListener('pointerup', up)
  target.addEventListener('pointercancel', up)
}

/** Devant le texte : saisir l'image la déplace. Dans le texte, le glisser natif de l'éditeur la déplace dans le flux. */
function grab(e: PointerEvent): void {
  select()
  if (!ctx.value.editable || attrs.value.wrap !== 'front' || e.button !== 0) return
  e.preventDefault()
  const start = { ...geom.value }
  const img = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const height = img.height * mmPerPx()
  const a = ctx.value.area
  // Sur le PAPIER, marges comprises (un tampon se pose dans la marge), jamais au-delà : hors de la feuille, il ne s'imprime pas.
  const clampX = (x: number) => Math.max(-a.left, Math.min(a.width + a.right - start.width, x))
  const clampY = (y: number) => Math.max(-a.top, Math.min(a.height + a.bottom - height, y))
  follow(e, (dx, dy) => {
    live.value = { width: start.width, left: round1(clampX(start.left + dx)), top: round1(clampY(start.top + dy)) }
  })
}

function resize(e: PointerEvent): void {
  if (!ctx.value.editable || e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  select()
  const start = { ...geom.value }
  follow(e, (dx) => {
    live.value = { ...start, width: round1(Math.max(5, Math.min(500, start.width + dx))) }
  })
}
</script>

<template>
  <NodeViewWrapper
    as="span"
    :class="['pd-img', `wrap-${attrs.wrap}`, { selected, moving: !!live }]"
    :style="style"
    contenteditable="false"
    :data-file="attrs.file"
    data-testid="pd-image"
    @pointerdown="grab"
  >
    <img v-if="src" :src="src" alt="" draggable="false" />
    <span v-else class="pd-img-missing">{{ t.printDoc.imageLoading }}</span>
    <span v-if="ctx.editable && selected" class="pd-img-handle" :title="t.printDoc.resize" data-testid="pd-image-resize" @pointerdown="resize" @mousedown.prevent />
    <span v-if="live" class="pd-img-size">{{ attrs.wrap === 'front' ? `${geom.left} × ${geom.top} mm · ` : '' }}{{ geom.width }} mm</span>
  </NodeViewWrapper>
</template>
