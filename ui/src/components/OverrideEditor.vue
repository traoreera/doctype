<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import Textarea from 'primevue/textarea'
import { computed, ref, watch } from 'vue'
import CellValue from '@/components/CellValue.vue'
import FieldInput from '@/components/FieldInput.vue'
import { t } from '@/i18n'
import { ADDED_TYPES, addedFields, customName, liveDefault, propRows, withAddedField, withoutAddedField, withProp, type AddedField, type LockReason, type PropKey, type PropRow } from '@/lib/overrideprops'
import type { FieldBase, FieldMeta, FormOverride } from '@/lib/types'

/**
 * Éditeur d'habillage : du JSON validé par le SERVEUR (le Desk n'invente aucune règle de disposition). Pour un formulaire (`fields` fourni),
 * un tableau règle aussi les propriétés d'écriture de chaque champ (obligatoire, lecture seule, masqué, valeur par défaut) : il écrit dans
 * ce même JSON, qui reste la seule vérité, et grise ce que le serveur refuserait (`lib/overrideprops.ts`). `definition` : ce que le YAML
 * impose (`GET /override/form`), `naming` : le champ qui nomme le document, `rules` : `false` quand l'utilisateur n'écrit pas TOUS les
 * documents du type (obligatoire, lecture seule et défaut lui sont alors refusés par le serveur : ils restent tels quels, grisés).
 * `problem` : le refus du serveur au dernier enregistrement, montré dans la fenêtre, qui reste ouverte.
 */
// `rules` vaut `true` sans indication : Vue lirait sinon une propriété booléenne absente comme `false`.
const props = withDefaults(
  defineProps<{
    visible: boolean
    title: string
    initial: unknown
    saving?: boolean
    fields?: FieldMeta[]
    definition?: Record<string, FieldBase> | null
    naming?: string | null
    doctype?: string
    rules?: boolean
    problem?: string | null
  }>(),
  { rules: true },
)
const emit = defineEmits<{ (e: 'update:visible', value: boolean): void; (e: 'save', config: unknown): void; (e: 'reset'): void }>()

const text = ref('{}')
const parseError = ref<string | null>(null)
const uid = `ov${Math.random().toString(36).slice(2, 8)}`

watch(
  () => [props.visible, props.initial] as const,
  () => {
    if (!props.visible) return
    text.value = JSON.stringify(props.initial ?? {}, null, 2)
    parseError.value = null
  },
  { immediate: true },
)

const asConfig = (v: unknown): FormOverride | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as FormOverride) : null)

/** Le JSON en cours, s'il se lit : le tableau des propriétés n'écrit que dans un JSON valide (sinon il se grise, rien n'est perdu). */
const parsed = computed<FormOverride | null>(() => {
  try {
    return asConfig(JSON.parse(text.value))
  } catch {
    return null
  }
})

// Ce que la définition impose vient du serveur (`definition`) ; sans lui, on le déduit de `/meta` (`fields`) moins l'habillage ENREGISTRÉ (`initial`).
const rows = computed<PropRow[]>(() =>
  props.fields
    ? propRows(props.fields, parsed.value, { definition: props.definition, applied: asConfig(props.initial), naming: props.naming, rules: props.rules })
    : [],
)

function setProp(fieldname: string, key: PropKey, value: unknown): void {
  if (!parsed.value) return
  text.value = JSON.stringify(withProp(parsed.value, fieldname, key, value), null, 2)
}

// ── Champs ajoutés par l'organisation (§3.5) : ils s'écrivent dans le MÊME JSON, qui reste la seule vérité ──────────
const draft = ref<{ name: string; fieldtype: string; label: string; required: boolean; in_list_view: boolean; options: string }>({
  name: '', fieldtype: 'Text', label: '', required: false, in_list_view: false, options: '',
})
const added = computed<AddedField[]>(() => addedFields(parsed.value))
const draftName = computed(() => customName(draft.value.name))
const needsOptions = computed(() => draft.value.fieldtype === 'Select')
const canAdd = computed(
  () =>
    !!parsed.value && props.rules !== false && !!draftName.value &&
    !added.value.some((f) => f.fieldname === draftName.value) &&
    !(props.fields ?? []).some((f) => f.fieldname === draftName.value) &&
    (!needsOptions.value || optionList.value.length > 0),
)
const optionList = computed(() => draft.value.options.split('\n').map((v) => v.trim()).filter(Boolean))

