<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable, { type DataTablePageEvent, type DataTableRowClickEvent, type DataTableSortEvent } from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
import Menu from 'primevue/menu'
import Message from 'primevue/message'
import Paginator from 'primevue/paginator'
import Skeleton from 'primevue/skeleton'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import type { MenuItem } from 'primevue/menuitem'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import BlockEditor from '@/components/BlockEditor.vue'
import BulkActions from '@/components/BulkActions.vue'
import { useExtensions } from '@/extensions/host'
import CellValue from '@/components/CellValue.vue'
import FileThumb from '@/components/FileThumb.vue'
import FilterBar from '@/components/FilterBar.vue'
import ImportDialog from '@/components/ImportDialog.vue'
import RecordCards from '@/components/RecordCards.vue'
import ListSidebar from '@/components/ListSidebar.vue'
import RowTags from '@/components/tags/RowTags.vue'
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
import { sidebarFields } from '@/lib/sidebar'
import { TAG_FILTER, sameTag, supportsTags, tagFilterLabel, type TagFilter } from '@/lib/tags'
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
const status = ref<number | string | null>(null)
/** Les filtres EN VIGUEUR (ce que les vues demandent au serveur) : ce que l'utilisateur a validé, pas ce qu'il est en train de taper. */
const applied = ref<Filters>({})
/** L'étiquette filtrée (`_tag`), hors de la barre de filtres : une pastille dans la barre d'outils la montre et la retire. */
const tagFilter = ref<TagFilter | null>(null)
/** L'étiquette filtrée par égalité : celle que la barre latérale et les pastilles mettent en évidence. */
const activeTag = computed(() => (typeof tagFilter.value === 'string' ? tagFilter.value : null))
/** Le type a-t-il des étiquettes en usage (dans ce que l'utilisateur lit) ? Sans elles, pas de groupe « Étiquettes » ni de barre latérale pour lui. */
const hasTags = ref(false)
/** Les étiquettes des lignes de la page, lues en une requête après elles. */
const rowTags = ref<Record<string, string[]>>({})
/** Change quand des étiquettes ont été posées depuis la liste : la barre latérale recompte. */
const tagStamp = ref(0)
// Un type à WORKFLOW se filtre par son état métier : « Brouillon / Soumis / Annulé » n'en dit pas assez (plusieurs états
// peuvent partager le même `docstatus`). Le filtre porte alors sur la colonne `workflow_state`.
/** L'état de départ d'une vue ou d'une adresse : un nom d'état pour un type à workflow, un `docstatus` pour un type soumettable, sinon rien. */
function startStatus(m: DocTypeMeta, value: number | string | null): number | string | null {
  if (m.workflow) return typeof value === 'string' ? value : null
  return m.submittable && typeof value === 'number' ? value : null
}

const flowStates = computed(() => meta.value?.workflow?.states.map((st) => st.name) ?? [])
const statusChoices = computed(() =>
  flowStates.value.length
    ? [{ label: t.list.allStatuses, value: null }, ...flowStates.value.map((name) => ({ label: name, value: name }))]
    : [{ label: t.list.allStatuses, value: null }, ...[0, 1, 2].map((v) => ({ label: statusLabel(v), value: v }))],
)

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
/** On coche des lignes quand une action en lot est possible : modifier, assigner, soumettre, annuler ou supprimer. */
const extensions = useExtensions()
// Cocher des lignes sert aux actions groupées : celles du Desk (selon les droits) ET celles des extensions (D28 ; le serveur juge chaque appel).
const selectable = computed(() => !!meta.value && ((['write', 'delete', 'submit', 'cancel'] as const).some((a) => meta.value!.rights[a] !== 'none') || extensions.hasListActions({ doctype: props.doctype, meta: meta.value })))

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

// ── Barre latérale : les valeurs d'un champ à choix et leur nombre (clic = filtrer) ─────────
const SIDEBAR_KEY = 'xdt.desk.listSidebar'
function readSidebar(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== 'off'
  } catch {
    return true
  }
}
const showSidebar = ref(readSidebar())
/** La colonne de la barre latérale n'existe que si le type a de quoi la remplir : sans cela, le tableau se retrouverait dans sa colonne étroite. */
const sidebarFilled = computed(() => !!meta.value && (sidebarFields(meta.value).length > 0 || hasTags.value))
const hasSidebar = computed(() => showSidebar.value && sidebarFilled.value)
function toggleSidebar(): void {
  showSidebar.value = !showSidebar.value
  try {
    localStorage.setItem(SIDEBAR_KEY, showSidebar.value ? 'on' : 'off')
  } catch {
    // un navigateur qui refuse le stockage garde le choix pour la session
  }
}
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
  status.value = startStatus(m, start.status)
  tagFilter.value = supportsTags(m) ? start.tag : null
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
  if (status.value !== null) {
    if (flowStates.value.length) filters.workflow_state = status.value
    else if (m.submittable) filters.docstatus = status.value
  }
  if (tagFilter.value !== null) filters[TAG_FILTER] = tagFilter.value
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
    void loadRowTags(m, res.items, mine)
  } catch (e) {
    if (mine !== ticket) return
    rows.value = []
    total.value = 0
    failure.value = e instanceof ApiError && e.status === 404 ? t.errors.unknownDoctype : describeError(e)
  } finally {
    if (mine === ticket) loading.value = false
  }
}

