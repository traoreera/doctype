<script setup lang="ts">
import { ref, watch } from 'vue'
import { describeError } from '@/composables/useNotify'
import { statusLabel, statusTone } from '@/lib/docstatus'
import { relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import { t } from '@/i18n'
import type { DocRecord, QuickListBlock } from '@/lib/types'

const props = defineProps<{ block: QuickListBlock }>()
const { api } = useServices()
const rows = ref<DocRecord[] | null>(null)
const error = ref<string | null>(null)
let ticket = 0

watch(
  () => JSON.stringify(props.block),
  async () => {
    const mine = ++ticket
    try {
      const res = await api.list(props.block.doctype, { filters: props.block.filters, limit: props.block.limit, order_by: 'modified', order: 'desc' })
      if (mine === ticket) rows.value = res.items
    } catch (e) {
      if (mine === ticket) error.value = describeError(e)
    }
  },
  { immediate: true },
)

const title = (r: DocRecord): string => {
  const v = props.block.title_field ? r.data[props.block.title_field] : undefined
  return typeof v === 'string' && v ? v : r.name
}
</script>

<template>
  <section class="desk-card block quick-list" :data-testid="`quick-list-${block.label}`">
    <header class="block-head">
      <h3 class="block-title">{{ block.label }}</h3>
      <span class="desk-spacer" />
      <RouterLink class="desk-link" :to="{ name: 'list', params: { doctype: block.doctype }, query: block.filters && Object.keys(block.filters).length ? { filters: JSON.stringify(block.filters) } : {} }">{{ t.charts.openList }}</RouterLink>
    </header>
    <p v-if="error" class="desk-muted block-error">{{ t.charts.failed }} · {{ error }}</p>
    <div v-else-if="rows === null" class="desk-muted block-loading">{{ t.loading }}</div>
    <p v-else-if="!rows.length" class="desk-muted block-loading" data-testid="quick-list-empty">{{ t.charts.noRecent }}</p>
    <ul v-else class="quick-rows">
      <li v-for="r in rows" :key="r.name">
        <RouterLink :to="{ name: 'form', params: { doctype: block.doctype, name: r.name } }" class="quick-title" :data-testid="`quick-${r.name}`">{{ title(r) }}</RouterLink>
        <span v-if="block.submittable" :class="['status-pill', statusTone(r.docstatus)]">{{ statusLabel(r.docstatus) }}</span>
        <span class="desk-muted quick-time">{{ relativeTime(r.modified) }}</span>
      </li>
    </ul>
  </section>
</template>
