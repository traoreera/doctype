<script setup lang="ts">
import { t } from '@/i18n'
import { STEP_ICONS, STEP_MIME, type StepKind } from '@/lib/flowgraph'

/**
 * La palette des étapes, à GLISSER sur le canevas (comme n8n) ou à cliquer pour les poser au bout du flux.
 *
 * Le glisser-déposer natif HTML5 (comme le Form Builder) : `dragstart` écrit le type, la page le lit dans `drop` et pose le nœud là où
 * le curseur l'a lâché. Le CLIC fait la même chose au clavier — une palette qui ne s'utilise qu'à la souris n'est pas utilisable.
 *
 * Elle ne propose que ce qui se règle SANS écrire de code. `transform` (une requête JMESPath) et `template` (du Jinja2) sont deux
 * outils de développeur : xflow les exécute toujours, l'éditeur les DESSINE toujours s'ils sont là, mais il ne les propose pas —
 * personne ne doit buter sur une syntaxe pour automatiser son travail.
 */
const OFFERED: readonly StepKind[] = ['action', 'condition', 'switch', 'parallel', 'foreach', 'wait', 'ai']

const emit = defineEmits<{ (e: 'add', kind: StepKind): void }>()

function start(event: DragEvent, kind: StepKind): void {
  event.dataTransfer?.setData(STEP_MIME, kind)
  event.dataTransfer?.setData('text/plain', kind) // repli : certains navigateurs n'autorisent le glisser qu'avec un type connu
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'copy'
}
</script>

<template>
  <aside class="fl-palette" :aria-label="t.flowBuilder.palette" data-testid="fl-palette">
    <h2 class="fl-palette-title">{{ t.flowBuilder.palette }}</h2>
    <p class="desk-muted fl-palette-hint">{{ t.flowBuilder.paletteHint }}</p>
    <button
      v-for="kind in OFFERED"
      :key="kind"
      type="button"
      class="fl-chip"
      :class="`fl-kind-${kind}`"
      draggable="true"
      :title="t.flowBuilder.kinds[kind].hint"
      :data-testid="`fl-add-${kind}`"
      @dragstart="start($event, kind)"
      @click="emit('add', kind)"
    >
      <span class="fl-chip-icon" aria-hidden="true"><i :class="['pi', STEP_ICONS[kind]]" /></span>
      <span class="fl-chip-text">
        <span class="fl-chip-label">{{ t.flowBuilder.kinds[kind].label }}</span>
        <span class="fl-chip-hint">{{ t.flowBuilder.kinds[kind].hint }}</span>
      </span>
    </button>
  </aside>
</template>
