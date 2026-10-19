<script setup lang="ts">
import AutoComplete from 'primevue/autocomplete'
import { ref, watch } from 'vue'
import { useServices } from '@/lib/services'
import { useLinks } from '@/stores/links'

const props = defineProps<{ modelValue: unknown; target: string; inputId?: string; invalid?: boolean; readonly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

interface Option {
  name: string
  label: string
}

const { api } = useServices()
const links = useLinks()

const shown = ref<string | Option | null>(null)
const options = ref<Option[]>([])

const asName = (v: unknown): string => (typeof v === 'string' ? v : '')

// Affiche le titre du document lié, pas son identifiant ; se remet à jour si la valeur change de l'extérieur.
watch(
  () => [props.modelValue, props.target] as const,
  async ([value, target]) => {
    const name = asName(value)
    if (!name) {
      shown.value = null
      return
    }
    await links.resolve(target, [name])
    shown.value = { name, label: links.title(target, name) }
  },
  { immediate: true },
)

async function complete(e: { query: string }): Promise<void> {
  try {
    const found = await api.search(props.target, e.query.trim(), 10)
    options.value = found.map((i) => ({ name: i.name, label: i.title || i.name }))
  } catch {
    options.value = []
  }
}

function select(e: { value: Option }): void {
  emit('update:modelValue', e.value.name)
}

function onInput(v: unknown): void {
  shown.value = v as string | Option | null
  // Champ vidé à la main : la valeur est effacée. Un texte libre n'est jamais envoyé (forceSelection).
  if (v === '' || v === null) emit('update:modelValue', null)
}
</script>

<template>
  <RouterLink v-if="readonly && asName(modelValue)" class="value cell-link" :to="{ name: 'form', params: { doctype: target, name: asName(modelValue) } }" data-testid="link-open">
    {{ links.title(target, asName(modelValue)) }}
  </RouterLink>
  <span v-else-if="readonly" class="value" />
  <div v-else class="link-field">
  <AutoComplete
    :model-value="shown"
    :suggestions="options"
    option-label="label"
    force-selection
    dropdown
    :invalid="invalid"
    :input-id="inputId"
    fluid
    @update:model-value="onInput"
    @complete="complete"
    @option-select="select"
  >
    <template #option="{ option }">
      <span>{{ option.label }}</span>
      <small v-if="option.label !== option.name" class="desk-muted" style="margin-left: 0.5rem">{{ option.name }}</small>
    </template>
  </AutoComplete>
  <!-- Ouvrir la fiche du document lié (comme la flèche d'un champ lien de Frappe). -->
  <RouterLink v-if="asName(modelValue)" class="link-open" :to="{ name: 'form', params: { doctype: target, name: asName(modelValue) } }" :aria-label="links.title(target, asName(modelValue))" :title="links.title(target, asName(modelValue))" data-testid="link-open">
    <i class="pi pi-arrow-up-right" />
  </RouterLink>
  </div>
</template>
