<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable, { type DataTablePageEvent, type DataTableSortEvent } from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
import Menu from 'primevue/menu'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import type { MenuItem } from 'primevue/menuitem'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BlockEditor from '@/components/BlockEditor.vue'
import CellValue from '@/components/CellValue.vue'
import FileThumb from '@/components/FileThumb.vue'
import FilterBar from '@/components/FilterBar.vue'
import ViewSwitcher from '@/components/ViewSwitcher.vue'
import CalendarView from '@/components/views/CalendarView.vue'
import GanttView from '@/components/views/GanttView.vue'
import KanbanView from '@/components/views/KanbanView.vue'
import ReportView from '@/components/views/ReportView.vue'
import TreeView from '@/components/views/TreeView.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { statusLabel, statusTone } from '@/lib/docstatus'
import { buildFilters, parseFiltersParam, rowsFromFilters, type FilterRow } from '@/lib/filters'
import { formatCell, hasReadableName, isNumeric, relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import { unitLinks, unitOf } from '@/lib/units'
import type { DocRecord, DocTypeMeta, FieldMeta, Filters, LayoutScope, SavedView, ViewName } from '@/lib/types'
import { pickView, readView, writeView } from '@/lib/viewprefs'
import { useFiles } from '@/stores/files'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'

/**
 * La page d'un type de document : un titre, le choix de la vue (Liste, Rapport, Tableau de bord, et Kanban, Calendrier, Gantt quand le type les
 * déclare) et, dessous, la vue. La recherche, les filtres et l'état sont COMMUNS à toutes les vues : changer de vue garde le même sous-ensemble
 * de documents. La vue et les filtres de départ peuvent venir de l'adresse (`?view=kanban`, `?filters={…}` : c'est ainsi qu'une carte de
 * chiffres ouvre la liste de ce qu'elle a compté).
 */
const props = defineProps<{ doctype: string; view?: string | null; filters?: string | null }>()

const { api } = useServices()
const metaStore = useMeta()
const links = useLinks()
const files = useFiles()
const confirm = useConfirm()
const notify = useNotify()
const router = useRouter()
const route = useRoute()

const meta = shallowRef<DocTypeMeta | null>(null)
const rows = ref<DocRecord[]>([])
const total = ref(0)
const loading = ref(false)
const failure = ref<string | null>(null)
const first = ref(0)
const pageSize = ref(20)
const sortField = ref('modified')
const sortOrder = ref<1 | -1>(-1)
/** Le tri d'une liste « nue » : le plus récemment modifié d'abord ; un DocType virtuel n'a pas de date (sa source ne date rien) : par titre, croissant. */
function resetSort(): void {
  const virtual = meta.value?.virtual === true
  sortField.value = virtual ? (meta.value?.title_field ?? 'name') : 'modified'
  sortOrder.value = virtual ? 1 : -1
}
const quick = ref('')
const filterRows = ref<FilterRow[]>([])
const showFilters = ref(false)
const selection = ref<DocRecord[]>([])
/** Documents soumettables : `null` tous, sinon 0 brouillons, 1 soumis, 2 annulés. */
const status = ref<number | null>(null)
/** Les filtres EN VIGUEUR (ce que les vues demandent au serveur) : ce que l'utilisateur a validé, pas ce qu'il est en train de taper. */
const applied = ref<Filters>({})
const statusChoices = computed(() => [
  { label: t.list.allStatuses, value: null },
  ...[0, 1, 2].map((v) => ({ label: statusLabel(v), value: v })),
])

// ── Vues sauvegardées (phase C) : une configuration de liste, privée à l'utilisateur ─────────
const savedViews = ref<SavedView[]>([])
const activeView = ref<string | null>(null)
const viewColumns = ref<string[] | null>(null)
const showSave = ref(false)
const saveName = ref('')
const saveDefault = ref(false)
const saveBusy = ref(false)
const viewsMenu = ref<InstanceType<typeof Menu> | null>(null)

const label = computed(() => meta.value?.label ?? props.doctype)
const columns = computed<FieldMeta[]>(() => {
  const m = meta.value
  if (!m) return []
  const names = viewColumns.value ?? m.list_columns
  return names.map((n) => m.fields.find((f) => f.fieldname === n)).filter((f): f is FieldMeta => !!f)
})
/** Un DocType numéroté (factures, mouvements) montre son numéro en première colonne : sans lui, deux lignes ne se distinguent pas. */
const numbered = computed(() => hasReadableName(meta.value))
const canCreate = computed(() => meta.value?.rights.create !== 'none')
const canDelete = computed(() => meta.value?.rights.delete !== 'none')

// Personnaliser le tableau de bord du type : le serveur valide, puis on relit la définition (qui porte le nouveau tableau).
const dashEditing = ref(false)
const dashEditor = ref<InstanceType<typeof BlockEditor> | null>(null)
async function saveDashboard(blocks: unknown[], scope: LayoutScope): Promise<void> {
  await api.saveDashboardOverride(props.doctype, { blocks }, scope)
  meta.value = await metaStore.refresh(props.doctype)
}
async function resetDashboard(scope: LayoutScope): Promise<void> {
  await api.removeDashboardOverride(props.doctype, scope)
  meta.value = await metaStore.refresh(props.doctype)
}
const quickField = computed(() => meta.value?.title_field ?? 'name')
// Seules les lignes complètes filtrent vraiment : « Filtres · 1 » ne doit pas s'afficher pour une ligne encore vide.
const activeFilters = computed(() => (meta.value ? Object.keys(buildFilters(filterRows.value, meta.value.fields)).length : 0))

// ── Vues sauvegardées : rejouer, enregistrer, supprimer ─────
async function loadViews(): Promise<void> {
  try {
    savedViews.value = await api.listViews(props.doctype)
  } catch {
    savedViews.value = [] // un serveur plus ancien n'a pas la route : la liste reste utilisable
  }
}

/** Rejoue une vue : ses filtres, son tri et ses colonnes remplacent l'état courant. */
function applyView(v: SavedView): void {
  const m = meta.value
  if (!m) return
  const start = rowsFromFilters(v.config.filters ?? {}, m.fields, () => ++seq)
  filterRows.value = start.rows
  status.value = m.submittable ? start.status : null
  quick.value = ''
  showFilters.value = start.rows.length > 0
  if (typeof v.config.order_by === 'string') sortField.value = v.config.order_by
  sortOrder.value = v.config.descending === false ? 1 : -1
  viewColumns.value = v.config.columns ?? null
  activeView.value = v.name
  commit()
}

/** Revient à la liste « nue » : tri par défaut, colonnes du type, aucun filtre. */
function clearView(): void {
  activeView.value = null
  viewColumns.value = null
  filterRows.value = []
  status.value = null
  quick.value = ''
  showFilters.value = false
  resetSort()
  commit()
}

function openSave(): void {
  saveName.value = activeView.value ?? ''
  saveDefault.value = false
  showSave.value = true
}

async function doSave(): Promise<void> {
  const m = meta.value
  const name = saveName.value.trim()
  if (!m || !name) return
  saveBusy.value = true
  try {
    const target = activeView.value ?? name
    const saved = await api.saveView(props.doctype, target, {
      label: name,
      kind: 'list',
      config: { columns: viewColumns.value ?? m.list_columns, filters: applied.value, order_by: sortField.value, descending: sortOrder.value === -1 },
      is_default: saveDefault.value,
      previous: activeView.value && activeView.value !== target ? activeView.value : undefined,
    })
    activeView.value = saved.name
    await loadViews()
    showSave.value = false
    notify.success(t.list.viewSaved)
  } catch (e) {
    notify.error(e)
  } finally {
    saveBusy.value = false
  }
}

function removeActiveView(): void {
  const name = activeView.value
  if (!name) return
  confirm.require({
    header: t.delete,
    message: t.list.confirmDeleteView(name),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.delete,
    rejectLabel: t.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      try {
        await api.removeView(props.doctype, name)
        activeView.value = null
        viewColumns.value = null
        await loadViews()
        notify.success(t.list.viewDeleted)
      } catch (e) {
        notify.error(e)
      }
    },
  })
}

