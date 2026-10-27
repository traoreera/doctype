<script setup lang="ts">
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Position } from '@vue-flow/core'
import { computed } from 'vue'
import { t } from '@/i18n'

/** Un lien entre deux cartes. Choisi, il montre un bouton pour le défaire (on relie en tirant, on délie ici ou dans le panneau). */
const props = defineProps<{
  id: string
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
  sourcePosition: Position
  targetPosition: Position
  markerEnd?: string
  data: { label: string; tone: string; canEdit: boolean }
  selected?: boolean
}>()
const emit = defineEmits<{ (e: 'unlink', id: string): void }>()
const geometry = computed(() => getBezierPath({ ...props }))
</script>

<template>
  <BaseEdge :id="id" :path="geometry[0]" :marker-end="markerEnd" :class="['af-edge', `af-edge-${data.tone}`, { 'af-edge-selected': selected }]" :interaction-width="18" />
  <EdgeLabelRenderer v-if="selected && data.canEdit">
    <button
      type="button"
      class="af-edge-unlink nodrag nopan"
      :style="{ transform: `translate(-50%, -50%) translate(${geometry[1]}px, ${geometry[2]}px)` }"
      :aria-label="t.automations.unlink(data.label || t.automations.triggerNode)"
      :title="t.automations.unlink(data.label || t.automations.triggerNode)"
      :data-testid="`af-unlink-${id}`"
      @click.stop="emit('unlink', id)"
    >
      <span class="pi pi-times" aria-hidden="true" />
    </button>
  </EdgeLabelRenderer>
</template>
