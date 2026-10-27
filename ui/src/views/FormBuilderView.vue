<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'
import FieldCard from '@/components/formbuilder/FieldCard.vue'
import FieldPanel from '@/components/formbuilder/FieldPanel.vue'
import FormCanvas, { type Selection } from '@/components/formbuilder/FormCanvas.vue'
import SectionPanel from '@/components/formbuilder/SectionPanel.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { DRAG_FIELD, boardProblems, fromLayout, locate, moveField, nudge, placed, toLayout, unplace, type Board, type Nudge } from '@/lib/formbuilder'
import { ADDED_TYPES, addedFields, customName, propRows, withAddedField, withoutAddedField, type AddedField } from '@/lib/overrideprops'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, FieldBase, FieldMeta, FormOverride } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * « Personnaliser le formulaire » : le Form Builder, à la manière de Frappe. Le formulaire tel qu'on le verra — onglets, sections, colonnes,
 * champs en cartes — qu'on réarrange en glissant (ou au clavier, depuis le panneau), avec à gauche les champs non placés et l'ajout d'un champ,
 * à droite les propriétés de ce qu'on a choisi.
 *
 * Tout ce qu'on fait ici s'écrit dans l'HABILLAGE de formulaire de l'organisation (`PUT /override/form`) — la disposition (`layout`), les
 * retouches de champ (`fields`) et les champs ajoutés (`added`) — exactement ce que le serveur validait déjà ; la page n'invente aucune
 * règle (le modèle pur est `lib/formbuilder`, les propriétés `lib/overrideprops`). L'application (le YAML) reste intacte : « Revenir à
 * l'application » retire l'habillage.
 *
 * Droits : enregistrer exige d'ÉCRIRE le type ; les règles d'écriture (obligatoire, lecture seule, défaut) et l'ajout de champs exigent
 * d'écrire TOUS ses documents (`rights.write === 'all'`). Un lecteur voit la disposition sans pouvoir la changer.
 */
const props = defineProps<{ doctype: string }>()
const { api } = useServices()
const notify = useNotify()
const metaStore = useMeta()

const meta = ref<DocTypeMeta | null>(null)
const base = ref<Record<string, FieldBase> | null>(null)
const appLabels = ref<Record<string, string>>({})
const config = ref<FormOverride>({})
const board = ref<Board>(fromLayout([]))
const initial = ref('')
const loading = ref(true)
const failure = ref('')
const problem = ref<string | null>(null)
const saving = ref(false)
const activeTab = ref(0)
const selected = ref<Selection>(null)

const label = computed(() => meta.value?.label || props.doctype)
const canEdit = computed(() => !!meta.value && meta.value.rights.write !== 'none')
const rules = computed(() => meta.value?.rights.write === 'all')

// ── Les champs : ceux du type (de `/meta`, sans les ajoutés) et ceux que l'organisation ajoute (de l'habillage en cours) ──
const added = computed<AddedField[]>(() => addedFields(config.value))
const typeFields = computed<FieldMeta[]>(() => (meta.value?.fields ?? []).filter((f) => !f.fieldname.startsWith('custom_')))
const rows = computed(() => propRows(typeFields.value, config.value, { definition: base.value, naming: namingField.value, rules: rules.value }))
const rowOf = (name: string) => rows.value.find((r) => r.field.fieldname === name) ?? null
const addedOf = (name: string) => added.value.find((a) => a.fieldname === name) ?? null
const namingField = computed(() => {
  const n = meta.value?.naming
  return n && typeof n === 'object' && 'field' in n ? n.field : null
})

function display(name: string): { label: string; fieldtype: string; mandatory: boolean; readOnly: boolean; hidden: boolean; custom: boolean } {
  const a = addedOf(name)
  if (a) return { label: a.label || a.fieldname, fieldtype: a.fieldtype, mandatory: !!a.required, readOnly: false, hidden: false, custom: true }
  const r = rowOf(name)
  const tw = config.value.fields?.[name]
  return {
    label: tw?.label || appLabels.value[name] || r?.field.label || name,
    fieldtype: r?.field.fieldtype ?? '',
    mandatory: !!r?.mandatory,
    readOnly: !!r?.readOnly,
    hidden: !!r?.hidden,
    custom: false,
  }
}

/** La palette : ce qui peut se placer et ne l'est pas (un champ que l'APPLICATION masque ne se place jamais). */
const unplaced = computed<string[]>(() => {
  const here = new Set(placed(board.value))
  const all = [...rows.value.filter((r) => !r.base.hidden).map((r) => r.field.fieldname), ...added.value.map((a) => a.fieldname)]
  return all.filter((n) => !here.has(n))
})

// ── L'habillage à envoyer, et ce qui a changé ──
function payload(): FormOverride {
  const out: FormOverride = { layout: toLayout(board.value) }
  if (config.value.fields && Object.keys(config.value.fields).length) out.fields = config.value.fields
  if (added.value.length) (out as { added?: AddedField[] }).added = added.value
  return out
}
const dirty = computed(() => !!initial.value && JSON.stringify(payload()) !== initial.value)
const problems = computed(() => boardProblems(board.value))

