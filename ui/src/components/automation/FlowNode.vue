<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import { computed } from 'vue'
import { t } from '@/i18n'
import type { Port } from '@/lib/automation'
import { statusTone } from '@/lib/xflow'

/**
 * Une carte du canevas, comme un nœud de n8n : une icône, un titre, ce qu'elle fait, et ses SORTIES nommées à droite (ensuite / en cas
 * d'erreur, vrai / faux, les cas d'un aiguillage…), chacune avec sa poignée. On relie en tirant d'une poignée de sortie vers une carte.
 * Le titre est un bouton : on choisit la carte au clavier comme à la souris. En lecture d'une exécution, la carte porte son statut.
 */
const props = defineProps<{
  id: string
  data: { title: string; subtitle: string; icon: string; ports: Port[]; trigger?: boolean; status?: string | null; problem?: boolean; canEdit: boolean }
  selected?: boolean
}>()
const emit = defineEmits<{ (e: 'choose', id: string): void }>()
const tone = computed(() => (props.data.status ? statusTone(props.data.status) : null))
</script>

<template>
  <div
    class="af-node"
    :class="{ 'af-node-selected': selected, 'af-node-trigger': data.trigger, 'af-node-problem': data.problem }"
    :data-tone="tone ?? undefined"
    :data-testid="`af-node-${id}`"
  >
    <Handle v-if="!data.trigger" id="in" type="target" :position="Position.Left" :connectable="data.canEdit" class="af-handle-in" />
    <button type="button" class="af-node-head" :aria-pressed="selected ? 'true' : 'false'" :data-testid="`af-choose-${id}`" @click.stop="emit('choose', id)">
      <span :class="data.icon" class="af-node-icon" aria-hidden="true" />
      <span class="af-node-text">
        <span v-if="data.trigger" class="af-node-kicker">{{ t.automations.triggerNode }}</span>
        <span class="af-node-title">{{ data.title }}</span>
        <span v-if="data.subtitle" class="af-node-sub">{{ data.subtitle }}</span>
      </span>
      <span v-if="data.status" class="flow-pill af-node-status" :data-tone="tone">{{ t.automations.status[data.status] ?? data.status }}</span>
      <span v-else-if="data.problem" class="pi pi-exclamation-triangle af-node-warn" :title="t.automations.incomplete" aria-hidden="true" />
    </button>
    <ul class="af-ports">
      <li v-for="p in data.ports" :key="p.id" class="af-port" :data-tone="p.tone">
        <span class="af-port-label">{{ p.label }}</span>
        <Handle :id="p.id" type="source" :position="Position.Right" :connectable="data.canEdit" class="af-handle-out" :data-testid="`af-port-${id}-${p.id}`" />
      </li>
    </ul>
  </div>
</template>
