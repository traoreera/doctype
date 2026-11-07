<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { computed, onMounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { readCatalog, type NodeFieldType, type PluginNode } from '@/lib/flowcatalog'
import {
  FIELD_TYPES,
  addField,
  emptyNode,
  fieldName,
  nodeProblems,
  patchField,
  removeAt,
  toDraft,
  toPayload,
  type DraftNode,
} from '@/lib/flownodesedit'
import { piIcon } from '@/lib/icons'
import { useServices } from '@/lib/services'

/**
 * « Nœuds personnalisés » : l'organisation décrit elle-même une action IPC qu'aucun plugin n'a décrite — son nom à l'écran, ses
 * champs, ce que son résultat contient — et l'éditeur d'automatisations la propose ensuite dans sa palette, sous « Des applications »,
 * comme un nœud de plugin. Rangé en base pour CETTE organisation (`flow_nodes_service.py`), jamais dans un fichier.
 *
 * Décrire n'accorde rien : xflow appelle l'action au nom de l'utilisateur du flux, et le plugin visé garde ses contrôles (il doit
 * nommer `xflow` dans ses `allowed_callers`). Le serveur réserve l'écriture à qui peut déployer une automatisation ; les autres lisent.
 */
const { api } = useServices()
const notify = useNotify()

const nodes = ref<PluginNode[]>([])
const canEdit = ref(false)
const loading = ref(true)
const failure = ref<string | null>(null)
const saving = ref(false)
const serverError = ref<string | null>(null)
/** Les plugins que le catalogue connaît déjà : proposés à la saisie (rien n'empêche d'en nommer un autre). */
const knownPlugins = ref<string[]>([])

/** Le nœud ouvert : `null` = aucun ; `editingKey` = sa clé enregistrée (absente pour un nouveau). */
const draft = ref<DraftNode | null>(null)
const editingKey = ref<{ plugin: string; action: string } | null>(null)
const saved = ref('')
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value) !== saved.value)

const typeOptions = FIELD_TYPES.map((value) => ({ value, label: t.flowNodes.types[value] }))
const problems = computed(() => (draft.value ? nodeProblems(draft.value, t.flowNodes.problems) : []))
const key = (n: { plugin: string; action: string }) => `${n.plugin}:${n.action}`

async function load(): Promise<void> {
  loading.value = true
  failure.value = null
  try {
    const [mine, catalog] = await Promise.all([api.flowNodes(), api.flowCatalog().catch(() => null)])
    nodes.value = readCatalog({ nodes: mine.nodes }).nodes
    canEdit.value = mine.can_edit === true
    const plugins = new Set(readCatalog(catalog).nodes.map((n) => n.plugin))
    for (const n of nodes.value) plugins.add(n.plugin)
    knownPlugins.value = [...plugins].sort()
  } catch (e) {
    failure.value = e instanceof ApiError ? describeError(e) : t.flowNodes.loadFailed
  } finally {
    loading.value = false
  }
}

function open(node: PluginNode | null): void {
  if (dirty.value && !window.confirm(t.flowNodes.leave)) return
  draft.value = node ? toDraft(node) : emptyNode()
  editingKey.value = node ? { plugin: node.plugin, action: node.action } : null
  saved.value = JSON.stringify(draft.value)
  serverError.value = null
}

function set<K extends keyof DraftNode>(name: K, value: DraftNode[K]): void {
  if (draft.value) draft.value = { ...draft.value, [name]: value }
}

/** Le libellé d'un champ nomme aussi sa clé, tant qu'on n'a pas choisi la clé soi-même. */
function setFieldLabel(index: number, label: string): void {
  if (!draft.value) return
  const field = draft.value.fields[index]!
  const follows = !field.name || field.name === fieldName(field.label) || field.name === 'champ'
  draft.value = patchField(draft.value, index, follows ? { label, name: fieldName(label) || field.name } : { label })
}

