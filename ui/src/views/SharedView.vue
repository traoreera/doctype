<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { SharedDoc } from '@/lib/types'
import { useUsers } from '@/stores/users'

/**
 * « Partagés avec moi » : les documents qu'on a partagés avec l'utilisateur, les plus récents d'abord. C'est leur seule entrée quand il n'a
 * aucun rôle sur le type (la liste du type lui reste fermée) ; un clic ouvre la fiche.
 */
const { api } = useServices()
const users = useUsers()
const items = ref<SharedDoc[] | null>(null)
const failure = ref<string | null>(null)

onMounted(async () => {
  try {
    items.value = await api.sharedWithMe()
  } catch (e) {
    failure.value = describeError(e)
  }
})
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head"><h1>{{ t.share.withMe }}</h1></div>
    <p v-if="failure" class="desk-muted" data-testid="shared-failed">{{ failure }}</p>
    <p v-else-if="items && !items.length" class="desk-muted" data-testid="shared-empty">{{ t.share.withMeEmpty }}</p>
    <ul v-else-if="items" class="desk-card shared-list" data-testid="shared-list">
      <li v-for="d in items" :key="`${d.doctype}/${d.name}`" :data-testid="`shared-${d.doctype}-${d.name}`">
        <RouterLink class="desk-link shared-title" :to="{ name: 'form', params: { doctype: d.doctype, name: d.name } }">{{ d.title || d.name }}</RouterLink>
        <span class="desk-muted">{{ d.label }}</span>
        <span class="status-pill">{{ d.write ? t.share.write : t.share.read }}</span>
        <span class="desk-muted shared-meta">{{ t.share.by(users.label(d.owner)) }} · {{ relativeTime(d.creation) }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.shared-list {
  list-style: none;
  margin: 0;
  padding: 0.5rem 1rem;
  display: grid;
}

.shared-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--p-content-border-color);
}

.shared-list li:last-child {
  border-bottom: none;
}

.shared-title {
  font-weight: 500;
}

.shared-meta {
  margin-left: auto;
  font-size: 0.85rem;
}
</style>
