<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { t } from '@/i18n'
import type { StateData } from '@/lib/workflowgraph'

/**
 * Un état du circuit, comme une carte du Workflow Builder de Frappe : son nom, l'état de document qu'il impose (brouillon, soumis,
 * annulé) et ce qu'il est dans le circuit (départ, fin, cul-de-sac). Le nœud est un BOUTON : on le choisit au clavier comme à la
 * souris, et le panneau de droite montre ses transitions.
 *
 * Quatre poignées, jamais reliables à la main (le circuit ne s'invente pas ici, process/programme.md §3.6) : entrée à gauche, sortie à
 * droite, et deux en bas pour les retours en arrière, qui passent sous le graphe au lieu de le traverser.
 */
const props = defineProps<{ id: string; data: StateData; selected?: boolean }>()
const emit = defineEmits<{ (e: 'choose', state: string): void }>()

const tone = computed(() => ({ draft: 'neutral', submitted: 'ok', cancelled: 'bad' })[props.data.docstatus])
</script>

<template>
  <button
    type="button"
    class="wf-state"
    :class="[`wf-tone-${tone}`, { 'wf-state-selected': selected, 'wf-state-stuck': data.deadEnd }]"
    :aria-pressed="selected ? 'true' : 'false'"
    :data-testid="`wf-state-${id}`"
    @click.stop="emit('choose', id)"
  >
    <Handle id="in" type="target" :position="Position.Left" :connectable="false" />
    <Handle id="loop-in" type="target" :position="Position.Bottom" :connectable="false" class="wf-handle-loop-in" />
    <span class="wf-state-top">
      <span v-if="data.initial" class="wf-state-tag" :title="t.workflowBuilder.initialHint">{{ t.workflowBuilder.initial }}</span>
      <span v-else-if="data.final" class="wf-state-tag">{{ t.workflowBuilder.final }}</span>
      <span v-if="data.deadEnd" class="wf-state-tag wf-state-tag-warn" :title="t.workflowBuilder.deadEndHint">
        <span class="pi pi-exclamation-triangle" aria-hidden="true" /> {{ t.workflowBuilder.deadEnd }}
      </span>
    </span>
    <span class="wf-state-name">{{ data.name }}</span>
    <span class="wf-docstatus">{{ t.workflowBuilder.docstatus[data.docstatus] }}</span>
    <Handle id="out" type="source" :position="Position.Right" :connectable="false" />
    <Handle id="loop-out" type="source" :position="Position.Bottom" :connectable="false" class="wf-handle-loop-out" />
  </button>
</template>
