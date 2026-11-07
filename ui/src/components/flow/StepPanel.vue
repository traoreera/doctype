<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { computed, provide, ref, watch } from 'vue'
import { t } from '@/i18n'
import { ACTIONS, actionOf, withAction, type ActionId } from '@/lib/flowactions'
import { AI_SERVICES, OPERATORS, UNARY_OPERATORS, outletLabel, outletsOf, type FlowStep, type Operator } from '@/lib/flowgraph'
import { EMPTY_CATALOG, FLOW_VALUE_CONTEXT, fieldtypeOf, nodeOf, type FlowCatalog } from '@/lib/flowcatalog'
import { readValue } from '@/lib/flowvalues'
import type { DocTypeMeta, DocTypeSummary, FieldMeta } from '@/lib/types'
import ValueField from './ValueField.vue'

/**
 * Les propriétés de l'étape choisie : un formulaire par type d'étape, en français, SANS JSON ni accolades.
 *
 * Une étape « action » se règle par un GESTE (créer un document, le soumettre, assigner une tâche…) pris dans le catalogue fermé de
 * `lib/flowactions` : l'écran demande ce que ce geste demande, et rien d'autre. Les valeurs passent par `ValueField` (une liste,
 * jamais une expression à taper). Une étape qui appelle un autre plugin est montrée sans être réglée ici : on ne l'abîme pas.
 *
 * Le panneau ne modifie jamais le modèle : il ÉMET le changement (`patch`, les clés de la définition ; une clé à `undefined` la
 * retire), et la page l'applique avec `patchStep`. Les fonctions de `lib/flowgraph` restent le seul endroit où une définition change.
 */
const props = defineProps<{
  step: FlowStep
  /** Les identifiants des autres étapes : ce qu'une sortie peut viser. */
  others: string[]
  /** Les étapes, AVANT celle-ci, dont on peut reprendre le document (lire, créer). */
  sources: string[]
  /** Les champs du document de chaque étape source, quand son type est connu (« le total de la facture lue »). */
  stepFields: Record<string, { value: string; label: string }[]>
  /** Les types de document de l'organisation (`/meta`), pour les listes. */
  doctypes: DocTypeSummary[]
  /** La définition du type visé par l'étape, quand il est connu : ses champs se proposent au lieu de se taper. */
  target: DocTypeMeta | null
  /** Ce que les plugins déclarent (`lib/flowcatalog`) : une étape qui appelle l'un de leurs nœuds se règle par SON formulaire. */
  catalog?: FlowCatalog
  /** Les résultats des nœuds de plugin AVANT celle-ci, qu'une valeur peut reprendre (« l'identifiant du message envoyé »). */
  outputs?: { step: string; path: string; label: string }[]
  /** Les clés du déclencheur quand c'est un événement de plugin (ses champs), sinon `undefined` (celles du moteur). */
  triggerKeys?: { value: string; label: string }[]
  disabled?: boolean
}>()
const emit = defineEmits<{
  (e: 'patch', patch: Record<string, unknown>): void
  (e: 'rename', id: string): void
  (e: 'connect', payload: { key: string; target: string | null }): void
  (e: 'remove'): void
  (e: 'add-branch'): void
  (e: 'remove-branch', index: number): void
  (e: 'add-case', value: string): void
  (e: 'rename-case', payload: { from: string; to: string }): void
  (e: 'remove-case', value: string): void
}>()

