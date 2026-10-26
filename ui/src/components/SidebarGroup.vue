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
