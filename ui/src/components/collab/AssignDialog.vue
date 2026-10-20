<script setup lang="ts">
import AutoComplete from 'primevue/autocomplete'
import Button from 'primevue/button'
import DatePicker from 'primevue/datepicker'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { ref, watch } from 'vue'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { datePattern, dateFromIso, isoFromDate } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { BulkResult, Person } from '@/lib/types'
import { useUsers } from '@/stores/users'

/**
 * Assigner un document : à qui (parmi les personnes que l'organisation a déjà fait apparaître, ou n'importe quel identifiant), quoi faire, pour quand.
 * Avec `names` (la liste), c'est plusieurs documents d'un coup : chacun est jugé seul, `saved` porte le compte rendu.
 */
const props = defineProps<{ visible: boolean; doctype: string; name?: string; names?: readonly string[]; me?: string }>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; saved: [result?: BulkResult] }>()
const { api } = useServices()
const users = useUsers()

// Ce qu'on a choisi (une personne de la liste) ou tapé (un texte libre : le serveur le rapprochera d'un e-mail, ou le refusera).
const assignee = ref<Person | string>('')
const description = ref('')
const due = ref<string | null>(null)
const suggestions = ref<Person[]>([])
const idOf = (v: Person | string): string => (typeof v === 'string' ? v.trim() : v.id)
const saving = ref(false)
const problem = ref<string | null>(null)

watch(
  () => props.visible,
  (open) => {
    if (!open) return
    assignee.value = ''
    description.value = ''
    due.value = null
    problem.value = null
  },
)

async function search(e: { query: string }): Promise<void> {
  try {
    suggestions.value = await api.people(props.doctype, e.query)
  } catch {
    suggestions.value = [] // la saisie libre reste possible
  }
}

async function submit(): Promise<void> {
  const who = idOf(assignee.value)
  if (!who) {
    problem.value = t.collab.assigneeHint
    return
  }
  saving.value = true
  problem.value = null
  try {
    const body = { assignee: who, description: description.value.trim() || null, due_date: due.value }
    if (props.names) {
      const result = await api.assignMany(props.doctype, props.names, body)
      emit('update:visible', false)
      emit('saved', result)
    } else {
      await api.assign(props.doctype, props.name ?? '', body)
      emit('update:visible', false)
      emit('saved')
    }
  } catch (e) {
    problem.value = describeError(e) // « déjà assigné », échéance invalide… : le message du serveur est écrit pour l'utilisateur
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog :visible="visible" modal :header="t.collab.assign" :style="{ width: '440px', maxWidth: '95vw' }" data-testid="assign-dialog" @update:visible="emit('update:visible', $event)">
    <form class="assign-form" @submit.prevent="submit">
      <label class="bf-field">
        <span>{{ t.collab.assignee }}</span>
        <AutoComplete v-model="assignee" :suggestions="suggestions" option-label="name" :force-selection="false" dropdown fluid :placeholder="t.collab.assigneeHint" data-testid="assign-who" @complete="search" />
        <button v-if="me && idOf(assignee) !== me" type="button" class="assign-me" data-testid="assign-me" @click="assignee = { id: me, email: null, name: users.label(me) }">{{ t.collab.me }} ({{ users.label(me) }})</button>
      </label>
      <label class="bf-field"><span>{{ t.collab.description }}</span><InputText v-model="description" maxlength="500" data-testid="assign-description" /></label>
      <label class="bf-field">
        <span>{{ t.collab.dueDate }}</span>
        <DatePicker :model-value="dateFromIso(due)" :date-format="datePattern()" show-icon icon-display="input" show-button-bar fluid data-testid="assign-due" @update:model-value="due = $event instanceof Date ? isoFromDate($event) : null" />
      </label>
      <Message v-if="problem" severity="error" class="banner-error" data-testid="assign-problem">{{ problem }}</Message>
      <button type="submit" hidden />
    </form>
    <template #footer>
      <Button :label="t.cancel" severity="secondary" text @click="emit('update:visible', false)" />
      <Button :label="t.collab.assign" icon="pi pi-user-plus" :loading="saving" data-testid="assign-save" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.assign-form {
  display: grid;
  gap: 0.875rem;
}

.bf-field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.bf-field > span {
  color: var(--p-text-muted-color);
}

.assign-me {
  justify-self: start;
  border: 0;
  background: none;
  padding: 0;
  color: var(--p-primary-color);
  font: inherit;
  font-size: 0.8rem;
  cursor: pointer;
}
</style>
