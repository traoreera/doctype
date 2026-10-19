<script setup lang="ts">
import type { LinksCardBlock } from '@/lib/types'

defineProps<{ block: LinksCardBlock }>()
</script>

<template>
  <section class="desk-card block links-card" :data-testid="`links-card-${block.title}`">
    <header class="block-head"><h3 class="block-title">{{ block.title }}</h3></header>
    <ul class="links-list">
      <li v-for="l in block.links" :key="l.doctype">
        <RouterLink :to="{ name: 'list', params: { doctype: l.doctype } }" class="desk-link" :data-testid="`link-${l.doctype}`">
          <i :class="`pi pi-${l.icon ?? 'file'}`" /> {{ l.label }}
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
