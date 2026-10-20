<script setup lang="ts">
import { computed } from 'vue'
import { useAggregate } from '@/composables/useAggregate'
import { t } from '@/i18n'
import { groupLabel } from '@/lib/aggregate'
import type { FieldMeta, Filters } from '@/lib/types'
import { useLinks } from '@/stores/links'

/** Un champ de la barre latérale d'une liste : ses valeurs et combien de documents chacune compte, dans les filtres EN VIGUEUR (sauf ceux de ce champ lui-même). */
const props = defineProps<{ doctype: string; field: FieldMeta; filters: Filters; active: unknown }>()
const emit = defineEmits<{ pick: [value: string | number | boolean] }>()
const links = useLinks()

const others = computed<Filters>(() => {
  const { [props.field.fieldname]: _own, ...rest } = props.filters
  void _own
  return rest
})
const { result } = useAggregate(() => ({ doctype: props.doctype, query: { group_by: props.field.fieldname, metrics: ['count'], filters: others.value, limit: 8 } }))

const rows = computed(() => (result.value?.rows ?? []).filter((r) => r.key !== null && r.key !== ''))
const label = (key: string | number | boolean | null) => groupLabel(result.value?.group ?? null, key, links.title)
</script>

<template>
  <section v-if="rows.length || active !== undefined" class="sidebar-group" :data-testid="`sidebar-${field.fieldname}`">
    <h3>{{ field.label ?? field.fieldname }}</h3>
    <ul>
      <li v-for="r in rows" :key="String(r.key)">
        <button type="button" :class="{ on: active === r.key }" :aria-pressed="active === r.key" :data-testid="`sidebar-${field.fieldname}-${r.key}`" @click="emit('pick', r.key as string | number | boolean)">
          <span class="name">{{ label(r.key) }}</span>
          <span class="n">{{ r.values[0] }}</span>
        </button>
      </li>
      <li v-if="result?.truncated" class="desk-muted more">{{ t.sidebar.more }}</li>
    </ul>
  </section>
</template>

<style scoped>
.sidebar-group + .sidebar-group {
  margin-top: 1rem;
}

h3 {
  margin: 0 0 0.35rem;
  font-size: 0.75rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
  font-weight: 600;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
}

button {
  all: unset;
  box-sizing: border-box;
  display: flex;
  width: 100%;
  justify-content: space-between;
  gap: 0.5rem;
  align-items: center;
  min-height: 28px; /* une cible de 24 px au moins (WCAG 2.5.8) */
  padding: 0.2rem 0.5rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.875rem;
}

button:hover {
  background: var(--p-content-hover-background);
}

button:focus-visible {
  outline: 2px solid var(--p-primary-color);
}

button.on {
  background: color-mix(in srgb, var(--p-primary-color) 14%, transparent);
  font-weight: 600;
}

.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.n {
  font-variant-numeric: tabular-nums;
  color: var(--p-text-muted-color);
}

.more {
  padding: 0.2rem 0.5rem;
  font-size: 0.75rem;
}
</style>
