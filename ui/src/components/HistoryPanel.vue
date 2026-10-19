<script setup lang="ts">
import Button from 'primevue/button'
import { ref, watch } from 'vue'
import { t } from '@/i18n'
import { changeLines, linkValues, verbOf } from '@/lib/history'
import { relativeTime, formatTimestamp } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, VersionEntry } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useUsers } from '@/stores/users'

const props = defineProps<{ meta: DocTypeMeta; doctype: string; name: string; version: number }>()
const PAGE = 20

const { api } = useServices()
const links = useLinks()
const users = useUsers()
const open = ref(false)
const entries = ref<VersionEntry[]>([])
const total = ref(0)
const loading = ref(false)
const failed = ref(false)
let ticket = 0

async function load(reset: boolean): Promise<void> {
  const mine = ++ticket
  loading.value = true
  failed.value = false
  try {
    const page = await api.history(props.doctype, props.name, { limit: PAGE, offset: reset ? 0 : entries.value.length })
    if (mine !== ticket) return
    for (const [target, names] of linkValues(props.meta, page.items)) await links.resolve(target, names)
    entries.value = reset ? page.items : [...entries.value, ...page.items]
    total.value = page.total
  } catch {
    if (mine === ticket) failed.value = true
  } finally {
    if (mine === ticket) loading.value = false
  }
}

function toggle(): void {
  open.value = !open.value
  if (open.value && !entries.value.length) void load(true)
}

// Une sauvegarde change la version du document : si le panneau est ouvert, il se met à jour ; sinon il se lira à l'ouverture.
watch(
  () => [props.name, props.version] as const,
  () => {
    entries.value = []
    total.value = 0
    if (open.value) void load(true)
  },
)
</script>

<template>
  <section class="desk-card form-section history" data-testid="history">
    <header class="collapsible" role="button" tabindex="0" :aria-expanded="open" @click="toggle" @keydown.enter="toggle">
      <i :class="['pi', open ? 'pi-chevron-down' : 'pi-chevron-right']" />
      {{ t.history.title }}
    </header>
    <div v-if="open">
      <p v-if="failed" class="desk-muted" data-testid="history-failed">{{ t.history.failed }}</p>
      <p v-else-if="!loading && !entries.length" class="desk-muted">{{ t.history.empty }}</p>
      <ol class="timeline">
        <li v-for="e in entries" :key="e.id" :data-action="e.action" data-testid="history-entry">
          <div class="who">
            <strong>{{ users.label(e.user) }}</strong> {{ verbOf(e.action) }}
            <time class="desk-muted" :datetime="e.creation" :title="formatTimestamp(e.creation)">· {{ relativeTime(e.creation) }}</time>
          </div>
          <ul v-if="e.action === 'update'" class="changes">
            <li v-for="c in changeLines(meta, e, links.title)" :key="c.field">
              <span class="desk-muted">{{ c.label }} :</span> <del>{{ c.old }}</del> → <ins>{{ c.new }}</ins>
            </li>
          </ul>
        </li>
      </ol>
      <Button v-if="entries.length < total" :label="t.history.more" text size="small" :loading="loading" data-testid="history-more" @click="load(false)" />
    </div>
  </section>
</template>
