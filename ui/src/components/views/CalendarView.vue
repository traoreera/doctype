<script setup lang="ts">
import Button from 'primevue/button'
import { computed, ref, watch } from 'vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { addDays, eventsByDay, firstOfMonth, monthGrid, moved, shiftMonth, span, weekDays, type CalEvent } from '@/lib/calendar'
import { layerFilters, passesLater } from '@/lib/filters'
import { formatDate, todayIn } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocRecord, DocTypeMeta, Filters } from '@/lib/types'
import { useLinks } from '@/stores/links'

/**
 * Le calendrier d'un type : les documents placés sur un mois ou une semaine par une date (ou une plage début → fin). Un clic sur un document
 * l'ouvre ; le « + » d'un jour en crée un à cette date ; glisser un document sur un autre jour déplace sa date (même durée), par une
 * modification ordinaire (mêmes droits, mêmes contrôles, même verrou de version) qui s'affiche tout de suite et se défait si elle est refusée.
 */
const props = defineProps<{ doctype: string; meta: DocTypeMeta; filters: Filters }>()

const LIMIT = 200
const { api } = useServices()
const links = useLinks()
const notify = useNotify()

const config = computed(() => props.meta.views?.calendar)
const today = computed(() => todayIn(props.meta.timezone))
const mode = ref<'month' | 'week'>('month')
const cursor = ref(today.value)
const records = ref<DocRecord[]>([])
const hidden = ref(0)
const error = ref<string | null>(null)
const loading = ref(false)
const dragging = ref<CalEvent | null>(null)
const overDay = ref<string | null>(null)

const days = computed(() => (mode.value === 'month' ? monthGrid(cursor.value) : weekDays(cursor.value)))
const canWrite = computed(() => props.meta.rights.write !== 'none')
const canCreate = computed(() => props.meta.rights.create !== 'none')
const colorField = computed(() => props.meta.fields.find((f) => f.fieldname === config.value?.color))
const titleField = computed(() => config.value?.title ?? props.meta.title_field)

/** Le titre d'un document : le champ choisi ; si c'est un lien (un client), le TITRE du document cité, pas son identifiant. */
function shownTitle(field: string, r: DocRecord): unknown {
  const f = props.meta.fields.find((x) => x.fieldname === field)
  const v = r.data[field]
  return f?.fieldtype === 'Link' && typeof f.options === 'string' && typeof v === 'string' ? links.title(f.options, v) : v
}

/** Les titres des documents cités par le champ titre sont lus en une requête par type. */
function resolveTitles(): void {
  const f = props.meta.fields.find((x) => x.fieldname === titleField.value)
  if (f?.fieldtype === 'Link' && typeof f.options === 'string') {
    void links.resolve(f.options, records.value.map((r) => r.data[f.fieldname]).filter((v): v is string => typeof v === 'string'))
  }
}

