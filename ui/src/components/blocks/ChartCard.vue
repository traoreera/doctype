<script setup lang="ts">
import Button from 'primevue/button'
import { computed, ref } from 'vue'
import ChartSvg from '@/components/ChartSvg.vue'
import { useAggregate } from '@/composables/useAggregate'
import { foldParts, formatMetric, groupLabel, plotValue, queryOf, type Part } from '@/lib/aggregate'
import { MAX_PARTS } from '@/lib/charts'
import { formatDecimal, sumDecimals } from '@/lib/decimal'
import { formatNumber } from '@/lib/format'
import { t } from '@/i18n'
import type { ChartBlock } from '@/lib/types'
import { useLinks } from '@/stores/links'

const props = defineProps<{ block: ChartBlock }>()
const links = useLinks()
const { result, loading, error, reload } = useAggregate(() => ({ doctype: props.block.doctype, query: queryOf(props.block) }))

const metric = computed(() => result.value?.metrics[0])
const decimals = computed(() => metric.value?.decimals ?? null)
const partToWhole = computed(() => props.block.kind === 'pie' || props.block.kind === 'donut' || props.block.kind === 'percentage')

/** Toutes les lignes du résultat, dans l'ordre du serveur : la vue tableau les montre toutes. */
const entries = computed<Part[]>(() => (result.value?.rows ?? []).map((r) => ({ label: groupLabel(result.value!.group, r.key, links.title), raw: r.values[0] ?? null })))

// Un camembert compare des parts d'un total : six au plus, la suite se replie en « Autres » (exact pour un Decimal). Barres et courbe gardent tout.
const shown = computed(() => (partToWhole.value ? foldParts(entries.value, MAX_PARTS, decimals.value, t.charts.others) : { parts: entries.value, folded: false }))
const labels = computed(() => shown.value.parts.map((p) => p.label))
const values = computed(() => shown.value.parts.map((p) => plotValue(p.raw)))
const display = computed(() => shown.value.parts.map((p) => formatMetric(p.raw, metric.value)))

// Le total au centre d'un anneau : ce que l'anneau dessine (les valeurs positives), EXACT pour un Decimal ; pas pour une moyenne ni un extremum.
const center = computed(() => {
  if (props.block.function !== 'count' && props.block.function !== 'sum') return undefined
  const parts = shown.value.parts
  if (decimals.value !== null) return formatDecimal(sumDecimals(parts.map((p) => String(p.raw)), decimals.value), decimals.value)
  return formatNumber(parts.reduce((sum, p) => sum + plotValue(p.raw), 0))
})

// Chaque graphique a son jumeau tableau : les valeurs ne dépendent ni de la couleur ni d'une infobulle.
const asTable = ref(false)
</script>

<template>
  <section :class="['desk-card', 'block', 'chart-card', { stale: loading && result }]" :data-testid="`chart-${block.label}`" :aria-busy="loading">
    <header class="block-head">
      <h3 class="block-title">{{ block.label }}</h3>
      <span class="desk-spacer" />
      <Button
        :icon="asTable ? 'pi pi-chart-bar' : 'pi pi-table'"
        text
        rounded
        size="small"
        severity="secondary"
        :aria-label="asTable ? t.charts.showChart : t.charts.showTable"
        :title="asTable ? t.charts.showChart : t.charts.showTable"
        :aria-pressed="asTable"
        data-testid="chart-table-toggle"
        @click="asTable = !asTable"
      />
      <Button icon="pi pi-refresh" text rounded size="small" severity="secondary" :loading="loading" :aria-label="t.charts.refresh" :title="t.charts.refresh" data-testid="chart-refresh" @click="reload" />
    </header>
    <p v-if="error" class="desk-muted block-error" data-testid="chart-error">{{ t.charts.failed }} · {{ error }}</p>
    <div v-else-if="!result" class="desk-muted block-loading" data-testid="chart-loading">{{ t.loading }}</div>
    <template v-else>
      <table v-if="asTable" class="chart-table" data-testid="chart-table">
        <thead>
          <tr>
            <th scope="col">{{ result.group?.label ?? block.group_by }}</th>
            <th scope="col" class="num">{{ metric?.label ?? t.charts.total }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in entries" :key="i">
            <td>{{ e.label }}</td>
            <td class="num">{{ formatMetric(e.raw, metric) }}</td>
          </tr>
        </tbody>
      </table>
      <ChartSvg v-else :kind="block.kind" :labels="labels" :values="values" :display="display" :integer="block.function === 'count'" :title="block.label" :center="center" :other="shown.folded" :color="block.color" />
      <p v-if="result.truncated" class="desk-muted block-foot">{{ t.charts.truncated(result.rows.length) }}</p>
    </template>
  </section>
</template>
