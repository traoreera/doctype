<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import MultiSelect from 'primevue/multiselect'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { computed, ref, watch } from 'vue'
import FilterBar from '@/components/FilterBar.vue'
import { isPartToWhole, missing, type DeclaredBlock } from '@/lib/blocksedit'
import { isHexTone } from '@/lib/brand'
import { buildFilters, rowsFromFilters, type FilterRow } from '@/lib/filters'
import { t } from '@/i18n'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'
import { isDecimalType } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * Le formulaire d'UN bloc : on y choisit un type de document, un calcul, un regroupement, des filtres : jamais de JSON. Il ne propose que ce
 * que le serveur acceptera (champs du type choisi, calculs compatibles avec le graphique) ; le serveur recontrôle tout à l'enregistrement.
 */
const props = defineProps<{ visible: boolean; block: DeclaredBlock | null; adding: boolean; /** Tableau de bord d'un type : ce type est imposé, on ne le choisit pas. */ doctype?: string }>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; save: [block: DeclaredBlock] }>()

const metaStore = useMeta()
const form = ref<Record<string, any>>({})
const problem = ref<string | null>(null)
const meta = ref<DocTypeMeta | null>(null)
const rows = ref<FilterRow[]>([])
let seq = 0
let ticket = 0

const type = computed(() => props.block?.type ?? 'header')
const title = computed(() => (props.adding ? t.editor.dialog.add : t.editor.dialog.change)(t.editor.kinds[type.value] ?? type.value))

watch(
  () => [props.visible, props.block] as const,
  () => {
    if (!props.visible || !props.block) return
    form.value = JSON.parse(JSON.stringify(props.block))
    problem.value = null
    rows.value = []
    void loadMeta(true)
  },
  { immediate: true },
)

async function loadMeta(initial = false): Promise<void> {
  const mine = ++ticket
  const doctype = form.value.doctype as string | undefined
  meta.value = null
  if (!doctype) return
  try {
    await metaStore.loadSummaries().catch(() => [])
    const m = await metaStore.get(doctype)
    if (mine !== ticket) return
    meta.value = m
    if (initial && form.value.filters) rows.value = rowsFromFilters(form.value.filters, m.fields, () => ++seq).rows
  } catch {
    /* illisible : les listes de champs restent vides, le serveur dira ce qu'il en pense */
  }
}

function changeDoctype(name: string): void {
  form.value.doctype = name
  // Les champs, filtres et regroupements appartiennent à l'ancien type : on ne garde rien de ce qui ne l'est plus.
  delete form.value.field
  delete form.value.filters
  if (type.value === 'chart') form.value.group_by = ''
  rows.value = []
  void loadMeta()
}

const doctypes = computed(() => (metaStore.summaries ?? []).map((d) => ({ value: d.name, label: d.label || d.name })))
const fields = computed<FieldMeta[]>(() => (meta.value?.fields ?? []).filter((f) => !f.hidden))
const numeric = computed(() => fields.value.filter((f) => f.fieldtype === 'Number' || isDecimalType(f.fieldtype)).map((f) => ({ value: f.fieldname, label: f.label ?? f.fieldname })))
const dated = (name: string): boolean => name === 'creation' || name === 'modified' || ['Date', 'Datetime'].includes(fields.value.find((f) => f.fieldname === name)?.fieldtype ?? '')
const groupable = computed(() => [
  ...fields.value.filter((f) => ['Select', 'Link', 'Boolean', 'Date', 'Datetime'].includes(f.fieldtype)).map((f) => ({ value: f.fieldname, label: f.label ?? f.fieldname })),
  ...(meta.value?.submittable ? [{ value: 'docstatus', label: t.editor.dialog.docstatus }] : []),
  { value: 'owner', label: t.editor.dialog.owner },
  { value: 'creation', label: t.editor.dialog.creation },
  { value: 'modified', label: t.editor.dialog.modified },
])
const functions = computed(() => (type.value === 'chart' && isPartToWhole(form.value.kind) ? ['count', 'sum'] : ['count', 'sum', 'avg', 'min', 'max']).map((v) => ({ value: v, label: t.editor.functions[v]! })))
const kinds = Object.entries(t.charts.kinds).map(([value, label]) => ({ value, label }))
const buckets = Object.entries(t.editor.buckets).map(([value, label]) => ({ value, label }))
const CUSTOM = 'custom'
// Une teinte de la palette, ou « Autre couleur » (un `#rrggbb` libre). Un graphique peut aussi garder sa teinte par défaut.
const colorChoices = computed(() => [
  ...(type.value === 'chart' ? [{ value: '', label: t.editor.dialog.colorDefault }] : []),
  ...Object.entries(t.editor.colors).map(([value, label]) => ({ value, label })),
  { value: CUSTOM, label: t.editor.dialog.colorCustom },
])
const colorKey = computed(() => (isHexTone(form.value.color) ? CUSTOM : (form.value.color ?? (type.value === 'number_card' ? 'blue' : ''))))
const hasColor = computed(() => type.value === 'number_card' || (type.value === 'chart' && (form.value.kind === 'bar' || form.value.kind === 'line')))
function pickColor(key: string): void {
  if (key === CUSTOM) form.value.color = isHexTone(form.value.color) ? form.value.color : '#2a78d6'
  else if (key) form.value.color = key
  else delete form.value.color
}
const setCustomColor = (e: Event): void => void (form.value.color = (e.target as HTMLInputElement).value.toLowerCase())
const showBucket = computed(() => type.value === 'chart' && dated(form.value.group_by))
const measured = computed(() => type.value === 'number_card' || type.value === 'chart')
const filtered = computed(() => measured.value || type.value === 'quick_list')

