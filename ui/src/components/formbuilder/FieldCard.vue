<script setup lang="ts">
import { t } from '@/i18n'
import { DRAG_FIELD } from '@/lib/formbuilder'

/**
 * Un champ dans le Form Builder : une carte qu'on glisse (souris) ou qu'on choisit (clavier, c'est un BOUTON) pour ouvrir ses propriétés.
 * Elle dit d'un coup d'œil ce qui compte : le libellé que verra l'utilisateur, le type, et ce qui le contraint (obligatoire, lecture
 * seule, masqué) ou d'où il vient (ajouté par l'organisation).
 */
defineProps<{
  name: string
  label: string
  fieldtype: string
  mandatory?: boolean
  readOnly?: boolean
  hidden?: boolean
  custom?: boolean
  selected?: boolean
  /** Glisser n'est offert qu'à qui peut enregistrer. */
  draggable?: boolean
}>()
const emit = defineEmits<{ (e: 'choose', name: string): void }>()

function start(e: DragEvent, name: string): void {
  e.dataTransfer?.setData(DRAG_FIELD, name)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'
}
</script>

<template>
  <button
    type="button"
    class="fb-field"
    :class="{ 'fb-field-selected': selected, 'fb-field-hidden': hidden }"
    :draggable="draggable ? 'true' : 'false'"
    :data-field="name"
    :data-testid="`fb-field-${name}`"
    :aria-pressed="selected ? 'true' : 'false'"
    @click="emit('choose', name)"
    @dragstart="start($event, name)"
  >
    <span v-if="draggable" class="pi pi-bars fb-grip" aria-hidden="true" />
    <span class="fb-field-main">
      <span class="fb-field-label">{{ label }}<span v-if="mandatory" class="fb-required" :title="t.overrideProps.mandatory"> *</span></span>
      <span class="fb-field-type">{{ fieldtype }}</span>
    </span>
    <span class="fb-badges">
      <span v-if="custom" class="fb-badge fb-badge-custom">{{ t.formBuilder.custom }}</span>
      <span v-if="readOnly" class="fb-badge" :title="t.overrideProps.readOnly"><span class="pi pi-lock" aria-hidden="true" /></span>
      <span v-if="hidden" class="fb-badge" :title="t.overrideProps.hidden"><span class="pi pi-eye-slash" aria-hidden="true" /></span>
    </span>
  </button>
</template>
