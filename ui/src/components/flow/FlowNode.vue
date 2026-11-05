<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { t } from '@/i18n'
import { outletLabel, stepSummary, STEP_ICONS, type StepNodeData } from '@/lib/flowgraph'

/**
 * Une étape du flux, comme un nœud de n8n : un carré d'icône à la couleur de son type, le nom de l'étape, ce qu'elle FAIT en
 * dessous, puis une ligne par SORTIE avec sa poignée au bord droit.
 *
 * Le nœud est un BOUTON : on le choisit au clavier comme à la souris, et le panneau de droite montre ses propriétés. Les poignées,
 * elles, se tirent à la souris pour relier (`@connect` de la page) ; au clavier, le panneau fait la même chose avec des listes.
 *
 * Deux pastilles disent ce qui ne tournera pas : « incomplète » (ce que le type exige et qui manque) et « jamais atteinte » (aucune
 * flèche n'y arrive) — xflow ne s'en plaindrait qu'à l'exécution.
 */
const props = defineProps<{ id: string; data: StepNodeData; selected?: boolean }>()
const emit = defineEmits<{ (e: 'choose', id: string): void }>()

const kind = computed(() => props.data.step.kind)
const icon = computed(() => (kind.value ? STEP_ICONS[kind.value] : 'pi-question-circle'))
const kindLabel = computed(() => (kind.value ? t.flowBuilder.kinds[kind.value].label : t.flowBuilder.unknownKind))
const summary = computed(() => stepSummary(props.data.step))
</script>

<template>
  <button
    type="button"
    class="fl-node"
    :class="[`fl-kind-${kind ?? 'unknown'}`, { 'fl-node-selected': selected, 'fl-node-orphan': data.orphan, 'fl-node-incomplete': data.missing.length }]"
    :aria-pressed="selected ? 'true' : 'false'"
    :data-testid="`fl-node-${id}`"
    @click.stop="emit('choose', id)"
  >
    <Handle id="in" type="target" :position="Position.Left" />
    <span class="fl-node-head">
      <span class="fl-node-icon" aria-hidden="true"><i :class="['pi', icon]" /></span>
      <span class="fl-node-titles">
        <span class="fl-node-name">{{ id }}</span>
        <span class="fl-node-kind">{{ kindLabel }}</span>
      </span>
    </span>
    <span class="fl-node-summary">{{ summary }}</span>
    <span v-if="data.missing.length || data.orphan" class="fl-node-tags">
      <span v-if="data.missing.length" class="fl-tag fl-tag-warn" :title="data.missing.join(', ')" :data-testid="`fl-incomplete-${id}`">
        <i class="pi pi-exclamation-triangle" aria-hidden="true" />{{ t.flowBuilder.incomplete }}
      </span>
      <span v-if="data.orphan" class="fl-tag" :title="t.flowBuilder.orphanHint" :data-testid="`fl-orphan-${id}`">{{ t.flowBuilder.orphan }}</span>
    </span>
    <span v-if="data.outlets.length" class="fl-outlets">
      <span v-for="o in data.outlets" :key="o.key" class="fl-outlet" :class="`fl-outlet-${o.tone}`">
        <span class="fl-outlet-label">{{ outletLabel(o) }}</span>
        <Handle :id="o.key" type="source" :position="Position.Right" class="fl-handle-out" :data-testid="`fl-out-${id}-${o.key}`" />
      </span>
    </span>
  </button>
</template>
