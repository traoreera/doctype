<script setup lang="ts">
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import { computed, reactive, ref, watch } from 'vue'
import CellValue from '@/components/CellValue.vue'
import FileThumb from '@/components/FileThumb.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { statusLabel, statusTone } from '@/lib/docstatus'
import { layerFilters, matchesFilter, passesLater } from '@/lib/filters'
import { savedFieldLocked } from '@/lib/form'
import { formatCell } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocRecord, DocTypeMeta, FieldMeta, Filters } from '@/lib/types'
import { useFiles } from '@/stores/files'
import { useLinks } from '@/stores/links'

/**
 * Le Kanban d'un type : une colonne par valeur d'un champ Select. Glisser une carte dans une autre colonne change la valeur de ce champ
 * (une modification ordinaire : mêmes droits, mêmes contrôles, même verrou de version que le formulaire). Le déplacement s'affiche tout de
 * suite ; si le serveur refuse, la carte revient et le motif est dit. Au clavier ou au toucher, le menu « Déplacer vers » fait de même.
 */
const props = defineProps<{ doctype: string; meta: DocTypeMeta; filters: Filters }>()

const PAGE = 20
const { api } = useServices()
const links = useLinks()
const files = useFiles()
const notify = useNotify()

interface Column {
  items: DocRecord[]
  total: number
  loading: boolean
  error: string | null
}

const config = computed(() => props.meta.views?.kanban)
const field = computed(() => props.meta.fields.find((f) => f.fieldname === config.value?.field))
const columnNames = computed<string[]>(() => (Array.isArray(field.value?.options) ? field.value!.options : []))
const cardFields = computed<FieldMeta[]>(() =>
  (config.value?.card_fields ?? []).map((n) => props.meta.fields.find((f) => f.fieldname === n)).filter((f): f is FieldMeta => !!f),
)
const board = reactive<Record<string, Column>>({})
const canWrite = computed(() => props.meta.rights.write !== 'none')
const canCreate = computed(() => props.meta.rights.create !== 'none')
// Le champ des colonnes verrouillé (lecture seule du YAML ou de l'organisation, condition…) : le serveur refuserait le déplacement.
const draggable = (r: DocRecord): boolean =>
  canWrite.value && (!props.meta.submittable || r.docstatus === 0) && !(field.value && savedFieldLocked(props.meta, field.value.fieldname, r))

const tickets: Record<string, number> = {}

async function loadColumn(value: string, append = false): Promise<void> {
  const name = field.value?.fieldname
  if (!name) return
  // Lu À TRAVERS le proxy réactif : `board[v] ??= {…}` rendrait l'objet brut, et ses mises à jour ne rafraîchiraient rien.
  board[value] ??= { items: [], total: 0, loading: false, error: null }
  const col = board[value]!
  const mine = (tickets[value] = (tickets[value] ?? 0) + 1)
  // Le serveur n'accepte qu'un filtre par champ : celui de la colonne l'emporte dans la requête ; celui de l'utilisateur sur ce même champ décide si la colonne est visible.
  const { request, later } = layerFilters(props.filters, { [name]: value })
  if (name in later && !matchesFilter(value, later[name])) {
    Object.assign(col, { items: [], total: 0, loading: false, error: null })
    return
  }
  col.loading = true
  col.error = null
  try {
    const res = await api.list(props.doctype, { filters: request, order_by: 'modified', order: 'desc', limit: PAGE, offset: append ? col.items.length : 0 })
    if (mine !== tickets[value]) return
    col.items = append ? [...col.items, ...res.items] : res.items
    col.total = res.total
    const photo = props.meta.image_field
    if (photo) void files.resolve(res.items.map((r) => r.data[photo]).filter((v): v is string => typeof v === 'string'))
    for (const f of cardFields.value) {
      if (f.fieldtype === 'Link' && typeof f.options === 'string') {
        void links.resolve(f.options, res.items.map((r) => r.data[f.fieldname]).filter((v): v is string => typeof v === 'string'))
      }
    }
  } catch (e) {
    if (mine === tickets[value]) col.error = describeError(e)
  } finally {
    if (mine === tickets[value]) col.loading = false
  }
}

const loadAll = (): Promise<unknown> => Promise.all(columnNames.value.map((v) => loadColumn(v)))
watch(() => [props.doctype, JSON.stringify(props.filters), columnNames.value.join('|')], () => void loadAll(), { immediate: true })

// ── Glisser-déposer ─────────────────────────────────────────
const dragging = ref<{ record: DocRecord; from: string } | null>(null)
const over = ref<string | null>(null)

function onDragStart(record: DocRecord, from: string, ev: DragEvent): void {
  dragging.value = { record, from }
  if (ev.dataTransfer) {
    ev.dataTransfer.effectAllowed = 'move'
    ev.dataTransfer.setData('text/plain', record.name)
  }
}

function onDragOver(column: string, ev: DragEvent): void {
  if (!dragging.value) return
  ev.preventDefault() // sans cela le navigateur refuse le dépôt
  over.value = column
}

function onDrop(column: string): void {
  const d = dragging.value
  dragging.value = null
  over.value = null
  if (d) void move(d.record, d.from, column)
}

