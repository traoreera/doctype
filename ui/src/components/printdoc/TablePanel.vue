<script setup lang="ts">
import Button from 'primevue/button'
import { t } from '@/i18n'
import type { TableCommand, TableStyle } from '@/lib/tablestyles'
import TableStyleGallery from './TableStyleGallery.vue'

/**
 * Le tableau de MISE EN PAGE où se trouve le curseur, dans le panneau de droite : son style (la galerie partagée
 * avec le tableau des lignes) et ses outils — ils encombraient la barre de mise en forme au-dessus de la page.
 */
defineProps<{ tableStyle: TableStyle; editable: boolean }>()
const emit = defineEmits<{ style: [style: TableStyle]; command: [name: TableCommand] }>()
</script>

<template>
  <!-- `mousedown.prevent` : comme la barre d'outils, un clic ici ne retire pas le curseur du tableau. -->
  <div class="tp" data-testid="pd-table-panel" @mousedown.prevent>
    <h2 class="tp-title">{{ t.printDoc.layoutTable }}</h2>
    <div class="tp-field">
      <span>{{ t.printBuilder.style }}</span>
      <TableStyleGallery :model-value="tableStyle" :label="t.printBuilder.style" :disabled="!editable" @update:model-value="emit('style', $event)" />
    </div>
    <div class="tp-tools">
      <Button :label="t.printDoc.addRow" icon="pi pi-plus" text size="small" :disabled="!editable" data-testid="pd-add-row" @click="emit('command', 'addRowAfter')" />
      <Button :label="t.printDoc.addColumn" icon="pi pi-plus" text size="small" :disabled="!editable" data-testid="pd-add-col" @click="emit('command', 'addColumnAfter')" />
      <Button :label="t.printDoc.mergeCells" icon="pi pi-arrows-h" text size="small" :disabled="!editable" data-testid="pd-merge" @click="emit('command', 'mergeOrSplit')" />
      <Button :label="t.printDoc.deleteRow" icon="pi pi-minus" text size="small" severity="danger" :disabled="!editable" data-testid="pd-delete-row" @click="emit('command', 'deleteRow')" />
      <Button :label="t.printDoc.deleteColumn" icon="pi pi-minus" text size="small" severity="danger" :disabled="!editable" data-testid="pd-delete-col" @click="emit('command', 'deleteColumn')" />
      <Button :label="t.printDoc.deleteTable" icon="pi pi-trash" text size="small" severity="danger" :disabled="!editable" data-testid="pd-delete-table" @click="emit('command', 'deleteTable')" />
    </div>
  </div>
</template>

<style scoped>
.tp {
  display: grid;
  gap: 0.6rem;
}

.tp-title {
  font-size: 0.9rem;
  margin: 0;
}

.tp-field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.tp-field > span {
  color: var(--p-text-muted-color);
}

.tp-tools {
  display: grid;
  justify-items: start;
  gap: 0.1rem;
}
</style>
