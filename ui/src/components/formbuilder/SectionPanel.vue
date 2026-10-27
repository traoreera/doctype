<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import { computed } from 'vue'
import { t } from '@/i18n'
import { MAX_COLUMNS, moveSection, removeSection, setColumns, updateSection, type Board } from '@/lib/formbuilder'
import type { Selection } from './FormCanvas.vue'

/** Les propriétés d'une section (titre, colonnes, repli, place), partagées par les deux constructeurs. */
const props = defineProps<{ board: Board; tab: number; section: number; canEdit: boolean }>()
const emit = defineEmits<{ (e: 'update:board', value: Board): void; (e: 'update:selected', value: Selection): void }>()

const current = computed(() => props.board.tabs[props.tab]?.sections[props.section] ?? null)
function by(delta: number): void {
  const next = moveSection(props.board, { tab: props.tab, section: props.section }, { tab: props.tab, index: props.section + (delta > 0 ? 2 : -1) })
  emit('update:board', next)
  emit('update:selected', { kind: 'section', tab: props.tab, section: Math.max(0, Math.min(props.section + delta, next.tabs[props.tab]!.sections.length - 1)) })
}
function remove(): void {
  emit('update:board', removeSection(props.board, props.tab, props.section))
  emit('update:selected', null)
}
</script>

<template>
  <div v-if="current" class="fb-panel-body" data-testid="fb-section-panel">
    <p class="wf-kicker">{{ t.formBuilder.section }}</p>
    <h2 class="wf-panel-title">{{ current.section || t.formBuilder.untitled }}</h2>
    <label class="wf-field" for="fb-section-name">
      <span>{{ t.formBuilder.sectionTitle }}</span>
      <InputText
        id="fb-section-name"
        :model-value="current.section ?? ''"
        :disabled="!canEdit"
        size="small"
        data-testid="fb-section-name"
        @update:model-value="emit('update:board', updateSection(board, tab, section, { section: $event || null }))"
      />
    </label>
    <div class="wf-field">
      <span>{{ t.formBuilder.columns }}</span>
      <div class="fb-inline" role="group" :aria-label="t.formBuilder.columns">
        <Button
          v-for="n in MAX_COLUMNS"
          :key="n"
          :label="String(n)"
          size="small"
          :outlined="current.columns.length !== n"
          :disabled="!canEdit"
          :aria-pressed="current.columns.length === n ? 'true' : 'false'"
          :data-testid="`fb-columns-${n}`"
          @click="emit('update:board', setColumns(board, tab, section, n))"
        />
      </div>
    </div>
    <label class="wf-check">
      <Checkbox :model-value="current.collapsible" binary input-id="fb-collapsible" :disabled="!canEdit" @update:model-value="emit('update:board', updateSection(board, tab, section, { collapsible: $event === true }))" />
      <span>{{ t.formBuilder.collapsible }}</span>
    </label>
    <label class="wf-check">
      <Checkbox
        :model-value="current.collapsed"
        binary
        input-id="fb-collapsed"
        :disabled="!canEdit || !current.collapsible"
        @update:model-value="emit('update:board', updateSection(board, tab, section, { collapsed: $event === true }))"
      />
      <span>{{ t.formBuilder.collapsed }}</span>
    </label>
    <div v-if="canEdit" class="fb-inline">
      <Button icon="pi pi-arrow-up" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.up" :title="t.formBuilder.up" @click="by(-1)" />
      <Button icon="pi pi-arrow-down" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.down" :title="t.formBuilder.down" data-testid="fb-section-down" @click="by(1)" />
      <Button :label="t.formBuilder.removeSection" icon="pi pi-trash" text severity="secondary" size="small" data-testid="fb-remove-section" @click="remove" />
    </div>
    <p class="desk-muted wf-help">{{ t.formBuilder.removeSectionHint }}</p>
  </div>
</template>