const viewsMenuItems = computed(() => {
  const items: MenuItem[] = savedViews.value.map((v) => ({
    label: v.label,
    icon: v.is_default ? 'pi pi-bookmark-fill' : 'pi pi-bookmark',
    command: () => applyView(v),
  }))
  if (items.length) items.push({ separator: true })
  items.push({ label: t.list.saveView, icon: 'pi pi-plus', command: openSave })
  if (activeView.value) items.push({ label: t.list.deleteView, icon: 'pi pi-trash', command: removeActiveView })
  return items
})

function toggleViewsMenu(event: Event): void {
  viewsMenu.value?.toggle(event)
}

const views = computed<ViewName[]>(() => meta.value?.views?.available ?? ['list'])
const current = computed<ViewName>(() => pickView(meta.value?.views, props.view, readView(props.doctype)))

function choose(view: ViewName): void {
  writeView(props.doctype, view)
  // Les filtres de départ de l'adresse ont fait leur office : la barre de filtres est maintenant la source.
  const { filters: _start, ...rest } = route.query
  void _start
  void router.replace({ name: 'list', params: { doctype: props.doctype }, query: { ...rest, view } })
}

// ── Filtres en vigueur ──────────────────────────────────────
function built(): Filters {
  const m = meta.value
  if (!m) return {}
  const filters = buildFilters(filterRows.value, m.fields, { field: quickField.value, text: quick.value })
  if (m.submittable && status.value !== null) filters.docstatus = status.value
  return filters
}

