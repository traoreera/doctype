<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import SelectButton from 'primevue/selectbutton'
import { computed, ref, watch } from 'vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { csvFilename } from '@/lib/csv'
import { decodeCsv, templateCsv, type ImportMode } from '@/lib/csvimport'
import { downloadText } from '@/lib/download'
import { todayIn } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, ImportItem, ImportResult } from '@/lib/types'

/**
 * Importer un fichier CSV dans un type : un document par ligne, créé ou mis à jour par le serveur avec les mêmes règles que le formulaire.
 * Le compte rendu liste les lignes refusées (numéro de ligne dans le fichier, en-tête = 1) et pourquoi ; celles qui sont passées restent.
 */
const props = defineProps<{ doctype: string; meta: DocTypeMeta }>()
const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{ imported: [] }>()
const { api } = useServices()
const notify = useNotify()

const modes = computed(() =>
  [
    { value: 'insert' as const, label: t.importCsv.insert, allowed: props.meta.rights.create !== 'none' },
    { value: 'update' as const, label: t.importCsv.update, allowed: props.meta.rights.write !== 'none' },
  ].filter((m) => m.allowed),
)
const mode = ref<ImportMode>('insert')
const file = ref<File | null>(null)
const busy = ref(false)
const failure = ref('')
const result = ref<ImportResult | null>(null)
const failures = computed<ImportItem[]>(() => result.value?.results.filter((r) => !r.ok) ?? [])

watch(visible, (open) => {
  if (!open) return
  mode.value = modes.value[0]?.value ?? 'insert'
  file.value = null
  failure.value = ''
  result.value = null
})

function pick(e: Event): void {
  file.value = (e.target as HTMLInputElement).files?.[0] ?? null
  failure.value = ''
  result.value = null
}

function template(): void {
  downloadText(csvFilename(`${props.doctype}-modele`, todayIn(props.meta.timezone)), templateCsv(props.meta, mode.value))
}

const reason = (r: ImportItem): string => {
  const detail = r.details?.find((d) => d.msg)
  return detail ? (detail.field ? `${detail.field} : ${detail.msg}` : detail.msg!) : (r.message ?? r.code ?? t.errors.generic)
}

async function run(): Promise<void> {
  if (!file.value) return
  busy.value = true
  failure.value = ''
  try {
    const content = decodeCsv(await file.value.arrayBuffer())
    const res = await api.importCsv(props.doctype, content, mode.value)
    if (res.done) emit('imported')
    if (!res.failed) {
      notify.success(t.importCsv.allDone(res.done))
      visible.value = false
    } else result.value = res
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal :header="t.importCsv.title(meta.label || doctype)" :style="{ width: '34rem', maxWidth: '95vw' }" data-testid="import-dialog">
    <div class="import-body">
      <p class="desk-muted">{{ t.importCsv.intro }}</p>
      <SelectButton v-if="modes.length > 1" v-model="mode" :options="modes" option-label="label" option-value="value" :allow-empty="false" size="small" :aria-label="t.importCsv.mode" data-testid="import-mode" />
      <label class="import-file">
        <span>{{ t.importCsv.file }}</span>
        <input type="file" accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values" data-testid="import-file" @change="pick" />
      </label>
      <Button :label="t.importCsv.template" icon="pi pi-download" text size="small" class="import-template" data-testid="import-template" @click="template" />
      <Message v-if="failure" severity="error" data-testid="import-error">{{ failure }}</Message>
      <template v-if="result">
        <Message severity="warn" data-testid="import-report">{{ t.importCsv.report(result.done, result.failed) }}</Message>
        <ul class="import-failures">
          <li v-for="r in failures" :key="r.row" :data-testid="`import-fail-${r.row}`">
            <strong>{{ t.importCsv.row(r.row) }}</strong> <span class="desk-muted">{{ reason(r) }}</span>
          </li>
        </ul>
      </template>
    </div>
    <template #footer>
      <Button :label="t.close" text severity="secondary" @click="visible = false" />
      <Button :label="t.importCsv.run" icon="pi pi-upload" :loading="busy" :disabled="!file || busy" data-testid="import-run" @click="run" />
    </template>
  </Dialog>
</template>

<style scoped>
.import-body {
  display: grid;
  gap: 0.875rem;
}

.import-file {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
}

.import-file > span {
  color: var(--p-text-muted-color);
}

.import-template {
  justify-self: start;
}

.import-failures {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.5rem;
  max-height: 40vh;
  overflow: auto;
}

.import-failures li {
  display: grid;
  gap: 0.1rem;
}
</style>
