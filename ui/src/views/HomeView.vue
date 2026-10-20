<script setup lang="ts">
import Button from 'primevue/button'
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { t } from '@/i18n'
import { isOverdue, sortAssignments } from '@/lib/feed'
import { formatDate, relativeTime, todayIn } from '@/lib/format'
import { piIcon } from '@/lib/icons'
import { useMeta } from '@/stores/meta'
import { useProfile } from '@/stores/profile'
import { useRecents } from '@/stores/recents'
import { useTasks } from '@/stores/tasks'
import { useWorkspaces } from '@/stores/workspaces'

const meta = useMeta()
const recents = useRecents()
const workspaces = useWorkspaces()
const tasks = useTasks()
const profile = useProfile()
onMounted(() => {
  recents.load()
  void workspaces.load().catch(() => {})
  void tasks.refresh()
})

const labelOf = (doctype: string) => meta.summaries?.find((d) => d.name === doctype)?.label
// Les récents d'un type que le compte ne voit plus (droits retirés) n'y figurent pas.
const shown = computed(() => recents.items.filter((r) => labelOf(r.doctype)))

/** « Bonjour, Eliezer » : le prénom du profil, sinon ce qui précède l'@ de l'adresse. */
const firstName = computed(() => {
  const full = profile.fullName?.trim()
  if (full) return full.split(/\s+/)[0] ?? ''
  const who = profile.label || ''
  return who.includes('@') ? (who.split('@')[0] ?? '') : who
})
const today = todayIn()
const late = computed(() => tasks.items.filter((a) => isOverdue(a, today)).length)
/** Les tâches les plus proches : l'échéance la plus proche d'abord. */
const upcoming = computed(() => sortAssignments(tasks.items).slice(0, 4))

/** « Créer » : les types qu'on a ouverts récemment d'abord (on y revient), puis les autres, six au plus. */
const creatable = computed(() => {
  const types = (meta.summaries ?? []).filter((d) => d.create && !d.single && !d.virtual)
  const used = [...new Set(recents.items.map((r) => r.doctype))]
  const rank = (name: string) => (used.includes(name) ? used.indexOf(name) : used.length)
  return [...types].sort((a, b) => rank(a.name) - rank(b.name) || a.label.localeCompare(b.label, 'fr')).slice(0, 6)
})
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head"><h1>{{ t.home }}</h1></div>
    <p class="home-greeting" data-testid="greeting">{{ t.welcome.greeting(new Date().getHours(), firstName) }}</p>
    <p v-if="workspaces.items && !workspaces.items.length" class="desk-muted" data-testid="no-doctype">Aucun type de document n’est accessible avec ce compte.</p>

    <div v-if="creatable.length" class="home-create" data-testid="home-create" role="group" :aria-label="t.welcome.create">
      <Button v-for="d in creatable" :key="d.name" :as="RouterLink" :to="{ name: 'new', params: { doctype: d.name } }" :label="d.label" icon="pi pi-plus" size="small" severity="secondary" :data-testid="`create-${d.name}`" />
    </div>

    <section v-if="upcoming.length" class="home-section" data-testid="home-tasks">
      <h2 class="home-title">{{ t.welcome.todo }}</h2>
      <div class="desk-card home-recents">
        <RouterLink v-for="a in upcoming" :key="a.id" class="desk-link recent-row" :to="{ name: 'form', params: { doctype: a.doctype, name: a.docname } }" :data-testid="`task-${a.id}`">
          <i class="pi pi-check-square" aria-hidden="true" />
          <span class="recent-title">{{ a.description || `${a.doctype_label ?? a.doctype} ${a.docname}` }}</span>
          <span class="desk-muted recent-type">{{ a.doctype_label ?? a.doctype }}</span>
          <span v-if="a.due_date" :class="['recent-time', isOverdue(a, today) ? 'task-late' : 'desk-muted']">{{ isOverdue(a, today) ? t.welcome.late : t.welcome.due(formatDate(a.due_date)) }}</span>
        </RouterLink>
        <RouterLink class="home-more" :to="{ name: 'tasks' }">{{ t.welcome.tasks(tasks.openCount, late) }} {{ t.welcome.seeAll }}</RouterLink>
      </div>
    </section>

    <section v-if="shown.length" class="home-section" data-testid="recents">
      <h2 class="home-title">{{ t.homeRecent }}</h2>
      <div class="desk-card home-recents">
        <RouterLink v-for="r in shown" :key="`${r.doctype}/${r.name}`" class="desk-link recent-row" :to="{ name: 'form', params: { doctype: r.doctype, name: r.name } }" :data-testid="`recent-${r.name}`">
          <i class="pi pi-clock" aria-hidden="true" />
          <span class="recent-title">{{ r.title }}</span>
          <span class="desk-muted recent-type">{{ labelOf(r.doctype) }}</span>
          <time class="desk-muted recent-time" :datetime="new Date(r.at).toISOString()">{{ relativeTime(new Date(r.at).toISOString()) }}</time>
        </RouterLink>
      </div>
    </section>

    <section v-if="workspaces.items?.length" class="home-section">
      <h2 class="home-title">{{ t.workspace.spaces }}</h2>
      <div class="home-grid">
        <RouterLink v-for="w in workspaces.items" :key="w.name" class="desk-card home-space" :to="{ name: 'workspace', params: { name: w.name } }" :data-testid="`space-${w.name}`">
          <i :class="piIcon(w.icon, 'folder')" aria-hidden="true" />
          <span class="home-space-text">
            <strong>{{ w.label }}</strong>
            <small v-if="w.description" class="desk-muted">{{ w.description }}</small>
          </span>
        </RouterLink>
      </div>
    </section>
  </div>
</template>
