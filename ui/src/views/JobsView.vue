<script setup lang="ts">
import Button from 'primevue/button'
import { onMounted, ref } from 'vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { Job } from '@/lib/types'

/**
 * Les tâches de fond du serveur que l'utilisateur peut voir (traoreera/doctype#71) : une installation longue, un envoi différé.
 * Le serveur décide de ce qu'il montre — les siennes, et celles dont il porte le grant ; ici on ne fait que lire, et relancer un
 * échec (le serveur refuse à qui n'en a pas le droit : on affiche sa raison).
 */
const { api } = useServices()
const notify = useNotify()
const items = ref<Job[] | null>(null)
const failure = ref<string | null>(null)
const busy = ref(false)

async function load(): Promise<void> {
  busy.value = true
  try {
    items.value = await api.jobs({ limit: 100 })
    failure.value = null
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    busy.value = false
  }
}

async function retry(job: Job): Promise<void> {
  try {
    await api.retryJob(job.id)
    notify.success(t.jobs.retried)
    await load()
  } catch (e) {
    notify.error(e)
  }
}

onMounted(load)
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head">
      <h1>{{ t.jobs.title }}</h1>
      <Button :label="t.jobs.refresh" icon="pi pi-refresh" size="small" severity="secondary" :loading="busy" data-testid="jobs-refresh" @click="load" />
    </div>
    <p v-if="failure" class="desk-muted" data-testid="jobs-failed">{{ failure }}</p>
    <p v-else-if="items && !items.length" class="desk-muted" data-testid="jobs-empty">{{ t.jobs.empty }}</p>
    <ul v-else-if="items" class="desk-card jobs-list" data-testid="jobs-list">
      <li v-for="job in items" :key="job.id" :data-testid="`job-${job.id}`">
        <span class="jobs-name">{{ job.name }}</span>
        <span v-if="job.key" class="desk-muted">{{ job.key }}</span>
        <span class="status-pill" :data-state="job.status">{{ t.jobs[job.status] }}</span>
        <span v-if="job.message" class="jobs-message" :data-testid="`job-message-${job.id}`">{{ job.message }}</span>
        <span class="desk-muted jobs-meta">
          {{ t.jobs.attempts(job.attempts, job.max_attempts) }}<template v-if="job.creation"> · {{ relativeTime(job.creation) }}</template>
        </span>
        <Button v-if="job.status === 'failed'" :label="t.jobs.retry" size="small" text :data-testid="`job-retry-${job.id}`" @click="retry(job)" />
      </li>
    </ul>
  </div>
</template>

<style scoped>
.jobs-list {
  list-style: none;
  margin: 0;
  padding: 0.5rem 1rem;
  display: grid;
}

.jobs-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--p-content-border-color);
}

.jobs-list li:last-child {
  border-bottom: none;
}

.jobs-name {
  font-weight: 500;
}

.jobs-message {
  min-width: 0;
  overflow-wrap: anywhere;
}

.jobs-meta {
  margin-left: auto;
  font-size: 0.85rem;
}
</style>
