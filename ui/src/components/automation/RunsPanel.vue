<script setup lang="ts">
import Button from 'primevue/button'
import { t } from '@/i18n'
import { formatTimestamp } from '@/lib/format'
import { statusTone, xflowTime, type FlowRun, type StepRun } from '@/lib/xflow'

/**
 * Les exécutions de l'automatisation, les plus récentes d'abord (n8n : « Executions »). En choisir une la pose sur le canevas : chaque
 * étape y porte son statut ; choisir alors une étape montre ce qu'elle a rendu ou pourquoi elle a échoué.
 */
defineProps<{ runs: FlowRun[] | null; current: FlowRun | null; step: StepRun | null; stepLabel: string | null; loading: boolean; canRun: boolean }>()
const emit = defineEmits<{ (e: 'show', run: FlowRun | null): void; (e: 'refresh'): void; (e: 'cancel', id: string): void }>()

const pretty = (v: unknown): string => JSON.stringify(v, null, 2)
const when = (iso: string | null): string => {
  const at = xflowTime(iso)
  return at ? formatTimestamp(at) : '—'
}
</script>

<template>
  <div class="af-panel-body" data-testid="af-runs">
    <div class="af-inline">
      <h2 class="wf-panel-title">{{ t.automations.runs }}</h2>
      <span class="desk-spacer" />
      <Button icon="pi pi-refresh" text rounded severity="secondary" size="small" :loading="loading" :aria-label="t.automations.refresh" :title="t.automations.refresh" data-testid="af-runs-refresh" @click="emit('refresh')" />
    </div>

    <template v-if="current">
      <p class="desk-muted">{{ t.automations.viewingRun }}</p>
      <div class="af-inline">
        <span class="flow-pill" :data-tone="statusTone(current.status)" data-testid="af-run-status">{{ t.automations.status[current.status] ?? current.status }}</span>
        <span class="desk-muted">{{ when(current.started_at) }}</span>
        <span class="desk-spacer" />
        <Button v-if="canRun && (current.status === 'running' || current.status === 'pending')" :label="t.automations.cancelRun" icon="pi pi-stop" text severity="danger" size="small" @click="emit('cancel', current.run_id)" />
        <Button :label="t.automations.hideRun" icon="pi pi-pencil" text size="small" data-testid="af-run-hide" @click="emit('show', null)" />
      </div>
      <p v-if="current.error" class="fb-error af-run-error">{{ current.error }}</p>
      <template v-if="step">
        <h3 class="wf-section">{{ stepLabel }} · <span class="flow-pill" :data-tone="statusTone(step.status)">{{ t.automations.status[step.status] ?? step.status }}</span></h3>
        <p v-if="step.attempt > 1" class="desk-muted">{{ t.automations.attempt(step.attempt) }}</p>
        <template v-if="step.error">
          <h4 class="af-run-h">{{ t.automations.stepError }}</h4>
          <pre class="af-pre af-pre-error" data-testid="af-step-error">{{ step.error }}</pre>
        </template>
        <template v-if="step.result !== null && step.result !== undefined">
          <h4 class="af-run-h">{{ t.automations.stepResult }}</h4>
          <pre class="af-pre" data-testid="af-step-result">{{ pretty(step.result) }}</pre>
        </template>
      </template>
      <h4 class="af-run-h">{{ t.automations.triggerNode }}</h4>
      <pre class="af-pre">{{ pretty(current.trigger_payload) }}</pre>
    </template>

    <template v-else>
      <p v-if="runs && !runs.length" class="desk-muted" data-testid="af-runs-empty">{{ t.automations.noRuns }}</p>
      <ul v-else-if="runs" class="af-run-list">
        <li v-for="r in runs" :key="r.run_id">
          <button type="button" class="af-run" :data-testid="`af-run-${r.run_id}`" @click="emit('show', r)">
            <span class="flow-pill" :data-tone="statusTone(r.status)">{{ t.automations.status[r.status] ?? r.status }}</span>
            <span>{{ when(r.started_at) }}</span>
            <span class="desk-muted af-run-trigger">{{ String(r.trigger_payload?._triggered_by ?? '') }}</span>
          </button>
        </li>
      </ul>
    </template>
  </div>
</template>
