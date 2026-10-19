<script setup lang="ts">
import { computed } from 'vue'
import { useAggregate } from '@/composables/useAggregate'
import { formatMetric, queryOf } from '@/lib/aggregate'
import { t } from '@/i18n'
import { customTone } from '@/lib/brand'
import type { NumberCardBlock } from '@/lib/types'

const props = defineProps<{ block: NumberCardBlock }>()
const { result, loading, error } = useAggregate(() => ({ doctype: props.block.doctype, query: queryOf(props.block) }))

const value = computed(() => {
  const r = result.value
  return r ? formatMetric(r.rows[0]?.values[0], r.metrics[0]) : null
})
// Une couleur libre (`#rrggbb`) devient deux variables, une par thème ; un nom de la palette, une classe.
const custom = computed(() => customTone(props.block.color))
const tone = computed(() => (custom.value ? 'tone-custom' : `tone-${props.block.color ?? 'blue'}`))
// Un clic ouvre la liste de ce qu'on vient de compter (mêmes filtres) : le chiffre est toujours vérifiable.
const to = computed(() => ({
  name: 'list',
  params: { doctype: props.block.doctype },
  query: props.block.filters && Object.keys(props.block.filters).length ? { filters: JSON.stringify(props.block.filters) } : {},
}))
</script>

<template>
  <RouterLink :to="to" :class="['desk-card', 'number-card', tone]" :style="custom ?? undefined" data-testid="number-card" :aria-label="`${block.label} : ${value ?? ''}`">
    <span class="number-card-label">{{ block.label }}</span>
    <span v-if="error" class="number-card-value desk-muted" :title="error" data-testid="number-card-error">{{ t.charts.failed }}</span>
    <span v-else-if="value === null" class="number-card-value desk-muted" data-testid="number-card-loading">{{ loading ? '…' : t.charts.none }}</span>
    <span v-else class="number-card-value" data-testid="number-card-value">{{ value }}</span>
  </RouterLink>
</template>
