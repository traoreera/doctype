<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { t } from '@/i18n'
import { readEvent } from '@/lib/flowactions'
import { TRIGGER_ICONS, type TriggerNodeData } from '@/lib/flowgraph'

/**
 * Le déclencheur : ce qui met le flux en route. Une seule carte, jamais supprimée (un flux sans déclencheur n'existe pas chez xflow),
 * de forme distincte comme chez n8n — arrondie à gauche, pas d'entrée, une seule sortie.
 *
 * Sa sortie est l'étape de DÉPART (`entry_step`) : la tirer ailleurs change par où le flux commence.
 */
const props = defineProps<{ id: string; data: TriggerNodeData; selected?: boolean }>()
const emit = defineEmits<{ (e: 'choose', id: string): void }>()

const label = computed(() => t.automations.triggers[props.data.kind] ?? props.data.kind)
/** Un événement d'un autre plugin : montré sur la carte, même quand on règle une étape. */
const otherEvent = computed(() => props.data.kind === 'event' && !!props.data.detail && !readEvent(props.data.detail))
</script>

<template>
  <button
    type="button"
    class="fl-node fl-trigger"
    :class="{ 'fl-node-selected': selected }"
    :aria-pressed="selected ? 'true' : 'false'"
    data-testid="fl-node-trigger"
    @click.stop="emit('choose', id)"
  >
    <span class="fl-node-head">
      <span class="fl-node-icon" aria-hidden="true"><i :class="['pi', TRIGGER_ICONS[data.kind]]" /></span>
      <span class="fl-node-titles">
        <span class="fl-node-name">{{ label }}</span>
        <span class="fl-node-kind">{{ t.flowBuilder.trigger }}</span>
      </span>
    </span>
    <span v-if="data.detail" class="fl-node-summary">{{ data.detail }}</span>
    <span v-if="otherEvent" class="fl-node-summary" data-testid="fl-event-other">{{ t.flowBuilder.trigger_.otherEvent(data.detail) }}</span>
    <span class="fl-outlets">
      <span class="fl-outlet fl-outlet-ok">
        <span class="fl-outlet-label">{{ t.flowBuilder.outlets.start }}</span>
        <Handle id="start" type="source" :position="Position.Right" class="fl-handle-out" data-testid="fl-out-trigger-start" />
      </span>
    </span>
  </button>
</template>
