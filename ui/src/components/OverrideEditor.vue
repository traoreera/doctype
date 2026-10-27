<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import Textarea from 'primevue/textarea'
import { ref, watch } from 'vue'
import { t } from '@/i18n'

/**
 * Éditeur d'habillage en JSON, validé par le SERVEUR (le Desk n'invente aucune règle) : il sert à l'habillage d'un ESPACE de travail
 * (`BlockEditor`, « Modifier le JSON »). Le formulaire a son propre éditeur visuel, le Form Builder (`FormBuilderView`, « Personnaliser »).
 */
const props = defineProps<{ visible: boolean; title: string; initial: unknown; saving?: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', value: boolean): void; (e: 'save', config: unknown): void; (e: 'reset'): void }>()

const text = ref('{}')
const parseError = ref<string | null>(null)

watch(
  () => [props.visible, props.initial] as const,
  () => {
    if (!props.visible) return
    text.value = JSON.stringify(props.initial ?? {}, null, 2)
    parseError.value = null
  },
  { immediate: true },
)

function submit(): void {
  let parsed: unknown
  try {
    parsed = JSON.parse(text.value)
  } catch {
    parseError.value = t.override.invalidJson
    return
  }
  parseError.value = null
  emit('save', parsed)
}
</script>

<template>
  <Dialog :visible="visible" modal :header="title" :style="{ width: '680px', maxWidth: '95vw' }" data-testid="override-dialog" @update:visible="emit('update:visible', $event)">
    <p class="desk-muted" style="margin-top: 0">{{ t.override.hint }}</p>
    <Textarea v-model="text" :rows="16" auto-resize class="override-text" data-testid="override-text" aria-label="override-json" />
    <Message v-if="parseError" severity="error" class="banner-error" data-testid="override-parse-error">{{ parseError }}</Message>
    <template #footer>
      <Button :label="t.override.reset" severity="secondary" text :loading="saving" data-testid="override-reset" @click="emit('reset')" />
      <Button :label="t.cancel" severity="secondary" text @click="emit('update:visible', false)" />
      <Button :label="t.save" icon="pi pi-check" :loading="saving" data-testid="override-save" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.override-text {
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.82rem;
}
</style>
