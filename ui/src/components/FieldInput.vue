<script setup lang="ts">
import Checkbox from 'primevue/checkbox'
import DatePicker from 'primevue/datepicker'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Select from 'primevue/select'
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import FileField from '@/components/FileField.vue'
import SecretField from '@/components/SecretField.vue'
import LinkField from '@/components/LinkField.vue'
import { dateFromUtc, utcFromDate } from '@/lib/datetime'
import { allowedDocstatus } from '@/lib/docstatus'
import { canonicalDecimal, normalizeDecimal } from '@/lib/decimal'
import { formatDuration, parseDuration } from '@/lib/duration'
import { normalizeTime } from '@/lib/fieldrules'
import { currencySymbol, datePattern, dateFromIso, isoFromDate } from '@/lib/format'
import { t } from '@/i18n'
import type { FieldMeta } from '@/lib/types'

const props = defineProps<{
  field: FieldMeta
  modelValue: unknown
  inputId: string
  invalid?: boolean
  doctype?: string
  /** DynamicLink : le DocType que le champ de type désigne en ce moment (vide tant qu'on ne l'a pas choisi). */
  dynamicTarget?: string | null
}>()
const emit = defineEmits<{ 'update:modelValue': [value: unknown] }>()

const set = (v: unknown) => emit('update:modelValue', v)
// Le champ Nombre de PrimeVue n'émet `update:modelValue` qu'au blur, à Tab ou à Entrée ; pendant la frappe il n'émet que `input`.
// Sans lui, une condition `depends_on` sur un nombre ne réagirait pas à la frappe et Ctrl+S enverrait l'ancienne valeur.
const options = computed(() => (Array.isArray(props.field.options) ? props.field.options : []))
const target = computed(() => (typeof props.field.options === 'string' ? props.field.options : ''))
const asText = computed(() => (typeof props.modelValue === 'string' ? props.modelValue : ''))
const asNumber = computed(() => (typeof props.modelValue === 'number' ? props.modelValue : null))
/**
 * Decimal : du TEXTE de bout en bout. Un champ numérique passerait par un `number` JavaScript (flottant) ; or le serveur refuse un
 * flottant et la valeur doit rester exacte. À la sortie du champ, une valeur valide prend sa forme canonique (« 12,5 » -> « 12.500 »).
 */
const decimals = computed(() => props.field.decimals ?? (props.field.fieldtype === 'Currency' ? 2 : 0))
function settleDecimal(): void {
  const canon = canonicalDecimal(asText.value, decimals.value)
  if (canon !== null && canon !== asText.value) set(canon)
}
const asDate = computed(() => dateFromIso(typeof props.modelValue === 'string' ? props.modelValue : null))

// Texte formaté : le bon clavier sur un téléphone (e-mail, numérique, adresse web) et la validation du navigateur en plus de la nôtre.
const textType = computed(() => (props.field.format ? ({ email: 'email', phone: 'tel', url: 'url' } as const)[props.field.format] : 'text'))

// Datetime : l'utilisateur lit et saisit l'heure LOCALE, la valeur envoyée est en UTC (lib/datetime.ts).
const asDatetime = computed(() => dateFromUtc(typeof props.modelValue === 'string' ? props.modelValue : null))
const setDatetime = (v: unknown) => set(v instanceof Date ? utcFromDate(new Date(new Date(v).setSeconds(0, 0))) : null)

// Rating : des étoiles cliquables ; recliquer la note choisie l'efface. Flèches gauche et droite au clavier.
const stars = computed(() => props.field.stars ?? 5)
const rating = computed(() => (typeof props.modelValue === 'number' ? props.modelValue : 0))
const rate = (n: number) => set(n === rating.value ? null : n)
const rateBy = (delta: number) => set(Math.min(stars.value, Math.max(0, rating.value + delta)) || null)

