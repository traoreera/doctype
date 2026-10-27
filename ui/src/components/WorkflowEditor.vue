<script setup lang="ts">
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import { computed } from 'vue'
import { t } from '@/i18n'
import { deadEnds, flowRows, hasTweaks, withTweak, type TweakKey } from '@/lib/workflowedit'
import type { WorkflowDef, WorkflowOverride } from '@/lib/types'

/**
 * Ce que l'organisation RESSERRE sur le circuit d'approbation d'un type (doc/programme.md §3.6).
 *
 * Une ligne par étape du circuit de l'application (`definition` : l'automate du YAML, pas celui que `/meta` montre — une étape fermée
 * n'y figure plus, on ne pourrait plus la rouvrir). Le composant n'écrit que dans le JSON que le serveur validera (`lib/workflowedit`,
 * pur et testé à part) et ne décide rien : ce qui est déjà exigé par l'application se montre coché et grisé, et un cul-de-sac se dit
 * ICI plutôt que d'attendre le refus du serveur.
 */
const props = defineProps<{
  visible: boolean
  definition: WorkflowDef | null
  config: WorkflowOverride
  /** `false` quand l'appelant n'écrit pas TOUS les documents du type : le serveur refuserait (422 `workflow_reserved`). */
  allowed: boolean
  saving?: boolean
  problem?: string | null
}>()
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'update:config', value: WorkflowOverride): void
  (e: 'save'): void
  (e: 'reset'): void
}>()

const rows = computed(() => flowRows(props.definition, props.config))
const stuck = computed(() => deadEnds(props.definition, props.config))
const dirty = computed(() => hasTweaks(props.config))

function set(action: string, key: TweakKey, value: boolean | string): void {
  emit('update:config', withTweak(props.config, action, key, value))
}
</script>

<template>
  <Dialog
    :visible="visible"
    modal
    :header="t.flowOverride.title"
    :style="{ width: '860px', maxWidth: '95vw' }"
    data-testid="flow-dialog"
    @update:visible="emit('update:visible', $event)"
  >
    <p class="desk-muted props-hint">{{ t.flowOverride.hint }}</p>
    <Message v-if="!allowed" severity="info" class="banner-error" data-testid="flow-reserved">{{ t.flowOverride.reserved }}</Message>
    <Message v-if="problem" severity="error" class="banner-error" data-testid="flow-problem">{{ problem }}</Message>
    <Message v-if="stuck.length" severity="warn" class="banner-error" data-testid="flow-deadend">
      {{ t.flowOverride.deadEnd }}{{ stuck.join(', ') }}
    </Message>

    <p v-if="!rows.length" class="desk-muted" data-testid="flow-empty">{{ t.flowOverride.empty }}</p>
    <div v-else class="props-scroll">
      <table class="props-table">
        <thead>
          <tr>
            <th scope="col">{{ t.flowOverride.action }}</th>
            <th scope="col">{{ t.flowOverride.path }}</th>
            <th scope="col">{{ t.flowOverride.closed }}</th>
            <th scope="col">{{ t.flowOverride.notOwner }}</th>
            <th scope="col">{{ t.flowOverride.condition }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.action" :data-testid="`flow-row-${row.action}`">
            <th scope="row" class="props-name">{{ row.action }}</th>
            <td class="desk-muted">{{ row.from }} → {{ row.to }}</td>
            <td>
              <Checkbox
                :model-value="row.closed"
                binary
                :disabled="!allowed || saving"
                :aria-label="`${t.flowOverride.closed} — ${row.action}`"
                :data-testid="`flow-closed-${row.action}`"
                @update:model-value="set(row.action, 'closed', $event === true)"
              />
            </td>
            <td>
              <Checkbox
                :model-value="row.notOwner"
                binary
                :disabled="row.locked || !allowed || saving"
                :title="row.locked ? t.flowOverride.locked : t.flowOverride.notOwnerHint"
                :aria-label="`${t.flowOverride.notOwner} — ${row.action}`"
                :data-testid="`flow-owner-${row.action}`"
                @update:model-value="set(row.action, 'not_owner', $event === true)"
              />
            </td>
            <td>
              <InputText
                :model-value="row.condition"
                :disabled="row.closed || !allowed || saving"
                size="small"
                class="flow-condition"
                :placeholder="t.flowOverride.conditionHint"
                :aria-label="`${t.flowOverride.condition} — ${row.action}`"
                :data-testid="`flow-condition-${row.action}`"
                @update:model-value="set(row.action, 'condition', $event ?? '')"
              />
              <span v-if="row.yamlCondition" class="desk-muted flow-yaml">{{ t.flowOverride.yamlCondition }}<code>{{ row.yamlCondition }}</code></span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <template #footer>
      <Button :label="t.cancel" severity="secondary" text :disabled="saving" @click="emit('update:visible', false)" />
      <Button
        :label="t.override.reset"
        severity="secondary"
        outlined
        :disabled="!allowed || saving"
        data-testid="flow-reset"
        @click="emit('reset')"
      />
      <Button
        :label="t.save"
        icon="pi pi-check"
        :loading="saving"
        :disabled="!allowed || !dirty || stuck.length > 0"
        data-testid="flow-save"
        @click="emit('save')"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.flow-condition {
  width: 100%;
}
.flow-yaml {
  display: block;
  font-size: 0.75rem;
  margin-top: 0.15rem;
}
</style>
