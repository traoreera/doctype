<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import { FIELD_GROUPS, optionsKind, typedDefault, type DevField } from '@/lib/doctypebuilder'
import type { Nudge } from '@/lib/formbuilder'
import type { FieldType } from '@/lib/types'

/**
 * Les propriétés d'un champ dans le constructeur de DocTypes (mode développeur) : TOUT ce que le schéma connaît d'un champ
 * (`schema.FieldDef`), groupé comme dans le DocType de Frappe. Le panneau n'écrit que des retouches (`patch`, `retype`, `rename`) que
 * `lib/doctypebuilder` applique ; c'est le serveur qui juge la définition entière (« Vérifier »).
 */
const props = defineProps<{
  field: DevField
  /** Les autres champs du type : `options` d'un DynamicLink, et pour dire qu'un nom est pris. */
  siblings: string[]
  /** Les types de document existants (cible d'un Link, type enfant d'une table). */
  doctypes: { name: string; label: string }[]
  isPlaced: boolean
}>()
const emit = defineEmits<{
  (e: 'patch', value: Record<string, unknown>): void
  (e: 'retype', value: FieldType): void
  (e: 'rename', value: string): void
  (e: 'remove'): void
  (e: 'nudge', how: Nudge): void
  (e: 'unplace'): void
  (e: 'place'): void
}>()

const TYPES = FIELD_GROUPS.map((g) => ({ label: g.label, items: g.types.map((type) => ({ value: type, label: type })) }))
const kind = computed(() => optionsKind(props.field.fieldtype))
const str = (key: string): string => (typeof props.field[key] === 'string' ? (props.field[key] as string) : '')
const bool = (key: string): boolean => props.field[key] === true

// Le nom technique se tape librement et ne s'applique qu'à la validation (Entrée, sortie du champ) : chaque frappe renommerait partout.
const nameDraft = ref(props.field.fieldname)
watch(() => props.field.fieldname, (n) => (nameDraft.value = n))
const nameProblem = computed(() =>
  nameDraft.value === props.field.fieldname ? null
  : !/^[a-z][a-z0-9_]{0,62}$/.test(nameDraft.value) ? t.devBuilder.badName
  : props.siblings.includes(nameDraft.value) ? t.devBuilder.takenName
  : null,
)
function applyName(): void {
  if (nameDraft.value !== props.field.fieldname && !nameProblem.value) emit('rename', nameDraft.value)
}

const values = computed(() => (Array.isArray(props.field.options) ? (props.field.options as string[]).join('\n') : ''))
const defaultText = computed(() => (props.field.default === undefined || props.field.default === null ? '' : String(props.field.default)))
const FLAGS: { key: string; label: () => string }[] = [
  { key: 'required', label: () => t.overrideProps.mandatory },
  { key: 'in_list_view', label: () => t.addedFields.inList },
  { key: 'read_only', label: () => t.overrideProps.readOnly },
  { key: 'hidden', label: () => t.overrideProps.hidden },
  { key: 'unique', label: () => t.devBuilder.unique },
  { key: 'set_only_once', label: () => t.devBuilder.setOnlyOnce },
  { key: 'allow_on_submit', label: () => t.devBuilder.allowOnSubmit },
  { key: 'no_copy', label: () => t.devBuilder.noCopy },
]
</script>

