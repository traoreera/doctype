<script setup lang="ts">
import Button from 'primevue/button'
import { computed, ref } from 'vue'
import AssignDialog from '@/components/collab/AssignDialog.vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { isOverdue, sortAssignments } from '@/lib/feed'
import { formatDate, todayIn } from '@/lib/format'
import { initials } from '@/lib/navstate'
import { useServices } from '@/lib/services'
import type { AssignmentItem, AssignmentStatus, Collab } from '@/lib/types'
import { useUsers } from '@/stores/users'

/** Les assignations d'un document : à qui il est confié, pour quand, où ça en est. L'assigné clôt la sienne ; qui écrit le document gère toutes les autres. */
const props = defineProps<{ collab: Collab; doctype: string; name: string; timezone?: string }>()
const emit = defineEmits<{ changed: [] }>()
const { api } = useServices()
const notify = useNotify()
const users = useUsers()

const dialog = ref(false)
const busy = ref<string | null>(null)
const items = computed(() => sortAssignments(props.collab.assignments))
const today = computed(() => todayIn(props.timezone))
const mine = (a: AssignmentItem): boolean => a.assignee === props.collab.me
const canClose = (a: AssignmentItem): boolean => a.status === 'open' && (mine(a) || props.collab.can_assign)

async function set(a: AssignmentItem, status: AssignmentStatus): Promise<void> {
  busy.value = a.id
  try {
    await api.updateAssignment(a.id, { status })
    emit('changed')
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section class="desk-card collab-card" data-testid="assignments">
    <header class="collab-card-head">
      <h3>{{ t.collab.assignments }}</h3>
      <span class="desk-spacer" />
      <Button v-if="collab.can_assign" :label="t.collab.assign" icon="pi pi-user-plus" text size="small" data-testid="assign-open" @click="dialog = true" />
    </header>
    <p v-if="!items.length" class="desk-muted collab-empty" data-testid="assignments-empty">{{ t.collab.noAssignments }}</p>
    <ul v-else class="assignment-list">
      <li v-for="a in items" :key="a.id" :class="['assignment', a.status]" :data-testid="`assignment-${a.assignee}`" :data-status="a.status">
        <span class="avatar" aria-hidden="true">{{ initials(users.label(a.assignee)) }}</span>
        <div class="assignment-body">
          <div class="assignment-who">
            <strong>{{ mine(a) ? `${users.label(a.assignee)} (${t.collab.me})` : users.label(a.assignee) }}</strong>
            <span class="status-pill" :class="a.status === 'open' ? 'warn' : a.status === 'done' ? 'ok' : ''">{{ t.collab.status[a.status] }}</span>
          </div>
          <p v-if="a.description" class="assignment-note">{{ a.description }}</p>
          <p v-if="a.due_date" :class="['desk-muted', 'assignment-due', { overdue: isOverdue(a, today) }]" data-testid="assignment-due">
            {{ t.collab.dueOn(formatDate(a.due_date)) }}<template v-if="isOverdue(a, today)"> · {{ t.collab.overdue }}</template>
          </p>
        </div>
        <div class="assignment-actions">
          <Button v-if="canClose(a)" icon="pi pi-check" text rounded size="small" severity="success" :aria-label="t.collab.done" :title="t.collab.done" :loading="busy === a.id" data-testid="assignment-done" @click="set(a, 'done')" />
          <Button v-if="canClose(a) && collab.can_assign" icon="pi pi-times" text rounded size="small" severity="secondary" :aria-label="t.collab.cancelAssignment" :title="t.collab.cancelAssignment" data-testid="assignment-cancel" @click="set(a, 'cancelled')" />
          <Button v-if="a.status !== 'open' && collab.can_assign" icon="pi pi-replay" text rounded size="small" severity="secondary" :aria-label="t.collab.reopen" :title="t.collab.reopen" data-testid="assignment-reopen" @click="set(a, 'open')" />
        </div>
      </li>
    </ul>
    <AssignDialog v-model:visible="dialog" :doctype="doctype" :name="name" :me="collab.me" @saved="emit('changed')" />
  </section>
</template>
