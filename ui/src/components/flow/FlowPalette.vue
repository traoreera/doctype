<script setup lang="ts">
import { t } from '@/i18n'
import { nodeKey, type PluginNode } from '@/lib/flowcatalog'
import { STEP_ICONS, STEP_MIME, type StepKind } from '@/lib/flowgraph'
import { piIcon } from '@/lib/icons'

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

/** Les nœuds que les PLUGINS déclarent (`lib/flowcatalog`) : une action IPC à eux, réglée par un formulaire comme les autres. */
const props = defineProps<{ nodes?: PluginNode[] }>()
const emit = defineEmits<{ (e: 'add', kind: StepKind): void; (e: 'add-node', node: PluginNode): void }>()

/** Ce que porte le glisser : un type d'étape, ou `node:<plugin>:<action>` pour un nœud de plugin (la page le relit au lâcher). */
function start(event: DragEvent, kind: string): void {
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

    <template v-if="props.nodes?.length">
      <h3 class="fl-palette-title fl-palette-sub">{{ t.flowBuilder.pluginNodes }}</h3>
      <button
        v-for="node in props.nodes"
        :key="nodeKey(node)"
        type="button"
        class="fl-chip fl-kind-action"
        draggable="true"
        :title="node.description ?? node.label"
        :data-testid="`fl-add-node-${nodeKey(node)}`"
        @dragstart="start($event, `node:${nodeKey(node)}`)"
        @click="emit('add-node', node)"
      >
        <span class="fl-chip-icon" aria-hidden="true"><i :class="piIcon(node.icon, 'bolt')" /></span>
        <span class="fl-chip-text">
          <span class="fl-chip-label">{{ node.label }}</span>
          <span class="fl-chip-hint">{{ node.description ?? node.plugin }}</span>
        </span>
      </button>
    </template>
  </aside>
</template>
