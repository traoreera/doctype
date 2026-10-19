<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import FieldControl from '@/components/FieldControl.vue'
import FileThumb from '@/components/FileThumb.vue'
import CollabPanel from '@/components/collab/CollabPanel.vue'
import HistoryPanel from '@/components/HistoryPanel.vue'
import OverrideEditor from '@/components/OverrideEditor.vue'
import TableField from '@/components/TableField.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { statusLabel, statusTone, transitionsFor, isFrozen } from '@/lib/docstatus'
import { cellKey, childOf, fieldStates, isDirty, mapServerErrors, recordValues, startValues, toPayload, validate, type Row, type Values } from '@/lib/form'
import { formatTimestamp, relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocRecord, DocTypeMeta, FieldMeta, FormOverride, LayoutSection } from '@/lib/types'
import { unitLinks, unitOf } from '@/lib/units'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'
import { PROFILE_DOCTYPE, useProfile } from '@/stores/profile'
import { useRecents } from '@/stores/recents'
import { useUsers } from '@/stores/users'

const props = defineProps<{
  doctype: string
  name: string | null
  /** Dans une autre page (les Paramètres) : pas de flèche de retour, pas de suppression, pas de trace dans les récents, pas de redirection. */
  embedded?: boolean
}>()
const emit = defineEmits<{ dirty: [value: boolean]; saved: [record: DocRecord] }>()

const { api } = useServices()
const metaStore = useMeta()
const router = useRouter()
const route = useRoute()
const confirm = useConfirm()
const notify = useNotify()
const links = useLinks()
const recents = useRecents()
const users = useUsers()
const profile = useProfile()

const meta = shallowRef<DocTypeMeta | null>(null)
const record = shallowRef<DocRecord | null>(null)
const values = ref<Values>({})
/** Valeurs de la dernière version enregistrée : c'est sur elles que le serveur évalue `read_only_depends_on`. */
const saved = ref<Values | null>(null)
const loading = ref(true)
const saving = ref(false)
const loadError = ref<{ title: string; detail: string } | null>(null)
const banner = ref<string | null>(null)
const errors = ref<Record<string, string>>({})
const collapsed = ref<Record<number, boolean>>({})
const formId = `f${Math.random().toString(36).slice(2, 8)}`
/** Éditeur d'habillage : disposition du formulaire, par-dessus le YAML du plugin. */
const editingLayout = ref(false)
const savingLayout = ref(false)
const layoutDraft = ref<FormOverride>({})

const isNew = computed(() => props.name === null)
const label = computed(() => meta.value?.label ?? props.doctype)
const states = computed(() => (meta.value ? fieldStates(meta.value, values.value, saved.value) : {}))
const original = computed<Values>(() => saved.value ?? (meta.value ? startValues(meta.value, route.query) : {}))
const dirty = computed(() => !!meta.value && (isNew.value ? true : isDirty(meta.value, values.value, original.value)))
/** Un nouveau document n'est « modifié » qu'une fois qu'on a touché à quelque chose. */
const touched = computed(() => !!meta.value && isDirty(meta.value, values.value, original.value))
watch(touched, (v) => emit('dirty', v))
/** Un document soumis ou annulé est figé : ni saisie, ni enregistrement, ni suppression (le serveur le refuse aussi). */
const frozen = computed(() => isFrozen(record.value))
const writable = computed(() => !frozen.value && (isNew.value ? meta.value?.rights.create : meta.value?.rights.write) !== 'none')
const canDelete = computed(() => !isNew.value && !frozen.value && meta.value?.rights.delete !== 'none')
const transitions = computed(() => transitionsFor(meta.value, record.value))
const submittable = computed(() => !!meta.value?.submittable && !isNew.value)

