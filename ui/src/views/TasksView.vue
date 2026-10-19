<script setup lang="ts">
import Button from 'primevue/button'
import { computed, onMounted, ref, watch } from 'vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { isOverdue } from '@/lib/feed'
import { formatDate, todayIn } from '@/lib/format'
import { initials } from '@/lib/navstate'
import { useServices } from '@/lib/services'
import type { AssignmentItem, AssignmentStatus } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useTasks } from '@/stores/tasks'
import { useUsers } from '@/stores/users'

/**
 * « Mes tâches » : les documents qu'on m'a confiés, l'échéance la plus proche d'abord. Une tâche se termine d'ici ; un clic ouvre son document.
 * Le serveur ne montre pas une tâche dont je ne peux plus lire le document.
 */
const { api } = useServices()
const notify = useNotify()
const links = useLinks()
const tasks = useTasks()
const users = useUsers()

const view = ref<'open' | 'done'>('open')
const items = ref<AssignmentItem[] | null>(null)
const failure = ref<string | null>(null)
const busy = ref<string | null>(null)
const today = computed(() => todayIn())
let ticket = 0

async function load(): Promise<void> {
  const mine = ++ticket
  failure.value = null
  try {
    const got = await api.myAssignments(view.value as AssignmentStatus)
    if (mine !== ticket) return
    items.value = got
    // Le titre que l'utilisateur reconnaît (le nom du client, pas un identifiant) : un lot par type de document.
    const byType = new Map<string, string[]>()
    for (const a of got) byType.set(a.doctype, [...(byType.get(a.doctype) ?? []), a.docname])
    for (const [type, names] of byType) void links.resolve(type, names).catch(() => {})
  } catch (e) {
    if (mine === ticket) failure.value = describeError(e)
  }
}

onMounted(load)
watch(view, () => {
  items.value = null
  void load()
})

async function finish(a: AssignmentItem): Promise<void> {
  busy.value = a.id
  try {
    await api.updateAssignment(a.id, { status: view.value === 'open' ? 'done' : 'open' })
    await load()
    void tasks.refresh()
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="desk-page" data-testid="tasks">
    <div class="desk-page-head"><i class="pi pi-check-square ws-icon" /><h1 data-testid="tasks-title">{{ t.collab.tasks.title }}</h1></div>
    <p class="desk-muted ws-description">{{ t.collab.tasks.intro }}</p>

    <div class="view-switcher" role="radiogroup" :aria-label="t.collab.tasks.title" style="margin-bottom: 1rem">
      <button type="button" role="radio" :aria-checked="view === 'open'" :class="['view-btn', { active: view === 'open' }]" data-testid="tasks-open" @click="view = 'open'"><span>{{ t.collab.tasks.open }}</span></button>
      <button type="button" role="radio" :aria-checked="view === 'done'" :class="['view-btn', { active: view === 'done' }]" data-testid="tasks-done" @click="view = 'done'"><span>{{ t.collab.tasks.done }}</span></button>
    </div>

    <p v-if="failure" class="desk-muted" data-testid="tasks-failed">{{ failure }}</p>
    <div v-else-if="items === null" class="desk-muted">{{ t.loading }}</div>
    <div v-else-if="!items.length" class="desk-card" style="padding: 2rem; text-align: center" data-testid="tasks-empty">
      <p class="desk-muted" style="margin: 0">{{ view === 'open' ? t.collab.tasks.empty : t.collab.tasks.emptyDone }}</p>
    </div>
    <ul v-else class="desk-card task-list">
      <li v-for="a in items" :key="a.id" class="task" data-testid="task">
        <span class="avatar" aria-hidden="true">{{ initials(users.label(a.assigned_by)) }}</span>
        <div class="task-body">
          <RouterLink class="desk-link task-title" :to="{ name: 'form', params: { doctype: a.doctype, name: a.docname } }" :title="t.collab.tasks.open_doc" data-testid="task-link">
            {{ links.title(a.doctype, a.docname) }}
          </RouterLink>
          <span class="desk-muted task-meta">
            {{ a.doctype_label ?? a.doctype }} · {{ t.collab.tasks.by(users.label(a.assigned_by)) }}<template v-if="a.description"> · {{ a.description }}</template>
          </span>
          <span v-if="a.due_date" :class="['task-meta', isOverdue(a, today) ? 'assignment-due overdue' : 'desk-muted']" data-testid="task-due">
            {{ t.collab.dueOn(formatDate(a.due_date)) }}<template v-if="isOverdue(a, today)"> · {{ t.collab.overdue }}</template>
          </span>
        </div>
        <Button :icon="view === 'open' ? 'pi pi-check' : 'pi pi-replay'" :label="view === 'open' ? t.collab.done : t.collab.reopen" text size="small" :severity="view === 'open' ? 'success' : 'secondary'" :loading="busy === a.id" data-testid="task-toggle" @click="finish(a)" />
      </li>
    </ul>
  </div>
</template>
