<script setup lang="ts">
import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable, { type DataTablePageEvent, type DataTableSortEvent } from 'primevue/datatable'
import MultiSelect from 'primevue/multiselect'
import Select from 'primevue/select'
import { computed, ref, watch } from 'vue'
import CellValue from '@/components/CellValue.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { formatMetric, groupLabel, metricSpec } from '@/lib/aggregate'
import { buildCsv, csvFilename, exportValue, isTextualColumn } from '@/lib/csv'
import { downloadText } from '@/lib/download'
import { statusLabel, statusTone } from '@/lib/docstatus'
import { formatCell, formatTimestamp, isNumeric, todayIn } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { AggregateResult, DocRecord, DocTypeMeta, FieldMeta, Filters } from '@/lib/types'
import { readColumns, writeColumns } from '@/lib/viewprefs'
import { useLinks } from '@/stores/links'

/**
 * Le rapport d'un type : la liste, mais on choisit les colonnes, on regroupe par une valeur (avec le nombre et les totaux de chaque colonne
 * numérique), et on exporte en CSV. Les totaux sont calculés par le serveur sur TOUS les documents qui correspondent aux filtres, pas sur la
 * page affichée. Il respecte les droits de lecture comme la liste : on ne compte et n'exporte que ce que l'on peut ouvrir.
 */
const props = defineProps<{ doctype: string; meta: DocTypeMeta; filters: Filters }>()
const emit = defineEmits<{ drill: [field: string, value: string | number | boolean] }>()

const EXPORT_CAP = 10_000
const PAGE = 200
const MAX_SUMS = 7 // une mesure `count` + sept sommes : le plafond d'une demande d'agrégat est de huit
const { api } = useServices()
const links = useLinks()
const notify = useNotify()

interface ColumnChoice {
  key: string
  label: string
  field?: FieldMeta
}

const sys = (key: string): string => t.report.system[key] ?? key
const choices = computed<ColumnChoice[]>(() => [
  { key: 'name', label: sys('name') },
  ...props.meta.fields.filter((f) => !f.hidden && f.fieldtype !== 'Table').map((f) => ({ key: f.fieldname, label: f.label ?? f.fieldname, field: f })),
  { key: 'owner', label: sys('owner') },
  { key: 'creation', label: sys('creation') },
  { key: 'modified', label: sys('modified') },
  ...(props.meta.submittable ? [{ key: 'docstatus', label: sys('docstatus') }] : []),
])
const byKey = computed(() => new Map(choices.value.map((c) => [c.key, c])))
const defaults = (): string[] => props.meta.list_columns.filter((n) => byKey.value.has(n))

const columns = ref<string[]>([])
function initColumns(): void {
  const saved = readColumns(props.doctype)?.filter((k) => byKey.value.has(k))
  columns.value = saved?.length ? saved : defaults()
}
watch(columns, (v) => writeColumns(props.doctype, v), { deep: true })

const shown = computed(() => columns.value.map((k) => byKey.value.get(k)).filter((c): c is ColumnChoice => !!c))
const sums = computed(() => shown.value.filter((c) => c.field && isNumeric(c.field)).slice(0, MAX_SUMS))

// ── Regrouper ───────────────────────────────────────────────
const groupable = computed(() => [
  ...props.meta.fields
    .filter((f) => !f.hidden && ['Select', 'Link', 'Boolean', 'Date', 'Datetime'].includes(f.fieldtype))
    .map((f) => ({ value: f.fieldname, label: f.label ?? f.fieldname })),
  ...(props.meta.submittable ? [{ value: 'docstatus', label: sys('docstatus') }] : []),
  { value: 'owner', label: sys('owner') },
  { value: 'creation', label: sys('creation') },
  { value: 'modified', label: sys('modified') },
])
const groupBy = ref('')
const bucket = ref<'day' | 'month' | 'year'>('month')
const isTime = computed(() => props.meta.fields.find((f) => f.fieldname === groupBy.value)?.fieldtype.startsWith('Date') || groupBy.value === 'creation' || groupBy.value === 'modified')
const bucketChoices = computed(() => (['day', 'month', 'year'] as const).map((v) => ({ value: v, label: t.report.periods[v] })))
const metricsList = computed(() => ['count', ...sums.value.map((c) => metricSpec('sum', c.key))])