async function save(): Promise<void> {
  if (!draft.value || problems.value.length) return
  saving.value = true
  serverError.value = null
  const payload = toPayload(draft.value)
  try {
    const old = editingKey.value
    const replacing = old && key(old) !== key(payload as { plugin: string; action: string }) ? old : undefined
    await api.saveFlowNode(payload, replacing)
    notify.success(t.flowNodes.saved(draft.value.label))
    editingKey.value = { plugin: String(payload.plugin), action: String(payload.action) }
    saved.value = JSON.stringify(draft.value)
    await load()
  } catch (e) {
    serverError.value = describeError(e)
  } finally {
    saving.value = false
  }
}

async function remove(): Promise<void> {
  const old = editingKey.value
  if (!old || !draft.value || !window.confirm(t.flowNodes.removeConfirm(draft.value.label))) return
  try {
    await api.deleteFlowNode(old.plugin, old.action)
    notify.success(t.flowNodes.removed(draft.value.label))
    draft.value = null
    editingKey.value = null
    saved.value = ''
    await load()
  } catch (e) {
    serverError.value = describeError(e)
  }
}

onMounted(load)
onBeforeRouteLeave(() => !dirty.value || window.confirm(t.flowNodes.leave))
</script>

<template>
  <div class="desk-page flow-nodes" data-testid="flow-nodes">
    <div class="desk-page-head">
      <h1>{{ t.flowNodes.title }}</h1>
      <span class="desk-spacer" />
      <Button :as="RouterLink" :to="{ name: 'automations' }" :label="t.flowNodes.back" icon="pi pi-list" text severity="secondary" size="small" data-testid="fn-back" />
      <Button v-if="canEdit" :label="t.flowNodes.create" icon="pi pi-plus" size="small" data-testid="fn-create" @click="open(null)" />
    </div>
    <p class="desk-muted">{{ t.flowNodes.intro }}</p>

    <Message v-if="failure" severity="error" :closable="false" data-testid="fn-failure">{{ failure }}</Message>
    <Message v-else-if="!loading && !canEdit" severity="secondary" :closable="false" data-testid="fn-reserved">{{ t.flowNodes.reserved }}</Message>

    <div v-if="!loading && !failure" class="fn-body">
      <!-- Les nœuds de l'organisation : un bouton chacun, dessiné comme dans la palette de l'éditeur. -->
      <section class="fn-list" :aria-label="t.flowNodes.listTitle" data-testid="fn-list">
        <h2 class="fn-list-title">{{ t.flowNodes.listTitle }}</h2>
        <p v-if="!nodes.length" class="desk-muted" data-testid="fn-empty">{{ t.flowNodes.empty }}</p>
        <button
          v-for="n in nodes"
          :key="key(n)"
          type="button"
          class="fl-chip fl-kind-action"
          :class="{ 'fn-chip-open': editingKey && key(editingKey) === key(n) }"
          :aria-pressed="editingKey && key(editingKey) === key(n) ? 'true' : 'false'"
          :data-testid="`fn-node-${key(n)}`"
          @click="open(n)"
        >
          <span class="fl-chip-icon" aria-hidden="true"><i :class="piIcon(n.icon, 'bolt')" /></span>
          <span class="fl-chip-text">
            <span class="fl-chip-label">{{ n.label }}</span>
            <span class="fl-chip-hint"><code>{{ n.plugin }} › {{ n.action }}</code></span>
            <span v-if="n.shadowed" class="fl-chip-hint fn-shadowed">{{ t.flowNodes.shadowed }}</span>
          </span>
        </button>
      </section>

      <!-- Le nœud ouvert. -->
      <section v-if="draft" class="fn-editor wf-panel" :aria-label="t.flowNodes.editorTitle" data-testid="fn-editor">
        <p class="wf-kicker">{{ editingKey ? t.flowNodes.editing : t.flowNodes.creating }}</p>

        <div class="fn-grid">
          <label class="wf-field" for="fn-label">
            <span>{{ t.flowNodes.label }}</span>
            <InputText id="fn-label" :model-value="draft.label" :disabled="!canEdit" size="small" data-testid="fn-label" @update:model-value="set('label', String($event ?? ''))" />
            <span class="desk-muted wf-help">{{ t.flowNodes.labelHint }}</span>
          </label>
          <label class="wf-field" for="fn-icon">
            <span>{{ t.flowNodes.icon }}</span>
            <span class="fn-icon-row">
              <i :class="piIcon(draft.icon, 'bolt')" aria-hidden="true" />
              <InputText id="fn-icon" :model-value="draft.icon" :disabled="!canEdit" size="small" data-testid="fn-icon" @update:model-value="set('icon', String($event ?? ''))" />
            </span>
            <span class="desk-muted wf-help">{{ t.flowNodes.iconHint }}</span>
          </label>
        </div>
        <label class="wf-field" for="fn-description">
          <span>{{ t.flowNodes.description }}</span>
          <InputText id="fn-description" :model-value="draft.description" :disabled="!canEdit" size="small" data-testid="fn-description" @update:model-value="set('description', String($event ?? ''))" />
        </label>

        <h3 class="wf-section">{{ t.flowNodes.callTitle }}</h3>
        <p class="desk-muted wf-help">{{ t.flowNodes.callHint }}</p>
        <div class="fn-grid">
          <label class="wf-field" for="fn-plugin">
            <span>{{ t.flowNodes.plugin }}</span>
            <InputText id="fn-plugin" :model-value="draft.plugin" :disabled="!canEdit" size="small" list="fn-plugins" data-testid="fn-plugin" @update:model-value="set('plugin', String($event ?? ''))" />
            <datalist id="fn-plugins"><option v-for="p in knownPlugins" :key="p" :value="p" /></datalist>
          </label>
          <label class="wf-field" for="fn-action">
            <span>{{ t.flowNodes.action }}</span>
            <InputText id="fn-action" :model-value="draft.action" :disabled="!canEdit" size="small" data-testid="fn-action" @update:model-value="set('action', String($event ?? ''))" />
          </label>
        </div>

        <!-- Les champs : ce que l'étape demandera, dans l'éditeur d'automatisations. -->
        <h3 class="wf-section">{{ t.flowNodes.fieldsTitle }}</h3>
        <p class="desk-muted wf-help">{{ t.flowNodes.fieldsHint }}</p>
        <div v-for="(f, i) in draft.fields" :key="i" class="fn-row" :data-testid="`fn-field-${i}`">
          <label class="wf-field">
            <span>{{ t.flowNodes.fieldLabel }}</span>
            <InputText :model-value="f.label" :disabled="!canEdit" size="small" :data-testid="`fn-field-label-${i}`" @update:model-value="setFieldLabel(i, String($event ?? ''))" />
          </label>
          <label class="wf-field">
            <span>{{ t.flowNodes.fieldType }}</span>
            <Select
              :model-value="f.type"
              :options="typeOptions"
              option-label="label"
              option-value="value"
              :disabled="!canEdit"
              size="small"
              :data-testid="`fn-field-type-${i}`"
              @update:model-value="draft = patchField(draft!, i, { type: $event as NodeFieldType })"
            />
          </label>
          <label class="wf-field">
            <span>{{ t.flowNodes.fieldName }}</span>
            <InputText :model-value="f.name" :disabled="!canEdit" size="small" class="fe-code" :data-testid="`fn-field-name-${i}`" @update:model-value="draft = patchField(draft!, i, { name: String($event ?? '') })" />
          </label>
          <label class="fn-check">
            <Checkbox :model-value="!!f.required" binary :disabled="!canEdit" :data-testid="`fn-field-required-${i}`" @update:model-value="draft = patchField(draft!, i, { required: $event === true })" />
            <span>{{ t.flowNodes.required }}</span>
          </label>
          <Button
            icon="pi pi-trash"
            text
            rounded
            size="small"
            severity="danger"
            :disabled="!canEdit"
            :aria-label="t.flowNodes.removeField(f.label || f.name)"
            :data-testid="`fn-field-remove-${i}`"
            @click="draft = { ...draft!, fields: removeAt(draft!.fields, i) }"
          />
          <label v-if="f.type === 'select'" class="wf-field fn-options">
            <span>{{ t.flowNodes.options }}</span>
            <Textarea
              :model-value="(f.options ?? []).join('\n')"
              :disabled="!canEdit"
              rows="3"
              :data-testid="`fn-field-options-${i}`"
              @update:model-value="draft = patchField(draft!, i, { options: String($event ?? '').split('\n') })"
            />
            <span class="desk-muted wf-help">{{ t.flowNodes.optionsHint }}</span>
          </label>
        </div>
        <Button v-if="canEdit" :label="t.flowNodes.addField" icon="pi pi-plus" text size="small" data-testid="fn-add-field" @click="draft = addField(draft!)" />

        <!-- Les sorties : ce qu'une étape suivante pourra reprendre (« Résultat d'une étape »). -->
        <h3 class="wf-section">{{ t.flowNodes.outputsTitle }}</h3>
        <p class="desk-muted wf-help">{{ t.flowNodes.outputsHint }}</p>
        <div v-for="(o, i) in draft.outputs" :key="i" class="fn-row fn-row-output" :data-testid="`fn-output-${i}`">
          <label class="wf-field">
            <span>{{ t.flowNodes.outputLabel }}</span>
            <InputText :model-value="o.label" :disabled="!canEdit" size="small" :data-testid="`fn-output-label-${i}`" @update:model-value="draft = { ...draft!, outputs: draft!.outputs.map((x, j) => (j === i ? { ...x, label: String($event ?? '') } : x)) }" />
          </label>
          <label class="wf-field">
            <span>{{ t.flowNodes.outputPath }}</span>
            <InputText :model-value="o.path" :disabled="!canEdit" size="small" class="fe-code" :data-testid="`fn-output-path-${i}`" @update:model-value="draft = { ...draft!, outputs: draft!.outputs.map((x, j) => (j === i ? { ...x, path: String($event ?? '') } : x)) }" />
          </label>
          <Button
            icon="pi pi-trash"
            text
            rounded
            size="small"
            severity="danger"
            :disabled="!canEdit"
            :aria-label="t.flowNodes.removeOutput(o.label || o.path)"
            :data-testid="`fn-output-remove-${i}`"
            @click="draft = { ...draft!, outputs: removeAt(draft!.outputs, i) }"
          />
        </div>
        <Button v-if="canEdit" :label="t.flowNodes.addOutput" icon="pi pi-plus" text size="small" data-testid="fn-add-output" @click="draft = { ...draft!, outputs: [...draft!.outputs, { path: '', label: '' }] }" />

        <Message v-for="p in problems" :key="p" severity="warn" :closable="false" class="fn-problem" data-testid="fn-problem">{{ p }}</Message>
        <Message v-if="serverError" severity="error" :closable="false" data-testid="fn-server-error">{{ serverError }}</Message>

        <div v-if="canEdit" class="fn-actions">
          <Button v-if="editingKey" :label="t.flowNodes.remove" icon="pi pi-trash" text severity="danger" size="small" data-testid="fn-remove" @click="remove" />
          <span class="desk-spacer" />
          <Button :label="t.flowNodes.save" icon="pi pi-check" size="small" :loading="saving" :disabled="saving || !!problems.length || !dirty" data-testid="fn-save" @click="save" />
        </div>
      </section>
      <section v-else class="fn-editor fn-placeholder desk-muted" data-testid="fn-placeholder">{{ canEdit ? t.flowNodes.pick : t.flowNodes.pickReadOnly }}</section>
    </div>
  </div>
</template>
