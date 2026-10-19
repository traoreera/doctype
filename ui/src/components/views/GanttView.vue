<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { span } from '@/lib/calendar'
import { DAY_PX, ZOOMS, barBox, ganttRange, ticks, todayLeft, type GanttItem, type Zoom } from '@/lib/gantt'
import { formatDate, todayIn } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocRecord, DocTypeMeta, Filters } from '@/lib/types'
import { useLinks } from '@/stores/links'

/** Le Gantt d'un type : une ligne par document, une barre du début à la fin sur une échelle de temps (jour, semaine, mois). Lecture seule : un clic ouvre le document. */
const props = defineProps<{ doctype: string; meta: DocTypeMeta; filters: Filters }>()

const LIMIT = 200
const { api } = useServices()
const links = useLinks()
const config = computed(() => props.meta.views?.gantt)
const today = computed(() => todayIn(props.meta.timezone))
const zoom = ref<Zoom>('week')
const records = ref<DocRecord[]>([])
const hidden = ref(0)
const error = ref<string | null>(null)
const loading = ref(false)

const colorField = computed(() => props.meta.fields.find((f) => f.fieldname === config.value?.color))
function shownTitle(field: string, r: DocRecord): unknown {
  const f = props.meta.fields.find((x) => x.fieldname === field)
  const v = r.data[field]
  return f?.fieldtype === 'Link' && typeof f.options === 'string' && typeof v === 'string' ? links.title(f.options, v) : v
}

const items = computed<GanttItem[]>(() => {
  const c = config.value
  if (!c) return []
  const out: GanttItem[] = []
  for (const r of records.value) {
    const start = r.data[c.start]
    const end = r.data[c.end]
    // Une barre a un début ET une fin : un document qui n'en a qu'une n'a pas de place sur l'échelle.
    if (typeof start !== 'string' || typeof end !== 'string') continue
    const range = span(start, end)
    if (!range) continue
    const tf = c.title ?? props.meta.title_field
    const raw = tf ? shownTitle(tf, r) : undefined
    const options = Array.isArray(colorField.value?.options) ? colorField.value!.options : []
    const idx = colorField.value ? options.indexOf(String(r.data[colorField.value.fieldname])) : -1
    out.push({ name: r.name, title: typeof raw === 'string' && raw ? raw : r.name, ...range, color: idx >= 0 ? idx : null })
  }
  return out
})

const px = computed(() => DAY_PX[zoom.value])
const range = computed(() => ganttRange(items.value, today.value))
const width = computed(() => range.value.days * px.value)
const marks = computed(() => ticks(range.value.from, range.value.days, zoom.value))
const nowAt = computed(() => todayLeft(range.value.from, range.value.days, today.value, px.value))
const bars = computed(() => items.value.map((i) => ({ item: i, box: barBox(i, range.value.from, px.value) })))

let ticket = 0
async function load(): Promise<void> {
  const c = config.value
  if (!c) return
  const mine = ++ticket
  loading.value = true
  error.value = null
  try {
    const res = await api.list(props.doctype, { filters: props.filters, order_by: c.start, order: 'asc', limit: LIMIT })
    if (mine !== ticket) return
    records.value = res.items
    const f = props.meta.fields.find((x) => x.fieldname === (config.value?.title ?? props.meta.title_field))
    if (f?.fieldtype === 'Link' && typeof f.options === 'string') {
      void links.resolve(f.options, res.items.map((r) => r.data[f.fieldname]).filter((v): v is string => typeof v === 'string'))
    }
    hidden.value = Math.max(0, res.total - res.items.length)
  } catch (e) {
    if (mine === ticket) {
      records.value = []
      error.value = describeError(e)
    }
  } finally {
    if (mine === ticket) loading.value = false
  }
}
watch(() => [props.doctype, JSON.stringify(props.filters), config.value?.start, config.value?.end], () => void load(), { immediate: true })
</script>

<template>
  <div class="gantt" data-testid="gantt">
    <div class="calendar-bar">
      <span class="desk-muted">{{ t.gantt.zoom }}</span>
      <div class="view-switcher" role="radiogroup" :aria-label="t.gantt.zoom">
        <button v-for="z in ZOOMS" :key="z" type="button" role="radio" :aria-checked="zoom === z" :class="['view-btn', { active: zoom === z }]" :data-testid="`gantt-zoom-${z}`" @click="zoom = z">
          <span>{{ t.gantt.zooms[z] }}</span>
        </button>
      </div>
      <span class="desk-spacer" />
      <span v-if="items.length" class="desk-muted" data-testid="gantt-range">{{ t.gantt.range(formatDate(range.from), formatDate(range.to)) }}</span>
    </div>

    <p v-if="error" class="desk-muted block-error" data-testid="gantt-error">{{ error }}</p>
    <p v-if="hidden" class="desk-muted block-foot" data-testid="gantt-truncated">{{ t.gantt.truncated(records.length) }}</p>
    <div v-if="!error && !items.length && !loading" class="desk-card desk-muted gantt-empty" data-testid="gantt-empty">{{ t.gantt.empty }}</div>

    <div v-else-if="items.length" class="desk-card gantt-scroll" role="group" :aria-label="t.gantt.chart" :aria-busy="loading">
      <div class="gantt-inner" :style="{ width: `calc(12rem + ${width}px)` }">
        <div class="gantt-row gantt-head">
          <div class="gantt-label" />
          <div class="gantt-track" :style="{ width: `${width}px` }">
            <span v-for="(m, i) in marks" :key="i" :class="['gantt-tick', { major: m.major }]" :style="{ left: `${m.left}px` }">{{ m.label }}</span>
          </div>
        </div>
        <div v-for="b in bars" :key="b.item.name" class="gantt-row" :data-testid="`gantt-row-${b.item.name}`">
          <div class="gantt-label" :title="b.item.title">{{ b.item.title }}</div>
          <div class="gantt-track" :style="{ width: `${width}px` }">
            <RouterLink
              :to="{ name: 'form', params: { doctype, name: b.item.name } }"
              :class="['gantt-bar', b.item.color === null ? 'c-none' : `c-${b.item.color % 8}`]"
              :style="{ left: `${b.box.left}px`, width: `${b.box.width}px` }"
              :title="`${b.item.title} · ${formatDate(b.item.start)} → ${formatDate(b.item.end)}`"
              :data-testid="`gantt-bar-${b.item.name}`"
            />
          </div>
        </div>
        <span v-if="nowAt !== null" class="gantt-today" :style="{ left: `calc(12rem + ${nowAt}px)` }" :title="t.gantt.today" data-testid="gantt-today" />
      </div>
    </div>
  </div>
</template>
