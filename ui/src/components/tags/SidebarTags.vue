<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import { withoutTag } from '@/lib/tags'
import { useServices } from '@/lib/services'
import type { Filters, TagCount } from '@/lib/types'

/**
 * Les étiquettes dans la barre latérale d'une liste : les plus fréquentes parmi les documents que les AUTRES filtres retiennent (comme un groupe
 * de valeurs ignore son propre filtre), avec leur nombre. Un clic filtre la liste sur l'étiquette, un second clic retire le filtre.
 */
const props = defineProps<{ doctype: string; filters: Filters; active: string | null }>()
const emit = defineEmits<{ pick: [tag: string] }>()
const { api } = useServices()

const LIMIT = 8
const counts = ref<TagCount[]>([])
const others = computed(() => withoutTag(props.filters))
let ticket = 0

watch(
  () => [props.doctype, JSON.stringify(others.value)],
  async () => {
    const mine = ++ticket
    try {
      const found = await api.tagCounts(props.doctype, { filters: others.value, limit: LIMIT + 1 })
      if (mine === ticket) counts.value = found
    } catch {
      if (mine === ticket) counts.value = [] // la liste reste utilisable sans ce panneau
    }
  },
  { immediate: true },
)
const shown = computed(() => counts.value.slice(0, LIMIT))
</script>

<template>
  <section v-if="shown.length || active !== null" class="sidebar-group" data-testid="sidebar-tags">
    <h3>{{ t.tags.title }}</h3>
    <ul>
      <li v-for="c in shown" :key="c.tag">
        <button type="button" :class="{ on: active === c.tag }" :aria-pressed="active === c.tag" :data-testid="`sidebar-tag-${c.tag}`" @click="emit('pick', c.tag)">
          <span class="name"><span class="pi pi-tag" aria-hidden="true" /> {{ c.tag }}</span>
          <span class="n">{{ c.count }}</span>
        </button>
      </li>
      <li v-if="counts.length > LIMIT" class="desk-muted more">{{ t.sidebar.more }}</li>
    </ul>
  </section>
</template>