const raw = computed(() => props.step.raw)
provide(FLOW_VALUE_CONTEXT, { triggerKeys: computed(() => props.triggerKeys), outputs: computed(() => props.outputs ?? []) })
/** Le nœud de plugin que l'étape appelle, s'il est déclaré : il passe AVANT le message « action étrangère ». */
const pluginNode = computed(() => (props.step.kind === 'action' ? nodeOf(props.catalog ?? EMPTY_CATALOG, raw.value) : undefined))
const str = (key: string): string => (typeof raw.value[key] === 'string' ? (raw.value[key] as string) : '')
const num = (key: string): number | null => (typeof raw.value[key] === 'number' ? (raw.value[key] as number) : null)
const condition = computed(() => (raw.value.condition && typeof raw.value.condition === 'object' ? (raw.value.condition as Record<string, unknown>) : {}))
const operator = computed(() => (typeof condition.value.operator === 'string' ? (condition.value.operator as Operator) : '=='))
const branches = computed(() => (Array.isArray(raw.value.branches) ? (raw.value.branches as unknown[]) : []))
const cases = computed(() => Object.entries(raw.value.cases && typeof raw.value.cases === 'object' ? (raw.value.cases as Record<string, unknown>) : {}))
const outlets = computed(() => outletsOf(props.step))
const targetOptions = computed(() => [{ value: null, label: t.flowBuilder.endOfFlow }, ...props.others.map((id) => ({ value: id as string | null, label: id }))])
const doctypeOptions = computed(() => props.doctypes.map((d) => ({ value: d.name, label: d.label })))
const operatorOptions = OPERATORS.map((o) => ({ value: o, label: t.flowBuilder.operators[o] ?? o }))
const serviceOptions = AI_SERVICES.map((s) => ({ value: s, label: t.flowBuilder.services[s] ?? s }))

function setCondition(patch: Record<string, unknown>): void {
  emit('patch', { condition: { ...condition.value, ...patch } })
}

// ── Une étape « action » : un geste du catalogue ───────────────────────────────────────────────────────────────
const payload = computed(() => (raw.value.payload && typeof raw.value.payload === 'object' ? (raw.value.payload as Record<string, unknown>) : {}))
const gesture = computed(() => actionOf(raw.value))
const asks = computed(() => (gesture.value ? ACTIONS.find((a) => a.id === gesture.value)!.asks : []))
const gestureOptions = ACTIONS.map((a) => ({ value: a.id, label: t.flowBuilder.gestures[a.id].label, hint: t.flowBuilder.gestures[a.id].hint }))
const gestureHint = computed(() => (gesture.value ? t.flowBuilder.gestures[gesture.value].hint : ''))

function setGesture(id: ActionId): void {
  const next = withAction(raw.value, id)
  emit('patch', { plugin: next.plugin, action: next.action, payload: next.payload })
}
function setPayload(key: string, value: unknown): void {
  emit('patch', { payload: { ...payload.value, [key]: value } })
}
/** Un champ de nœud de plugin laissé vide n'est pas envoyé (`undefined` retire la clé) : l'action garde son défaut. */
function setNodeField(key: string, value: unknown): void {
  const next = { ...payload.value, [key]: value }
  if (value === '' || value === undefined) delete next[key]
  emit('patch', { payload: next })
}

/** Le type visé : un nom choisi dans la liste, ou celui du document déclencheur (le cas courant, et rien à saisir). */
const doctypeValue = computed(() => (typeof payload.value.doctype === 'string' ? payload.value.doctype : ''))
const doctypeIsToken = computed(() => readValue(doctypeValue.value).kind !== 'fixed')

/** Les champs qu'on peut remplir : ceux du type visé, sans les tables, les mots de passe ni ce que le serveur écrit lui-même. */
const WRITABLE = new Set(['Text', 'LongText', 'Number', 'Int', 'Percent', 'Decimal', 'Currency', 'Date', 'Datetime', 'Time', 'Boolean', 'Select', 'Link', 'Color', 'Rating', 'Duration'])
const fields = computed<FieldMeta[]>(() => (props.target?.fields ?? []).filter((f) => WRITABLE.has(f.fieldtype) && !f.read_only && !f.compute && !f.fetch_from))
const fieldOf = (name: string): FieldMeta | undefined => fields.value.find((f) => f.fieldname === name)

/** Les valeurs à écrire : une ligne par champ. */
const data = computed(() => (payload.value.data && typeof payload.value.data === 'object' ? (payload.value.data as Record<string, unknown>) : {}))
const rows = computed(() => Object.entries(data.value))
const freeFields = computed(() => fields.value.filter((f) => !(f.fieldname in data.value)))
const newField = ref('')

