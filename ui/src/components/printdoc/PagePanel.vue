<script setup lang="ts">
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import { ref } from 'vue'
import { t } from '@/i18n'
import type { Letterhead, Page } from '@/lib/printedit'

/**
 * La PAGE, dans le panneau de droite quand rien n'est choisi : format, orientation, marges, et le FOND PDF (le
 * papier préimprimé de l'entreprise, `process/impression.md` §5) — déposé ici, imprimé SOUS le contenu, sur la
 * première page ou sur toutes, avec des zones réservées en haut et en bas où le contenu ne descend pas.
 *
 * Le fond n'existe qu'au PDF : la feuille et l'aperçu HTML ne le montrent pas, d'où « Aperçu PDF ».
 * Présentation seulement : la vue fait les appels (dépôt, aperçu).
 */
const props = defineProps<{
  page: Page
  letterhead: Letterhead | null
  editable: boolean
  /** Le nom du PDF de fond déposé, s'il est connu. */
  backgroundName: string | null
  uploading: boolean
  /** L'aperçu PDF a besoin d'un document d'exemple. */
  canPreview: boolean
  previewing: boolean
}>()
const emit = defineEmits<{
  page: [patch: Partial<Page>]
  letterhead: [value: Letterhead | null]
  upload: [file: File]
  preview: []
}>()

const sizes = ['A3', 'A4', 'A5', 'B4', 'B5', 'Letter', 'Legal', 'Ledger'].map((s) => ({ value: s, label: s }))
const orientations = [
  { value: 'portrait', label: t.printBuilder.portrait },
  { value: 'landscape', label: t.printBuilder.landscape },
]
const pagesChoices = [
  { value: 'first', label: t.printBuilder.pdfFirst },
  { value: 'all', label: t.printBuilder.pdfAll },
]
const sides = ['top', 'right', 'bottom', 'left'] as const
const fileInput = ref<HTMLInputElement | null>(null)

function picked(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) emit('upload', file)
}
const setBackground = (patch: Partial<Letterhead>) => emit('letterhead', { ...(props.letterhead ?? {}), ...patch })
</script>

<template>
  <div class="pp" data-testid="pb-page-panel">
    <h2 class="pp-title">{{ t.printBuilder.page }}</h2>
    <div class="pp-pair">
      <label class="pp-field">
        <span>{{ t.printBuilder.pageSize }}</span>
        <Select :model-value="page.size" :options="sizes" option-label="label" option-value="value" :disabled="!editable" size="small" fluid data-testid="pb-page-size" @update:model-value="emit('page', { size: $event })" />
      </label>
      <label class="pp-field">
        <span>{{ t.printBuilder.orientation }}</span>
        <Select :model-value="page.orientation" :options="orientations" option-label="label" option-value="value" :disabled="!editable" size="small" fluid data-testid="pb-orientation" @update:model-value="emit('page', { orientation: $event })" />
      </label>
    </div>
    <fieldset class="pp-margins">
      <legend>{{ t.printBuilder.margins }}</legend>
      <label v-for="side in sides" :key="side" class="pp-field">
        <span>{{ t.printBuilder.sides[side] }}</span>
        <InputNumber :model-value="page[side]" :min="0" :max="100" :disabled="!editable" size="small" fluid :data-testid="`pb-margin-${side}`" @update:model-value="emit('page', { [side]: $event ?? 0 })" />
      </label>
    </fieldset>

    <h2 class="pp-title">{{ t.printBuilder.background }}</h2>
    <p class="desk-muted pp-hint">{{ t.printBuilder.backgroundHint }}</p>
    <input ref="fileInput" type="file" accept="application/pdf,.pdf" class="pp-file" data-testid="pb-background-file" @change="picked" />
    <template v-if="letterhead?.pdf">
      <p class="pp-chosen" data-testid="pb-background-name"><i class="pi pi-file-pdf" aria-hidden="true" /> {{ backgroundName || t.printBuilder.pdfChosen }}</p>
      <div class="pp-field">
        <span>{{ t.printBuilder.pdfPages }}</span>
        <SelectButton
          :model-value="letterhead.pages ?? 'all'"
          :options="pagesChoices"
          option-label="label"
          option-value="value"
          :allow-empty="false"
          :disabled="!editable"
          size="small"
          data-testid="pb-background-pages"
          @update:model-value="setBackground({ pages: $event })"
        />
      </div>
      <div class="pp-pair">
        <label class="pp-field">
          <span>{{ t.printBuilder.offsetTop }}</span>
          <InputNumber :model-value="letterhead.offset_top ?? 0" :min="0" :max="150" :disabled="!editable" size="small" fluid data-testid="pb-background-top" @update:model-value="setBackground({ offset_top: $event ?? 0 })" />
        </label>
        <label class="pp-field">
          <span>{{ t.printBuilder.offsetBottom }}</span>
          <InputNumber :model-value="letterhead.offset_bottom ?? 0" :min="0" :max="150" :disabled="!editable" size="small" fluid data-testid="pb-background-bottom" @update:model-value="setBackground({ offset_bottom: $event ?? 0 })" />
        </label>
      </div>
      <p class="desk-muted pp-hint">{{ t.printBuilder.offsetHint }}</p>
      <div class="pp-actions">
        <Button :label="t.printBuilder.pdfPreview" icon="pi pi-download" size="small" severity="secondary" :loading="previewing" :disabled="!canPreview || previewing" data-testid="pb-background-preview" @click="emit('preview')" />
        <Button :label="t.printBuilder.pdfReplace" icon="pi pi-upload" size="small" text :loading="uploading" :disabled="!editable || uploading" data-testid="pb-background-replace" @click="fileInput?.click()" />
        <Button :label="t.printBuilder.pdfRemove" icon="pi pi-trash" size="small" text severity="danger" :disabled="!editable" data-testid="pb-background-remove" @click="emit('letterhead', null)" />
      </div>
    </template>
    <Button v-else :label="t.printBuilder.pdfImport" icon="pi pi-upload" size="small" severity="secondary" :loading="uploading" :disabled="!editable || uploading" data-testid="pb-background-upload" @click="fileInput?.click()" />
  </div>
</template>

<style scoped>
.pp {
  display: grid;
  gap: 0.6rem;
}

.pp-title {
  font-size: 0.9rem;
  margin: 0.25rem 0 0;
}

.pp-hint {
  font-size: 0.75rem;
  margin: 0;
}

.pp-field {
  display: grid;
  gap: 0.25rem;
  font-size: 0.8rem;
  min-width: 0;
}

.pp-field > span {
  color: var(--p-text-muted-color);
}

.pp-pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.5rem;
}

.pp-margins {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.4rem 0.5rem;
  border: 0;
  margin: 0;
  padding: 0;
  min-width: 0;
}

.pp-margins > legend {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
  padding: 0;
  margin-bottom: 0.25rem;
}

.pp-file {
  display: none;
}

.pp-chosen {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  margin: 0;
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}

.pp-chosen i {
  color: var(--p-primary-color);
}

.pp-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
</style>