const events = computed<CalEvent[]>(() => {
  const c = config.value
  if (!c) return []
  const out: CalEvent[] = []
  for (const r of records.value) {
    const range = span(r.data[c.start], c.end ? r.data[c.end] : null)
    if (!range) continue
    const tf = titleField.value
    const raw = tf ? shownTitle(tf, r) : undefined
    const options = Array.isArray(colorField.value?.options) ? colorField.value!.options : []
    const idx = colorField.value ? options.indexOf(String(r.data[colorField.value.fieldname])) : -1
    out.push({
      name: r.name,
      ...range,
      title: typeof raw === 'string' && raw ? raw : r.name,
      color: idx >= 0 ? idx : null,
      movable: canWrite.value && (!props.meta.submittable || r.docstatus === 0),
    })
  }
  return out
})
const byDay = computed(() => eventsByDay(days.value, events.value))
const heading = computed(() => {
  const first = days.value[0]!
  const last = days.value.at(-1)!
  if (mode.value === 'week') return `${formatDate(first)} → ${formatDate(last)}`
  return new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${firstOfMonth(cursor.value)}T00:00:00Z`))
})
const inMonth = (d: string): boolean => d.slice(0, 7) === cursor.value.slice(0, 7)

let ticket = 0
async function load(): Promise<void> {
  const c = config.value
  if (!c) return
  const mine = ++ticket
  loading.value = true
  error.value = null
  try {
    const first = days.value[0]!
    // Un seul filtre par champ côté serveur : la fenêtre de dates prime dans la requête, et le filtre de l'utilisateur sur ce même champ
    // s'applique ensuite aux lignes reçues. `in` accepte 100 valeurs ; une fenêtre en compte 42 au plus.
    const a = layerFilters(props.filters, { [c.start]: ['in', days.value] })
    const queries = [api.list(props.doctype, { filters: a.request, order_by: c.start, order: 'asc', limit: LIMIT })]
    const later = [a.later]
    if (c.end) {
      // Les événements commencés avant la fenêtre et encore en cours à son début.
      const b = layerFilters(props.filters, { [c.start]: ['<', first], [c.end]: ['>=', first] })
      queries.push(api.list(props.doctype, { filters: b.request, order_by: c.start, order: 'asc', limit: LIMIT }))
      later.push(b.later)
    }
    const results = await Promise.all(queries)
    if (mine !== ticket) return
    const seen = new Map<string, DocRecord>()
    results.forEach((res, i) => {
      for (const r of res.items) if (passesLater(r.data, later[i]!)) seen.set(r.name, r)
    })
    records.value = [...seen.values()]
    resolveTitles()
    hidden.value = results.reduce((sum, res) => sum + Math.max(0, res.total - res.items.length), 0)
  } catch (e) {
    if (mine === ticket) {
      records.value = []
      error.value = describeError(e)
    }
  } finally {
    if (mine === ticket) loading.value = false
  }
}
watch(() => [props.doctype, JSON.stringify(props.filters), days.value[0], days.value.at(-1), config.value?.start], () => void load(), { immediate: true })

const go = (delta: number): void => {
  cursor.value = mode.value === 'month' ? shiftMonth(cursor.value, delta) : addDays(cursor.value, delta * 7)
}

function openWeek(day: string): void {
  cursor.value = day
  mode.value = 'week'
}

// ── Glisser-déposer : changer la date d'un document ─────────
function onDrop(day: string): void {
  const ev = dragging.value
  dragging.value = null
  overDay.value = null
  if (ev) void reschedule(ev, day)
}

async function reschedule(ev: CalEvent, day: string): Promise<void> {
  const c = config.value
  const rec = records.value.find((r) => r.name === ev.name)
  if (!c || !rec || !ev.movable || day === ev.start) return
  const next = moved(ev, day)
  const data: Record<string, unknown> = { ...rec.data, [c.start]: next.start }
  if (c.end && typeof rec.data[c.end] === 'string') data[c.end] = next.end
  const before = records.value
  records.value = before.map((r) => (r.name === rec.name ? { ...r, data } : r)) // la date change à l'écran avant la réponse
  try {
    const saved = await api.update(props.doctype, rec.name, rec.version, data)
    records.value = records.value.map((r) => (r.name === saved.name ? saved : r))
    notify.success(t.calendar.moved(formatDate(next.start)))
  } catch (e) {
    records.value = before
    if (e instanceof ApiError && (e.isConflict || e.isStale)) void load()
    notify.error(e)
  }
}
</script>

<template>
  <div class="calendar" data-testid="calendar">
    <div class="calendar-bar">
      <Button icon="pi pi-chevron-left" text rounded size="small" severity="secondary" :aria-label="t.calendar.previous" data-testid="cal-prev" @click="go(-1)" />
      <Button icon="pi pi-chevron-right" text rounded size="small" severity="secondary" :aria-label="t.calendar.next" data-testid="cal-next" @click="go(1)" />
      <Button :label="t.calendar.today" text size="small" severity="secondary" data-testid="cal-today" @click="cursor = today" />
      <h2 class="calendar-title" data-testid="cal-title" aria-live="polite">{{ heading }}</h2>
      <span class="desk-spacer" />
      <div class="view-switcher" role="radiogroup" :aria-label="t.calendar.grid">
        <button v-for="m in (['month', 'week'] as const)" :key="m" type="button" role="radio" :aria-checked="mode === m" :class="['view-btn', { active: mode === m }]" :data-testid="`cal-mode-${m}`" @click="mode = m">
          <span>{{ t.calendar[m] }}</span>
        </button>
      </div>
    </div>

    <p v-if="error" class="desk-muted block-error" data-testid="cal-error">{{ error }}</p>
    <p v-if="hidden" class="desk-muted block-foot" data-testid="cal-truncated">{{ t.calendar.truncated(records.length) }}</p>

    <div :class="['calendar-grid', mode, { loading }]" role="grid" :aria-label="t.calendar.grid" :aria-busy="loading">
      <div v-for="w in t.calendar.weekdays" :key="w" class="calendar-dow" role="columnheader">{{ w }}</div>
      <div
        v-for="d in days"
        :key="d"
        :class="['calendar-day', { today: d === today, muted: mode === 'month' && !inMonth(d), over: overDay === d && dragging }]"
        role="gridcell"
        :data-testid="`cal-day-${d}`"
        @dragover="dragging && ($event.preventDefault(), (overDay = d))"
        @dragleave="overDay = overDay === d ? null : overDay"
        @drop.prevent="onDrop(d)"
      >
        <div class="calendar-day-head">
          <span class="calendar-num">{{ Number(d.slice(8)) }}</span>
          <RouterLink
            v-if="canCreate && config"
            :to="{ name: 'new', params: { doctype }, query: { [config.start]: d } }"
            class="calendar-add"
            :aria-label="t.calendar.newOn(formatDate(d))"
            :title="t.calendar.newOn(formatDate(d))"
            :data-testid="`cal-add-${d}`"
          >
            <i class="pi pi-plus" />
          </RouterLink>
        </div>
        <ul class="calendar-events">
          <li v-for="ev in (byDay[d] ?? []).slice(0, mode === 'month' ? 3 : 20)" :key="ev.name">
            <RouterLink
              :to="{ name: 'form', params: { doctype, name: ev.name } }"
              :class="['cal-event', ev.color === null ? 'c-none' : `c-${ev.color % 8}`, { movable: ev.movable }]"
              :draggable="ev.movable"
              :title="`${ev.title} · ${formatDate(ev.start)}${ev.end !== ev.start ? ` → ${formatDate(ev.end)}` : ''}`"
              :data-testid="`cal-event-${ev.name}-${d}`"
              @dragstart="dragging = ev"
              @dragend="(dragging = null), (overDay = null)"
            >
              {{ ev.title }}
            </RouterLink>
          </li>
          <li v-if="mode === 'month' && (byDay[d]?.length ?? 0) > 3">
            <button type="button" class="calendar-more" :data-testid="`cal-more-${d}`" :title="t.calendar.openWeek" @click="openWeek(d)">{{ t.calendar.more((byDay[d]?.length ?? 0) - 3) }}</button>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