function addField(): void {
  if (!parsed.value || !canAdd.value) return
  text.value = JSON.stringify(
    withAddedField(parsed.value, {
      fieldname: draftName.value,
      fieldtype: draft.value.fieldtype,
      label: draft.value.label,
      required: draft.value.required,
      in_list_view: draft.value.in_list_view,
      ...(needsOptions.value ? { options: optionList.value } : {}),
    }),
    null,
    2,
  )
  draft.value = { name: '', fieldtype: 'Text', label: '', required: false, in_list_view: false, options: '' }
}

function removeField(fieldname: string): void {
  if (!parsed.value) return
  text.value = JSON.stringify(withoutAddedField(parsed.value, fieldname), null, 2)
}

const labelOf = (row: PropRow): string => row.field.label || row.field.fieldname
// Le champ de saisie du défaut : celui du formulaire, toujours effaçable (un défaut vidé rend la main à la définition).
const inputFor = (f: FieldMeta): FieldMeta => ({ ...f, required: false })
const liveLabel = (f: FieldMeta): string => (liveDefault(f) === 'today' ? t.overrideProps.today : t.overrideProps.now)
const WHY: Record<LockReason, string> = {
  definition: t.overrideProps.fromDefinition,
  opaque: t.overrideProps.opaque,
  condition: t.overrideProps.condition,
  reserved: t.overrideProps.reserved,
  unfilled: t.overrideProps.unfilled,
}
/** Pourquoi une case est grisée (`lib/overrideprops`, `why`), en mots, au survol de sa cellule. */
const lockedWhy = (row: PropRow, key: PropKey): string | undefined => {
  const reason = row.why[key]
  return reason ? WHY[reason] : undefined
}
/** Le défaut du YAML, montré tant que l'habillage n'en pose pas : `today` / `now` se lisent en mots, le reste comme dans une liste. */
const baseDefault = (row: PropRow): string | undefined =>
  row.base.default === 'today' || row.base.default === 'now' ? liveLabel(row.field) : undefined

function submit(): void {
  let parsedNow: unknown
  try {
    parsedNow = JSON.parse(text.value)
  } catch {
    parseError.value = t.override.invalidJson
    return
  }
  parseError.value = null
  emit('save', parsedNow)
}
</script>

