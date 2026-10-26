<script setup lang="ts">
import { computed } from 'vue'
import SidebarGroup from '@/components/SidebarGroup.vue'
import SidebarTags from '@/components/tags/SidebarTags.vue'
import { t } from '@/i18n'
import { sidebarFields } from '@/lib/sidebar'
import { tagFilterOf } from '@/lib/tags'
import type { DocTypeMeta, FieldMeta, Filters } from '@/lib/types'

/**
 * La barre latérale d'une liste : pour quelques champs à choix (Select, Link, Booléen), les valeurs et leur nombre. Un clic filtre la liste sur cette valeur,
 * un second clic retire le filtre. Les champs viennent des colonnes de la liste d'abord (ce que l'utilisateur regarde), au plus trois. `tags` : le
 * type a des étiquettes en usage, leur groupe suit.
 */
const props = defineProps<{ meta: DocTypeMeta; filters: Filters; tags?: boolean }>()
const emit = defineEmits<{ pick: [field: string, value: string | number | boolean]; pickTag: [tag: string] }>()

const fields = computed<FieldMeta[]>(() => sidebarFields(props.meta))
/** La valeur déjà filtrée par `=` pour ce champ (la mettre en évidence). */
const activeOf = (f: FieldMeta): unknown => {
  const v = props.filters[f.fieldname]
  return Array.isArray(v) ? undefined : v
}
</script>

<template>
  <aside v-if="fields.length || tags" class="list-sidebar" :aria-label="t.sidebar.title" data-testid="list-sidebar">
    <SidebarGroup v-for="f in fields" :key="f.fieldname" :doctype="meta.name" :field="f" :filters="filters" :active="activeOf(f)" @pick="emit('pick', f.fieldname, $event)" />
    <SidebarTags v-if="tags" :doctype="meta.name" :filters="filters" :active="tagFilterOf(filters)" @pick="emit('pickTag', $event)" />
  </aside>
</template>
