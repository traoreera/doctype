<script setup lang="ts">
import InputText from 'primevue/inputtext'
import { ref } from 'vue'
import { t } from '@/i18n'
import { normalizeTag, sameTag } from '@/lib/tags'
import { useServices } from '@/lib/services'

/**
 * La saisie d'une étiquette : Entrée (ou une virgule) la valide, Échap abandonne. Les étiquettes déjà en usage dans le type (celles des documents
 * que l'utilisateur lit) sont proposées par une `datalist` native, lue une fois au premier focus : le navigateur filtre pendant la frappe.
 * Un nom refusé par les règles (`lib/tags.normalizeTag`, jumelles du serveur) est signalé sans aller-retour.
 */
const props = defineProps<{ doctype: string; inputId: string; exclude?: readonly string[]; autofocus?: boolean }>()
const emit = defineEmits<{ commit: [tag: string]; cancel: [] }>()
const { api } = useServices()

const text = ref('')
const invalid = ref(false)
const known = ref<string[]>([])
let loaded = false

async function load(): Promise<void> {
  if (loaded) return
  loaded = true
  try {
    known.value = (await api.tagCounts(props.doctype, { limit: 50 })).map((c) => c.tag)
  } catch {
    known.value = [] // sans propositions, la saisie reste possible
  }
}

function commit(): void {
  if (!text.value.trim()) return
  const tag = normalizeTag(text.value)
  if (tag === null) {
    invalid.value = true
    return
  }
  // L'écriture déjà en usage (« Urgent » quand on tape « urgent ») : le serveur la reprendrait de toute façon.
  emit('commit', known.value.find((k) => sameTag(k, tag)) ?? tag)
  text.value = ''
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    commit()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    text.value = ''
    emit('cancel')
  }
}
</script>

<template>
  <span class="tag-input">
    <InputText
      :id="inputId"
      v-model="text"
      size="small"
      :list="`${inputId}-known`"
      :placeholder="t.tags.placeholder"
      :aria-label="t.tags.add"
      :aria-invalid="invalid || undefined"
      :aria-describedby="invalid ? `${inputId}-error` : undefined"
      :invalid="invalid"
      :autofocus="autofocus"
      maxlength="80"
      autocomplete="off"
      data-testid="tag-input"
      @focus="void load()"
      @keydown="onKey"
      @update:model-value="invalid = false"
    />
    <datalist :id="`${inputId}-known`">
      <option v-for="k in known.filter((k) => !(exclude ?? []).some((x) => sameTag(x, k)))" :key="k" :value="k" />
    </datalist>
    <span v-if="invalid" :id="`${inputId}-error`" class="error" role="alert" data-testid="tag-invalid">{{ t.tags.invalid }}</span>
  </span>
</template>

<style scoped>
.tag-input {
  display: inline-grid;
  gap: 0.2rem;
}

.error {
  font-size: 0.8rem;
  color: var(--desk-danger);
}
</style>
