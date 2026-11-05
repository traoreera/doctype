<script setup lang="ts">
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed } from 'vue'
import { t } from '@/i18n'
import { TRIGGER_KINDS, type TriggerKind } from '@/lib/automations'
import { DEFAULT_SCHEDULE, MOMENTS, readCron, readEvent, writeCron, writeEvent, type Every, type Moment, type Schedule } from '@/lib/flowactions'
import type { DocTypeSummary } from '@/lib/types'

/**
 * Ce qui MET EN ROUTE le flux, choisi dans des listes : « quand une facture est soumise », « chaque lundi à 9 h ».
 *
 * Personne ne tape `doctype.Sales Invoice.after_submit` ni `0 9 * * 1` : `lib/flowactions` traduit les listes en l'un et en l'autre.
 * Une expression cron plus savante (écrite ailleurs) est montrée en lecture seule plutôt que réécrite de travers.
 */
const props = defineProps<{
  kind: TriggerKind
  event: string
  cron: string
  description: string
  doctypes: DocTypeSummary[]
  disabled?: boolean
}>()
const emit = defineEmits<{
  (e: 'update', patch: Partial<{ kind: TriggerKind; event: string; cron: string }>): void
  (e: 'describe', description: string): void
}>()

const kindOptions = TRIGGER_KINDS.map((k) => ({ value: k, label: t.automations.triggers[k] ?? k }))
const hint = computed(
  () => ({ manual: t.automations.manualHint, event: t.flowBuilder.trigger_.eventHint, schedule: t.flowBuilder.trigger_.scheduleHint, webhook: t.automations.webhookHint })[props.kind],
)

// ── Un événement : quel type de document, à quel moment ──
const parsed = computed(() => readEvent(props.event))
const momentOptions = MOMENTS.map((m) => ({ value: m, label: t.flowBuilder.moments[m] }))
const doctypeOptions = computed(() => props.doctypes.filter((d) => !d.single).map((d) => ({ value: d.name, label: d.label })))
function setEvent(patch: { doctype?: string; moment?: Moment }): void {
  const now = parsed.value ?? { doctype: '', moment: 'after_submit' as Moment }
  emit('update', { event: writeEvent(patch.doctype ?? now.doctype, patch.moment ?? now.moment) })
}

// ── Une planification : tous les jours / toutes les semaines / tous les mois, à une heure ──
const schedule = computed<Schedule | null>(() => (props.cron.trim() ? readCron(props.cron) : DEFAULT_SCHEDULE))
const everyOptions: { value: Every; label: string }[] = [
  { value: 'day', label: t.flowBuilder.every.day },
  { value: 'week', label: t.flowBuilder.every.week },
  { value: 'month', label: t.flowBuilder.every.month },
]
const weekdayOptions = t.flowBuilder.weekdays.map((label, value) => ({ value, label }))
const monthdayOptions = Array.from({ length: 28 }, (_, i) => i + 1)
/** L'heure telle qu'un champ `time` la donne et la prend (`09:00`). */
const time = computed(() => (schedule.value ? `${pad(schedule.value.hour)}:${pad(schedule.value.minute)}` : ''))
const pad = (n: number) => String(n).padStart(2, '0')

function setSchedule(patch: Partial<Schedule>): void {
  if (!schedule.value) return
  emit('update', { cron: writeCron({ ...schedule.value, ...patch }) })
}
function setTime(value: string): void {
  const [h, m] = value.split(':')
  setSchedule({ hour: Number(h), minute: Number(m) })
}
</script>