async function load(): Promise<void> {
  loading.value = true
  failure.value = ''
  try {
    meta.value = await metaStore.refresh(props.doctype) // l'état ACTUEL (un autre onglet a pu personnaliser entre-temps)
    const current = await api.formOverride(props.doctype)
    base.value = current.definition ?? null
    appLabels.value = current.labels ?? {}
    config.value = { ...(current.config ?? {}) }
    board.value = fromLayout(current.config?.layout ?? meta.value.layout)
    activeTab.value = 0
    selected.value = null
    initial.value = JSON.stringify(payload())
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  problem.value = null
  try {
    const body = payload()
    await api.saveFormOverride(props.doctype, body)
    initial.value = JSON.stringify(body)
    meta.value = await metaStore.refresh(props.doctype) // la fiche et la liste lisent la nouvelle disposition
    notify.success(t.override.savedToast)
  } catch (e) {
    problem.value = describeError(e) // le refus du serveur reste sous les yeux, rien n'est perdu
  } finally {
    saving.value = false
  }
}

async function reset(): Promise<void> {
  saving.value = true
  problem.value = null
  try {
    await api.removeFormOverride(props.doctype)
    await load()
    notify.success(t.override.resetToast)
  } catch (e) {
    problem.value = describeError(e)
  } finally {
    saving.value = false
  }
}

// ── Choisir, placer (au clavier comme à la souris) ──
const chosenField = computed(() => (selected.value?.kind === 'field' ? selected.value.name : null))
function chooseField(name: string): void {
  selected.value = { kind: 'field', name }
}
/** Où placer un champ non placé : la section choisie, sinon la dernière de l'onglet ouvert. */
function where(): { tab: number; section: number } {
  const s = selected.value
  if (s?.kind === 'section') return { tab: s.tab, section: s.section }
  return { tab: activeTab.value, section: Math.max(0, (board.value.tabs[activeTab.value]?.sections.length ?? 1) - 1) }
}
function place(name: string): void {
  const at = where()
  board.value = moveField(board.value, name, { ...at, column: 0, index: Infinity })
  selected.value = { kind: 'field', name }
}
function onNudge(how: Nudge): void {
  if (!chosenField.value) return
  board.value = nudge(board.value, chosenField.value, how)
  const at = locate(board.value, chosenField.value)
  if (at) activeTab.value = at.tab
}
function onUnplace(): void {
  if (chosenField.value) board.value = unplace(board.value, chosenField.value)
}
function removeAdded(): void {
  const name = chosenField.value
  if (!name) return
  board.value = unplace(board.value, name)
  config.value = withoutAddedField(config.value, name)
  selected.value = null
}

// La palette reçoit une carte qu'on y dépose : le champ quitte le formulaire (le canevas, lui, gère ses propres dépôts).
const overPalette = ref(false)
const dragsField = (e: DragEvent): boolean => canEdit.value && !!e.dataTransfer?.types.includes(DRAG_FIELD)
function dropOnPalette(e: DragEvent): void {
  overPalette.value = false
  const name = e.dataTransfer?.getData(DRAG_FIELD)
  if (!name) return
  e.preventDefault()
  board.value = unplace(board.value, name)
}

// ── Ajouter un champ (l'organisation, §3.5) ──
const draft = ref({ label: '', fieldtype: 'Text', options: '' })
const draftName = computed(() => customName(draft.value.label))
const optionList = computed(() => draft.value.options.split('\n').map((v) => v.trim()).filter(Boolean))
const canAdd = computed(
  () => rules.value && !!draftName.value && !added.value.some((a) => a.fieldname === draftName.value) && (draft.value.fieldtype !== 'Select' || optionList.value.length > 0),
)
function addField(): void {
  if (!canAdd.value) return
  const name = draftName.value
  config.value = withAddedField(config.value, { fieldname: name, fieldtype: draft.value.fieldtype, label: draft.value.label, ...(draft.value.fieldtype === 'Select' ? { options: optionList.value } : {}) })
  place(name)
  draft.value = { label: '', fieldtype: 'Text', options: '' }
}

onBeforeRouteLeave(() => !dirty.value || window.confirm(t.workflowBuilder.leave))
watch(() => props.doctype, () => void load())
onMounted(load)
</script>

<template>
  <div class="desk-page fb-page">
    <div class="desk-page-head">
      <h1 data-testid="fb-title">{{ t.formBuilder.title(label) }}</h1>
      <span class="desk-spacer" />
      <Button :as="RouterLink" :to="{ name: 'list', params: { doctype } }" :label="t.workflowBuilder.toList" icon="pi pi-list" text severity="secondary" size="small" />
      <Button v-if="meta?.workflow" :as="RouterLink" :to="{ name: 'workflow', params: { doctype } }" :label="t.workflowBuilder.menu" icon="pi pi-sitemap" text severity="secondary" size="small" />
      <template v-if="canEdit">
        <Button :label="t.formBuilder.reset" severity="secondary" outlined size="small" :disabled="saving" data-testid="fb-reset" @click="reset" />
        <Button :label="t.save" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty || problems.length > 0" data-testid="fb-save" @click="save" />
      </template>
    </div>

    <Message v-if="failure" severity="error" class="banner-error" data-testid="fb-error">{{ failure }}</Message>
    <template v-else-if="meta && !loading">
      <p class="desk-muted wf-hint">{{ t.formBuilder.hint }}</p>
      <Message v-if="!canEdit" severity="info" class="banner-error" data-testid="fb-readonly">{{ t.formBuilder.readOnly }}</Message>
      <Message v-if="problem" severity="error" class="banner-error" data-testid="fb-problem">{{ problem }}</Message>
      <Message v-for="p in problems" :key="p" severity="warn" class="banner-error" data-testid="fb-board-problem">{{ p }}</Message>

      <div class="fb-body">
        <!-- Palette : les champs non placés, et l'ajout d'un champ. Y déposer une carte la retire du formulaire. -->
        <aside
          class="fb-palette"
          :class="{ 'fb-drop': overPalette }"
          :aria-label="t.formBuilder.palette"
          data-testid="fb-palette"
          @dragover="dragsField($event) && ($event.preventDefault(), (overPalette = true))"
          @dragleave="overPalette = false"
          @drop="dropOnPalette"
        >
          <h2 class="fb-side-title">{{ t.formBuilder.unplacedTitle }}</h2>
          <p v-if="!unplaced.length" class="desk-muted wf-help">{{ t.formBuilder.allPlaced }}</p>
          <div class="fb-stack">
            <FieldCard
              v-for="name in unplaced"
              :key="name"
              v-bind="display(name)"
              :name="name"
              :selected="chosenField === name"
              :draggable="canEdit"
              @choose="chooseField"
            />
          </div>
          <p v-if="unplaced.length" class="desk-muted wf-help">{{ t.formBuilder.unplacedHint }}</p>

          <template v-if="canEdit">
            <h2 class="fb-side-title">{{ t.formBuilder.addTitle }}</h2>
            <p v-if="!rules" class="desk-muted wf-help">{{ t.addedFields.reserved }}</p>
            <template v-else>
              <label class="wf-field" for="fb-new-label">
                <span>{{ t.addedFields.label }}</span>
                <InputText id="fb-new-label" v-model="draft.label" size="small" data-testid="fb-new-label" @keydown.enter.prevent="addField" />
                <span v-if="draftName" class="desk-muted wf-help">{{ t.addedFields.nameHint(draftName) }}</span>
              </label>
              <label class="wf-field" for="fb-new-type">
                <span>{{ t.addedFields.type }}</span>
                <Select v-model="draft.fieldtype" input-id="fb-new-type" :options="[...ADDED_TYPES]" size="small" data-testid="fb-new-type" />
              </label>
              <label v-if="draft.fieldtype === 'Select'" class="wf-field" for="fb-new-options">
                <span>{{ t.addedFields.options }}</span>
                <textarea id="fb-new-options" v-model="draft.options" class="p-textarea p-component fb-textarea" rows="3" data-testid="fb-new-options" />
              </label>
              <Button :label="t.addedFields.add" icon="pi pi-plus" size="small" outlined :disabled="!canAdd" data-testid="fb-add" @click="addField" />
            </template>
          </template>
        </aside>

        <!-- Le formulaire : onglets, sections, colonnes. -->
        <FormCanvas v-model:board="board" v-model:selected="selected" v-model:active-tab="activeTab" :display="display" :can-edit="canEdit" />

        <!-- Propriétés de ce qu'on a choisi. -->
        <aside class="fb-props" :aria-label="t.workflowBuilder.properties" data-testid="fb-panel">
          <FieldPanel
            v-if="chosenField && (rowOf(chosenField) || addedOf(chosenField))"
            v-model:config="config"
            :row="rowOf(chosenField)"
            :added="addedOf(chosenField)"
            :app-label="appLabels[chosenField] ?? null"
            :doctype="doctype"
            :can-edit="canEdit"
            :rules="rules"
            :is-placed="!!locate(board, chosenField)"
            @nudge="onNudge"
            @unplace="onUnplace"
            @place="place(chosenField)"
            @remove="removeAdded"
          />
          <SectionPanel
            v-else-if="selected?.kind === 'section'"
            v-model:board="board"
            v-model:selected="selected"
            :tab="selected.tab"
            :section="selected.section"
            :can-edit="canEdit"
          />
          <div v-else class="fb-panel-body">
            <h2 class="wf-panel-title">{{ t.formBuilder.overview }}</h2>
            <p class="desk-muted">{{ t.formBuilder.pick }}</p>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>