let ticket = 0 // une réponse tardive d'une requête périmée ne doit pas écraser la dernière

async function fetchPage(): Promise<void> {
  const m = meta.value
  if (!m) return
  const mine = ++ticket
  loading.value = true
  failure.value = null
  try {
    const res = await api.list(props.doctype, {
      filters: applied.value,
      order_by: sortField.value,
      order: sortOrder.value === 1 ? 'asc' : 'desc',
      limit: pageSize.value,
      offset: first.value,
    })
    if (mine !== ticket) return
    rows.value = res.items
    total.value = res.total
    selection.value = []
    // Les photos de la page, en une requête (un lien signé par fichier).
    if (m.image_field) void files.resolve(res.items.map((r) => r.data[m.image_field!]).filter((v): v is string => typeof v === 'string'))
    // Les unités (« kg ») des colonnes de quantités viennent des articles de ces lignes : une lecture pour toute la page.
    for (const [target, names] of unitLinks(m.fields, res.items.map((r) => r.data))) void links.resolve(target, names, { data: true })
    for (const f of columns.value.filter((c) => c.fieldtype === 'Link' && typeof c.options === 'string')) {
      void links.resolve(f.options as string, res.items.map((r) => r.data[f.fieldname]).filter((v): v is string => typeof v === 'string'))
    }
  } catch (e) {
    if (mine !== ticket) return
    rows.value = []
    total.value = 0
    failure.value = e instanceof ApiError && e.status === 404 ? t.errors.unknownDoctype : describeError(e)
  } finally {
    if (mine === ticket) loading.value = false
  }
}

/** Met en vigueur ce que l'utilisateur a saisi (recherche, filtres, état) : la liste se relit, les autres vues se rechargent d'elles-mêmes. */
function commit(): void {
  first.value = 0
  applied.value = built()
  if (current.value === 'list') void fetchPage()
}

let seq = Date.now()

