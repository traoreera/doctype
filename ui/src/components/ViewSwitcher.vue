<script setup lang="ts">
import { computed } from 'vue'
import { t } from '@/i18n'
import type { ViewName } from '@/lib/types'

/** Le choix de la vue d'un type : Liste, Rapport, Tableau de bord, et ce que le type déclare (Kanban, Calendrier, Gantt). */
const props = defineProps<{ views: ViewName[]; modelValue: ViewName }>()
const emit = defineEmits<{ 'update:modelValue': [view: ViewName] }>()

const items = computed(() => props.views.map((v) => ({ value: v, label: t.views.names[v] ?? v, icon: t.views.icons[v] ?? 'pi-eye' })))

// Flèches gauche/droite d'un groupe de boutons radio : même confort clavier que le sélecteur d'un tableau de bord.
function move(delta: number): void {
  const i = props.views.indexOf(props.modelValue)
  const next = props.views[(i + delta + props.views.length) % props.views.length]
  if (next) emit('update:modelValue', next)
}
</script>

<template>
  <div class="view-switcher" role="radiogroup" :aria-label="t.views.label" data-testid="view-switcher">
    <button
      v-for="it in items"
      :key="it.value"
      type="button"
      role="radio"
      :aria-checked="it.value === modelValue"
      :tabindex="it.value === modelValue ? 0 : -1"
      :class="['view-btn', { active: it.value === modelValue }]"
      :title="it.label"
      :data-testid="`view-${it.value}`"
      @click="emit('update:modelValue', it.value)"
      @keydown.right.prevent="move(1)"
      @keydown.left.prevent="move(-1)"
    >
      <i :class="['pi', it.icon]" aria-hidden="true" />
      <span class="view-label">{{ it.label }}</span>
    </button>
  </div>
</template>