const heading = computed(() => {
  if (isNew.value) return t.form.newTitle(label.value)
  if (meta.value?.single) return label.value // un réglage s'intitule comme son type, pas par un identifiant
  const tf = meta.value?.title_field
  const v = tf ? saved.value?.[tf] : undefined
  return typeof v === 'string' && v ? v : (record.value?.name ?? '')
})

/** Les unités (« kg ») viennent des documents que les champs Link désignent : on les lit dès que ces liens changent. */
watch(values, (v) => {
  if (!meta.value) return
  for (const [target, names] of unitLinks(meta.value.fields, [v])) void links.resolve(target, names, { data: true })
})
const unitFor = (f: FieldMeta) => (meta.value ? unitOf(f, meta.value.fields, values.value, links.field) : null)

function init(m: DocTypeMeta, rec: DocRecord | null): void {
  if (m.single && !props.embedded) {
    // Un réglage vit dans la page Paramètres, avec tous les autres : on n'a pas deux endroits où le trouver.
    void router.replace(m.settings_group ? { name: 'settings' } : { name: 'settings', params: { doctype: m.name } })
  }
  meta.value = m
  record.value = rec
  const base = rec ? recordValues(m, rec) : startValues(m, route.query)
  values.value = { ...base }
  saved.value = rec ? { ...base } : null
  if (rec) {
    // L'accueil se souvient de ce qu'on a ouvert (le titre que l'utilisateur reconnaît, pas l'identifiant).
    const tf = m.title_field
    const title = tf && typeof rec.data[tf] === 'string' ? (rec.data[tf] as string) : ''
    if (!props.embedded) recents.touch(m.name, rec.name, title)
  }
  errors.value = {}
  banner.value = null
  collapsed.value = Object.fromEntries(m.layout.map((s, i) => [i, s.collapsible && s.collapsed]))
}

// Ce composant sert TOUS les documents (la route change, l'instance reste) : une réponse qui arrive après qu'on a changé de
// page ne doit jamais toucher la page où l'on est. Chaque chargement porte un numéro ; seul le dernier a le droit d'écrire.
let loadTicket = 0

async function load(): Promise<void> {
  const mine = ++loadTicket
  const { doctype, name } = props
  loading.value = true
  loadError.value = null
  try {
    const m = await metaStore.get(doctype)
    if (mine !== loadTicket) return
    if (name === null) return init(m, null)
    // Après une création, la route change vers le document qu'on vient d'enregistrer : inutile de le relire.
    if (record.value?.name === name && meta.value?.name === m.name) return
    const rec = await api.get(doctype, name)
    if (mine !== loadTicket) return
    init(m, rec)
  } catch (e) {
    if (mine !== loadTicket) return
    meta.value = null
    record.value = null
    if (e instanceof ApiError && e.status === 404) loadError.value = { title: t.form.notFound, detail: t.form.notFoundDetail }
    else loadError.value = { title: t.errors.generic, detail: describeError(e) }
  } finally {
    if (mine === loadTicket) loading.value = false
  }
}
watch(
  () => [props.doctype, props.name] as const,
  () => {
    saving.value = false // un enregistrement encore en vol appartient à la page qu'on quitte, pas à celle-ci
    return load()
  },
  { immediate: true },
)

// ── Sections ───────────────────────────────────────────────
function visibleColumns(section: LayoutSection): string[][] {
  return section.columns.map((c) => c.filter((n) => states.value[n]?.visible)).filter((c) => c.length)
}
const sections = computed(() =>
  (meta.value?.layout ?? [])
    .map((s, index) => ({ s, index, cols: visibleColumns(s) }))
    // Une section dont tous les champs sont masqués par leurs conditions disparaît (comme dans Frappe).
    .filter((x) => x.cols.length),
)
const fieldOf = (name: string) => meta.value?.fields.find((f) => f.fieldname === name)