// ── Données ─────────────────────────────────────────────────
const rows = ref<DocRecord[]>([])
const total = ref(0)
const first = ref(0)
const pageSize = ref(20)
const sortField = ref('modified')
const sortOrder = ref<1 | -1>(-1)
const grouped = ref<AggregateResult | null>(null)
const totals = ref<AggregateResult | null>(null)
const loading = ref(false)
const failure = ref<string | null>(null)
let ticket = 0

async function load(): Promise<void> {
  const mine = ++ticket
  loading.value = true
  failure.value = null
  try {
    const totalsQuery = api.aggregate(props.doctype, { metrics: metricsList.value, filters: props.filters })
    if (groupBy.value) {
      const [g, tt] = await Promise.all([
        api.aggregate(props.doctype, { group_by: groupBy.value, bucket: isTime.value ? bucket.value : undefined, metrics: metricsList.value, filters: props.filters, limit: 400 }),
        totalsQuery,
      ])
      if (g.group?.fieldtype === 'Link' && typeof g.group.options === 'string') {
        await links.resolve(g.group.options, g.rows.map((r) => r.key).filter((k): k is string => typeof k === 'string'))
      }
      if (mine !== ticket) return
      grouped.value = g
      totals.value = tt
      rows.value = []
      return
    }
    const [page, tt] = await Promise.all([
      api.list(props.doctype, { filters: props.filters, order_by: sortField.value, order: sortOrder.value === 1 ? 'asc' : 'desc', limit: pageSize.value, offset: first.value }),
      totalsQuery,
    ])
    if (mine !== ticket) return
    rows.value = page.items
    total.value = page.total
    grouped.value = null
    totals.value = tt
    for (const c of shown.value) {
      if (c.field?.fieldtype === 'Link' && typeof c.field.options === 'string') {
        void links.resolve(c.field.options, page.items.map((r) => r.data[c.key]).filter((v): v is string => typeof v === 'string'))
      }
    }
  } catch (e) {
    if (mine !== ticket) return
    rows.value = []
    grouped.value = null
    totals.value = null
    failure.value = describeError(e)
  } finally {
    if (mine === ticket) loading.value = false
  }
}

initColumns()
watch(() => [props.doctype, JSON.stringify(props.filters), groupBy.value, bucket.value, sums.value.map((c) => c.key).join('|')], () => {
  first.value = 0
  void load()
}, { immediate: true })

function onPage(e: DataTablePageEvent): void {
  first.value = e.first
  pageSize.value = e.rows
  void load()
}

function onSort(e: DataTableSortEvent): void {
  if (typeof e.sortField === 'string') {
    sortField.value = e.sortField
    sortOrder.value = e.sortOrder === 1 ? 1 : -1
    first.value = 0
    void load()
  }
}

// ── Affichage ───────────────────────────────────────────────
function cell(c: ColumnChoice, r: DocRecord): string {
  if (c.key === 'name') return r.name
  if (c.key === 'owner') return r.owner
  if (c.key === 'creation' || c.key === 'modified') return formatTimestamp(r[c.key])
  const v = r.data[c.key]
  if (c.field?.fieldtype === 'Link' && typeof v === 'string' && typeof c.field.options === 'string') return links.title(c.field.options, v)
  return c.field ? formatCell(c.field, v) : String(v ?? '')
}
const totalOf = (key: string): string => {
  const i = sums.value.findIndex((c) => c.key === key)
  const r = totals.value
  return i < 0 || !r ? '' : formatMetric(r.rows[0]?.values[i + 1], r.metrics[i + 1])
}
const groupTitle = computed(() => groupable.value.find((g) => g.value === groupBy.value)?.label ?? groupBy.value)
const groupRows = computed(() =>
  (grouped.value?.rows ?? []).map((r) => ({
    key: r.key,
    label: groupLabel(grouped.value!.group, r.key, links.title),
    count: formatMetric(r.values[0], grouped.value!.metrics[0]),
    sums: sums.value.map((_, i) => formatMetric(r.values[i + 1], grouped.value!.metrics[i + 1])),
    // Cliquer un groupe filtre la liste sur sa valeur (pas pour une période : elle n'est pas une valeur du champ).
    drill: !isTime.value && r.key !== null && groupBy.value !== 'owner',
  })),
)
const grandCount = computed(() => formatMetric(totals.value?.rows[0]?.values[0], totals.value?.metrics[0]))

