<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import DevFieldPanel from '@/components/formbuilder/DevFieldPanel.vue'
import FieldCard from '@/components/formbuilder/FieldCard.vue'
import FormCanvas, { type CardView, type Selection } from '@/components/formbuilder/FormCanvas.vue'
import SectionPanel from '@/components/formbuilder/SectionPanel.vue'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import {
  FIELD_GROUPS, addField, assemble, boardOf, emptyDefinition, patchField, removeField, renameField, retypeField, yamlFileName, type DevDefinition, type DevField,
} from '@/lib/doctypebuilder'
import { downloadText } from '@/lib/download'
import { DRAG_FIELD, boardProblems, locate, moveField, nudge, placed, unplace, type Board, type Nudge } from '@/lib/formbuilder'
import { useServices } from '@/lib/services'
import type { FieldType } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * Le constructeur de DocTypes du MODE DÉVELOPPEUR (process/programme.md §3.5), à la manière du DocType de Frappe en developer mode : créer un
 * type entier — ou en modifier un existant, à partir de son YAML d'origine — puis le TÉLÉCHARGER en YAML, à poser dans le dossier
 * `doctypes/` d'un plugin. Rien n'est enregistré : un type vit dans un plugin, chargé au démarrage, avec ses grants déclarés à auth.
 *
 * Même canevas que « Personnaliser » (`FormCanvas`), mais les champs ont ici TOUTES leurs propriétés (`DevFieldPanel`) et le type les
 * siennes (nom, module, nommage…). « Vérifier » fait juger la définition par le serveur COMME AU CHARGEMENT (`POST /devtools/check`) :
 * ce qu'il refuse s'affiche ligne par ligne ; ce qu'il accepte donne son YAML et les grants que le plugin devra déclarer. Ce que le
 * constructeur n'édite pas (hooks, permissions, workflow, vues) voyage tel quel.
 *
 * La page n'existe que si l'opérateur a ouvert le mode développeur (`doctype.desk.developer_mode`) ; le serveur répond sinon 404.
 */
const props = defineProps<{ doctype?: string }>()
const { api } = useServices()
const metaStore = useMeta()

const def = ref<DevDefinition>(emptyDefinition())
const board = ref<Board>(boardOf(def.value))
const owner = ref<string | null>(null)
const doctypes = ref<{ name: string; label: string }[]>([])
const selected = ref<Selection | { kind: 'type' }>({ kind: 'type' })
const activeTab = ref(0)
const loading = ref(true)
const failure = ref('')
const checking = ref(false)
const verdict = ref<{ ok: true; yaml: string; grants: string[] } | { ok: false; message: string; lines: string[] } | null>(null)
const initial = ref('')

const assembled = computed(() => assemble(def.value, board.value))
const dirty = computed(() => !!initial.value && JSON.stringify(assembled.value) !== initial.value)
const problems = computed(() => boardProblems(board.value))
const isNew = computed(() => !props.doctype)
const canvasSelected = computed<Selection>({
  get: () => (selected.value?.kind === 'type' ? null : (selected.value as Selection)),
  set: (v) => (selected.value = v ?? { kind: 'type' }),
})
const chosenField = computed<DevField | null>(() => {
  const s = selected.value
  return s?.kind === 'field' ? def.value.fields.find((f) => f.fieldname === s.name) ?? null : null
})

function display(name: string): CardView {
  const f = def.value.fields.find((x) => x.fieldname === name)
  return { label: f?.label || name, fieldtype: f?.fieldtype ?? '?', mandatory: f?.required === true, readOnly: f?.read_only === true, hidden: f?.hidden === true }
}
/** La palette : les champs visibles qui ne sont pas placés (le serveur les mettrait à la fin du premier onglet). */
const unplaced = computed(() => {
  const here = new Set(placed(board.value))
  return def.value.fields.filter((f) => !f.hidden && !here.has(f.fieldname)).map((f) => f.fieldname)
})

async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  verdict.value = null
  try {
    doctypes.value = (await metaStore.loadSummaries()).map((d) => ({ name: d.name, label: d.label && d.label !== d.name ? `${d.label} (${d.name})` : d.name }))
    if (props.doctype) {
      const got = await api.devDefinition(props.doctype)
      def.value = got.definition
      owner.value = got.owner
    } else {
      def.value = emptyDefinition()
      owner.value = null
    }
    board.value = boardOf(def.value)
    selected.value = { kind: 'type' }
    activeTab.value = 0
    initial.value = JSON.stringify(assemble(def.value, board.value))
  } catch (e) {
    failure.value = e instanceof ApiError && e.code === 'developer_mode_off' ? t.devBuilder.off : describeError(e)
  } finally {
    loading.value = false
  }
}