function edit(name: string, value: unknown): void {
  values.value = { ...values.value, [name]: value }
  // L'erreur d'un champ disparaît dès qu'on le corrige ; pour un champ Table, celles de ses cellules (`items.0.qty`) aussi.
  const prefix = `${name}.`
  const stale = Object.keys(errors.value).filter((k) => k === name || k.startsWith(prefix))
  if (stale.length) errors.value = Object.fromEntries(Object.entries(errors.value).filter(([k]) => !stale.includes(k)))
}

/** Une cellule d'un champ Table : on remplace la ligne, et seule l'erreur de cette cellule disparaît. */
function editCell(table: string, i: number, col: string, value: unknown): void {
  const rows = (values.value[table] as Row[]).map((r, j) => (j === i ? { ...r, [col]: value } : r))
  values.value = { ...values.value, [table]: rows }
  const key = cellKey(table, i, col)
  if (errors.value[key]) errors.value = Object.fromEntries(Object.entries(errors.value).filter(([k]) => k !== key))
}

// ── Enregistrement ─────────────────────────────────────────
/** Mon profil vient d'être enregistré : le nom et la photo de la barre du haut le suivent sans recharger la page. */
function refreshProfile(doctype: string): void {
  if (doctype === PROFILE_DOCTYPE) void profile.load(true)
}

async function save(): Promise<void> {
  const m = meta.value
  if (!m || saving.value || !writable.value) return
  banner.value = null
  const found = validate(m, values.value, states.value)
  if (Object.keys(found).length) {
    errors.value = found
    banner.value = t.form.fixErrors
    return
  }
  errors.value = {}
  saving.value = true
  // La cible est figée AVANT d'attendre : si l'utilisateur change de page pendant l'envoi, la réponse n'a plus rien à faire ici.
  const target = { doctype: props.doctype, name: props.name }
  const current = record.value
  const here = () => props.doctype === target.doctype && props.name === target.name
  try {
    const data = toPayload(m, values.value)
    if (target.name === null) {
      const rec = await api.create(target.doctype, data)
      notify.success(t.form.createdToast)
      refreshProfile(target.doctype)
      if (!here()) return // enregistré, mais l'utilisateur est déjà ailleurs : on ne l'y suit pas
      init(m, rec)
      await router.replace({ name: 'form', params: { doctype: target.doctype, name: rec.name } })
    } else {
      const rec = await api.update(target.doctype, current!.name, current!.version, data)
      notify.success(t.form.savedToast)
      refreshProfile(target.doctype)
      if (here()) init(m, rec)
      emit('saved', rec)
    }
  } catch (e) {
    if (!here()) return void notify.error(e)
    if (e instanceof ApiError && e.isConflict) return askReload()
    if (e instanceof ApiError && (e.status === 422 || e.status === 413 || e.code.startsWith('hook_'))) {
      const mapped = mapServerErrors(e, m)
      errors.value = mapped.fields
      banner.value = mapped.general
    } else notify.error(e)
  } finally {
    if (here()) saving.value = false
  }
}

function askReload(stale = false): void {
  confirm.require({
    header: stale ? t.form.staleTitle : t.form.conflictTitle,
    message: stale ? t.form.stale : t.form.conflict,
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.reload,
    rejectLabel: t.cancel,
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      record.value = null // force la relecture
      await load()
    },
  })
}

// ── Soumission, annulation, amendement ──────────────────────
/** Les transitions passent par le serveur seul : lui seul sait si le stock le permet, si le document est encore à jour. */
async function transition(kind: 'submit' | 'cancel'): Promise<void> {
  const m = meta.value
  const current = record.value
  if (!m || !current || saving.value) return
  banner.value = null
  errors.value = {}
  saving.value = true
  const target = { doctype: props.doctype, name: props.name }
  const here = () => props.doctype === target.doctype && props.name === target.name
  try {
    const next = kind === 'submit' ? await api.submit(target.doctype, current.name, current.version) : await api.cancel(target.doctype, current.name, current.version)
    notify.success(kind === 'submit' ? t.form.submittedToast : t.form.cancelledToast)
    if (here()) init(m, next)
  } catch (e) {
    if (!here()) return void notify.error(e)
    if (e instanceof ApiError && (e.isConflict || e.isStale)) return askReload(e.isStale)
    if (e instanceof ApiError && (e.status === 422 || e.code.startsWith('hook_'))) {
      // Un refus du métier (stock insuffisant…) : son message est écrit pour l'utilisateur, sa ligne en cause est signalée.
      const mapped = mapServerErrors(e, m)
      errors.value = mapped.fields
      banner.value = mapped.general
    } else notify.error(e)
  } finally {
    if (here()) saving.value = false
  }
}

