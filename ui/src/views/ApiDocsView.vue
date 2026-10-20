<script setup lang="ts">
import Button from 'primevue/button'
import Select from 'primevue/select'
import { computed, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { endpoints, fieldDocs, sampleData } from '@/lib/apidocs'
import { useServices } from '@/lib/services'
import type { DocTypeMeta } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/** La documentation de l'API d'un type, générée de sa définition (`lib/apidocs.ts`) : routes que l'utilisateur peut employer, champs, filtres, exemples à copier. */
const props = defineProps<{ doctype?: string }>()
const router = useRouter()
const metaStore = useMeta()
const { config } = useServices()

const names = ref<{ name: string; label: string }[]>([])
const meta = shallowRef<DocTypeMeta | null>(null)
const failure = ref<string | null>(null)
const copied = ref<string | null>(null)

const origin = typeof location === 'undefined' ? '' : location.origin
const base = computed(() => `${config.baseUrl.startsWith('http') ? '' : origin}${config.baseUrl}`)
const routes = computed(() => (meta.value ? endpoints(meta.value, base.value) : []))
const fields = computed(() => (meta.value ? fieldDocs(meta.value) : []))
const example = computed(() => (meta.value ? JSON.stringify(sampleData(meta.value), null, 2) : ''))

async function open(name: string | undefined): Promise<void> {
  failure.value = null
  try {
    const found = await metaStore.loadSummaries()
    names.value = found.filter((d) => !d.single).map((d) => ({ name: d.name, label: d.label })).sort((a, b) => a.label.localeCompare(b.label, 'fr'))
    const target = name ?? names.value[0]?.name
    meta.value = target ? await metaStore.get(target) : null
  } catch (e) {
    meta.value = null
    failure.value = describeError(e)
  }
}
watch(() => props.doctype, open, { immediate: true })

function pick(name: string): void {
  void router.push({ name: 'api-docs', params: { doctype: name } })
}

async function copy(id: string, text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = id
    setTimeout(() => (copied.value = null), 1500)
  } catch {
    // un navigateur sans presse-papiers : le texte reste sélectionnable
  }
}

const methodTone = (m: string) => ({ GET: 'ok', POST: 'info', PUT: 'warn', DELETE: 'bad' })[m] ?? ''
</script>

<template>
  <div class="desk-page api-docs">
    <div class="desk-page-head">
      <h1 data-testid="api-title">{{ t.apiDocs.title }}</h1>
      <span class="desk-spacer" />
      <Select
        v-if="names.length"
        :model-value="meta?.name ?? null"
        :options="names"
        option-label="label"
        option-value="name"
        filter
        :placeholder="t.apiDocs.pick"
        :aria-label="t.apiDocs.pick"
        data-testid="api-pick"
        @update:model-value="pick"
      />
    </div>
    <p class="desk-muted">{{ t.apiDocs.intro }}</p>

    <p v-if="failure" class="error" role="alert">{{ failure }}</p>
    <p v-else-if="!meta" class="desk-muted">{{ t.apiDocs.empty }}</p>

    <template v-else>
      <section class="desk-card api-block">
        <h2>{{ t.apiDocs.base }}</h2>
        <code data-testid="api-base">{{ base }}</code>
        <h2>{{ t.apiDocs.auth }}</h2>
        <p class="desk-muted">{{ t.apiDocs.authText }}</p>
      </section>

      <section class="desk-card api-block">
        <h2>{{ t.apiDocs.routes }}</h2>
        <article v-for="e in routes" :key="e.id" class="route" :data-testid="`route-${e.id}`">
          <header>
            <span :class="['method', methodTone(e.method)]">{{ e.method }}</span>
            <code class="path">{{ e.path }}</code>
            <span class="desk-spacer" />
            <span class="desk-muted">{{ e.title }}</span>
          </header>
          <div class="curl">
            <pre>{{ e.curl }}</pre>
            <Button :label="copied === e.id ? t.apiDocs.copied : t.apiDocs.copy" size="small" text severity="secondary" :data-testid="`copy-${e.id}`" @click="copy(e.id, e.curl)" />
          </div>
        </article>
      </section>

      <section class="desk-card api-block">
        <h2>{{ t.apiDocs.fields }}</h2>
        <div class="table-wrap">
          <table class="grid" :aria-label="t.apiDocs.fields">
            <thead>
              <tr>
                <th>{{ t.apiDocs.field }}</th>
                <th>{{ t.apiDocs.type }}</th>
                <th>{{ t.apiDocs.required }}</th>
                <th>{{ t.apiDocs.constraints }}</th>
                <th>{{ t.apiDocs.operators }}</th>
                <th>{{ t.apiDocs.rules }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in fields" :key="f.fieldname" :data-testid="`field-${f.fieldname}`">
                <td><code>{{ f.fieldname }}</code><div class="desk-muted small">{{ f.label }}</div></td>
                <td>{{ f.type }}</td>
                <td>{{ f.required ? '✔' : '' }}</td>
                <td class="small">{{ f.detail }}</td>
                <td class="small">{{ f.filter.length ? f.filter.join('  ') : t.apiDocs.none }}</td>
                <td class="small">{{ f.rules.join(' · ') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <h2>{{ t.apiDocs.filters }}</h2>
        <p class="desk-muted">{{ t.apiDocs.filtersText }}</p>
        <h2>{{ t.apiDocs.example }}</h2>
        <div class="curl">
          <pre data-testid="api-example">{{ example }}</pre>
          <Button :label="copied === 'example' ? t.apiDocs.copied : t.apiDocs.copy" size="small" text severity="secondary" @click="copy('example', example)" />
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.api-docs h2 {
  font-size: 1rem;
  margin: 1rem 0 0.4rem;
}

.api-docs h2:first-child {
  margin-top: 0;
}

.api-block {
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}

.route {
  border-top: 1px solid var(--p-content-border-color);
  padding: 0.6rem 0;
}

.route:first-of-type {
  border-top: 0;
}

.route header {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.method {
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  padding: 0.1rem 0.45rem;
  border-radius: 4px;
  color: var(--p-primary-contrast-color, #fff);
  background: var(--p-primary-color);
}

.method.bad {
  background: var(--p-red-600, #dc2626);
}

.method.warn {
  background: var(--p-orange-600, #ea580c);
}

.method.ok {
  background: var(--p-green-600, #16a34a);
}

.curl {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}

.curl pre {
  flex: 1;
  margin: 0.4rem 0 0;
  padding: 0.5rem 0.7rem;
  overflow: auto;
  border-radius: 6px;
  background: var(--p-content-hover-background);
  font-size: 0.78rem;
  white-space: pre-wrap;
  word-break: break-all;
}

.small {
  font-size: 0.8rem;
}

.path {
  font-weight: 600;
}
</style>
