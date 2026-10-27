<script setup lang="ts">
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type Position } from '@vue-flow/core'
import { computed } from 'vue'
import { t } from '@/i18n'
import type { TransitionEdge } from '@/lib/workflowgraph'

/**
 * Une transition : une flèche, et son action en étiquette. L'étiquette est un BOUTON (choisir la transition au clavier), et porte ce
 * qui la resserre : barrée et en pointillés si l'organisation l'a fermée, un filtre si une condition s'applique (de l'application ou de
 * l'organisation), un cadenas si l'auteur du document ne peut pas la faire.
 */
const props = defineProps<{
  id: string
  sourceX: number
  sourceY: number
  targetX: number
  targetY: number
  sourcePosition: Position
  targetPosition: Position
  markerEnd?: string
  data: TransitionEdge['data']
  selected?: boolean
}>()
const emit = defineEmits<{ (e: 'choose', action: string): void }>()

// Un retour en arrière part du bas et revient par le bas : `offset` l'écarte assez du graphe pour qu'on le lise.
const geometry = computed(() =>
  getSmoothStepPath({ ...props, borderRadius: 12, offset: props.data.back ? 46 : 24 }),
)
const conditioned = computed(() => !!props.data.condition || !!props.data.yamlCondition)
</script>

<template>
  <BaseEdge :id="id" :path="geometry[0]" :marker-end="markerEnd" :class="['wf-edge', { 'wf-edge-closed': data.closed, 'wf-edge-selected': selected }]" />
  <EdgeLabelRenderer>
    <button
      type="button"
      class="wf-edge-label nodrag nopan"
      :class="{ 'wf-edge-label-closed': data.closed, 'wf-edge-label-selected': selected }"
      :style="{ transform: `translate(-50%, -50%) translate(${geometry[1]}px, ${geometry[2]}px)` }"
      :aria-pressed="selected ? 'true' : 'false'"
      :aria-label="`${t.workflowBuilder.transition} ${data.action} : ${data.from} → ${data.to}${data.closed ? ` (${t.flowOverride.closed})` : ''}`"
      :data-testid="`wf-edge-${id}`"
      @click.stop="emit('choose', id)"
    >
      <span v-if="data.closed" class="pi pi-ban" aria-hidden="true" />
      {{ data.action }}
      <span v-if="conditioned" class="pi pi-filter" :title="t.flowOverride.condition" aria-hidden="true" />
      <span v-if="data.notOwner" class="pi pi-lock" :title="t.flowOverride.notOwner" aria-hidden="true" />
    </button>
  </EdgeLabelRenderer>
</template>