function confirmTransition(kind: 'submit' | 'cancel'): void {
  confirm.require({
    header: kind === 'submit' ? t.form.submit : t.form.cancelDocument,
    message: kind === 'submit' ? t.form.confirmSubmit : t.form.confirmCancel,
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: kind === 'submit' ? t.form.submit : t.form.cancelDocument,
    rejectLabel: t.cancel,
    acceptProps: { severity: kind === 'cancel' ? 'danger' : 'primary' },
    rejectProps: { severity: 'secondary', text: true },
    accept: () => void transition(kind),
  })
}

async function amend(): Promise<void> {
  const current = record.value
  if (!current || saving.value) return
  banner.value = null
  saving.value = true
  const target = { doctype: props.doctype, name: props.name }
  const here = () => props.doctype === target.doctype && props.name === target.name
  try {
    const copy = await api.amend(target.doctype, current.name)
    notify.success(t.form.amendedToast)
    if (here()) await router.push({ name: 'form', params: { doctype: target.doctype, name: copy.name } })
  } catch (e) {
    if (!here()) return void notify.error(e)
    if (e instanceof ApiError && e.isStale) return askReload(true)
    notify.error(e)
  } finally {
    if (here()) saving.value = false
  }
}

function remove(): void {
  confirm.require({
    header: t.delete,
    message: t.form.confirmDelete,
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.delete,
    rejectLabel: t.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      const target = { doctype: props.doctype, name: props.name }
      try {
        await api.remove(target.doctype, record.value!.name)
      } catch (e) {
        notify.error(e)
        return
      }
      notify.success(t.form.deletedToast)
      if (props.doctype !== target.doctype || props.name !== target.name) return // déjà ailleurs
      init(meta.value!, record.value) // plus rien à perdre : la garde de sortie ne doit pas se déclencher
      saved.value = { ...values.value }
      await router.replace({ name: 'list', params: { doctype: target.doctype } })
    },
  })
}

// ── Habillage du formulaire ────────────────────────────────
async function openLayout(): Promise<void> {
  if (!meta.value) return
  let config: FormOverride = { layout: meta.value.layout }
  try {
    const current = await api.formOverride(props.doctype)
    config = { ...current.config, layout: current.config.layout ?? meta.value.layout }
  } catch {
    // Un habillage illisible ne doit pas empêcher d'en écrire un : on part de la disposition affichée.
  }
  layoutDraft.value = config
  editingLayout.value = true
}

/** Après un habillage, la disposition a changé côté serveur : on relit la définition SANS toucher à la saisie en cours. */
async function reloadMeta(): Promise<void> {
  const m = await metaStore.refresh(props.doctype)
  meta.value = m
  collapsed.value = Object.fromEntries(m.layout.map((s, i) => [i, s.collapsible && s.collapsed]))
}

async function saveLayout(config: unknown): Promise<void> {
  savingLayout.value = true
  try {
    await api.saveFormOverride(props.doctype, config as FormOverride)
    await reloadMeta()
    editingLayout.value = false
    notify.success(t.override.savedToast)
  } catch (e) {
    notify.error(e)
  } finally {
    savingLayout.value = false
  }
}

