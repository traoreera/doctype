<script setup lang="ts">
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import { computed } from 'vue'
import { formatCell } from '@/lib/format'
import { tokenLabel } from '@/lib/printdoc'
import { docContext } from './extensions'

/**
 * Un champ inséré dans le texte. L'éditeur montre la VALEUR du document d'exemple (« Durand », « 29/10/2026 »),
 * comme sur la facture ; sans document, son libellé. Le survol rappelle toujours quel champ c'est.
 */
const props = defineProps(nodeViewProps)
const ctx = computed(() => docContext(props.editor))
const token = computed(() => String(props.node.attrs.value))
const label = computed(() => (ctx.value.meta ? tokenLabel(token.value, ctx.value.meta) : token.value))
const shown = computed(() => {
  const c = ctx.value
  if (token.value === 'name') return c.name || label.value
  if (token.value === 'doctype') return c.meta?.label || c.meta?.name || label.value
  const name = token.value.slice(4)
  const field = c.meta?.fields.find((f) => f.fieldname === name)
  const value = c.example?.[name]
  if (!field || value === null || value === undefined || value === '') return label.value
  if (field.fieldtype === 'Link' && typeof field.options === 'string' && typeof value === 'string') return c.linkTitle(field.options, value)
  if (field.fieldtype === 'Boolean') return value ? 'Oui' : 'Non'
  return formatCell(field, value) || label.value
})
</script>

<template>
  <NodeViewWrapper as="span" :class="['pd-token', { selected }]" :title="label" :data-token="token">{{ shown }}</NodeViewWrapper>
</template>
