<script setup lang="ts">
import Button from 'primevue/button'
import { Panel, useVueFlow } from '@vue-flow/core'
import { onBeforeUnmount, onMounted } from 'vue'
import { t } from '@/i18n'

/** Zoom et recadrage du canevas. Monté DANS `VueFlow` : `useVueFlow()` y trouve le graphe de la page.
 *
 * Le graphe est recadré quand le canevas change de taille (téléphone tourné, fenêtre agrandie, panneau qui passe dessous) : Vue Flow ne
 * cadre qu'au premier affichage, et le circuit restait sinon tassé dans un coin. */
const props = defineProps<{ /** Plafond du zoom quand on recadre (un graphe d'une carte ne doit pas remplir l'écran). */ maxZoom?: number }>()
const emit = defineEmits<{ (e: 'arrange'): void }>()
const fit = () => fitView({ padding: 0.2, ...(props.maxZoom ? { maxZoom: props.maxZoom } : {}) })
const { zoomIn, zoomOut, fitView, vueFlowRef } = useVueFlow()

let observer: ResizeObserver | null = null
let last = ''
onMounted(() => {
  if (typeof ResizeObserver === 'undefined' || !vueFlowRef.value) return
  observer = new ResizeObserver(([entry]) => {
    const size = `${Math.round(entry?.contentRect.width ?? 0)}x${Math.round(entry?.contentRect.height ?? 0)}`
    if (size === last) return
    const first = last === ''
    last = size
    if (!first) void fit() // le premier cadrage est celui de `fit-view-on-init`
  })
  observer.observe(vueFlowRef.value)
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <Panel position="top-left" class="wf-controls">
    <Button icon="pi pi-plus" text rounded severity="secondary" size="small" :aria-label="t.workflowBuilder.zoomIn" :title="t.workflowBuilder.zoomIn" @click="zoomIn()" />
    <Button icon="pi pi-minus" text rounded severity="secondary" size="small" :aria-label="t.workflowBuilder.zoomOut" :title="t.workflowBuilder.zoomOut" @click="zoomOut()" />
    <Button icon="pi pi-expand" text rounded severity="secondary" size="small" :aria-label="t.workflowBuilder.fit" :title="t.workflowBuilder.fit" data-testid="wf-fit" @click="fit()" />
    <Button icon="pi pi-sitemap" text rounded severity="secondary" size="small" :aria-label="t.workflowBuilder.arrange" :title="t.workflowBuilder.arrange" data-testid="wf-arrange" @click="emit('arrange')" />
  </Panel>
</template>
