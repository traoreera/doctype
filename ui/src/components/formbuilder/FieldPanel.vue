<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import { computed } from 'vue'
import CellValue from '@/components/CellValue.vue'
import FieldInput from '@/components/FieldInput.vue'
import { t } from '@/i18n'
import { withDisplay, type Nudge } from '@/lib/formbuilder'
import { liveDefault, withAddedField, withProp, type AddedField, type LockReason, type PropKey, type PropRow } from '@/lib/overrideprops'
import type { FieldMeta, FormOverride } from '@/lib/types'

/**
 * Les propriétés du champ choisi dans le Form Builder. Deux sortes de champs, deux sortes de propriétés :
 * - un champ du TYPE (`row`, de `lib/overrideprops.propRows`) : l'organisation retouche son libellé et sa description (affichage) et ne peut
 *   que RESSERRER ses règles d'écriture (obligatoire, lecture seule, masqué, défaut) ; ce que l'application impose se montre coché et grisé ;
 * - un champ AJOUTÉ par l'organisation (`added`, §3.5) : sa définition entière lui appartient (libellé, valeurs, obligatoire…).
 * Le panneau n'écrit que dans l'habillage (`update:config`) ; le serveur valide à l'enregistrement.
 */
const props = defineProps<{
  config: FormOverride
  row: PropRow | null
  added: AddedField | null
  /** Le libellé du champ selon l'application (le YAML), montré à côté de celui de l'organisation. */
  appLabel: string | null
  doctype: string
  /** Peut enregistrer un habillage (écrire le type). */
  canEdit: boolean
  /** Peut régler les règles d'écriture et ajouter des champs (écrire TOUS les documents du type). */
  rules: boolean
  isPlaced: boolean
}>()
const emit = defineEmits<{
  (e: 'update:config', value: FormOverride): void
  (e: 'nudge', how: Nudge): void
  (e: 'unplace'): void
  (e: 'place'): void
  (e: 'remove'): void
}>()

const name = computed(() => props.row?.field.fieldname ?? props.added?.fieldname ?? '')
const tweak = computed(() => props.config.fields?.[name.value] ?? {})
const WHY: Record<LockReason, string> = {
  definition: t.overrideProps.fromDefinition,
  opaque: t.overrideProps.opaque,
  condition: t.overrideProps.condition,
  reserved: t.overrideProps.reserved,
  unfilled: t.overrideProps.unfilled,
}
const why = (key: PropKey): string | undefined => (props.row?.why[key] ? WHY[props.row.why[key]!] : undefined)
const liveLabel = (f: FieldMeta): string => (liveDefault(f) === 'today' ? t.overrideProps.today : t.overrideProps.now)
// Le champ de saisie du défaut : celui du formulaire, toujours effaçable (un défaut vidé rend la main à la définition).
const inputFor = (f: FieldMeta): FieldMeta => ({ ...f, required: false })

function setProp(key: PropKey, value: unknown): void {
  emit('update:config', withProp(props.config, name.value, key, value))
}
function setDisplay(key: 'label' | 'description', value: string): void {
  emit('update:config', withDisplay(props.config, name.value, key, value))
}
function setAdded(patch: Partial<AddedField>): void {
  if (!props.added) return
  emit('update:config', withAddedField(props.config, { ...props.added, ...patch }))
}
const options = computed(() => (props.added?.options ?? []).join('\n'))
</script>