// ── Les champs ──
function newField(type: FieldType): void {
  const got = addField(def.value, type)
  def.value = got.def
  const s = selected.value
  const at = s?.kind === 'section' ? { tab: s.tab, section: s.section } : { tab: activeTab.value, section: Math.max(0, (board.value.tabs[activeTab.value]?.sections.length ?? 1) - 1) }
  board.value = moveField(board.value, got.name, { ...at, column: 0, index: Infinity })
  selected.value = { kind: 'field', name: got.name }
}
function patch(value: Record<string, unknown>): void {
  if (!chosenField.value) return
  const name = chosenField.value.fieldname
  def.value = patchField(def.value, name, value)
  if (value.hidden === true) board.value = unplace(board.value, name) // un champ masqué ne se place pas (le schéma le refuserait)
}
function retype(type: FieldType): void {
  if (chosenField.value) def.value = retypeField(def.value, chosenField.value.fieldname, type)
}
function rename(to: string): void {
  if (!chosenField.value) return
  const got = renameField(def.value, board.value, chosenField.value.fieldname, to)
  if (!got) return
  def.value = got.def
  board.value = got.board
  selected.value = { kind: 'field', name: to }
}
function remove(): void {
  if (!chosenField.value) return
  const got = removeField(def.value, board.value, chosenField.value.fieldname)
  def.value = got.def
  board.value = got.board
  selected.value = { kind: 'type' }
}
function onNudge(how: Nudge): void {
  if (!chosenField.value) return
  board.value = nudge(board.value, chosenField.value.fieldname, how)
  const at = locate(board.value, chosenField.value.fieldname)
  if (at) activeTab.value = at.tab
}
function place(): void {
  if (!chosenField.value) return
  board.value = moveField(board.value, chosenField.value.fieldname, { tab: activeTab.value, section: Math.max(0, (board.value.tabs[activeTab.value]?.sections.length ?? 1) - 1), column: 0, index: Infinity })
}
const overPalette = ref(false)
function dropOnPalette(e: DragEvent): void {
  overPalette.value = false
  const name = e.dataTransfer?.getData(DRAG_FIELD)
  if (!name) return
  e.preventDefault()
  board.value = unplace(board.value, name)
}

// ── Le type ──
type NamingKind = 'hash' | 'owner' | 'prompt' | 'series' | 'field'
const NAMINGS: { value: NamingKind; label: string }[] = [
  { value: 'hash', label: t.devBuilder.naming.hash },
  { value: 'series', label: t.devBuilder.naming.series },
  { value: 'field', label: t.devBuilder.naming.field },
  { value: 'prompt', label: t.devBuilder.naming.prompt },
  { value: 'owner', label: t.devBuilder.naming.owner },
]
const namingKind = computed<NamingKind>(() => {
  const n = def.value.naming
  return !n ? 'hash' : typeof n === 'string' ? n : 'series' in n ? 'series' : 'field'
})
function setNaming(kind: NamingKind): void {
  const next: DevDefinition = { ...def.value }
  if (kind === 'hash') delete next.naming
  else if (kind === 'series') next.naming = { series: `${(def.value.name || 'DOC').slice(0, 3).toUpperCase()}-{YYYY}-{#####}` }
  else if (kind === 'field') next.naming = { field: def.value.fields[0]?.fieldname ?? '' }
  else next.naming = kind
  def.value = next
}
function setType(key: string, value: unknown): void {
  const next: DevDefinition = { ...def.value }
  if (value === '' || value === false || value === null || value === undefined) delete next[key]
  else next[key] = value
  def.value = next
}
const fieldNames = computed(() => def.value.fields.map((f) => f.fieldname))

// ── Juger, télécharger ──
async function check(): Promise<boolean> {
  checking.value = true
  try {
    const got = await api.devCheck(assembled.value)
    verdict.value = { ok: true, yaml: got.yaml, grants: got.grants }
    return true
  } catch (e) {
    const lines = e instanceof ApiError ? e.details.map((d) => (d.field ? `${d.field} : ${d.msg}` : String(d.msg))) : []
    verdict.value = { ok: false, message: describeError(e), lines }
    return false
  } finally {
    checking.value = false
  }
}
async function download(): Promise<void> {
  if (!(await check()) || verdict.value?.ok !== true) return
  downloadText(yamlFileName(def.value), verdict.value.yaml, 'application/yaml;charset=utf-8')
  initial.value = JSON.stringify(assembled.value) // téléchargé : plus rien à perdre en quittant
}
// Le verdict vaut pour la définition jugée : on la change, il ne dit plus rien.
watch(assembled, () => {
  if (verdict.value?.ok) verdict.value = null
}, { deep: true })

