<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Menu from 'primevue/menu'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import type { MenuItem } from 'primevue/menuitem'
import { computed, ref } from 'vue'
import AssignDialog from '@/components/collab/AssignDialog.vue'
import FieldInput from '@/components/FieldInput.vue'
import TagInput from '@/components/tags/TagInput.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { useExtensions } from '@/extensions/host'
import { piIcon } from '@/lib/icons'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { toPayload } from '@/lib/form'
import { sameTag, supportsTags } from '@/lib/tags'
import { useServices } from '@/lib/services'
import type { BulkItem, BulkResult, DocRecord, DocTypeMeta, FieldMeta } from '@/lib/types'

/**
 * Les actions sur les documents COCHÉS dans la liste : modifier un champ, assigner, étiqueter, soumettre, annuler, supprimer. Le serveur traite chaque document
 * par la porte ordinaire (droits, validation, hooks) et dit pour chacun s'il est passé : un refus n'arrête pas les autres, et on le lit dans un
 * compte rendu (« Facture FA-12 : stock insuffisant »), pas dans un message qui s'efface.
 */
const props = defineProps<{ doctype: string; meta: DocTypeMeta; selection: readonly DocRecord[] }>()
const emit = defineEmits<{ done: [] }>()

const { api } = useServices()
const notify = useNotify()
const confirm = useConfirm()
const menu = ref<InstanceType<typeof Menu> | null>(null)
const extensions = useExtensions()

const names = computed(() => props.selection.map((r) => r.name))
const rights = computed(() => props.meta.rights)
const canWrite = computed(() => rights.value.write !== 'none')

/** Les champs qu'on peut poser en lot : simples, modifiables, jamais un identifiant (unique, nom) ni une valeur que le serveur calcule. */
const OPAQUE = ['Table', 'Password', 'Image', 'Attach', 'DynamicLink']
const editable = computed<FieldMeta[]>(() => {
  const naming = typeof props.meta.naming === 'object' && 'field' in props.meta.naming ? props.meta.naming.field : null
  return props.meta.fields.filter((f) => !f.hidden && !f.read_only && !f.fetch_from && !f.unique && f.fieldname !== naming && !OPAQUE.includes(f.fieldtype))
})

const items = computed<MenuItem[]>(() => {
  const out: MenuItem[] = []
  if (canWrite.value && editable.value.length) out.push({ label: t.bulk.edit, icon: 'pi pi-pencil', command: openEdit })
  if (canWrite.value && !props.meta.virtual) out.push({ label: t.bulk.assign, icon: 'pi pi-user-plus', command: () => (assigning.value = true) })
  if (canWrite.value && supportsTags(props.meta)) out.push({ label: t.tags.bulk, icon: 'pi pi-tag', command: openTagging })
  if (props.meta.submittable && rights.value.submit !== 'none') out.push({ label: t.form.submit, icon: 'pi pi-send', command: () => confirmRun('submit') })
  if (props.meta.submittable && rights.value.cancel !== 'none') out.push({ label: t.form.cancelDocument, icon: 'pi pi-ban', command: () => confirmRun('cancel') })
  // D28 : les actions des extensions sur les documents cochés (le serveur juge chaque appel qu'elles font).
  const ctx = { doctype: props.doctype, meta: props.meta, names: names.value }
  const ext = extensions.listActions(ctx).map<MenuItem>((a) => ({
    label: a.label,
    icon: piIcon(a.icon, 'bolt'), // un nom inconnu retombe sur l'icône par défaut au lieu de laisser un trou
    command: () => void extensions.run(a.extension, (tools) => a.run({ ...ctx, ...tools, reload: () => emit('done') })),
  }))
  if (ext.length && out.length) out.push({ separator: true })
  return [...out, ...ext]
})
const canDelete = computed(() => rights.value.delete !== 'none')

// ── Le compte rendu ─────────────────────────────────────────
const report = ref<{ verb: string; result: BulkResult } | null>(null)
const showReport = computed({ get: () => report.value !== null, set: (v) => { if (!v) report.value = null } })
const failures = computed<BulkItem[]>(() => report.value?.result.results.filter((r) => !r.ok) ?? [])
const labelOf = (name: string): string => {
  const r = props.selection.find((x) => x.name === name)
  const tf = props.meta.title_field
  const v = tf && r ? r.data[tf] : undefined
  return typeof v === 'string' && v ? v : name
}
const reason = (r: BulkItem): string => r.details?.find((d) => d.msg)?.msg ?? r.message ?? r.code ?? t.errors.generic