/** Les étiquettes des lignes affichées : une requête pour la page, jamais une par ligne. Une erreur laisse la liste sans pastilles. */
async function loadRowTags(m: DocTypeMeta, items: readonly DocRecord[], mine: number): Promise<void> {
  await tagsReady
  if (mine !== ticket) return
  if (!supportsTags(m) || !hasTags.value || !items.length) {
    rowTags.value = {}
    return
  }
  try {
    const found = await api.tagsOf(props.doctype, items.map((r) => r.name))
    if (mine === ticket) rowTags.value = found
  } catch {
    if (mine === ticket) rowTags.value = {}
  }
}

let tagsReady: Promise<void> = Promise.resolve()
/** Existe-t-il au moins une étiquette lisible dans ce type ? (Une seule ligne de compte suffit.) */
async function loadHasTags(m: DocTypeMeta): Promise<void> {
  if (!supportsTags(m)) {
    hasTags.value = false
    return
  }
  const asked = m.name // une réponse tardive pour un autre type ne doit rien régler ici
  let used = false
  try {
    used = await api.tagsUsed(asked)
  } catch {
    used = false
  }
  if (meta.value?.name === asked) hasTags.value = used
}

/** Une étiquette cliquée (barre latérale, pastille d'une ligne) filtre la liste ; la même, cliquée à nouveau, retire le filtre. */
function pickTag(tag: string): void {
  tagFilter.value = activeTag.value !== null && sameTag(activeTag.value, tag) ? null : tag
  commit()
}
function clearTag(): void {
  tagFilter.value = null
  commit()
}