onBeforeRouteLeave(() => !dirty.value || window.confirm(t.devBuilder.leave))
watch(() => props.doctype, () => void load())
onMounted(load)
</script>

<template>
  <div class="desk-page fb-page">
    <div class="desk-page-head">
      <h1 data-testid="dev-title">{{ isNew ? t.devBuilder.titleNew : t.devBuilder.title(doctype!) }}</h1>
      <span class="desk-spacer" />
      <template v-if="!failure && !loading">
        <Button :label="t.devBuilder.check" icon="pi pi-check-circle" severity="secondary" outlined size="small" :loading="checking" data-testid="dev-check" @click="check" />
        <Button :label="t.devBuilder.download" icon="pi pi-download" size="small" :disabled="problems.length > 0" data-testid="dev-download" @click="download" />
      </template>
    </div>

    <Message v-if="failure" severity="info" class="banner-error" data-testid="dev-error">{{ failure }}</Message>
    <template v-else-if="!loading">
      <p class="desk-muted wf-hint">{{ t.devBuilder.hint }}</p>
      <Message v-if="owner" severity="secondary" class="banner-error">{{ t.devBuilder.ownedBy(owner) }}</Message>
      <Message v-for="p in problems" :key="p" severity="warn" class="banner-error">{{ p }}</Message>

      <div class="fb-body">
        <aside
          class="fb-palette"
          :class="{ 'fb-drop': overPalette }"
          :aria-label="t.formBuilder.palette"
          data-testid="fb-palette"
          @dragover="$event.dataTransfer?.types.includes(DRAG_FIELD) && ($event.preventDefault(), (overPalette = true))"
          @dragleave="overPalette = false"
          @drop="dropOnPalette"
        >
          <Button :label="t.devBuilder.typeSettings" icon="pi pi-cog" text size="small" class="fb-type-button" :class="{ 'fb-type-active': selected?.kind === 'type' }" data-testid="dev-type-settings" @click="selected = { kind: 'type' }" />
          <h2 class="fb-side-title">{{ t.devBuilder.addField }}</h2>
          <div v-for="g in FIELD_GROUPS" :key="g.label" class="fb-group">
            <span class="fb-group-label">{{ g.label }}</span>
            <div class="fb-type-grid">
              <button v-for="type in g.types" :key="type" type="button" class="fb-type" :data-testid="`dev-add-${type}`" @click="newField(type)">{{ type }}</button>
            </div>
          </div>
          <template v-if="unplaced.length">
            <h2 class="fb-side-title">{{ t.formBuilder.unplacedTitle }}</h2>
            <div class="fb-stack">
              <FieldCard v-for="name in unplaced" :key="name" v-bind="display(name)" :name="name" :selected="selected?.kind === 'field' && selected.name === name" draggable @choose="selected = { kind: 'field', name: $event }" />
            </div>
          </template>
        </aside>

        <FormCanvas v-model:board="board" v-model:selected="canvasSelected" v-model:active-tab="activeTab" :display="display" can-edit />

        <aside class="fb-props" :aria-label="t.workflowBuilder.properties" data-testid="fb-panel">
          <DevFieldPanel
            v-if="chosenField"
            :key="chosenField.fieldname"
            :field="chosenField"
            :siblings="fieldNames.filter((n) => n !== chosenField!.fieldname)"
            :doctypes="doctypes"
            :is-placed="!!locate(board, chosenField.fieldname)"
            @patch="patch"
            @retype="retype"
            @rename="rename"
            @remove="remove"
            @nudge="onNudge"
            @place="place"
            @unplace="board = unplace(board, chosenField!.fieldname)"
          />
          <SectionPanel
            v-else-if="selected?.kind === 'section'"
            v-model:board="board"
            v-model:selected="canvasSelected"
            :tab="selected.tab"
            :section="selected.section"
            can-edit
          />
          <div v-else class="fb-panel-body" data-testid="dev-type-panel">
            <p class="wf-kicker">{{ t.devBuilder.typeKicker }}</p>
            <h2 class="wf-panel-title">{{ def.label || def.name || t.devBuilder.titleNew }}</h2>
            <label class="wf-field" for="dev-type-name">
              <span>{{ t.devBuilder.typeName }}</span>
              <InputText id="dev-type-name" :model-value="def.name" size="small" placeholder="Stock Entry" data-testid="dev-type-name" @update:model-value="setType('name', $event ?? '')" />
              <span class="desk-muted wf-help">{{ t.devBuilder.typeNameHint(yamlFileName(def)) }}</span>
            </label>
            <label class="wf-field" for="dev-type-label">
              <span>{{ t.addedFields.label }}</span>
              <InputText id="dev-type-label" :model-value="def.label ?? ''" size="small" data-testid="dev-type-label" @update:model-value="setType('label', $event ?? '')" />
            </label>
            <label v-if="!def.istable" class="wf-field" for="dev-type-module">
              <span>{{ t.devBuilder.module }}</span>
              <InputText id="dev-type-module" :model-value="def.module ?? ''" size="small" placeholder="stock" data-testid="dev-type-module" @update:model-value="setType('module', $event ?? '')" />
              <span class="desk-muted wf-help">{{ t.devBuilder.moduleHint }}</span>
            </label>
            <label v-if="!def.istable" class="wf-field" for="dev-type-icon">
              <span>{{ t.devBuilder.icon }}</span>
              <InputText id="dev-type-icon" :model-value="def.icon ?? ''" size="small" placeholder="box" @update:model-value="setType('icon', $event ?? '')" />
            </label>
            <template v-if="!def.istable">
              <label class="wf-field" for="dev-naming">
                <span>{{ t.devBuilder.namingLabel }}</span>
                <Select input-id="dev-naming" :model-value="namingKind" :options="NAMINGS" option-label="label" option-value="value" size="small" data-testid="dev-naming" @update:model-value="setNaming($event)" />
              </label>
              <label v-if="namingKind === 'series'" class="wf-field" for="dev-series">
                <span>{{ t.devBuilder.series }}</span>
                <InputText id="dev-series" :model-value="(def.naming as { series: string }).series" size="small" @update:model-value="def = { ...def, naming: { series: $event ?? '' } }" />
              </label>
              <label v-if="namingKind === 'field'" class="wf-field" for="dev-naming-field">
                <span>{{ t.devBuilder.namingField }}</span>
                <Select input-id="dev-naming-field" :model-value="(def.naming as { field: string }).field" :options="fieldNames" size="small" @update:model-value="def = { ...def, naming: { field: $event } }" />
              </label>
              <label class="wf-field" for="dev-title-field">
                <span>{{ t.devBuilder.titleField }}</span>
                <Select input-id="dev-title-field" :model-value="def.title_field ?? null" :options="fieldNames" show-clear size="small" @update:model-value="setType('title_field', $event)" />
              </label>
            </template>
            <h3 class="wf-section">{{ t.devBuilder.behaviour }}</h3>
            <label v-for="flag in (['istable', 'submittable', 'single', 'allow_rename'] as const)" :key="flag" class="wf-check">
              <Checkbox :model-value="def[flag] === true" binary :input-id="`dev-type-${flag}`" :data-testid="`dev-type-${flag}`" @update:model-value="setType(flag, $event === true)" />
              <span>{{ t.devBuilder.flags[flag] }}</span>
            </label>
            <label class="wf-check">
              <Checkbox :model-value="def.track_changes !== false" binary input-id="dev-type-track" @update:model-value="setType('track_changes', $event === true ? undefined : false)" />
              <span>{{ t.devBuilder.flags.track_changes }}</span>
            </label>
          </div>
        </aside>
      </div>

      <!-- Le verdict du serveur : ce qu'il refuse, ligne par ligne ; ou le YAML et les grants à déclarer. -->
      <section v-if="verdict" class="fb-verdict" aria-live="polite" data-testid="dev-verdict">
        <template v-if="verdict.ok">
          <Message severity="success" class="banner-error" data-testid="dev-ok">{{ t.devBuilder.valid(yamlFileName(def)) }}</Message>
          <p class="desk-muted">{{ t.devBuilder.grants }} <code v-for="g in verdict.grants" :key="g" class="fb-grant">{{ g }}</code></p>
          <pre class="fb-yaml" data-testid="dev-yaml">{{ verdict.yaml }}</pre>
        </template>
        <template v-else>
          <Message severity="error" class="banner-error" data-testid="dev-refused">{{ verdict.message }}</Message>
          <ul class="fb-refusals">
            <li v-for="(line, i) in verdict.lines" :key="i">{{ line }}</li>
          </ul>
        </template>
      </section>
    </template>
  </div>
</template>
