<script setup lang="ts">
import { NodeViewWrapper, nodeViewProps } from '@tiptap/vue-3'
import { t } from '@/i18n'

defineProps(nodeViewProps)
</script>

<template>
  <NodeViewWrapper :class="['pd-break', { selected }]" contenteditable="false" data-drag-handle>{{ t.printDoc.pageBreak }}</NodeViewWrapper>
</template>