function changeFunction(fn: string): void {
  form.value.function = fn
  if (fn === 'count') delete form.value.field
}

function changeKind(kind: string): void {
  form.value.kind = kind
  if (kind !== 'bar' && kind !== 'line') delete form.value.color // un camembert garde la palette : le serveur refuserait une couleur
  // Une moyenne ou un extremum ne dit pas « la part de chacun » : on revient au nombre plutôt que d'envoyer ce que le serveur refuse.
  if (isPartToWhole(kind) && !['count', 'sum'].includes(form.value.function)) changeFunction('count')
}

function changeGroup(name: string): void {
  form.value.group_by = name
  if (dated(name)) {
    form.value.bucket ??= 'month'
    form.value.periods ??= 12
  } else {
    delete form.value.bucket
    delete form.value.periods
  }
}

const linkChoices = computed({
  get: () => (form.value.links ?? []).map((l: { doctype: string }) => l.doctype),
  set: (names: string[]) => {
    const old = new Map<string, string | undefined>((form.value.links ?? []).map((l: { doctype: string; label?: string }) => [l.doctype, l.label]))
    form.value.links = names.map((d) => ({ doctype: d, ...(old.get(d) ? { label: old.get(d) } : {}) }))
  },
})

function submit(): void {
  const out = { ...form.value } as Record<string, any>
  if (filtered.value) {
    const f = buildFilters(rows.value, fields.value)
    if (Object.keys(f).length) out.filters = f
    else delete out.filters
  }
  if (type.value === 'shortcut' && !out.label) delete out.label // un libellé vide = celui du type
  const lack = missing(out as DeclaredBlock)
  if (lack) {
    problem.value = t.editor.dialog.missing[lack] ?? lack
    return
  }
  emit('save', out as DeclaredBlock)
  emit('update:visible', false)
}
</script>