<template>
  <div class="fb-panel-body" data-testid="dev-field-panel">
    <p class="wf-kicker">{{ t.devBuilder.field }}</p>
    <h2 class="wf-panel-title" data-testid="fb-panel-title">{{ field.label || field.fieldname }}</h2>

    <div class="fb-moves" role="group" :aria-label="t.formBuilder.move">
      <template v-if="isPlaced">
        <Button icon="pi pi-arrow-up" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.up" :title="t.formBuilder.up" @click="emit('nudge', 'up')" />
        <Button icon="pi pi-arrow-down" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.down" :title="t.formBuilder.down" @click="emit('nudge', 'down')" />
        <Button icon="pi pi-arrow-left" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.left" :title="t.formBuilder.left" @click="emit('nudge', 'left')" />
        <Button icon="pi pi-arrow-right" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.right" :title="t.formBuilder.right" @click="emit('nudge', 'right')" />
        <Button icon="pi pi-angle-double-up" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.prevSection" :title="t.formBuilder.prevSection" @click="emit('nudge', 'prevSection')" />
        <Button icon="pi pi-angle-double-down" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.nextSection" :title="t.formBuilder.nextSection" @click="emit('nudge', 'nextSection')" />
        <Button :label="t.formBuilder.unplace" icon="pi pi-sign-out" text size="small" severity="secondary" @click="emit('unplace')" />
      </template>
      <Button v-else-if="!field.hidden" :label="t.formBuilder.place" icon="pi pi-sign-in" size="small" outlined @click="emit('place')" />
    </div>

    <label class="wf-field" for="dev-label">
      <span>{{ t.addedFields.label }}</span>
      <InputText id="dev-label" :model-value="str('label')" size="small" data-testid="dev-label" @update:model-value="emit('patch', { label: $event ?? '' })" />
    </label>
    <label class="wf-field" for="dev-name">
      <span>{{ t.addedFields.name }}</span>
      <InputText id="dev-name" v-model="nameDraft" size="small" :invalid="!!nameProblem" data-testid="dev-name" @blur="applyName" @keydown.enter.prevent="applyName" />
      <span v-if="nameProblem" class="wf-help fb-error">{{ nameProblem }}</span>
      <span v-else class="desk-muted wf-help">{{ t.devBuilder.renameHint }}</span>
    </label>
    <label class="wf-field" for="dev-type">
      <span>{{ t.addedFields.type }}</span>
      <Select
        input-id="dev-type"
        :model-value="field.fieldtype"
        :options="TYPES"
        option-label="label"
        option-value="value"
        option-group-label="label"
        option-group-children="items"
        size="small"
        data-testid="dev-type"
        @update:model-value="emit('retype', $event)"
      />
    </label>

    <label v-if="kind === 'values'" class="wf-field" for="dev-values">
      <span>{{ t.addedFields.options }}</span>
      <Textarea id="dev-values" :model-value="values" rows="4" auto-resize data-testid="dev-values" @update:model-value="emit('patch', { options: ($event ?? '').split('\n').map((v: string) => v.trim()).filter(Boolean) })" />
    </label>
    <label v-else-if="kind === 'doctype' || kind === 'child'" class="wf-field" for="dev-target">
      <span>{{ kind === 'doctype' ? t.devBuilder.linkTarget : t.devBuilder.childType }}</span>
      <Select
        v-if="kind === 'doctype'"
        input-id="dev-target"
        :model-value="str('options') || null"
        :options="doctypes"
        option-label="label"
        option-value="name"
        filter
        size="small"
        data-testid="dev-target"
        @update:model-value="emit('patch', { options: $event ?? '' })"
      />
      <InputText v-else id="dev-target" :model-value="str('options')" size="small" data-testid="dev-target" @update:model-value="emit('patch', { options: $event ?? '' })" />
    </label>
    <label v-else-if="kind === 'typeField'" class="wf-field" for="dev-typefield">
      <span>{{ t.devBuilder.typeField }}</span>
      <Select input-id="dev-typefield" :model-value="str('options') || null" :options="siblings" size="small" @update:model-value="emit('patch', { options: $event ?? '' })" />
    </label>
    <label v-if="field.fieldtype === 'Decimal' || field.fieldtype === 'Currency'" class="wf-field" for="dev-decimals">
      <span>{{ t.devBuilder.decimals }}</span>
      <InputNumber input-id="dev-decimals" :model-value="typeof field.decimals === 'number' ? field.decimals : null" :min="0" :max="9" size="small" @update:model-value="emit('patch', { decimals: $event ?? undefined })" />
    </label>
    <label v-if="field.fieldtype === 'Rating'" class="wf-field" for="dev-stars">
      <span>{{ t.devBuilder.stars }}</span>
      <InputNumber input-id="dev-stars" :model-value="typeof field.stars === 'number' ? field.stars : null" :min="1" :max="10" size="small" @update:model-value="emit('patch', { stars: $event ?? undefined })" />
    </label>

    <h3 class="wf-section">{{ t.devBuilder.behaviour }}</h3>
    <label v-for="flag in FLAGS" :key="flag.key" class="wf-check">
      <Checkbox :model-value="bool(flag.key)" binary :input-id="`dev-${flag.key}`" :data-testid="`dev-${flag.key}`" @update:model-value="emit('patch', { [flag.key]: $event === true })" />
      <span>{{ flag.label() }}</span>
    </label>
    <label class="wf-field" for="dev-default">
      <span>{{ t.overrideProps.default }}</span>
      <InputText id="dev-default" :model-value="defaultText" size="small" :placeholder="field.fieldtype === 'Date' ? 'today' : ''" data-testid="dev-default" @update:model-value="emit('patch', { default: typedDefault(field.fieldtype, $event ?? '') })" />
    </label>
    <label class="wf-field" for="dev-description">
      <span>{{ t.formBuilder.description }}</span>
      <InputText id="dev-description" :model-value="str('description')" size="small" @update:model-value="emit('patch', { description: $event ?? '' })" />
    </label>

    <h3 class="wf-section">{{ t.devBuilder.conditions }}</h3>
    <label v-for="key in ['depends_on', 'mandatory_depends_on', 'read_only_depends_on']" :key="key" class="wf-field" :for="`dev-${key}`">
      <span>{{ t.devBuilder.condition[key] }}</span>
      <InputText :id="`dev-${key}`" :model-value="str(key)" size="small" placeholder="statut == 'Ouvert'" @update:model-value="emit('patch', { [key]: $event ?? '' })" />
    </label>
    <label class="wf-field" for="dev-fetch">
      <span>{{ t.devBuilder.fetchFrom }}</span>
      <InputText id="dev-fetch" :model-value="str('fetch_from')" size="small" placeholder="client.ville" @update:model-value="emit('patch', { fetch_from: $event ?? '' })" />
    </label>

    <Button :label="t.devBuilder.removeField" icon="pi pi-trash" severity="danger" text size="small" data-testid="dev-remove" @click="emit('remove')" />
  </div>
</template>
