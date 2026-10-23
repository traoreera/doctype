<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { t } from '@/i18n'
import { statusLabel, statusTone } from '@/lib/docstatus'
import { formatCell } from '@/lib/format'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'

/**
 * L'aperçu d'un document lié : au survol (ou au focus) d'un lien, son titre et quelques champs, sans quitter la page. Rien n'est lu avant le survol ;
 * un document illisible ou disparu n'affiche simplement pas d'aperçu (le lien, lui, reste un lien).
 */
const props = defineProps<{ target: string; name: string }>()
const links = useLinks()
const metaStore = useMeta()

const open = ref(false)
const meta = ref<DocTypeMeta | null>(null)
const ready = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

const PREVIEW_FIELDS = 4
const lines = computed<{ field: FieldMeta; text: string }[]>(() => {
  const m = meta.value
  if (!m || !ready.value) return []
  const skip = new Set([m.title_field])
  const wanted = [...m.list_columns, ...m.fields.filter((f) => f.in_list_view).map((f) => f.fieldname)]
  const seen = new Set<string>()
  const out: { field: FieldMeta; text: string }[] = []
  for (const n of wanted) {
    const f = m.fields.find((x) => x.fieldname === n)
    if (!f || seen.has(n) || skip.has(n) || f.hidden || ['Table', 'Password', 'Image', 'Attach', 'JSON', 'TextEditor', 'Code', 'LongText'].includes(f.fieldtype)) continue
    seen.add(n)
    const raw = links.field(props.target, props.name, n)
    const text = raw === undefined || raw === null || raw === '' ? '' : f.fieldtype === 'Link' && typeof f.options === 'string' && typeof raw === 'string' ? links.title(f.options, raw) : formatCell(f, raw)
    if (text) out.push({ field: f, text })
    if (out.length >= PREVIEW_FIELDS) break
  }
  return out
})
const heading = computed(() => links.title(props.target, props.name))
/** L'état d'un document soumettable (brouillon, soumis, annulé) : un lien vers une facture annulée doit se voir avant qu'on l'ouvre. */
const state = computed(() => (meta.value?.submittable ? links.status(props.target, props.name) : undefined))
const found = computed(() => ready.value && links.field(props.target, props.name, meta.value?.title_field ?? '') !== undefined || lines.value.length > 0)

async function load(): Promise<void> {
  try {
    meta.value = await metaStore.get(props.target)
    await links.resolve(props.target, [props.name], { data: true })
    const m = meta.value
    // Les liens des lignes de l'aperçu (un client cité par une facture…) se lisent aussi, pour montrer des titres et non des identifiants.
    for (const f of m.fields) {
      const v = links.field(props.target, props.name, f.fieldname)
      if (f.fieldtype === 'Link' && typeof f.options === 'string' && typeof v === 'string') await links.resolve(f.options, [v])
    }
    ready.value = true
  } catch {
    ready.value = false // illisible : pas d'aperçu
  }
}

function show(): void {
  clearTimeout(timer)
  timer = setTimeout(() => {
    open.value = true
    if (!ready.value) void load()
  }, 350)
}
function hide(): void {
  clearTimeout(timer)
  open.value = false
}
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <span class="link-preview" @mouseenter="show" @mouseleave="hide" @focusin="show" @focusout="hide" @keydown.esc="hide">
    <slot />
    <span v-if="open && ready && found" class="preview-card" role="tooltip" data-testid="link-preview">
      <span class="preview-head">
        <strong class="preview-title">{{ heading }}</strong>
        <span v-if="state !== undefined" :class="['status-pill', statusTone(state)]" data-testid="link-preview-status" :data-docstatus="state">{{ statusLabel(state) }}</span>
      </span>
      <span v-if="meta && meta.label !== heading" class="desk-muted preview-kind">{{ meta.label ?? target }} · {{ name }}</span>
      <dl v-if="lines.length">
        <template v-for="l in lines" :key="l.field.fieldname">
          <dt>{{ l.field.label ?? l.field.fieldname }}</dt>
          <dd>{{ l.text }}</dd>
        </template>
      </dl>
      <span v-else class="desk-muted">{{ t.linkPreview.nothing }}</span>
    </span>
  </span>
</template>

<style scoped>
.link-preview {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.preview-card {
  position: absolute;
  z-index: 20;
  top: calc(100% + 0.35rem);
  left: 0;
  min-width: 14rem;
  max-width: 22rem;
  display: grid;
  gap: 0.2rem;
  padding: 0.7rem 0.85rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 8px;
  background: var(--p-content-background);
  color: var(--p-text-color);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
  font-size: 0.85rem;
  white-space: normal;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.preview-title {
  font-size: 0.95rem;
}

.preview-kind {
  font-size: 0.75rem;
}

dl {
  margin: 0.35rem 0 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.15rem 0.75rem;
}

dt {
  color: var(--p-text-muted-color);
}

dd {
  margin: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
</style>
