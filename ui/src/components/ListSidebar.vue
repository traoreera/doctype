<script setup lang="ts">
import { computed } from 'vue'
import SidebarGroup from '@/components/SidebarGroup.vue'
import { t } from '@/i18n'
import { sidebarFields } from '@/lib/sidebar'
import type { DocTypeMeta, FieldMeta, Filters } from '@/lib/types'

/**
 * La barre latérale d'une liste : pour quelques champs à choix (Select, Link, Booléen), les valeurs et leur nombre. Un clic filtre la liste sur cette valeur,
 * un second clic retire le filtre. Les champs viennent des colonnes de la liste d'abord (ce que l'utilisateur regarde), au plus trois.
 */
const props = defineProps<{ meta: DocTypeMeta; filters: Filters }>()
const emit = defineEmits<{ pick: [field: string, value: string | number | boolean] }>()

const fields = computed<FieldMeta[]>(() => sidebarFields(props.meta))
/** La valeur déjà filtrée par `=` pour ce champ (la mettre en évidence). */
const activeOf = (f: FieldMeta): unknown => {
  const v = props.filters[f.fieldname]
  return Array.isArray(v) ? undefined : v
}
</script>

<template>
  <aside v-if="fields.length" class="list-sidebar" :aria-label="t.sidebar.title" data-testid="list-sidebar">
    <SidebarGroup v-for="f in fields" :key="f.fieldname" :doctype="meta.name" :field="f" :filters="filters" :active="activeOf(f)" @pick="emit('pick', f.fieldname, $event)" />
  </aside>
</template>