<template>
  <Dialog :visible="visible" modal :header="title" :style="{ width: fields?.length ? '920px' : '680px', maxWidth: '95vw' }" data-testid="override-dialog" @update:visible="emit('update:visible', $event)">
    <p class="desk-muted" style="margin-top: 0">{{ t.override.hint }}</p>
    <Message v-if="problem" severity="error" class="banner-error" role="alert" data-testid="override-problem">{{ problem }}</Message>

    <section v-if="fields" class="props" data-testid="override-added">
      <h3 class="props-title">{{ t.addedFields.title }}</h3>
      <p class="desk-muted props-hint">{{ t.addedFields.hint }}</p>
      <Message v-if="rules === false" severity="info" class="banner-error" data-testid="added-reserved">{{ t.addedFields.reserved }}</Message>

      <ul v-if="added.length" class="added-list">
        <li v-for="f in added" :key="f.fieldname" :data-testid="`added-${f.fieldname}`">
          <strong>{{ f.label || f.fieldname }}</strong>
          <code class="desk-muted">{{ f.fieldname }}</code>
          <span class="desk-muted">{{ f.fieldtype }}</span>
          <span v-if="f.required" class="desk-muted">· {{ t.addedFields.required }}</span>
          <Button
            icon="pi pi-times" text severity="danger" size="small"
            :disabled="!parsed || rules === false"
            :aria-label="t.addedFields.remove(f.label || f.fieldname)"
            :data-testid="`added-remove-${f.fieldname}`"
            @click="removeField(f.fieldname)"
          />
        </li>
      </ul>
      <p v-else class="desk-muted" data-testid="added-none">{{ t.addedFields.none }}</p>

      <div class="added-form">
        <label :for="`${uid}-add-name`">{{ t.addedFields.name }}</label>
        <InputText :id="`${uid}-add-name`" v-model="draft.name" :disabled="rules === false" data-testid="added-name" />
        <label :for="`${uid}-add-label`">{{ t.addedFields.label }}</label>
        <InputText :id="`${uid}-add-label`" v-model="draft.label" :disabled="rules === false" data-testid="added-label" />
        <label :for="`${uid}-add-type`">{{ t.addedFields.type }}</label>
        <Select :id="`${uid}-add-type`" v-model="draft.fieldtype" :options="[...ADDED_TYPES]" :disabled="rules === false" data-testid="added-type" />
        <template v-if="needsOptions">
          <label :for="`${uid}-add-options`">{{ t.addedFields.options }}</label>
          <Textarea :id="`${uid}-add-options`" v-model="draft.options" rows="3" :disabled="rules === false" data-testid="added-options" />
        </template>
        <div class="added-flags">
          <Checkbox v-model="draft.required" binary :input-id="`${uid}-add-req`" :disabled="rules === false" data-testid="added-required" />
          <label :for="`${uid}-add-req`">{{ t.addedFields.required }}</label>
          <Checkbox v-model="draft.in_list_view" binary :input-id="`${uid}-add-list`" :disabled="rules === false" data-testid="added-inlist" />
          <label :for="`${uid}-add-list`">{{ t.addedFields.inList }}</label>
        </div>
        <p v-if="draftName" class="desk-muted" data-testid="added-name-hint">{{ t.addedFields.nameHint(draftName) }}</p>
        <Button :label="t.addedFields.add" icon="pi pi-plus" size="small" :disabled="!canAdd" data-testid="added-add" @click="addField" />
        <p class="desk-muted">{{ t.addedFields.removed }}</p>
      </div>
    </section>

    <section v-if="rows.length" class="props" data-testid="override-props">
      <h3 class="props-title">{{ t.overrideProps.title }}</h3>
      <p class="desk-muted props-hint">{{ t.overrideProps.hint }}</p>
      <Message v-if="rules === false" severity="info" class="banner-error" data-testid="override-props-reserved">{{ t.overrideProps.reservedHint }}</Message>
      <Message v-if="!parsed" severity="warn" class="banner-error" data-testid="override-props-blocked">{{ t.overrideProps.fixJson }}</Message>
      <div class="props-scroll">
        <table class="props-table">
          <thead>
            <tr>
              <th scope="col">{{ t.overrideProps.field }}</th>
              <th scope="col">{{ t.overrideProps.mandatory }}</th>
              <th scope="col">{{ t.overrideProps.readOnly }}</th>
              <th scope="col">{{ t.overrideProps.hidden }}</th>
              <th scope="col">{{ t.overrideProps.default }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.field.fieldname" :data-testid="`prop-row-${row.field.fieldname}`">
              <th scope="row" class="props-name">
                {{ labelOf(row) }}<code class="desk-muted">{{ row.field.fieldname }}</code>
              </th>
              <td :title="lockedWhy(row, 'mandatory')">
                <Checkbox
                  :model-value="row.mandatory"
                  binary
                  :disabled="!parsed || !row.can.mandatory"
                  :aria-label="t.overrideProps.mandatoryOf(labelOf(row))"
                  :data-testid="`prop-mandatory-${row.field.fieldname}`"
                  @update:model-value="setProp(row.field.fieldname, 'mandatory', $event)"
                />
              </td>
              <td :title="lockedWhy(row, 'read_only')">
                <Checkbox
                  :model-value="row.readOnly"
                  binary
                  :disabled="!parsed || !row.can.read_only"
                  :aria-label="t.overrideProps.readOnlyOf(labelOf(row))"
                  :data-testid="`prop-read_only-${row.field.fieldname}`"
                  @update:model-value="setProp(row.field.fieldname, 'read_only', $event)"
                />
              </td>
              <td :title="lockedWhy(row, 'hidden')">
                <Checkbox
                  :model-value="row.hidden"
                  binary
                  :disabled="!parsed || !row.can.hidden"
                  :aria-label="t.overrideProps.hiddenOf(labelOf(row))"
                  :data-testid="`prop-hidden-${row.field.fieldname}`"
                  @update:model-value="setProp(row.field.fieldname, 'hidden', $event)"
                />
              </td>
              <td class="props-default" :data-testid="`prop-default-${row.field.fieldname}`">
                <template v-if="row.can.default">
                  <span v-if="row.default !== undefined && row.default === liveDefault(row.field)" class="props-live" data-testid="prop-live">{{ liveLabel(row.field) }}</span>
                  <div v-else class="props-input">
                    <label :for="`${uid}-${row.field.fieldname}`" class="visually-hidden">{{ t.overrideProps.defaultOf(labelOf(row)) }}</label>
                    <FieldInput
                      :field="inputFor(row.field)"
                      :model-value="row.default ?? null"
                      :input-id="`${uid}-${row.field.fieldname}`"
                      :doctype="doctype"
                      @update:model-value="setProp(row.field.fieldname, 'default', $event)"
                    />
                  </div>
                  <Button
                    v-if="liveDefault(row.field) && row.default !== liveDefault(row.field)"
                    :label="liveLabel(row.field)"
                    text
                    size="small"
                    :disabled="!parsed"
                    :data-testid="`prop-live-${row.field.fieldname}`"
                    @click="setProp(row.field.fieldname, 'default', liveDefault(row.field))"
                  />
                  <Button
                    v-if="row.default !== undefined"
                    icon="pi pi-times"
                    text
                    rounded
                    severity="secondary"
                    :disabled="!parsed"
                    :aria-label="t.overrideProps.clearDefault(labelOf(row))"
                    :data-testid="`prop-clear-${row.field.fieldname}`"
                    @click="setProp(row.field.fieldname, 'default', undefined)"
                  />
                  <span v-if="row.default === undefined && row.base.default !== undefined && row.base.default !== null" class="desk-muted props-base" data-testid="prop-base-default">
                    {{ t.overrideProps.definitionDefault }}
                    <CellValue :field="row.field" :value="row.base.default" :text="baseDefault(row)" />
                  </span>
                </template>
                <!-- Réservé : le défaut que l'organisation a posé se lit, il ne se change pas ici. -->
                <span v-else-if="row.default !== undefined" class="props-live" :title="lockedWhy(row, 'default')" data-testid="prop-default-value">
                  <CellValue :field="row.field" :value="row.default" :text="row.default === liveDefault(row.field) ? liveLabel(row.field) : undefined" />
                </span>
                <span v-else class="desk-muted" :title="lockedWhy(row, 'default') ?? t.overrideProps.noDefault">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <h3 class="props-title">{{ t.overrideProps.json }}</h3>
    </section>

    <Textarea v-model="text" :rows="rows.length ? 8 : 16" auto-resize class="override-text" data-testid="override-text" aria-label="override-json" />
    <Message v-if="parseError" severity="error" class="banner-error" data-testid="override-parse-error">{{ parseError }}</Message>
    <template #footer>
      <Button :label="t.override.reset" severity="secondary" text :loading="saving" data-testid="override-reset" @click="emit('reset')" />
      <Button :label="t.cancel" severity="secondary" text @click="emit('update:visible', false)" />
      <Button :label="t.save" icon="pi pi-check" :loading="saving" data-testid="override-save" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.override-text {
  width: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.82rem;
}
.props-title {
  font-size: 0.95rem;
  margin: 0.75rem 0 0.25rem;
}
.props-hint {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
}
/* Sur un téléphone, le tableau défile dans la fenêtre, jamais la page. */
.props-scroll {
  overflow-x: auto;
  margin-bottom: 0.5rem;
}
.props-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}
.props-table th,
.props-table td {
  padding: 0.3rem 0.5rem;
  border-bottom: 1px solid var(--p-content-border-color);
  text-align: center;
  vertical-align: middle;
}
.props-table thead th {
  font-weight: 600;
  white-space: nowrap;
}
.props-table .props-name {
  text-align: left;
  font-weight: 500;
  white-space: nowrap;
}
.props-name code {
  margin-left: 0.4rem;
  font-size: 0.75rem;
}
.props-default {
  min-width: 15rem;
  text-align: left !important;
}
.props-default > * {
  vertical-align: middle;
}
.props-input {
  display: inline-block;
  width: calc(100% - 6rem);
  min-width: 9rem;
}
.props-live {
  font-style: italic;
  margin-right: 0.25rem;
}
.props-base {
  display: block;
  font-size: 0.8rem;
  margin-top: 0.15rem;
}
</style>