function drill(key: string | number | boolean | null): void {
  if (key === null) return
  // La barre de filtres comprend l'état (`docstatus`) et les champs ; le reste ne se filtre pas ici.
  emit('drill', groupBy.value, key)
  groupBy.value = ''
}

// ── Export CSV ──────────────────────────────────────────────
interface Table {
  headers: string[]
  rows: string[][]
  textual: boolean[]
  capped: boolean
}

/** Le rapport regroupé tel qu'on le voit : un groupe par ligne, puis le total. */
function groupedTable(): Table {
  const rows = groupRows.value.map((r) => [r.label, r.count, ...r.sums])
  rows.push([t.report.total, grandCount.value, ...sums.value.map((c) => totalOf(c.key))])
  return { headers: [groupTitle.value, t.report.count, ...sums.value.map((c) => c.label)], rows, textual: [true, false, ...sums.value.map(() => false)], capped: false }
}

/** Les documents qui correspondent aux filtres, page à page (le serveur plafonne une page à 200), dans le tri affiché, jusqu'au plafond d'export. */
async function documentsTable(): Promise<Table> {
  const all: DocRecord[] = []
  let available = 0
  while (all.length < EXPORT_CAP) {
    const res = await api.list(props.doctype, { filters: props.filters, order_by: sortField.value, order: sortOrder.value === 1 ? 'asc' : 'desc', limit: PAGE, offset: all.length })
    available = res.total
    all.push(...res.items)
    if (all.length >= res.total || !res.items.length) break
  }
  const exported = all.slice(0, EXPORT_CAP)
  return {
    headers: shown.value.map((c) => c.label),
    rows: exported.map((r) => shown.value.map((c) => exportValue(c.field, c.key, r).text)),
    textual: shown.value.map((c) => isTextualColumn(c.field)),
    capped: available > EXPORT_CAP,
  }
}