/** Tout est passé : un message ; un seul document et refusé : le message du serveur ; sinon le compte rendu s'ouvre. La liste se relit dans tous les cas où quelque chose a changé. */
function finish(verb: string, result: BulkResult): void {
  const refused = result.results.find((r) => !r.ok)
  if (result.failed === 0) notify.success(t.bulk.allDone(verb, result.done))
  else if (result.done === 0 && result.failed === 1 && refused) notify.error(new ApiError(422, refused.code ?? 'refused', reason(refused), [])) // un seul document, refusé : son message suffit, pas de compte rendu
  else report.value = { verb, result }
  if (result.done > 0) emit('done')
}

async function run(action: 'delete' | 'submit' | 'cancel' | 'update', verb: string, data?: Record<string, unknown>): Promise<boolean> {
  try {
    finish(verb, await api.bulk(props.doctype, action, names.value, data))
    return true
  } catch (e) {
    notify.error(e) // refus d'ensemble (aucun droit, action inconnue) : rien n'a été touché
    return false
  }
}

function confirmRun(action: 'delete' | 'submit' | 'cancel'): void {
  const n = names.value.length
  const verb = { delete: t.bulk.verbDelete, submit: t.bulk.verbSubmit, cancel: t.bulk.verbCancel }[action]
  confirm.require({
    header: { delete: t.delete, submit: t.form.submit, cancel: t.form.cancelDocument }[action],
    message: { delete: t.list.confirmDelete(n), submit: t.bulk.confirmSubmit(n), cancel: t.bulk.confirmCancel(n) }[action],
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: { delete: t.delete, submit: t.form.submit, cancel: t.form.cancelDocument }[action],
    rejectLabel: t.cancel,
    acceptProps: { severity: action === 'submit' ? 'primary' : 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: () => void run(action, verb),
  })
}

// ── Modifier un champ ───────────────────────────────────────
const editing = ref(false)
const chosen = ref<string | null>(null)
const value = ref<unknown>(null)
const busy = ref(false)
const field = computed(() => editable.value.find((f) => f.fieldname === chosen.value) ?? null)

function openEdit(): void {
  chosen.value = editable.value[0]?.fieldname ?? null
  value.value = null
  editing.value = true
}
function pick(name: string | null): void {
  chosen.value = name
  value.value = null
}
async function applyEdit(): Promise<void> {
  const f = field.value
  if (!f) return
  // La valeur part comme dans un formulaire (un Decimal en texte canonique, un JSON en objet) ; vide : le champ est vidé.
  const sent = toPayload({ ...props.meta, fields: [f] }, { [f.fieldname]: value.value })[f.fieldname] ?? null
  busy.value = true
  const ok = await run('update', t.bulk.verbEdit, { [f.fieldname]: sent })
  busy.value = false
  if (ok) editing.value = false
}

// ── Étiqueter ───────────────────────────────────────────────
const tagging = ref(false)
const tagMode = ref<'add' | 'remove'>('add')
const tagDraft = ref<string[]>([])
const tagModes = computed(() => [
  { label: t.tags.bulkAdd, value: 'add' },
  { label: t.tags.bulkRemove, value: 'remove' },
])
function openTagging(): void {
  tagMode.value = 'add'
  tagDraft.value = []
  tagging.value = true
}
function draftTag(tag: string): void {
  if (!tagDraft.value.some((x) => sameTag(x, tag))) tagDraft.value = [...tagDraft.value, tag]
}
async function applyTags(): Promise<void> {
  if (!tagDraft.value.length) return
  busy.value = true
  try {
    const verb = tagMode.value === 'add' ? t.tags.verbAdd : t.tags.verbRemove
    finish(verb, await api.tagMany(props.doctype, names.value, { [tagMode.value]: tagDraft.value }))
    tagging.value = false
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = false
  }
}

// ── Assigner ────────────────────────────────────────────────
const assigning = ref(false)
function assigned(result?: BulkResult): void {
  if (result) finish(t.bulk.verbAssign, result)
}
</script>

<template>
  <span v-if="items.length || canDelete" class="bulk-actions">
    <Button
      v-if="canDelete"
      :label="t.list.deleteSelected(selection.length)"
      icon="pi pi-trash"
      severity="danger"
      size="small"
      outlined
      data-testid="bulk-delete"
      @click="confirmRun('delete')"
    />
    <Button
      v-if="items.length"
      :label="t.bulk.actions(selection.length)"
      icon="pi pi-bolt"
      icon-pos="left"
      severity="secondary"
      size="small"
      aria-haspopup="true"
      data-testid="bulk-actions"
      @click="menu?.toggle($event)"
    />
    <Menu ref="menu" :model="items" popup />

    <Dialog v-model:visible="editing" modal :header="t.bulk.editTitle(selection.length)" :style="{ width: '28rem', maxWidth: '95vw' }" data-testid="bulk-edit-dialog">
      <div class="bulk-form">
        <label class="bf-field">
          <span>{{ t.bulk.field }}</span>
          <Select :model-value="chosen" :options="editable" option-label="label" option-value="fieldname" fluid data-testid="bulk-field" @update:model-value="pick">
            <template #option="{ option }">{{ option.label ?? option.fieldname }}</template>
            <template #value="{ value: v }">{{ editable.find((f) => f.fieldname === v)?.label ?? v }}</template>
          </Select>
        </label>
        <label v-if="field" class="bf-field">
          <span>{{ t.bulk.value }}</span>
          <FieldInput :key="field.fieldname" :field="field" :model-value="value" input-id="bulk-value" :doctype="doctype" @update:model-value="value = $event" />
          <small class="desk-muted">{{ t.bulk.emptyClears }}</small>
        </label>
      </div>
      <template #footer>
        <Button :label="t.cancel" severity="secondary" text @click="editing = false" />
        <Button :label="t.bulk.apply" icon="pi pi-check" :loading="busy" :disabled="!field" data-testid="bulk-edit-apply" @click="applyEdit" />
      </template>
    </Dialog>

    <AssignDialog v-model:visible="assigning" :doctype="doctype" :names="names" @saved="assigned" />

    <Dialog v-model:visible="tagging" modal :header="t.tags.bulkTitle(selection.length)" :style="{ width: '28rem', maxWidth: '95vw' }" data-testid="bulk-tag-dialog">
      <div class="bulk-form">
        <SelectButton v-model="tagMode" :options="tagModes" option-label="label" option-value="value" :allow-empty="false" size="small" :aria-label="t.tags.title" data-testid="bulk-tag-mode" />
        <ul v-if="tagDraft.length" class="tag-chips" :aria-label="t.tags.title">
          <li v-for="tag in tagDraft" :key="tag" class="tag-chip" :data-testid="`bulk-tag-${tag}`">
            <span>{{ tag }}</span>
            <button type="button" class="tag-remove" :aria-label="t.tags.remove(tag)" @click="tagDraft = tagDraft.filter((x) => x !== tag)"><span class="pi pi-times" aria-hidden="true" /></button>
          </li>
        </ul>
        <TagInput :doctype="doctype" input-id="bulk-tag-input" :exclude="tagDraft" @commit="draftTag" />
      </div>
      <template #footer>
        <Button :label="t.cancel" severity="secondary" text @click="tagging = false" />
        <Button :label="t.bulk.apply" icon="pi pi-check" :loading="busy" :disabled="!tagDraft.length" data-testid="bulk-tag-apply" @click="applyTags" />
      </template>
    </Dialog>

    <Dialog v-model:visible="showReport" modal :header="report ? t.bulk.reportTitle(report.verb, report.result.done, report.result.failed) : ''" :style="{ width: '34rem', maxWidth: '95vw' }" data-testid="bulk-report">
      <ul class="bulk-failures">
        <li v-for="r in failures" :key="r.name" :data-testid="`bulk-fail-${r.name}`">
          <strong>{{ labelOf(r.name) }}</strong> <span class="desk-muted">{{ reason(r) }}</span>
        </li>
      </ul>
      <template #footer><Button :label="t.close" data-testid="bulk-report-close" @click="report = null" /></template>
    </Dialog>
  </span>
</template>

<style scoped>
.bulk-form {
  display: grid;
  gap: 0.875rem;
}

.bf-field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.bf-field > span {
  color: var(--p-text-muted-color);
}

.bulk-failures {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.5rem;
  max-height: 50vh;
  overflow: auto;
}

.bulk-failures li {
  display: grid;
  gap: 0.1rem;
}
</style>