// Duration : l'utilisateur écrit « 1h30 » ; le champ garde son TEXTE tant qu'on tape et envoie des SECONDES quand il se lit. Un texte illisible est gardé tel quel
// (le formulaire le signale) plutôt que d'effacer en silence ce qu'on a tapé.
const durationDraft = ref('')
watch(
  () => props.modelValue,
  (v) => {
    if (typeof v === 'number') {
      if (parseDuration(durationDraft.value) !== v) durationDraft.value = formatDuration(v)
    } else if (v === null || v === undefined) {
      if (parseDuration(durationDraft.value) !== null) durationDraft.value = ''
    }
  },
  { immediate: true },
)
const onDuration = (value: string | undefined): void => {
  const text = value ?? ''
  durationDraft.value = text
  set(text.trim() ? (parseDuration(text) ?? text) : null)
}
const settleDuration = (): void => {
  const s = parseDuration(durationDraft.value)
  if (s !== null) durationDraft.value = formatDuration(s)
}

// Texte enrichi : l'éditeur (TipTap) n'est chargé que si un formulaire en a besoin.
const RichTextField = defineAsyncComponent(() => import('@/components/RichTextField.vue'))
const asInt = computed(() => (typeof props.modelValue === 'number' ? props.modelValue : null))

const asColor = computed(() => (typeof props.modelValue === 'string' && /^#[0-9a-f]{6}$/.test(props.modelValue) ? props.modelValue : '#000000'))
</script>

<template>
  <InputText
    v-if="field.fieldtype === 'Text'"
    :id="inputId"
    :model-value="asText"
    :invalid="invalid"
    :type="textType"
    :inputmode="field.format === 'phone' ? 'tel' : undefined"
    :autocomplete="field.format ? 'off' : undefined"
    fluid
    @update:model-value="set($event || null)"
  />

  <Textarea v-else-if="field.fieldtype === 'LongText'" :id="inputId" :model-value="asText" :invalid="invalid" rows="4" auto-resize fluid @update:model-value="set($event || null)" />

  <InputNumber
    v-else-if="field.fieldtype === 'Number'"
    :input-id="inputId"
    :model-value="asNumber"
    :invalid="invalid"
    :use-grouping="false"
    :max-fraction-digits="10"
    locale="fr-FR"
    fluid
    @input="set($event.value ?? null)"
    @update:model-value="set($event ?? null)"
  />

  <InputNumber
    v-else-if="field.fieldtype === 'Int'"
    :input-id="inputId"
    :model-value="asInt"
    :invalid="invalid"
    :use-grouping="false"
    :max-fraction-digits="0"
    locale="fr-FR"
    fluid
    @input="set($event.value ?? null)"
    @update:model-value="set($event ?? null)"
  />

  <InputNumber
    v-else-if="field.fieldtype === 'Percent'"
    :input-id="inputId"
    :model-value="asInt"
    :invalid="invalid"
    :use-grouping="false"
    :max-fraction-digits="10"
    :min="0"
    :max="100"
    suffix=" %"
    locale="fr-FR"
    fluid
    @input="set($event.value ?? null)"
    @update:model-value="set($event ?? null)"
  />

  <InputText
    v-else-if="field.fieldtype === 'Time'"
    :id="inputId"
    :model-value="asText"
    :invalid="invalid"
    type="time"
    step="1"
    fluid
    @update:model-value="set($event ? normalizeTime($event) : null)"
  />

  <InputText
    v-else-if="field.fieldtype === 'Duration'"
    :id="inputId"
    :model-value="durationDraft"
    :invalid="invalid"
    :placeholder="t.form.durationPlaceholder"
    autocomplete="off"
    fluid
    @update:model-value="onDuration"
    @blur="settleDuration"
  />

  <Textarea
    v-else-if="field.fieldtype === 'Code' || field.fieldtype === 'JSON'"
    :id="inputId"
    :model-value="asText"
    :invalid="invalid"
    :rows="field.fieldtype === 'JSON' ? 8 : 10"
    class="code-input"
    spellcheck="false"
    autocapitalize="off"
    :data-language="field.fieldtype === 'Code' ? (typeof field.options === 'string' ? field.options : 'text') : 'json'"
    fluid
    @update:model-value="set($event || null)"
  />

  <RichTextField v-else-if="field.fieldtype === 'TextEditor'" :model-value="modelValue" :input-id="inputId" :invalid="invalid" @update:model-value="set" />

  <SecretField v-else-if="field.fieldtype === 'Password'" :model-value="modelValue" :input-id="inputId" :invalid="invalid" @update:model-value="set" />

  <div v-else-if="field.fieldtype === 'Decimal' || field.fieldtype === 'Currency'" class="amount-input">
    <InputText
      :id="inputId"
      :model-value="asText"
      :invalid="invalid"
      inputmode="decimal"
      autocomplete="off"
      class="decimal-input"
      fluid
      @update:model-value="set($event ? normalizeDecimal($event) : null)"
      @blur="settleDecimal"
    />
    <span v-if="field.fieldtype === 'Currency' && typeof field.options === 'string'" class="amount-unit desk-muted">{{ currencySymbol(field.options) }}</span>
  </div>

  <div v-else-if="field.fieldtype === 'Rating'" :id="inputId" class="rating" role="radiogroup" :aria-label="field.label ?? field.fieldname" tabindex="-1" @keydown.right.prevent="rateBy(1)" @keydown.left.prevent="rateBy(-1)">
    <button
      v-for="n in stars"
      :key="n"
      type="button"
      role="radio"
      :aria-checked="n === rating"
      :aria-label="`${n} / ${stars}`"
      :tabindex="n === (rating || 1) ? 0 : -1"
      :class="['rating-star', { on: n <= rating }]"
      :data-testid="`rating-${n}`"
      @click="rate(n)"
    >
      <i :class="['pi', n <= rating ? 'pi-star-fill' : 'pi-star']" aria-hidden="true" />
    </button>
  </div>

  <div v-else-if="field.fieldtype === 'Color'" class="color-input">
    <input :id="inputId" type="color" :value="asColor" class="color-swatch" :aria-invalid="invalid || undefined" @input="set(($event.target as HTMLInputElement).value)" />
    <code class="desk-muted">{{ asText || '—' }}</code>
    <button v-if="asText" type="button" class="link-button" :aria-label="t.delete" data-testid="color-clear" @click="set(null)">✕</button>
  </div>

  <DatePicker
    v-else-if="field.fieldtype === 'Datetime'"
    :input-id="inputId"
    :model-value="asDatetime"
    :invalid="invalid"
    :date-format="datePattern()"
    show-time
    hour-format="24"
    show-icon
    icon-display="input"
    show-button-bar
    fluid
    @update:model-value="setDatetime"
  />

  <Checkbox v-else-if="field.fieldtype === 'Boolean'" :input-id="inputId" :model-value="modelValue === true" binary :invalid="invalid" @update:model-value="set($event)" />

  <DatePicker
    v-else-if="field.fieldtype === 'Date'"
    :input-id="inputId"
    :model-value="asDate"
    :invalid="invalid"
    :date-format="datePattern()"
    show-icon
    icon-display="input"
    show-button-bar
    fluid
    @update:model-value="set($event instanceof Date ? isoFromDate($event) : null)"
  />

  <Select
    v-else-if="field.fieldtype === 'Select'"
    :input-id="inputId"
    :model-value="typeof modelValue === 'string' ? modelValue : null"
    :options="options"
    :invalid="invalid"
    :show-clear="!field.required"
    fluid
    @update:model-value="set($event ?? null)"
  />

  <FileField v-else-if="(field.fieldtype === 'Image' || field.fieldtype === 'Attach') && doctype" :field="field" :model-value="modelValue" :doctype="doctype" :input-id="inputId" :invalid="invalid" @update:model-value="set" />

  <LinkField v-else-if="field.fieldtype === 'Link'" :input-id="inputId" :model-value="modelValue" :target="target" :invalid="invalid" :statuses="allowedDocstatus(field)" @update:model-value="set" />

  <LinkField
    v-else-if="field.fieldtype === 'DynamicLink' && dynamicTarget"
    :input-id="inputId"
    :model-value="modelValue"
    :target="dynamicTarget"
    :invalid="invalid"
    :statuses="allowedDocstatus(field)"
    @update:model-value="set"
  />
  <InputText v-else-if="field.fieldtype === 'DynamicLink'" :id="inputId" :placeholder="t.form.dynamicFirst" disabled fluid data-testid="dynamic-first" />
</template>
