<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import { useMeta } from '@/stores/meta'
import { useRecents } from '@/stores/recents'
import { useWorkspaces } from '@/stores/workspaces'

const meta = useMeta()
const recents = useRecents()
const workspaces = useWorkspaces()
onMounted(() => {
  recents.load()
  void workspaces.load().catch(() => {})
})

const labelOf = (doctype: string) => meta.summaries?.find((d) => d.name === doctype)?.label
// Les récents d'un type que le compte ne voit plus (droits retirés) n'y figurent pas.
const shown = computed(() => recents.items.filter((r) => labelOf(r.doctype)))
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head"><h1>{{ t.home }}</h1></div>
    <p v-if="workspaces.items && !workspaces.items.length" class="desk-muted" data-testid="no-doctype">Aucun type de document n’est accessible avec ce compte.</p>

    <section v-if="shown.length" class="home-section" data-testid="recents">
      <h3 class="home-title">{{ t.homeRecent }}</h3>
      <div class="desk-card home-recents">
        <RouterLink v-for="r in shown" :key="`${r.doctype}/${r.name}`" class="desk-link recent-row" :to="{ name: 'form', params: { doctype: r.doctype, name: r.name } }" :data-testid="`recent-${r.name}`">
          <i class="pi pi-clock" />
          <span class="recent-title">{{ r.title }}</span>
          <span class="desk-muted recent-type">{{ labelOf(r.doctype) }}</span>
          <time class="desk-muted recent-time" :datetime="new Date(r.at).toISOString()">{{ relativeTime(new Date(r.at).toISOString()) }}</time>
        </RouterLink>
      </div>
    </section>

    <section v-if="workspaces.items?.length" class="home-section">
      <h3 class="home-title">{{ t.workspace.spaces }}</h3>
      <div class="home-grid">
        <RouterLink v-for="w in workspaces.items" :key="w.name" class="desk-card home-space" :to="{ name: 'workspace', params: { name: w.name } }" :data-testid="`space-${w.name}`">
          <i :class="`pi pi-${w.icon ?? 'folder'}`" />
          <span class="home-space-text">
            <strong>{{ w.label }}</strong>
            <small v-if="w.description" class="desk-muted">{{ w.description }}</small>
          </span>
        </RouterLink>
      </div>
    </section>
  </div>
</template>