/** Une action en lot a abouti : la page se relit, et des étiquettes ont pu apparaître (la barre latérale recompte). */
async function afterBulk(): Promise<void> {
  tagStamp.value++
  if (meta.value && !hasTags.value) await (tagsReady = loadHasTags(meta.value))
  await fetchPage()
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
  tagFilter.value = null
  hasTags.value = false
  rowTags.value = {}
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
  status.value = startStatus(meta.value, start.status)
  tagFilter.value = supportsTags(meta.value) ? start.tag : null
  showFilters.value = start.rows.length > 0
  applied.value = built()
  tagsReady = loadHasTags(meta.value) // en même temps que la première page, pas avant elle
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

function onPage(e: Pick<DataTablePageEvent, 'first' | 'rows'>): void {
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

function pickStatus(value: number | string | null): void {
  status.value = value
  commit()
}

// ── Une liste qui se lit sur un téléphone, qu'on ouvre d'un clic sur la ligne, qu'on relit à la demande ─────────────────────────
const compact = ref(typeof matchMedia === 'function' && matchMedia('(max-width: 700px)').matches)
let compactQuery: MediaQueryList | null = null
const onCompact = (e: MediaQueryListEvent) => (compact.value = e.matches)
onMounted(() => {
  if (typeof matchMedia === 'function') {
    compactQuery = matchMedia('(max-width: 700px)')
    compact.value = compactQuery.matches
    compactQuery.addEventListener('change', onCompact)
  }
  window.addEventListener('keydown', onEscape)
})
onBeforeUnmount(() => {
  compactQuery?.removeEventListener('change', onCompact)
  window.removeEventListener('keydown', onEscape)
})
/** Échap vide la sélection (sauf dans un champ de saisie ou une boîte ouverte, qui ont leur propre Échap). */
function onEscape(e: KeyboardEvent): void {
  if (e.key !== 'Escape' || !selection.value.length) return
  // Une case cochée a le focus après un clic : Échap y vaut « tout désélectionner ». Un champ de saisie ou une boîte ouverte ont le leur.
  if ((e.target as HTMLElement | null)?.closest('input:not([type="checkbox"]):not([type="radio"]), textarea, [role="dialog"], [role="listbox"], [role="menu"]')) return
  selection.value = []
}
function openRow(row: DocRecord): void {
  void router.push({ name: 'form', params: { doctype: props.doctype, name: row.name } })
}
/** Un clic sur la ligne l'ouvre, sauf sur ce qui a son propre rôle (un lien, un bouton, la case à cocher). */
function onRowClick(e: DataTableRowClickEvent): void {
  const target = e.originalEvent.target as HTMLElement | null
  if (target?.closest('a, button, input, label, .p-checkbox')) return
  if (window.getSelection()?.toString()) return // on était en train de sélectionner du texte
  openRow(e.data as DocRecord)
}
/** Importer : créer (droit de créer) ou mettre à jour (droit d'écrire) ; un document unique n'a pas de liste. */
const canImport = computed(() => !!meta.value && !meta.value.single && (meta.value.rights.create !== 'none' || meta.value.rights.write !== 'none'))
const importing = ref(false)
const moreMenu = ref<InstanceType<typeof Menu> | null>(null)
const moreItems = computed<MenuItem[]>(() => [
  { label: t.list.refresh, icon: 'pi pi-refresh', command: () => void fetchPage(), disabled: current.value !== 'list' },
  { label: t.sidebar.toggle, icon: 'pi pi-align-left', command: toggleSidebar, disabled: current.value !== 'list' },
  { label: t.formBuilder.menu, icon: 'pi pi-objects-column', testid: 'list-customize', command: () => void router.push({ name: 'customize', params: { doctype: props.doctype } }), visible: !!meta.value && meta.value.rights.write !== 'none' && !meta.value.virtual },
  { label: t.workflowBuilder.menu, icon: 'pi pi-sitemap', testid: 'list-workflow', command: () => void router.push({ name: 'workflow', params: { doctype: props.doctype } }), visible: !!meta.value?.workflow },
  { label: t.importCsv.menu, icon: 'pi pi-upload', testid: 'import-csv', command: () => (importing.value = true), visible: canImport.value },
  { separator: true },
  { label: t.apiDocs.title, icon: 'pi pi-code', command: () => void router.push(`/api/${encodeURIComponent(props.doctype)}`) },
])
/** Aucun filtre ne cache de documents : la liste est vide pour de bon, on propose d'en créer un. */
const trulyEmpty = computed(() => !loading.value && !total.value && !quick.value && !activeFilters.value && status.value === null)

/** Un clic dans la barre latérale : filtre sur cette valeur, ou retire le filtre si elle y est déjà. */
function pickValue(field: string, value: string | number | boolean): void {
  const already = filterRows.value.find((r) => r.field === field && r.op === '=' && r.value === value)
  if (already) {
    filterRows.value = filterRows.value.filter((r) => r !== already)
    commit()
  } else drill(field, value)
}

/** Un groupe du rapport a été cliqué : on filtre sur sa valeur (la barre de filtres la montre) et on revient à la liste des documents. */
function drill(field: string, value: string | number | boolean): void {
  if (field === 'docstatus' || field === 'workflow_state') {
    if (meta.value) status.value = startStatus(meta.value, value as number | string)
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
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head">
      <h1 data-testid="list-title">{{ label }}</h1>
      <ViewSwitcher v-if="meta && views.length > 1" :views="views" :model-value="current" @update:model-value="choose" />
      <span class="desk-spacer" />
      <Button v-if="meta" icon="pi pi-ellipsis-h" text severity="secondary" size="small" :aria-label="t.list.more" :title="t.list.more" aria-haspopup="menu" data-testid="more-actions" @click="moreMenu?.toggle($event)" />
      <Menu ref="moreMenu" :model="moreItems" popup>
        <template #item="{ item, props: slot }">
          <a v-bind="slot.action" :data-testid="item.testid">
            <span :class="item.icon" aria-hidden="true" />
            <span class="p-menu-item-label">{{ item.label }}</span>
          </a>
        </template>
      </Menu>
      <ImportDialog v-if="meta && canImport" v-model:visible="importing" :doctype="doctype" :meta="meta" @imported="fetchPage" />
      <Button v-if="canCreate && meta" :as="RouterLink" :to="{ name: 'new', params: { doctype } }" :label="t.new" icon="pi pi-plus" size="small" data-testid="new-doc" />
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
            v-if="meta.submittable || flowStates.length"
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
          <Button
            v-if="current === 'list' && sidebarFilled"
            icon="pi pi-align-left"
            size="small"
            :severity="showSidebar ? 'primary' : 'secondary'"
            text
            :aria-pressed="showSidebar"
            :aria-label="t.sidebar.toggle"
            :title="t.sidebar.toggle"
            class="sidebar-toggle"
            data-testid="sidebar-toggle"
            @click="toggleSidebar"
          />
          <span v-if="tagFilter" class="tag-chip on" data-testid="tag-filter">
            <span class="pi pi-tag" aria-hidden="true" style="margin-right: 0.3rem" />{{ t.tags.filtered(tagFilterLabel(tagFilter)) }}
            <button type="button" class="tag-remove" :aria-label="t.tags.clearFilter" :title="t.tags.clearFilter" data-testid="tag-filter-clear" @click="clearTag">
              <span class="pi pi-times" aria-hidden="true" />
            </button>
          </span>
          <span class="desk-spacer" />
          <span v-if="current === 'list'" class="desk-muted" data-testid="list-count">{{ t.list.count(rows.length, total) }}</span>
        </div>

        <FilterBar v-if="showFilters" v-model:rows="filterRows" :fields="meta.fields" @apply="commit" />
      </template>

      <div v-if="current === 'list'" :class="['list-layout', { 'with-sidebar': hasSidebar }]">
        <ListSidebar v-if="hasSidebar" :key="tagStamp" :meta="meta" :filters="applied" :tags="hasTags" @pick="pickValue" @pick-tag="pickTag" />
        <div class="desk-card list-main">
        <div v-if="selection.length && selectable" class="selection-bar" role="region" :aria-label="t.list.selectionBar" data-testid="selection-bar">
          <strong>{{ t.list.selected(selection.length) }}</strong>
          <BulkActions :doctype="doctype" :meta="meta" :selection="selection" @done="void afterBulk()" />
          <span class="desk-spacer" />
          <Button :label="t.list.clearSelection" text severity="secondary" size="small" data-testid="clear-selection" @click="selection = []" />
        </div>
        <!-- Premier affichage : des lignes grises à la place d'un cadre vide (on voit venir la forme de la liste). -->
        <div v-if="loading && !rows.length" class="skeleton-rows" role="status" :aria-label="t.loading" data-testid="list-skeleton">
          <Skeleton v-for="n in 6" :key="n" height="1.4rem" :width="`${96 - n * 3}%`" />
        </div>
        <template v-else-if="compact">
          <RecordCards v-if="rows.length" v-model:selection="selection" :doctype="doctype" :meta="meta" :rows="rows" :columns="columns" :selectable="selectable" :cell="cell" :title="title" @open="openRow" />
          <div v-else class="empty-state" data-testid="list-empty">
            <p class="desk-muted">{{ quick || activeFilters || status !== null || tagFilter ? t.list.emptyFiltered : t.list.empty(label) }}</p>
            <Button v-if="trulyEmpty && canCreate" :as="RouterLink" :to="{ name: 'new', params: { doctype } }" :label="t.list.createFirst(label)" icon="pi pi-plus" size="small" data-testid="create-first" />
          </div>
          <Paginator v-if="total > pageSize || first > 0" :rows="pageSize" :first="first" :total-records="total" :rows-per-page-options="[20, 50, 100]" @page="onPage" />
        </template>
        <DataTable
          v-else
          v-model:selection="selection"
          :row-class="() => 'row-open'"
          @row-click="onRowClick"
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
          <Column v-if="selectable" selection-mode="multiple" header-style="width: 2.5rem" />
          <Column v-if="numbered" field="name" sort-field="name" :header="t.list.number" sortable>
            <template #body="{ data }">
              <span class="avatar-cell"><FileThumb v-if="meta.image_field" :id="data.data[meta.image_field]" :size="28" round /><RouterLink :to="{ name: 'form', params: { doctype, name: data.name } }" :data-testid="`open-${data.name}`" style="font-weight: 500; white-space: nowrap">{{ data.name }}</RouterLink><RowTags :tags="rowTags[data.name]" :active="activeTag" @pick="pickTag" /></span>
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
                <RowTags :tags="rowTags[data.name]" :active="activeTag" @pick="pickTag" />
              </span>
              <CellValue v-else :field="f" :value="data.data[f.fieldname]" :text="cell(f, data)" />
            </template>
          </Column>
          <Column
            v-if="meta.submittable || flowStates.length"
            :field="flowStates.length ? 'workflow_state' : 'docstatus'"
            :sort-field="flowStates.length ? 'workflow_state' : 'docstatus'"
            :header="t.list.status"
            sortable
          >
            <template #body="{ data }">
              <span :class="['status-pill', statusTone(data.docstatus)]" :data-testid="`status-${data.name}`" :data-state="data.workflow_state">
                {{ flowStates.length ? data.workflow_state : statusLabel(data.docstatus) }}
              </span>
            </template>
          </Column>
          <Column v-if="!meta.virtual" field="modified" sort-field="modified" :header="t.list.modified" sortable>
            <template #body="{ data }"><span class="desk-muted">{{ relativeTime(data.modified) }}</span></template>
          </Column>
          <template #empty>
            <div class="empty-state" data-testid="list-empty">
              <p class="desk-muted">{{ quick || activeFilters || status !== null || tagFilter ? t.list.emptyFiltered : t.list.empty(label) }}</p>
              <Button v-if="trulyEmpty && canCreate" :as="RouterLink" :to="{ name: 'new', params: { doctype } }" :label="t.list.createFirst(label)" icon="pi pi-plus" size="small" data-testid="create-first" />
            </div>
          </template>
        </DataTable>
        </div>
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