function addRow(): void {
  const name = newField.value
  if (!name || name in data.value) return
  setPayload('data', { ...data.value, [name]: '' })
  newField.value = ''
}
function setRow(name: string, value: unknown): void {
  setPayload('data', { ...data.value, [name]: value })
}
function removeRow(name: string): void {
  const next = { ...data.value }
  delete next[name]
  setPayload('data', next)
}

// ── L'identifiant : renommé seulement quand il est valable (les flèches qui le citent suivent, côté page) ──
const id = ref(props.step.id)
watch(() => props.step.id, (next) => (id.value = next))
function commitId(): void {
  const next = id.value.trim()
  if (next === props.step.id) return
  if (!/^[A-Za-z0-9_-]{1,60}$/.test(next) || props.others.includes(next)) {
    id.value = props.step.id // refusé : on remet ce qui est
    return
  }
  emit('rename', next)
}

// ── Un cas d'aiguillage à nommer ──
const newCase = ref('')
function addCase(): void {
  const value = newCase.value.trim()
  if (!value) return
  emit('add-case', value)
  newCase.value = ''
}
</script>

<template>
  <div class="fl-panel-body" data-testid="fl-step-panel">
    <p class="wf-kicker">{{ props.step.kind ? t.flowBuilder.kinds[props.step.kind].label : t.flowBuilder.unknownKind }}</p>
    <label class="wf-field" for="fl-step-id">
      <span>{{ t.flowBuilder.fields.id }}</span>
      <InputText id="fl-step-id" v-model="id" :disabled="disabled" size="small" data-testid="fl-step-id" @blur="commitId" @keyup.enter="commitId" />
    </label>
    <label class="wf-field" for="fl-step-description">
      <span>{{ t.automations.description }}</span>
      <InputText
        id="fl-step-description"
        :model-value="str('description')"
        :disabled="disabled"
        size="small"
        data-testid="fl-step-description"
        @update:model-value="emit('patch', { description: $event || undefined })"
      />
    </label>

    <!-- Action : un geste du catalogue, et ce que ce geste demande. -->
    <template v-if="props.step.kind === 'action'">
      <!-- Un nœud déclaré par un plugin : son formulaire, champ par champ, et ce que son résultat offre à la suite. -->
      <template v-if="pluginNode">
        <h2 class="wf-panel-title" data-testid="fl-node-title">{{ pluginNode.label }}</h2>
        <p v-if="pluginNode.description" class="desk-muted wf-help">{{ pluginNode.description }}</p>
        <ValueField
          v-for="f in pluginNode.fields"
          :key="f.name"
          :model-value="payload[f.name]"
          :label="f.required ? `${f.label} *` : f.label"
          :fieldtype="fieldtypeOf(f.type)"
          :options="f.options"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          :hint="f.description"
          :testid="`fl-node-field-${f.name}`"
          @update:model-value="setNodeField(f.name, $event)"
        />
        <p v-if="pluginNode.outputs.length" class="desk-muted wf-help" data-testid="fl-node-outputs">
          {{ t.flowBuilder.nodeOutputs(pluginNode.outputs.map((o) => o.label).join(', ')) }}
        </p>
      </template>

      <template v-else-if="gesture">
        <label class="wf-field" for="fl-gesture">
          <span>{{ t.flowBuilder.fields.gesture }}</span>
          <Select
            input-id="fl-gesture"
            :model-value="gesture"
            :options="gestureOptions"
            option-label="label"
            option-value="value"
            :disabled="disabled"
            size="small"
            data-testid="fl-gesture"
            @update:model-value="setGesture($event)"
          />
          <span class="desk-muted wf-help">{{ gestureHint }}</span>
        </label>

        <label v-if="asks.includes('doctype')" class="wf-field" for="fl-doctype">
          <span>{{ t.flowBuilder.fields.doctype }}</span>
          <Select
            v-if="!doctypeIsToken"
            input-id="fl-doctype"
            :model-value="doctypeValue"
            :options="doctypeOptions"
            option-label="label"
            option-value="value"
            :disabled="disabled"
            size="small"
            filter
            :placeholder="t.flowBuilder.fields.pickDoctype"
            data-testid="fl-doctype"
            @update:model-value="setPayload('doctype', $event)"
          />
          <template v-else>
            <InputText :model-value="t.flowBuilder.fields.sameAsTrigger" disabled size="small" data-testid="fl-doctype" />
            <Button :label="t.flowBuilder.fields.pickAnother" text size="small" :disabled="disabled" @click="setPayload('doctype', '')" />
          </template>
        </label>

        <ValueField
          v-if="asks.includes('target')"
          :model-value="payload.name"
          :label="t.flowBuilder.fields.target"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          :hint="t.flowBuilder.fields.targetHint"
          testid="fl-target"
          @update:model-value="setPayload('name', $event)"
        />

        <ValueField
          v-if="asks.includes('assignee')"
          :model-value="payload.assignee"
          :label="t.flowBuilder.fields.assignee"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          :hint="t.flowBuilder.fields.assigneeHint"
          testid="fl-assignee"
          @update:model-value="setPayload('assignee', $event)"
        />
        <ValueField
          v-if="asks.includes('description')"
          :model-value="payload.description"
          :label="t.flowBuilder.fields.taskDescription"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          testid="fl-task-description"
          @update:model-value="setPayload('description', $event || undefined)"
        />
        <ValueField
          v-if="asks.includes('due_date')"
          :model-value="payload.due_date"
          :label="t.flowBuilder.fields.dueDate"
          fieldtype="Date"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          testid="fl-due-date"
          @update:model-value="setPayload('due_date', $event || undefined)"
        />
        <ValueField
          v-if="asks.includes('content')"
          :model-value="payload.content"
          :label="t.flowBuilder.fields.content"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          testid="fl-content"
          @update:model-value="setPayload('content', $event)"
        />
        <ValueField
          v-if="asks.includes('transition')"
          :model-value="payload.action"
          :label="t.flowBuilder.fields.transition"
          :options="(target?.workflow?.transitions ?? []).map((x) => x.action)"
          :steps="sources"
          :step-fields="stepFields"
          :disabled="disabled"
          testid="fl-transition"
          @update:model-value="setPayload('action', $event)"
        />

        <!-- Ce que l'étape écrit : une ligne par champ, la valeur prise dans une liste. -->
        <template v-if="asks.includes('data')">
          <h3 class="wf-section">{{ t.flowBuilder.fields.data }}</h3>
          <p v-if="doctypeIsToken || !target" class="desk-muted wf-help" data-testid="fl-data-unknown">{{ t.flowBuilder.fields.dataUnknown }}</p>
          <template v-for="[name, value] in rows" :key="name">
            <div class="fl-row">
              <ValueField
                :model-value="value"
                :label="fieldOf(name)?.label ?? name"
                :fieldtype="fieldOf(name)?.fieldtype"
                :options="Array.isArray(fieldOf(name)?.options) ? (fieldOf(name)!.options as string[]) : undefined"
                :steps="sources"
                :step-fields="stepFields"
                :disabled="disabled"
                :testid="`fl-data-${name}`"
                @update:model-value="setRow(name, $event)"
              />
              <Button
                icon="pi pi-trash"
                text
                rounded
                size="small"
                severity="danger"
                :disabled="disabled"
                :aria-label="`${t.flowBuilder.fields.removeValue} ${name}`"
                :data-testid="`fl-data-remove-${name}`"
                @click="removeRow(name)"
              />
            </div>
          </template>
          <div v-if="freeFields.length" class="fl-case">
            <Select
              v-model="newField"
              :options="freeFields.map((f) => ({ value: f.fieldname, label: f.label ?? f.fieldname }))"
              option-label="label"
              option-value="value"
              :disabled="disabled"
              size="small"
              filter
              :placeholder="t.flowBuilder.fields.addValue"
              data-testid="fl-new-field"
            />
            <Button icon="pi pi-plus" text rounded size="small" :disabled="disabled || !newField" :aria-label="t.flowBuilder.fields.addValue" data-testid="fl-add-field" @click="addRow" />
          </div>
        </template>
      </template>

      <!-- Une action d'un autre plugin : on la montre, on n'y touche pas. -->
      <Message v-else severity="secondary" :closable="false" data-testid="fl-foreign-action">
        {{ t.flowBuilder.foreignAction(`${str('plugin')}.${str('action')}`) }}
      </Message>
    </template>

    <!-- Condition : la comparaison, puis où vont « vrai » et « faux » (les deux sorties ci-dessous). -->
    <template v-else-if="props.step.kind === 'condition'">
      <ValueField
        :model-value="condition.left"
        :label="t.flowBuilder.fields.left"
        :steps="sources"
        :step-fields="stepFields"
        :disabled="disabled"
        testid="fl-cond-left"
        @update:model-value="setCondition({ left: $event })"
      />
      <label class="wf-field" for="fl-cond-op">
        <span>{{ t.flowBuilder.fields.operator }}</span>
        <Select
          input-id="fl-cond-op"
          :model-value="operator"
          :options="operatorOptions"
          option-label="label"
          option-value="value"
          :disabled="disabled"
          size="small"
          data-testid="fl-cond-op"
          @update:model-value="setCondition({ operator: $event })"
        />
      </label>
      <ValueField
        v-if="!UNARY_OPERATORS.includes(operator)"
        :model-value="condition.right"
        :label="t.flowBuilder.fields.right"
        :steps="sources"
        :step-fields="stepFields"
        :disabled="disabled"
        testid="fl-cond-right"
        @update:model-value="setCondition({ right: $event })"
      />
    </template>

    <!-- Aiguillage : la valeur regardée, et un chemin par cas attendu. -->
    <template v-else-if="props.step.kind === 'switch'">
      <ValueField
        :model-value="raw.expression"
        :label="t.flowBuilder.fields.expression"
        :steps="sources"
        :step-fields="stepFields"
        :disabled="disabled"
        testid="fl-switch-expr"
        @update:model-value="emit('patch', { expression: $event })"
      />
      <h3 class="wf-section">{{ t.flowBuilder.fields.cases }}</h3>
      <div v-for="[value] in cases" :key="value" class="fl-case">
        <InputText
          :model-value="value"
          :disabled="disabled"
          size="small"
          :aria-label="t.flowBuilder.fields.caseValue"
          @blur="emit('rename-case', { from: value, to: ($event.target as HTMLInputElement).value.trim() })"
        />
        <Button icon="pi pi-trash" text rounded size="small" severity="danger" :disabled="disabled" :aria-label="t.flowBuilder.fields.removeCase" @click="emit('remove-case', value)" />
      </div>
      <div class="fl-case">
        <InputText v-model="newCase" :disabled="disabled" size="small" :placeholder="t.flowBuilder.fields.newCase" data-testid="fl-new-case" @keyup.enter="addCase" />
        <Button icon="pi pi-plus" text rounded size="small" :disabled="disabled" :aria-label="t.flowBuilder.fields.addCase" data-testid="fl-add-case" @click="addCase" />
      </div>
    </template>

    <!-- Parallèle : combien de branches, et faut-il les attendre toutes. -->
    <template v-else-if="props.step.kind === 'parallel'">
      <label class="wf-check">
        <Checkbox :model-value="raw.wait_all !== false" binary :disabled="disabled" input-id="fl-wait-all" data-testid="fl-wait-all" @update:model-value="emit('patch', { wait_all: $event === true })" />
        <span>{{ t.flowBuilder.fields.waitAll }} <span class="desk-muted wf-help">{{ t.flowBuilder.fields.waitAllHint }}</span></span>
      </label>
      <h3 class="wf-section">{{ t.flowBuilder.fields.branches }}</h3>
      <div v-for="(_, i) in branches" :key="i" class="fl-case">
        <span>{{ t.flowBuilder.outlets.branch(String(i + 1)) }}</span>
        <Button
          icon="pi pi-trash"
          text
          rounded
          size="small"
          severity="danger"
          :disabled="disabled || branches.length <= 1"
          :aria-label="t.flowBuilder.fields.removeBranch"
          @click="emit('remove-branch', i)"
        />
      </div>
      <Button :label="t.flowBuilder.fields.addBranch" icon="pi pi-plus" text size="small" :disabled="disabled" data-testid="fl-add-branch" @click="emit('add-branch')" />
    </template>

    <!-- Pour chaque : la liste à parcourir. -->
    <template v-else-if="props.step.kind === 'foreach'">
      <ValueField
        :model-value="raw.items"
        :label="t.flowBuilder.fields.items"
        :steps="sources"
        :step-fields="stepFields"
        :disabled="disabled"
        :hint="t.flowBuilder.fields.itemsHint"
        testid="fl-items"
        @update:model-value="emit('patch', { items: $event })"
      />
      <label class="wf-check">
        <Checkbox :model-value="raw.parallel === true" binary :disabled="disabled" input-id="fl-parallel" @update:model-value="emit('patch', { parallel: $event === true })" />
        <span>{{ t.flowBuilder.fields.loopParallel }}</span>
      </label>
    </template>

    <!-- Attente : un délai. -->
    <template v-else-if="props.step.kind === 'wait'">
      <label class="wf-field" for="fl-delay">
        <span>{{ t.flowBuilder.fields.delay }}</span>
        <InputText
          id="fl-delay"
          :model-value="num('delay_seconds') === null ? '' : String(num('delay_seconds'))"
          :disabled="disabled"
          size="small"
          inputmode="numeric"
          data-testid="fl-delay"
          @update:model-value="emit('patch', { delay_seconds: String($event ?? '').trim() && Number.isFinite(Number($event)) ? Number($event) : undefined })"
        />
        <span class="desk-muted wf-help">{{ t.flowBuilder.fields.delayHint }}</span>
      </label>
    </template>

    <!-- IA : le service et la consigne, en langage ordinaire. -->
    <template v-else-if="props.step.kind === 'ai'">
      <label class="wf-field" for="fl-ai-service">
        <span>{{ t.flowBuilder.fields.service }}</span>
        <Select
          input-id="fl-ai-service"
          :model-value="str('service') || 'summarize'"
          :options="serviceOptions"
          option-label="label"
          option-value="value"
          :disabled="disabled"
          size="small"
          @update:model-value="emit('patch', { service: $event })"
        />
      </label>
      <label class="wf-field" for="fl-prompt">
        <span>{{ t.flowBuilder.fields.prompt }}</span>
        <Textarea id="fl-prompt" :model-value="str('prompt')" :disabled="disabled" rows="4" data-testid="fl-prompt" @update:model-value="emit('patch', { prompt: $event ?? '' })" />
      </label>
    </template>

    <!-- Un type d'étape que l'écran ne règle pas (venu d'ailleurs) : gardé intact. -->
    <Message v-else severity="secondary" :closable="false" data-testid="fl-unknown-kind">{{ t.flowBuilder.unknownHint }}</Message>

    <!-- Les sorties : la même chose que tirer une poignée, au clavier. -->
    <template v-if="outlets.length">
      <h3 class="wf-section">{{ t.flowBuilder.outletsTitle }}</h3>
      <label v-for="o in outlets" :key="o.key" class="wf-field" :for="`fl-outlet-${o.key}`">
        <span>{{ outletLabel(o) }}</span>
        <Select
          :input-id="`fl-outlet-${o.key}`"
          :model-value="o.target"
          :options="targetOptions"
          option-label="label"
          option-value="value"
          :disabled="disabled"
          size="small"
          :data-testid="`fl-outlet-${o.key}`"
          @update:model-value="emit('connect', { key: o.key, target: $event ?? null })"
        />
      </label>
    </template>

    <div class="fl-panel-foot">
      <Button :label="t.flowBuilder.removeStep" icon="pi pi-trash" severity="danger" text size="small" :disabled="disabled" data-testid="fl-remove-step" @click="emit('remove')" />
    </div>
  </div>
</template>