async function open(): Promise<void> {
  ticket++
  meta.value = null
  rows.value = []
  total.value = 0
  failure.value = null
  first.value = 0
  quick.value = ''
  filterRows.value = []
  status.value = null
  applied.value = {}
  showFilters.value = false
  resetSort() // meta est vide ici : le tri par défaut ; il se corrige dès que le type est lu
  savedViews.value = []
  activeView.value = null
  viewColumns.value = null
  showSave.value = false
  loading.value = true
  try {
    meta.value = await metaStore.get(props.doctype)
  } catch (e) {
    loading.value = false
    failure.value = e instanceof ApiError && e.status === 404 ? t.errors.unknownDoctype : describeError(e)
    return
  }
  resetSort()
  if (meta.value.single) {
    // Un document unique n'a pas de liste : c'est un réglage, qui vit dans la page Paramètres.
    loading.value = false
    void router.replace(meta.value.settings_group ? { name: 'settings' } : { name: 'settings', params: { doctype: props.doctype } })
    return
  }
  // Les filtres de départ de l'adresse (une carte de chiffres) apparaissent dans la barre : on VOIT ce qui filtre, et on peut le retirer.
  const start = rowsFromFilters(parseFiltersParam(props.filters), meta.value.fields, () => ++seq)
  filterRows.value = start.rows
  status.value = meta.value.submittable ? start.status : null
  showFilters.value = start.rows.length > 0
  applied.value = built()
  // Une vue par défaut s'ouvre d'elle-même, SAUF si l'adresse impose déjà une vue ou des filtres (l'adresse est explicite).
  await loadViews()
  const preferred = savedViews.value.find((v) => v.is_default)
  if (preferred && !props.view && !props.filters) {
    applyView(preferred)
    if (current.value !== 'list') loading.value = false
    return
  }
  if (current.value === 'list') await fetchPage()
  else loading.value = false
}

// Le type change, ou une adresse porte de nouveaux filtres : on repart de zéro. Une adresse qui PERD ses filtres (on vient de changer de vue) ne
// doit pas effacer ce que l'utilisateur a réglé depuis.
watch(
  () => [props.doctype, props.filters ?? ''] as const,
  ([doctype, filters], old) => {
    if (old && old[0] === doctype && !filters) return
    void open()
  },
  { immediate: true },
)

// Revenir à la liste : ses lignes ont pu vieillir pendant qu'on était ailleurs.
// Quitter le tableau de bord (autre vue, autre type) abandonne le brouillon : le composant est démonté, son état d'édition ne doit pas survivre.
watch([() => props.doctype, current], () => (dashEditing.value = false))

watch(current, (view, old) => {
  if (view === 'list' && old !== 'list' && meta.value) void fetchPage()
})

// Recherche rapide : on attend la fin de la frappe.
let timer: ReturnType<typeof setTimeout> | undefined
watch(quick, () => {
  clearTimeout(timer)
  timer = setTimeout(commit, 300)
})
onBeforeUnmount(() => clearTimeout(timer))

function onPage(e: DataTablePageEvent): void {
  first.value = e.first
  pageSize.value = e.rows
  void fetchPage()
}

function onSort(e: DataTableSortEvent): void {
  if (typeof e.sortField === 'string') {
    sortField.value = e.sortField
    sortOrder.value = e.sortOrder === 1 ? 1 : -1
    first.value = 0
    void fetchPage()
  }
}

function pickStatus(value: number | null): void {
  status.value = value
  commit()
}

/** Un groupe du rapport a été cliqué : on filtre sur sa valeur (la barre de filtres la montre) et on revient à la liste des documents. */
function drill(field: string, value: string | number | boolean): void {
  if (field === 'docstatus') {
    if (typeof value === 'number') status.value = value
  } else {
    filterRows.value = [...filterRows.value.filter((r) => r.field !== field), { id: ++seq, field, op: '=', value }]
    showFilters.value = true
  }
  commit()
}

function cell(field: FieldMeta, r: DocRecord): string {
  const v = r.data[field.fieldname]
  if (field.fieldtype === 'Link' && typeof v === 'string' && typeof field.options === 'string') return links.title(field.options, v)
  const text = formatCell(field, v)
  const unit = text && meta.value ? unitOf(field, meta.value.fields, r.data, links.field) : null
  return unit ? `${text} ${unit}` : text
}