async function resetLayout(): Promise<void> {
  savingLayout.value = true
  try {
    await api.removeFormOverride(props.doctype)
    await reloadMeta()
    editingLayout.value = false
    notify.success(t.override.resetToast)
  } catch (e) {
    notify.error(e)
  } finally {
    savingLayout.value = false
  }
}

// ── Garde des modifications ────────────────────────────────
onBeforeRouteLeave(() => {
  if (!touched.value || saving.value) return true
  return new Promise<boolean>((resolve) => {
    confirm.require({
      header: t.form.unsavedTitle,
      message: t.form.unsaved,
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: t.form.leave,
      rejectLabel: t.form.stay,
      rejectProps: { severity: 'secondary', text: true },
      accept: () => resolve(true),
      reject: () => resolve(false),
      onHide: () => resolve(false),
    })
  })
})

function beforeUnload(e: BeforeUnloadEvent): void {
  if (touched.value) e.preventDefault()
}
function onKey(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    void save()
  }
}
onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload)
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div :class="embedded ? 'form-embedded' : 'desk-page'">
    <div v-if="loading && !meta" class="desk-muted" data-testid="form-loading">{{ t.loading }}</div>

    <div v-else-if="loadError" class="desk-card" style="padding: 2rem; text-align: center" data-testid="form-error">
      <h1>{{ loadError.title }}</h1>
      <p class="desk-muted">{{ loadError.detail }}</p>
      <RouterLink v-if="!embedded" :to="{ name: 'list', params: { doctype } }"><Button :label="label" severity="secondary" /></RouterLink>
    </div>

    <template v-else-if="meta">
      <div class="desk-page-head">
        <Button v-if="!embedded" icon="pi pi-arrow-left" text severity="secondary" size="small" :aria-label="label" @click="router.push({ name: 'list', params: { doctype } })" />
        <FileThumb v-if="meta?.image_field && saved?.[meta.image_field]" :id="saved[meta.image_field]" :size="44" round :alt="heading" />
        <h1 data-testid="form-title">{{ heading }}</h1>
        <span v-if="isNew || touched" class="status-pill warn" data-testid="status">{{ t.form.notSaved }}</span>
        <span v-else-if="submittable && record" :class="['status-pill', statusTone(record.docstatus)]" data-testid="status" :data-docstatus="record.docstatus">
          {{ statusLabel(record.docstatus) }}
        </span>
        <span v-else-if="!writable" class="status-pill" data-testid="status">{{ t.rights.readOnlyBadge }}</span>
        <span v-else class="status-pill ok" data-testid="status">{{ t.form.saved }}</span>
        <span class="desk-spacer" />
        <Button
          v-if="writable"
          :label="t.override.edit"
          icon="pi pi-table"
          severity="secondary"
          text
          size="small"
          data-testid="form-layout-edit"
          @click="openLayout"
        />
        <Button
          v-if="transitions.submit"
          :label="t.form.submit"
          icon="pi pi-send"
          size="small"
          :loading="saving"
          :disabled="touched"
          :title="touched ? t.form.saveFirst : undefined"
          data-testid="submit"
          @click="confirmTransition('submit')"
        />
        <Button v-if="transitions.cancel" :label="t.form.cancelDocument" icon="pi pi-ban" severity="danger" outlined size="small" :loading="saving" data-testid="cancel-doc" @click="confirmTransition('cancel')" />
        <Button v-if="transitions.amend" :label="t.form.amend" icon="pi pi-copy" size="small" :loading="saving" data-testid="amend" @click="amend" />
        <Button v-if="canDelete && !embedded" :label="t.delete" icon="pi pi-trash" severity="danger" text size="small" data-testid="delete" @click="remove" />
        <Button v-if="writable" :label="t.save" icon="pi pi-check" size="small" :loading="saving" :disabled="!isNew && !touched" data-testid="save" @click="save" />      </div>

      <Message v-if="banner" severity="error" class="banner-error" data-testid="form-banner">{{ banner }}</Message>
      <Message v-if="frozen && submittable" severity="info" class="banner-error" data-testid="form-frozen">{{ t.form.frozen }}</Message>

      <form @submit.prevent="save">
        <section v-for="x in sections" :key="x.index" class="desk-card form-section" :data-section="x.s.section ?? ''">
          <header
            v-if="x.s.section"
            :class="{ collapsible: x.s.collapsible }"
            :tabindex="x.s.collapsible ? 0 : undefined"
            :role="x.s.collapsible ? 'button' : undefined"
            :aria-expanded="x.s.collapsible ? !collapsed[x.index] : undefined"
            @click="x.s.collapsible && (collapsed[x.index] = !collapsed[x.index])"
            @keydown.enter="x.s.collapsible && (collapsed[x.index] = !collapsed[x.index])"
          >
            <i v-if="x.s.collapsible" :class="['pi', collapsed[x.index] ? 'pi-chevron-right' : 'pi-chevron-down']" />
            {{ x.s.section }}
          </header>
          <div v-show="!collapsed[x.index]" class="form-columns" :style="{ '--cols': x.cols.length }">
            <div v-for="(col, i) in x.cols" :key="i">
              <template v-for="n in col" :key="n">
                <TableField
                  v-if="fieldOf(n)?.fieldtype === 'Table' && states[n] && childOf(meta, fieldOf(n)!)"
                  :field="fieldOf(n)!"
                  :child="childOf(meta, fieldOf(n)!)!"
                  :model-value="(values[n] as Row[])"
                  :required="states[n]!.required"
                  :read-only="!writable || states[n]!.readOnly"
                  :errors="errors"
                  :parent-values="values"
                  :form-id="formId"
                  @update:model-value="edit(n, $event)"
                  @cell="(i, col, v) => editCell(n, i, col, v)"
                />
                <FieldControl
                  v-else-if="fieldOf(n) && fieldOf(n)!.fieldtype !== 'Table' && states[n]"
                  :field="fieldOf(n)!"
                  :model-value="values[n]"
                  :required="states[n]!.required"
                  :read-only="!writable || states[n]!.readOnly"
                  :error="errors[n]"
                  :unit="unitFor(fieldOf(n)!)"
                  :form-id="formId"
                  :doctype="doctype"
                  @update:model-value="edit(n, $event)"
                />
              </template>
            </div>
          </div>
        </section>
        <button type="submit" hidden tabindex="-1" aria-hidden="true" />
      </form>

      <p v-if="record && record.version === 0" class="desk-muted" data-testid="single-unsaved">{{ t.settings.notSaved }}</p>
      <!-- Un document ordinaire : son activité (commentaires + modifications), ses assignations et ses pièces jointes. Un réglage (document unique) garde l'historique seul. -->
      <CollabPanel v-else-if="record && record.doctype === doctype && !meta.single" :meta="meta" :doctype="doctype" :name="record.name" :version="record.version" />
      <HistoryPanel v-else-if="record && meta.track_changes !== false" :meta="meta" :doctype="doctype" :name="record.name" :version="record.version" />

      <OverrideEditor
        v-model:visible="editingLayout"
        :title="t.override.formTitle"
        :initial="layoutDraft"
        :saving="savingLayout"
        @save="saveLayout"
        @reset="resetLayout"
      />

      <p v-if="record" class="desk-muted" style="font-size: 0.8rem" data-testid="form-footer">
        <template v-if="record.amended_from">
          {{ t.form.amendedFrom }}
          <RouterLink :to="{ name: 'form', params: { doctype, name: record.amended_from } }" data-testid="amended-from">{{ record.amended_from }}</RouterLink> ·
        </template>
        {{ t.form.createdBy(users.label(record.owner)) }} ·
        <span :title="formatTimestamp(record.modified)">{{ t.form.modifiedAt(relativeTime(record.modified), record.version) }}</span>
      </p>
    </template>
  </div>
</template>