<template>
  <div class="fb-panel-body" data-testid="fb-field-panel">
    <p class="wf-kicker">{{ added ? t.formBuilder.addedField : t.formBuilder.typeField }}</p>
    <h2 class="wf-panel-title" data-testid="fb-panel-title">{{ added ? added.label || added.fieldname : tweak.label || appLabel || name }}</h2>
    <p class="desk-muted fb-tech"><code>{{ name }}</code> · {{ row?.field.fieldtype ?? added?.fieldtype }}</p>

    <!-- Place : l'équivalent clavier du glisser-déposer. -->
    <div v-if="canEdit" class="fb-moves" role="group" :aria-label="t.formBuilder.move">
      <template v-if="isPlaced">
        <Button icon="pi pi-arrow-up" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.up" :title="t.formBuilder.up" data-testid="fb-up" @click="emit('nudge', 'up')" />
        <Button icon="pi pi-arrow-down" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.down" :title="t.formBuilder.down" data-testid="fb-down" @click="emit('nudge', 'down')" />
        <Button icon="pi pi-arrow-left" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.left" :title="t.formBuilder.left" @click="emit('nudge', 'left')" />
        <Button icon="pi pi-arrow-right" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.right" :title="t.formBuilder.right" data-testid="fb-right" @click="emit('nudge', 'right')" />
        <Button icon="pi pi-angle-double-up" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.prevSection" :title="t.formBuilder.prevSection" @click="emit('nudge', 'prevSection')" />
        <Button icon="pi pi-angle-double-down" text rounded severity="secondary" size="small" :aria-label="t.formBuilder.nextSection" :title="t.formBuilder.nextSection" data-testid="fb-next-section" @click="emit('nudge', 'nextSection')" />
        <Button :label="t.formBuilder.unplace" icon="pi pi-sign-out" text size="small" severity="secondary" data-testid="fb-unplace" @click="emit('unplace')" />
      </template>
      <Button v-else :label="t.formBuilder.place" icon="pi pi-sign-in" size="small" outlined data-testid="fb-place" @click="emit('place')" />
    </div>

    <!-- Un champ du type : affichage retouché, règles d'écriture resserrées. -->
    <template v-if="row">
      <label class="wf-field" for="fb-label">
        <span>{{ t.addedFields.label }}</span>
        <InputText id="fb-label" :model-value="tweak.label ?? ''" :placeholder="appLabel ?? ''" :disabled="!canEdit" size="small" data-testid="fb-label" @update:model-value="setDisplay('label', $event ?? '')" />
        <span class="desk-muted wf-help">{{ t.formBuilder.appLabel }} « {{ appLabel }} »</span>
      </label>
      <label class="wf-field" for="fb-description">
        <span>{{ t.formBuilder.description }}</span>
        <InputText id="fb-description" :model-value="tweak.description ?? ''" :placeholder="row.field.description ?? ''" :disabled="!canEdit" size="small" @update:model-value="setDisplay('description', $event ?? '')" />
      </label>

      <h3 class="wf-section">{{ t.formBuilder.rules }}</h3>
      <p v-if="!rules" class="desk-muted wf-help" data-testid="fb-rules-reserved">{{ t.overrideProps.reservedHint }}</p>
      <label class="wf-check" :title="why('mandatory')">
        <Checkbox :model-value="row.mandatory" binary input-id="fb-mandatory" :disabled="!canEdit || !row.can.mandatory" data-testid="fb-mandatory" @update:model-value="setProp('mandatory', $event)" />
        <span>{{ t.overrideProps.mandatory }} <span v-if="why('mandatory')" class="desk-muted wf-help">{{ why('mandatory') }}</span></span>
      </label>
      <label class="wf-check" :title="why('read_only')">
        <Checkbox :model-value="row.readOnly" binary input-id="fb-readonly" :disabled="!canEdit || !row.can.read_only" data-testid="fb-readonly" @update:model-value="setProp('read_only', $event)" />
        <span>{{ t.overrideProps.readOnly }} <span v-if="why('read_only')" class="desk-muted wf-help">{{ why('read_only') }}</span></span>
      </label>
      <label class="wf-check" :title="why('hidden')">
        <Checkbox :model-value="row.hidden" binary input-id="fb-hidden" :disabled="!canEdit || !row.can.hidden" data-testid="fb-hidden" @update:model-value="setProp('hidden', $event)" />
        <span>{{ t.overrideProps.hidden }} <span v-if="why('hidden')" class="desk-muted wf-help">{{ why('hidden') }}</span></span>
      </label>
      <div class="wf-field">
        <span>{{ t.overrideProps.default }}</span>
        <template v-if="row.can.default && canEdit">
          <span v-if="row.default !== undefined && row.default === liveDefault(row.field)" class="props-live">{{ liveLabel(row.field) }}</span>
          <FieldInput v-else :field="inputFor(row.field)" :model-value="row.default ?? null" input-id="fb-default" :doctype="doctype" @update:model-value="setProp('default', $event)" />
          <span class="fb-inline">
            <Button v-if="liveDefault(row.field) && row.default !== liveDefault(row.field)" :label="liveLabel(row.field)" text size="small" @click="setProp('default', liveDefault(row.field))" />
            <Button v-if="row.default !== undefined" :label="t.formBuilder.clearDefault" icon="pi pi-times" text size="small" severity="secondary" @click="setProp('default', undefined)" />
          </span>
          <span v-if="row.default === undefined && row.base.default !== undefined && row.base.default !== null" class="desk-muted wf-help">
            {{ t.overrideProps.definitionDefault }} <CellValue :field="row.field" :value="row.base.default" />
          </span>
        </template>
        <span v-else-if="row.default !== undefined" class="props-live"><CellValue :field="row.field" :value="row.default" /></span>
        <span v-else class="desk-muted wf-help">{{ why('default') ?? t.overrideProps.noDefault }}</span>
      </div>
    </template>

    <!-- Un champ ajouté : sa définition lui appartient. -->
    <template v-else-if="added">
      <label class="wf-field" for="fb-added-label">
        <span>{{ t.addedFields.label }}</span>
        <InputText id="fb-added-label" :model-value="added.label ?? ''" :disabled="!rules" size="small" data-testid="fb-added-label" @update:model-value="setAdded({ label: $event ?? '' })" />
      </label>
      <label class="wf-field" for="fb-added-description">
        <span>{{ t.formBuilder.description }}</span>
        <InputText id="fb-added-description" :model-value="added.description ?? ''" :disabled="!rules" size="small" @update:model-value="setAdded({ description: $event ?? '' })" />
      </label>
      <label v-if="added.fieldtype === 'Select'" class="wf-field" for="fb-added-options">
        <span>{{ t.addedFields.options }}</span>
        <Textarea id="fb-added-options" :model-value="options" :disabled="!rules" rows="4" auto-resize data-testid="fb-added-options" @update:model-value="setAdded({ options: ($event ?? '').split('\n').map((v: string) => v.trim()).filter(Boolean) })" />
      </label>
      <label class="wf-check">
        <Checkbox :model-value="!!added.required" binary input-id="fb-added-required" :disabled="!rules" @update:model-value="setAdded({ required: $event === true })" />
        <span>{{ t.addedFields.required }}</span>
      </label>
      <label class="wf-check">
        <Checkbox :model-value="!!added.in_list_view" binary input-id="fb-added-list" :disabled="!rules" @update:model-value="setAdded({ in_list_view: $event === true })" />
        <span>{{ t.addedFields.inList }}</span>
      </label>
      <p v-if="!rules" class="desk-muted wf-help">{{ t.addedFields.reserved }}</p>
      <Button
        v-if="rules"
        :label="t.formBuilder.removeAdded"
        icon="pi pi-trash"
        severity="danger"
        text
        size="small"
        data-testid="fb-remove-added"
        @click="emit('remove')"
      />
      <p class="desk-muted wf-help">{{ t.addedFields.removed }}</p>
    </template>
  </div>
</template>