async function move(record: DocRecord, from: string, to: string): Promise<void> {
  const name = field.value?.fieldname
  if (!name || from === to || !draggable(record)) return
  const src = board[from]
  const dst = board[to]
  if (!src || !dst) return
  // Le déplacement se voit tout de suite : la carte change de colonne avant la réponse.
  src.items = src.items.filter((r) => r.name !== record.name)
  src.total = Math.max(0, src.total - 1)
  dst.items = [record, ...dst.items]
  dst.total += 1
  try {
    const saved = await api.update(props.doctype, record.name, record.version, { ...record.data, [name]: to })
    dst.items = dst.items.map((r) => (r.name === saved.name ? saved : r))
    notify.success(t.kanban.moved(to))
  } catch (e) {
    // Refusé : la carte revient où elle était. Un conflit de version recharge les deux colonnes (le document a changé ailleurs).
    dst.items = dst.items.filter((r) => r.name !== record.name)
    dst.total = Math.max(0, dst.total - 1)
    src.items = [record, ...src.items].sort((a, b) => b.modified.localeCompare(a.modified))
    src.total += 1
    if (e instanceof ApiError && (e.isConflict || e.isStale)) {
      notify.warn(t.kanban.conflict)
      void Promise.all([loadColumn(from), loadColumn(to)])
    } else notify.error(e)
  }
}

// Au clavier et au toucher : un menu « Déplacer vers ».
const menu = ref<InstanceType<typeof Menu> | null>(null)
const menuItems = ref<{ label: string; command: () => void }[]>([])

function openMenu(ev: Event, record: DocRecord, from: string): void {
  menuItems.value = columnNames.value.filter((c) => c !== from).map((c) => ({ label: c, command: () => void move(record, from, c) }))
  menu.value?.toggle(ev)
}

const title = (r: DocRecord): string => {
  const f = props.meta.title_field
  const v = f ? r.data[f] : undefined
  return typeof v === 'string' && v ? v : r.name
}
const cell = (f: FieldMeta, r: DocRecord): string => {
  const v = r.data[f.fieldname]
  if (f.fieldtype === 'Link' && typeof v === 'string' && typeof f.options === 'string') return links.title(f.options, v)
  return formatCell(f, v)
}
const errors = computed(() => Object.values(board).find((c) => c.error)?.error ?? null)
</script>

<template>
  <div class="kanban" data-testid="kanban" :aria-label="t.kanban.board(field?.label ?? config?.field ?? '')">
    <p v-if="errors" class="desk-muted block-error" data-testid="kanban-error">{{ errors }}</p>
    <div class="kanban-board">
      <section
        v-for="column in columnNames"
        :key="column"
        :class="['kanban-col', { over: over === column && dragging && dragging.from !== column }]"
        :data-testid="`kanban-col-${column}`"
        @dragover="onDragOver(column, $event)"
        @dragleave="over = over === column ? null : over"
        @drop.prevent="onDrop(column)"
      >
        <header class="kanban-head">
          <span class="kanban-dot" :class="`c-${columnNames.indexOf(column) % 8}`" aria-hidden="true" />
          <h3 class="kanban-title">{{ column }}</h3>
          <span class="count" :data-testid="`kanban-count-${column}`">{{ board[column]?.total ?? 0 }}</span>
          <span class="desk-spacer" />
          <RouterLink
            v-if="canCreate"
            :to="{ name: 'new', params: { doctype }, query: { [config?.field ?? '']: column } }"
            class="kanban-add"
            :aria-label="t.kanban.addIn(column)"
            :title="t.kanban.addIn(column)"
            :data-testid="`kanban-add-${column}`"
          >
            <i class="pi pi-plus" />
          </RouterLink>
        </header>

        <div class="kanban-cards">
          <article
            v-for="r in board[column]?.items ?? []"
            :key="r.name"
            :class="['kanban-card', { movable: draggable(r), dragging: dragging?.record.name === r.name }]"
            :draggable="draggable(r)"
            :data-testid="`kanban-card-${r.name}`"
            @dragstart="onDragStart(r, column, $event)"
            @dragend="(dragging = null), (over = null)"
          >
            <div class="kanban-card-head">
              <FileThumb v-if="meta.image_field" :id="r.data[meta.image_field]" :size="28" round />
              <RouterLink :to="{ name: 'form', params: { doctype, name: r.name } }" class="kanban-card-title" :data-testid="`kanban-open-${r.name}`">{{ title(r) }}</RouterLink>
              <Button
                v-if="draggable(r)"
                icon="pi pi-ellipsis-v"
                text
                rounded
                size="small"
                severity="secondary"
                :aria-label="`${t.kanban.moveTo} — ${title(r)}`"
                :data-testid="`kanban-menu-${r.name}`"
                @click="openMenu($event, r, column)"
              />
            </div>
            <dl v-if="cardFields.length" class="kanban-facts">
              <template v-for="f in cardFields" :key="f.fieldname">
                <template v-if="cell(f, r)">
                  <dt class="desk-muted">{{ f.label ?? f.fieldname }}</dt>
                  <dd><CellValue :field="f" :value="r.data[f.fieldname]" :text="cell(f, r)" /></dd>
                </template>
              </template>
            </dl>
            <span v-if="meta.submittable" :class="['status-pill', statusTone(r.docstatus)]">{{ statusLabel(r.docstatus) }}</span>
          </article>
          <p v-if="!board[column]?.items.length && !board[column]?.loading" class="desk-muted kanban-empty">{{ t.kanban.empty }}</p>
        </div>

        <Button
          v-if="board[column] && board[column].items.length < board[column].total"
          :label="t.kanban.more"
          text
          size="small"
          severity="secondary"
          :loading="board[column].loading"
          class="kanban-more"
          :data-testid="`kanban-more-${column}`"
          @click="loadColumn(column, true)"
        />
      </section>
    </div>
    <Menu ref="menu" :model="menuItems" popup />
  </div>
</template>
