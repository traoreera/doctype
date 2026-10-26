<script setup lang="ts">
import { t } from '@/i18n'

/** Les étiquettes d'une ligne de liste, à la suite de son titre : un clic filtre la liste sur l'étiquette (sans ouvrir la ligne). */
defineProps<{ tags?: readonly string[]; active: string | null }>()
const emit = defineEmits<{ pick: [tag: string] }>()
</script>

<template>
  <ul v-if="tags?.length" class="tag-chips row-tags" :aria-label="t.tags.title">
    <li v-for="tag in tags" :key="tag" :class="['tag-chip', { on: active === tag }]">
      <button type="button" class="tag-filter" :title="t.tags.filterBy(tag)" :aria-pressed="active === tag" :data-testid="`row-tag-${tag}`" @click.stop="emit('pick', tag)">{{ tag }}</button>
    </li>
  </ul>
</template>
