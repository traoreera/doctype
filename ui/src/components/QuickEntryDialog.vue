<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { computed, ref, shallowRef, watch } from 'vue'
import FieldControl from '@/components/FieldControl.vue'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { fieldStates, mapServerErrors, quickEntryFields, startValues, toPayload, validate, type Values } from '@/lib/form'
import { useServices } from '@/lib/services'
import type { DocRecord, DocTypeMeta } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * La saisie rapide : créer un document depuis un champ Link, sans quitter le formulaire en cours. On ne demande que l'indispensable (les champs obligatoires et le titre),
 * le reste prendra ses valeurs par défaut ; le document créé est aussitôt le choix du champ. Un type qui exige un tableau ou un fichier ne se saisit pas ainsi
 * (`quickEntryFields` rend `null`) : le champ propose alors la fiche complète.
 */
const props = defineProps<{ visible: boolean; doctype: string; initial?: string }>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; created: [record: DocRecord] }>()

const { api } = useServices()
const metaStore = useMeta()

const meta = shallowRef<DocTypeMeta | null>(null)
const values = ref<Values>({})
const errors = ref<Record<string, string>>({})
const banner = ref<string | null>(null)
const failure = ref<string | null>(null)
const saving = ref(false)
const name = ref('')
const formId = `q${Math.random().toString(36).slice(2, 8)}`

const fields = computed(() => (meta.value ? (quickEntryFields(meta.value) ?? []) : []))
const prompt = computed(() => meta.value?.naming === 'prompt')
const states = computed(() => (meta.value ? fieldStates(meta.value, values.value, null) : {}))

watch(
  () => props.visible,
  async (open) => {
    if (!open) return
    meta.value = null
    failure.value = null
    banner.value = null
    errors.value = {}
    name.value = ''
    try {
      const m = await metaStore.get(props.doctype)
      meta.value = m
      const start = startValues(m)
      // Ce que l'utilisateur avait tapé dans le champ devient le titre du nouveau document (ou son nom, s'il se saisit).
      const typed = props.initial?.trim()
      if (typed) {
        if (m.naming === 'prompt') name.value = typed
        else if (m.title_field) start[m.title_field] = typed
      }
      values.value = start
    } catch (e) {
      failure.value = describeError(e)
    }
  },
  { immediate: true },
)

function edit(field: string, value: unknown): void {
  values.value = { ...values.value, [field]: value }
  if (errors.value[field]) errors.value = Object.fromEntries(Object.entries(errors.value).filter(([k]) => k !== field))
}

async function save(): Promise<void> {
  const m = meta.value
  if (!m || saving.value) return
  banner.value = null
  const found = validate(m, values.value, states.value)
  if (prompt.value && !name.value.trim()) found.name = t.form.required
  if (Object.keys(found).length) {
    errors.value = found
    banner.value = t.form.fixErrors
    return
  }
  saving.value = true
  try {
    const data = toPayload(m, values.value)
    const rec = prompt.value ? await api.create(props.doctype, data, name.value.trim()) : await api.create(props.doctype, data)
    emit('created', rec)
    emit('update:visible', false)
  } catch (e) {
    if (e instanceof ApiError && (e.status === 422 || e.status === 409 || e.code.startsWith('hook_'))) {
      const mapped = mapServerErrors(e, m)
      errors.value = mapped.fields
      banner.value = mapped.general
    } else banner.value = describeError(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog :visible="visible" modal :header="t.quickEntry.title(meta?.label ?? doctype)" :style="{ width: '30rem', maxWidth: '95vw' }" data-testid="quick-entry" @update:visible="emit('update:visible', $event)">
    <Message v-if="failure" severity="error">{{ failure }}</Message>
    <form v-else-if="meta" class="quick-form" @submit.prevent="save">
      <div v-if="prompt" class="form-field" data-field="name">
        <label :for="`${formId}-name`">{{ t.form.nameLabel }}<span class="req" aria-hidden="true">*</span></label>
        <InputText :id="`${formId}-name`" v-model="name" :invalid="!!errors.name" maxlength="140" autocomplete="off" fluid data-testid="quick-name" />
        <span v-if="errors.name" class="error" role="alert">{{ errors.name }}</span>
      </div>
      <FieldControl
        v-for="f in fields"
        :key="f.fieldname"
        :field="f"
        :model-value="values[f.fieldname]"
        :required="states[f.fieldname]?.required ?? f.required"
        :read-only="false"
        :error="errors[f.fieldname]"
        :form-id="formId"
        :doctype="doctype"
        @update:model-value="edit(f.fieldname, $event)"
      />
      <Message v-if="banner" severity="error" class="banner-error" data-testid="quick-banner">{{ banner }}</Message>
      <button type="submit" hidden />
    </form>
    <p v-else class="desk-muted">{{ t.loading }}</p>
    <template #footer>
      <Button :label="t.cancel" severity="secondary" text @click="emit('update:visible', false)" />
      <Button :label="t.quickEntry.create" icon="pi pi-check" :loading="saving" :disabled="!meta" data-testid="quick-save" @click="save" />
    </template>
  </Dialog>
</template>

<style scoped>
.quick-form {
  display: grid;
  gap: 0.875rem;
}
</style>
