<script setup lang="ts">
import Button from 'primevue/button'
import Password from 'primevue/password'
import { ref, watch } from 'vue'
import { t } from '@/i18n'

/**
 * Un champ `Password` : le serveur ne renvoie jamais le secret, seulement `********` quand il en existe un. Tant qu'on n'y touche pas, ce masque repart tel quel
 * (« inchangé ») ; « Changer » ouvre une saisie vide, et le NOUVEAU secret remplace l'ancien à l'enregistrement.
 */
const MASK = '********'
const props = defineProps<{ modelValue: unknown; inputId: string; invalid?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const kept = ref(props.modelValue === MASK)
watch(() => props.modelValue, (v) => (kept.value = v === MASK))

const draft = (v: unknown): string => (typeof v === 'string' && v !== MASK ? v : '')
const change = () => {
  kept.value = false
  emit('update:modelValue', null)
}
</script>

<template>
  <div v-if="kept" class="secret-set" data-testid="secret-set">
    <span class="secret-dots" :aria-label="t.form.secretSet">••••••••</span>
    <span class="desk-muted">{{ t.form.secretSet }}</span>
    <Button :label="t.form.secretChange" text size="small" severity="secondary" data-testid="secret-change" @click="change" />
  </div>
  <div v-else class="secret-edit">
    <Password :input-id="inputId" :model-value="draft(modelValue)" :feedback="false" toggle-mask fluid autocomplete="new-password" :placeholder="t.form.secretPlaceholder" :invalid="invalid" data-testid="secret-input" @update:model-value="emit('update:modelValue', $event || null)" />
  </div>
</template>
