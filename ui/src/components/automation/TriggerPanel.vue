<script setup lang="ts">
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import SelectButton from 'primevue/selectbutton'
import Select from 'primevue/select'
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import { DOC_EVENTS, docEventName, parseDocEvent, type DocEvent, type FlowTrigger, type TriggerType } from '@/lib/automation'
import type { CatalogEvent } from '@/lib/xflow'

/**
 * Ce qui lance l'automatisation. Le cas courant d'un Desk — « quand un document Facture est soumis » — se choisit en deux listes
 * (le type, ce qui lui arrive) et devient l'événement que le moteur émet (`doctype.<Type>.after_<quoi>`) ; tout autre événement du
 * catalogue de xflow reste possible, tapé.
 */
const props = defineProps<{ trigger: FlowTrigger; doctypes: { name: string; label: string }[]; events: CatalogEvent[]; canEdit: boolean }>()
const emit = defineEmits<{ (e: 'patch', value: Partial<FlowTrigger>): void }>()

const TYPES = (['manual', 'event', 'schedule', 'webhook'] as TriggerType[]).map((value) => ({ value, label: t.automations.trigger[value] }))
const EVENTS = DOC_EVENTS.map((value) => ({ value, label: t.automations.docEvents[value] }))
const parsed = computed(() => parseDocEvent(props.trigger.event_name))
const kind = ref<'doc' | 'other'>(props.trigger.event_name && !parsed.value ? 'other' : 'doc')
watch(() => props.trigger.event_name, () => {
  if (props.trigger.event_name && !parsed.value) kind.value = 'other'
})
const KINDS = (['doc', 'other'] as const).map((value) => ({ value, label: t.automations.eventKind[value] }))

function setType(type: TriggerType): void {
  // Ce qui n'a pas de sens pour le nouveau type part : un cron sur un déclenchement manuel tromperait à la relecture.
  emit('patch', { type, event_name: type === 'event' ? props.trigger.event_name ?? null : null, cron: type === 'schedule' ? props.trigger.cron ?? null : null, interval_seconds: type === 'schedule' ? props.trigger.interval_seconds ?? null : null })
}
function setDoc(doctype: string | null, event: DocEvent | null): void {
  const d = doctype ?? parsed.value?.doctype ?? null
  const e = event ?? parsed.value?.event ?? 'save'
  emit('patch', { event_name: d ? docEventName(d, e) : null })
}
</script>

<template>
  <div class="af-panel-body" data-testid="af-trigger-panel">
    <p class="wf-kicker">{{ t.automations.triggerNode }}</p>
    <h2 class="wf-panel-title" data-testid="af-panel-title">{{ t.automations.trigger[trigger.type] }}</h2>

    <label class="wf-field" for="af-trigger-type">
      <span>{{ t.automations.triggerType }}</span>
      <Select input-id="af-trigger-type" :model-value="trigger.type" :options="TYPES" option-label="label" option-value="value" size="small" :disabled="!canEdit" data-testid="af-trigger-type" @update:model-value="setType" />
    </label>

    <template v-if="trigger.type === 'event'">
      <SelectButton v-model="kind" :options="KINDS" option-label="label" option-value="value" :allow-empty="false" size="small" :disabled="!canEdit" :aria-label="t.automations.triggerType" />
      <template v-if="kind === 'doc'">
        <label class="wf-field" for="af-trigger-doctype">
          <span>{{ t.automations.doctype }}</span>
          <Select
            input-id="af-trigger-doctype"
            :model-value="parsed?.doctype ?? null"
            :options="doctypes"
            option-label="label"
            option-value="name"
            filter
            size="small"
            :disabled="!canEdit"
            data-testid="af-trigger-doctype"
            @update:model-value="setDoc($event, null)"
          />
        </label>
        <label class="wf-field" for="af-trigger-event">
          <span>{{ t.automations.docEvent }}</span>
          <Select
            input-id="af-trigger-event"
            :model-value="parsed?.event ?? 'save'"
            :options="EVENTS"
            option-label="label"
            option-value="value"
            size="small"
            :disabled="!canEdit || !parsed"
            data-testid="af-trigger-event"
            @update:model-value="setDoc(null, $event)"
          />
        </label>
      </template>
      <label v-else class="wf-field" for="af-trigger-name">
        <span>{{ t.automations.eventName }}</span>
        <InputText id="af-trigger-name" :model-value="trigger.event_name ?? ''" size="small" list="af-events" :disabled="!canEdit" data-testid="af-trigger-name" @update:model-value="emit('patch', { event_name: $event || null })" />
        <datalist id="af-events"><option v-for="e in events" :key="e.name" :value="e.name">{{ e.description }}</option></datalist>
      </label>
    </template>

    <template v-else-if="trigger.type === 'schedule'">
      <label class="wf-field" for="af-cron">
        <span>{{ t.automations.cron }}</span>
        <InputText id="af-cron" :model-value="trigger.cron ?? ''" size="small" class="af-code" placeholder="0 9 * * 1-5" :disabled="!canEdit" data-testid="af-cron" @update:model-value="emit('patch', { cron: $event || null })" />
        <span class="desk-muted wf-help">{{ t.automations.cronHint }}</span>
      </label>
      <label class="wf-field" for="af-interval">
        <span>{{ t.automations.interval }}</span>
        <InputNumber input-id="af-interval" :model-value="trigger.interval_seconds ?? null" :min="60" size="small" :disabled="!canEdit" @update:model-value="emit('patch', { interval_seconds: $event ?? null })" />
      </label>
    </template>
    <p v-else-if="trigger.type === 'webhook'" class="desk-muted">{{ t.automations.webhookHint }}</p>
    <p v-else class="desk-muted">{{ t.automations.manualHint }}</p>

    <p v-if="trigger.type !== 'manual'" class="desk-muted wf-help af-runs-as">{{ t.automations.runsAsHint }}</p>
  </div>
</template>
