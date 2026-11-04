<script setup lang="ts">
import { ref } from 'vue'
import { t } from '@/i18n'
import { TABLE_STYLES, type TableStyle } from '@/lib/tablestyles'
import './table-styles.css'

/**
 * La galerie des styles de tableau, façon Word : une vignette par style, DESSINÉE avec les règles mêmes de la
 * feuille (`table-styles.css`, jumelle du PDF), pour le tableau des lignes comme pour un tableau de mise en page.
 * Un groupe de boutons radio : flèches pour changer, un seul arrêt de tabulation.
 */
const props = defineProps<{ modelValue: TableStyle; disabled?: boolean; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [style: TableStyle] }>()
const buttons = ref<HTMLButtonElement[]>([])

function choose(style: TableStyle): void {
  if (!props.disabled && style !== props.modelValue) emit('update:modelValue', style)
}

function onKey(e: KeyboardEvent, index: number): void {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]
  if (!step) return
  e.preventDefault()
  const next = (index + step + TABLE_STYLES.length) % TABLE_STYLES.length
  choose(TABLE_STYLES[next]!)
  buttons.value[next]?.focus()
}
</script>

<template>
  <div class="ts-gallery" role="radiogroup" :aria-label="label" data-testid="table-styles">
    <button
      v-for="(style, i) in TABLE_STYLES"
      :key="style"
      ref="buttons"
      type="button"
      role="radio"
      class="ts-choice"
      :class="{ chosen: style === modelValue }"
      :aria-checked="style === modelValue"
      :tabindex="style === modelValue ? 0 : -1"
      :disabled="disabled"
      :title="t.printBuilder.styles[style]"
      :data-testid="`table-style-${style}`"
      @click="choose(style)"
      @keydown="onKey($event, i)"
    >
      <span class="ts-thumb print-sheet" aria-hidden="true">
        <table :class="['lines', style]">
          <thead>
            <tr><th /><th /><th /></tr>
          </thead>
          <tbody>
            <tr v-for="r in 3" :key="r"><td /><td /><td /></tr>
          </tbody>
        </table>
      </span>
      <span class="ts-name">{{ t.printBuilder.styles[style] }}</span>
    </button>
  </div>
</template>

<style scoped>
.ts-gallery {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.4rem;
}

.ts-choice {
  display: grid;
  gap: 0.25rem;
  justify-items: center;
  padding: 0.35rem 0.25rem 0.3rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-sm, 4px);
  background: var(--p-content-background);
  color: var(--p-text-color);
  font: inherit;
  font-size: 0.72rem;
  cursor: pointer;
  min-width: 0;
}

.ts-choice:hover:not(:disabled) {
  border-color: var(--p-primary-color);
}

.ts-choice.chosen {
  border-color: var(--p-primary-color);
  box-shadow: 0 0 0 1px var(--p-primary-color);
}

.ts-choice:disabled {
  cursor: default;
  opacity: 0.6;
}

.ts-choice:focus-visible {
  outline: 2px solid var(--p-primary-color);
  outline-offset: 2px;
}

/* La vignette est du PAPIER : blanche dans les deux thèmes, comme la feuille. */
.ts-thumb {
  display: block;
  width: 100%;
  padding: 4px;
  background: var(--desk-paper);
  border-radius: 2px;
}

.ts-thumb table {
  width: 100%;
  table-layout: fixed;
}

.ts-thumb :is(th, td) {
  height: 6px;
  padding: 0;
}

.ts-name {
  line-height: 1.2;
  text-align: center;
  overflow-wrap: anywhere;
}
</style>
