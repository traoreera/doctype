<script setup lang="ts">
import { t } from '@/i18n'

/**
 * La barre d'onglets d'une fiche (motif « tabs » de l'APG : flèches, Début, Fin ; l'onglet suit le focus). Elle ne cache rien elle-même :
 * FormView garde TOUS les champs montés et ne montre que les sections de l'onglet ouvert, pour que la validation, le focus d'une erreur et
 * la saisie en cours ne dépendent pas de l'onglet. Une pastille dit combien d'erreurs attendent dans chaque onglet.
 */
const props = defineProps<{ tabs: string[]; current: string | null; errors: Record<string, number>; idPrefix: string; panelId: string }>()
const emit = defineEmits<{ (e: 'select', tab: string): void }>()

const tabId = (i: number) => `${props.idPrefix}-tab-${i}`

function move(from: number, to: number): void {
  const n = props.tabs.length
  const i = ((to % n) + n) % n
  if (i === from) return
  emit('select', props.tabs[i]!)
  document.getElementById(tabId(i))?.focus()
}

function onKey(e: KeyboardEvent, i: number): void {
  const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: props.tabs.length - 1 }[e.key]
  if (to === undefined) return
  e.preventDefault()
  move(i, to)
}
</script>

<template>
  <div class="form-tabs" role="tablist" :aria-label="t.form.tabs" data-testid="form-tabs">
    <button
      v-for="(tab, i) in tabs"
      :id="tabId(i)"
      :key="tab"
      type="button"
      role="tab"
      :class="['form-tab', { active: tab === current }]"
      :aria-selected="tab === current"
      :aria-controls="panelId"
      :tabindex="tab === current ? 0 : -1"
      :data-testid="`form-tab-${tab}`"
      @click="emit('select', tab)"
      @keydown="onKey($event, i)"
    >
      <span>{{ tab }}</span>
      <span v-if="errors[tab]" class="form-tab-errors" :aria-label="t.form.tabErrors(errors[tab]!)" :data-testid="`form-tab-errors-${tab}`">{{ errors[tab] }}</span>
    </button>
  </div>
</template>