function title(r: DocRecord): string {
  const f = meta.value?.title_field
  const v = f ? r.data[f] : undefined
  return typeof v === 'string' && v ? v : r.name
}

function deleteSelected(): void {
  const targets = selection.value
  if (!targets.length) return
  confirm.require({
    header: t.delete,
    message: t.list.confirmDelete(targets.length),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.delete,
    rejectLabel: t.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      let ok = 0
      let ko = 0
      let last: unknown
      for (const r of targets) {
        try {
          await api.remove(props.doctype, r.name)
          ok++
        } catch (e) {
          ko++
          last = e
        }
      }
      if (ko === 0) notify.success(t.list.deleted(ok))
      else if (ok === 0) notify.error(last)
      else notify.warn(t.list.partialDelete(ok, ko))
      void fetchPage()
    },
  })
}
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head">
      <h1 data-testid="list-title">{{ label }}</h1>
      <ViewSwitcher v-if="meta && views.length > 1" :views="views" :model-value="current" @update:model-value="choose" />
      <span class="desk-spacer" />
      <Button
        v-if="selection.length && canDelete && current === 'list'"
        :label="t.list.deleteSelected(selection.length)"
        icon="pi pi-trash"
        severity="danger"
        size="small"
        outlined
        data-testid="bulk-delete"
        @click="deleteSelected"
      />
      <RouterLink v-if="canCreate && meta" :to="{ name: 'new', params: { doctype } }">
        <Button :label="t.new" icon="pi pi-plus" size="small" data-testid="new-doc" />
      </RouterLink>
    </div>

    <Message v-if="failure" severity="error" class="banner-error" data-testid="list-error">{{ failure }}</Message>

    <template v-if="meta">
      <template v-if="current !== 'dashboard'">
        <div class="desk-toolbar">
          <IconField>
            <InputIcon class="pi pi-search" />
            <InputText v-model="quick" size="small" :placeholder="t.list.searchIn(label)" :aria-label="t.search" data-testid="quick-search" />
          </IconField>
          <Button
            icon="pi pi-filter"
            :label="activeFilters ? `${t.list.filters} · ${activeFilters}` : t.list.filters"
            size="small"
            :severity="activeFilters ? 'primary' : 'secondary'"
            :outlined="!!activeFilters"
            :text="!activeFilters"
            data-testid="toggle-filters"
            @click="showFilters = !showFilters"
          />
          <SelectButton
            v-if="meta.submittable"
            :model-value="status"
            :options="statusChoices"
            option-label="label"
            option-value="value"
            :allow-empty="false"
            size="small"
            :aria-label="t.list.status"
            data-testid="status-filter"
            @update:model-value="pickStatus"
          />
          <Button
            v-if="current === 'list'"
            icon="pi pi-bookmark"
            :label="activeView ?? t.list.views"
            size="small"
            :severity="activeView ? 'primary' : 'secondary'"
            :outlined="!!activeView"
            :text="!activeView"
            data-testid="views-menu"
            @click="toggleViewsMenu"
          />
          <Menu ref="viewsMenu" :model="viewsMenuItems" :popup="true" />
          <span class="desk-spacer" />
          <span v-if="current === 'list'" class="desk-muted" data-testid="list-count">{{ t.list.count(rows.length, total) }}</span>
        </div>

        <FilterBar v-if="showFilters" v-model:rows="filterRows" :fields="meta.fields" @apply="commit" />
      </template>

      <div v-if="current === 'list'" class="desk-card">
        <DataTable
          v-model:selection="selection"
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
          <Column v-if="canDelete" selection-mode="multiple" header-style="width: 2.5rem" />
          <Column v-if="numbered" field="name" sort-field="name" :header="t.list.number" sortable>
            <template #body="{ data }">
              <span class="avatar-cell"><FileThumb v-if="meta.image_field" :id="data.data[meta.image_field]" :size="28" round /><RouterLink :to="{ name: 'form', params: { doctype, name: data.name } }" :data-testid="`open-${data.name}`" style="font-weight: 500; white-space: nowrap">{{ data.name }}</RouterLink></span>
            </template>
          </Column>
          <Column
            v-for="(f, i) in columns"
            :key="f.fieldname"
            :field="`data.${f.fieldname}`"
            :sort-field="f.fieldname"
            :header="f.label ?? f.fieldname"
            :header-class="isNumeric(f) ? 'num' : ''"
            :body-class="isNumeric(f) ? 'num' : ''"
            sortable
          >
            <template #body="{ data }">
              <span v-if="i === 0 && !numbered" class="avatar-cell">
                <FileThumb v-if="meta.image_field" :id="data.data[meta.image_field]" :size="28" round />
                <RouterLink :to="{ name: 'form', params: { doctype, name: data.name } }" :data-testid="`open-${data.name}`" style="font-weight: 500">
                  {{ cell(f, data) || title(data) }}
                </RouterLink>
              </span>
              <CellValue v-else :field="f" :value="data.data[f.fieldname]" :text="cell(f, data)" />
            </template>
          </Column>
          <Column v-if="meta.submittable" field="docstatus" sort-field="docstatus" :header="t.list.status" sortable>
            <template #body="{ data }">
              <span :class="['status-pill', statusTone(data.docstatus)]" :data-testid="`status-${data.name}`">{{ statusLabel(data.docstatus) }}</span>
            </template>
          </Column>
          <Column v-if="!meta.virtual" field="modified" sort-field="modified" :header="t.list.modified" sortable>
            <template #body="{ data }"><span class="desk-muted">{{ relativeTime(data.modified) }}</span></template>
          </Column>
          <template #empty>
            <div class="desk-muted" style="padding: 1.5rem; text-align: center" data-testid="list-empty">
              {{ quick || activeFilters || status !== null ? t.list.emptyFiltered : t.list.empty(label) }}
            </div>
          </template>
        </DataTable>
      </div>

      <ReportView v-else-if="current === 'report'" :doctype="doctype" :meta="meta" :filters="applied" @drill="drill" />
      <div v-else-if="current === 'dashboard'" data-testid="doctype-dashboard">
        <div v-if="!dashEditing" class="dash-tools">
          <Button :label="t.editor.start" icon="pi pi-pencil" severity="secondary" text size="small" data-testid="dashboard-edit" @click="dashEditor?.start()" />
        </div>
        <BlockEditor ref="dashEditor" v-model:editing="dashEditing" :blocks="meta.views?.dashboard ?? []" :doctype="doctype" :layer="meta.views?.dashboard_layer" :shareable="meta.views?.dashboard_shareable" :save="saveDashboard" :reset="resetDashboard" />
      </div>
      <KanbanView v-else-if="current === 'kanban'" :doctype="doctype" :meta="meta" :filters="applied" />
      <CalendarView v-else-if="current === 'calendar'" :doctype="doctype" :meta="meta" :filters="applied" />
      <GanttView v-else-if="current === 'gantt'" :doctype="doctype" :meta="meta" :filters="applied" />
      <TreeView v-else-if="current === 'tree'" :doctype="doctype" :meta="meta" />
    </template>

    <Dialog
      v-model:visible="showSave"
      :header="t.list.saveView"
      modal
      :draggable="false"
      :style="{ width: '24rem' }"
      data-testid="save-view-dialog"
    >
      <div class="desk-field">
        <label for="view-name">{{ t.list.viewName }}</label>
        <InputText id="view-name" v-model="saveName" data-testid="view-name" autofocus @keyup.enter="doSave" />
      </div>
      <label class="desk-inline">
        <Checkbox v-model="saveDefault" binary data-testid="view-default" />
        <span>{{ t.list.viewDefault }}</span>
      </label>
      <template #footer>
        <Button :label="t.cancel" text severity="secondary" @click="showSave = false" />
        <Button :label="t.save" :loading="saveBusy" data-testid="save-view" @click="doSave" />
      </template>
    </Dialog>
  </div>
</template>