<template>
  <div class="fl-panel-body" data-testid="fl-trigger-panel">
    <p class="wf-kicker">{{ t.flowBuilder.trigger }}</p>
    <label class="wf-field" for="fl-trigger">
      <span>{{ t.flowBuilder.trigger_.when }}</span>
      <Select
        input-id="fl-trigger"
        :model-value="kind"
        :options="kindOptions"
        option-label="label"
        option-value="value"
        :disabled="disabled"
        size="small"
        data-testid="fl-trigger"
        @update:model-value="emit('update', { kind: $event })"
      />
      <span class="desk-muted wf-help">{{ hint }}</span>
    </label>

    <template v-if="kind === 'event'">
      <label class="wf-field" for="fl-event-doctype">
        <span>{{ t.flowBuilder.trigger_.eventDoctype }}</span>
        <Select
          input-id="fl-event-doctype"
          :model-value="parsed?.doctype ?? ''"
          :options="doctypeOptions"
          option-label="label"
          option-value="value"
          :disabled="disabled"
          size="small"
          filter
          :placeholder="t.flowBuilder.fields.pickDoctype"
          data-testid="fl-event-doctype"
          @update:model-value="setEvent({ doctype: String($event) })"
        />
      </label>
      <label class="wf-field" for="fl-event-moment">
        <span>{{ t.flowBuilder.trigger_.eventMoment }}</span>
        <Select
          input-id="fl-event-moment"
          :model-value="parsed?.moment ?? 'after_submit'"
          :options="momentOptions"
          option-label="label"
          option-value="value"
          :disabled="disabled"
          size="small"
          data-testid="fl-event-moment"
          @update:model-value="setEvent({ moment: $event })"
        />
      </label>
      <!-- Un événement d'un autre plugin : gardé tel quel, montré sans être réécrit. -->
      <Message v-if="event && !parsed" severity="secondary" :closable="false" data-testid="fl-event-other">{{ t.flowBuilder.trigger_.otherEvent(event) }}</Message>
    </template>

    <template v-else-if="kind === 'schedule'">
      <template v-if="schedule">
        <label class="wf-field" for="fl-every">
          <span>{{ t.flowBuilder.trigger_.every }}</span>
          <Select
            input-id="fl-every"
            :model-value="schedule.every"
            :options="everyOptions"
            option-label="label"
            option-value="value"
            :disabled="disabled"
            size="small"
            data-testid="fl-every"
            @update:model-value="setSchedule({ every: $event })"
          />
        </label>
        <label v-if="schedule.every === 'week'" class="wf-field" for="fl-weekday">
          <span>{{ t.flowBuilder.trigger_.weekday }}</span>
          <Select
            input-id="fl-weekday"
            :model-value="schedule.weekday"
            :options="weekdayOptions"
            option-label="label"
            option-value="value"
            :disabled="disabled"
            size="small"
            data-testid="fl-weekday"
            @update:model-value="setSchedule({ weekday: Number($event) })"
          />
        </label>
        <label v-if="schedule.every === 'month'" class="wf-field" for="fl-monthday">
          <span>{{ t.flowBuilder.trigger_.monthday }}</span>
          <Select
            input-id="fl-monthday"
            :model-value="schedule.day"
            :options="monthdayOptions"
            :disabled="disabled"
            size="small"
            data-testid="fl-monthday"
            @update:model-value="setSchedule({ day: Number($event) })"
          />
          <span class="desk-muted wf-help">{{ t.flowBuilder.trigger_.monthdayHint }}</span>
        </label>
        <label class="wf-field" for="fl-time">
          <span>{{ t.flowBuilder.trigger_.at }}</span>
          <InputText id="fl-time" :model-value="time" type="time" :disabled="disabled" size="small" data-testid="fl-time" @update:model-value="setTime(String($event ?? ''))" />
        </label>
      </template>
      <!-- Une expression écrite ailleurs : on la montre, on ne la réécrit pas. -->
      <template v-else>
        <label class="wf-field" for="fl-cron">
          <span>{{ t.automations.cronLabel }}</span>
          <InputText id="fl-cron" :model-value="cron" disabled size="small" class="fe-code" data-testid="fl-cron" />
          <span class="desk-muted wf-help">{{ t.flowBuilder.trigger_.cronKept }}</span>
        </label>
      </template>
    </template>

    <label class="wf-field" for="fl-description">
      <span>{{ t.automations.description }}</span>
      <InputText
        id="fl-description"
        :model-value="description"
        :disabled="disabled"
        size="small"
        data-testid="fl-description"
        @update:model-value="emit('describe', String($event ?? ''))"
      />
    </label>
  </div>
</template>