<template>
  <Dialog :visible="visible" modal :header="title" :style="{ width: '640px', maxWidth: '95vw' }" data-testid="block-dialog" @update:visible="emit('update:visible', $event)">
    <form class="block-form" @submit.prevent="submit">
      <template v-if="type === 'header'">
        <label class="bf-field"><span>{{ t.editor.dialog.text }}</span><InputText v-model="form.text" maxlength="80" data-testid="bf-text" /></label>
      </template>

      <template v-else-if="type === 'paragraph'">
        <label class="bf-field"><span>{{ t.editor.dialog.text }}</span><Textarea v-model="form.text" rows="3" auto-resize maxlength="500" data-testid="bf-text" /></label>
      </template>

      <template v-else-if="type === 'shortcut'">
        <label class="bf-field">
          <span>{{ t.editor.dialog.doctype }}</span>
          <Select :model-value="form.doctype" :options="doctypes" option-label="label" option-value="value" filter :placeholder="t.editor.dialog.choose" data-testid="bf-doctype" @update:model-value="changeDoctype" />
        </label>
        <label class="bf-field"><span>{{ t.editor.dialog.shortcutLabel }}</span><InputText v-model="form.label" maxlength="80" data-testid="bf-label" /></label>
        <label class="bf-check"><Checkbox v-model="form.new" binary input-id="bf-new" data-testid="bf-new" /><span>{{ t.editor.dialog.shortcutNew }}</span></label>
      </template>

      <template v-else-if="type === 'card'">
        <label class="bf-field"><span>{{ t.editor.dialog.label }}</span><InputText v-model="form.title" maxlength="80" data-testid="bf-label" /></label>
        <label class="bf-field">
          <span>{{ t.editor.dialog.links }}</span>
          <MultiSelect v-model="linkChoices" :options="doctypes" option-label="label" option-value="value" filter display="chip" :placeholder="t.editor.dialog.choose" data-testid="bf-links" />
        </label>
      </template>

      <template v-else>
        <label class="bf-field"><span>{{ t.editor.dialog.label }}</span><InputText v-model="form.label" maxlength="80" data-testid="bf-label" /></label>
        <label v-if="!doctype" class="bf-field">
          <span>{{ t.editor.dialog.doctype }}</span>
          <Select :model-value="form.doctype" :options="doctypes" option-label="label" option-value="value" filter :placeholder="t.editor.dialog.choose" data-testid="bf-doctype" @update:model-value="changeDoctype" />
        </label>

        <template v-if="type === 'chart'">
          <label class="bf-field">
            <span>{{ t.editor.dialog.kind }}</span>
            <Select :model-value="form.kind" :options="kinds" option-label="label" option-value="value" data-testid="bf-kind" @update:model-value="changeKind" />
          </label>
          <label class="bf-field">
            <span>{{ t.editor.dialog.groupBy }}</span>
            <Select :model-value="form.group_by" :options="groupable" option-label="label" option-value="value" :disabled="!form.doctype" :placeholder="t.editor.dialog.choose" data-testid="bf-group" @update:model-value="changeGroup" />
          </label>
          <div v-if="showBucket" class="bf-row">
            <label class="bf-field">
              <span>{{ t.editor.dialog.bucket }}</span>
              <Select v-model="form.bucket" :options="buckets" option-label="label" option-value="value" data-testid="bf-bucket" />
            </label>
            <label class="bf-field">
              <span>{{ t.editor.dialog.periods }}</span>
              <InputNumber v-model="form.periods" :min="1" :max="366" show-buttons data-testid="bf-periods" />
            </label>
          </div>
        </template>

        <template v-if="measured">
          <div class="bf-row">
            <label class="bf-field">
              <span>{{ t.editor.dialog.function }}</span>
              <Select :model-value="form.function" :options="functions" option-label="label" option-value="value" data-testid="bf-function" @update:model-value="changeFunction" />
            </label>
            <label v-if="form.function !== 'count'" class="bf-field">
              <span>{{ t.editor.dialog.field }}</span>
              <Select v-model="form.field" :options="numeric" option-label="label" option-value="value" :disabled="!form.doctype" :placeholder="t.editor.dialog.choose" data-testid="bf-field" />
            </label>
          </div>
        </template>

        <div v-if="hasColor" class="bf-field">
          <span>{{ t.editor.dialog.color }}</span>
          <div class="bf-color">
            <Select :model-value="colorKey" :options="colorChoices" option-label="label" option-value="value" data-testid="bf-color" @update:model-value="pickColor" />
            <input v-if="colorKey === 'custom'" type="color" class="bf-color-input" :value="form.color" :aria-label="t.editor.dialog.colorCustom" data-testid="bf-color-custom" @input="setCustomColor" />
          </div>
        </div>

        <label v-if="type === 'chart' || type === 'quick_list'" class="bf-field">
          <span>{{ t.editor.dialog.limit }}</span>
          <InputNumber v-model="form.limit" :min="1" :max="type === 'chart' ? 50 : 10" show-buttons data-testid="bf-limit" />
        </label>

        <div v-if="filtered && fields.length" class="bf-field">
          <span>{{ t.editor.dialog.filters }}</span>
          <FilterBar v-model:rows="rows" :fields="fields" @apply="() => {}" />
        </div>
      </template>

      <Message v-if="problem" severity="error" class="banner-error" data-testid="bf-problem">{{ problem }}</Message>
      <button type="submit" hidden />
    </form>
    <template #footer>
      <Button :label="t.cancel" severity="secondary" text @click="emit('update:visible', false)" />
      <Button :label="t.editor.dialog.validate" icon="pi pi-check" data-testid="bf-validate" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.block-form {
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

.bf-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.bf-color {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.bf-color > :first-child {
  flex: 1;
}

.bf-color-input {
  width: 2.75rem;
  height: 2.25rem;
  padding: 0.15rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md);
  background: none;
  cursor: pointer;
}

.bf-check {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}
</style>