const exporting = ref(false)
async function exportCsv(): Promise<void> {
  exporting.value = true
  try {
    const table = groupBy.value && grouped.value ? groupedTable() : await documentsTable()
    downloadText(csvFilename(props.doctype, todayIn(props.meta.timezone)), buildCsv(table.headers, table.rows, table.textual))
    if (table.capped) notify.warn(t.report.exportCapped(EXPORT_CAP))
    else notify.success(t.report.exported(table.rows.length - (groupBy.value ? 1 : 0)))
  } catch (e) {
    notify.error(e)
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="report" data-testid="report">
    <div class="desk-toolbar report-bar">
      <MultiSelect
        v-model="columns"
        :options="choices"
        option-label="label"
        option-value="key"
        :placeholder="t.report.columns"
        :max-selected-labels="0"
        :selected-items-label="`${t.report.columns} · {0}`"
        size="small"
        display="comma"
        :aria-label="t.report.pickColumns"
        data-testid="report-columns"
      />
      <Select
        :model-value="groupBy || null"
        :options="groupable"
        option-label="label"
        option-value="value"
        :placeholder="t.report.groupBy"
        show-clear
        size="small"
        :aria-label="t.report.groupBy"
        data-testid="report-group"
        @update:model-value="groupBy = $event ?? ''"
      />
      <Select v-if="groupBy && isTime" v-model="bucket" :options="bucketChoices" option-label="label" option-value="value" size="small" :aria-label="t.report.period" data-testid="report-bucket" />
      <Button v-if="columns.join() !== defaults().join()" :label="t.report.resetColumns" text size="small" severity="secondary" data-testid="report-reset" @click="columns = defaults()" />
      <span class="desk-spacer" />
      <Button icon="pi pi-download" :label="t.report.export" size="small" severity="secondary" outlined :loading="exporting" :disabled="loading" data-testid="report-export" @click="exportCsv" />
    </div>

    <p v-if="failure" class="desk-muted block-error" data-testid="report-error">{{ failure }}</p>

    <div v-if="groupBy" class="desk-card report-groups" data-testid="report-grouped">
      <table class="chart-table report-table">
        <thead>
          <tr>
            <th scope="col">{{ groupTitle }}</th>
            <th scope="col" class="num">{{ t.report.count }}</th>
            <th v-for="c in sums" :key="c.key" scope="col" class="num">{{ c.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in groupRows" :key="i" :data-testid="`report-group-row-${i}`">
            <td>
              <button v-if="r.drill" type="button" class="link-button" :title="t.report.openGroup(r.label)" @click="drill(r.key)">{{ r.label }}</button>
              <span v-else>{{ r.label }}</span>
            </td>
            <td class="num">{{ r.count }}</td>
            <td v-for="(s, j) in r.sums" :key="j" class="num">{{ s }}</td>
          </tr>
          <tr v-if="!groupRows.length && !loading"><td :colspan="2 + sums.length" class="desk-muted">{{ t.list.emptyFiltered }}</td></tr>
        </tbody>
        <tfoot v-if="groupRows.length">
          <tr data-testid="report-total">
            <th scope="row">{{ t.report.total }}</th>
            <td class="num">{{ grandCount }}</td>
            <td v-for="c in sums" :key="c.key" class="num">{{ totalOf(c.key) }}</td>
          </tr>
        </tfoot>
      </table>
      <p v-if="grouped?.truncated" class="desk-muted block-foot">{{ t.report.truncated(grouped.rows.length) }}</p>
    </div>

    <div v-else class="desk-card">
      <DataTable
        :value="rows"
        data-key="name"
        lazy
        paginator
        :rows="pageSize"
        :first="first"
        :total-records="total"
        :rows-per-page-options="[20, 50, 100]"
        :loading="loading"
        scrollable
        :sort-field="sortField"
        :sort-order="sortOrder"
        removable-sort
        size="small"
        @page="onPage"
        @sort="onSort"
      >
        <Column
          v-for="(c, i) in shown"
          :key="c.key"
          :field="c.key"
          :sort-field="c.key"
          :header="c.label"
          :header-class="c.field && isNumeric(c.field) ? 'num' : ''"
          :body-class="c.field && isNumeric(c.field) ? 'num' : ''"
          :footer="i === 0 && !sums.some((s) => s.key === c.key) ? t.report.total : totalOf(c.key)"
          :footer-class="c.field && isNumeric(c.field) ? 'num' : ''"
          sortable
        >
          <template #body="{ data }">
            <RouterLink v-if="i === 0" :to="{ name: 'form', params: { doctype, name: data.name } }" style="font-weight: 500" :data-testid="`open-${data.name}`">{{ cell(c, data) || data.name }}</RouterLink>
            <span v-else-if="c.key === 'docstatus'" :class="['status-pill', statusTone(data.docstatus)]">{{ statusLabel(data.docstatus) }}</span>
            <CellValue v-else-if="c.field" :field="c.field" :value="data.data[c.key]" :text="cell(c, data)" />
            <span v-else>{{ cell(c, data) }}</span>
          </template>
        </Column>
        <template #empty>
          <div class="desk-muted" style="padding: 1.5rem; text-align: center" data-testid="report-empty">{{ t.list.emptyFiltered }}</div>
        </template>
      </DataTable>
    </div>
  </div>
</template>
