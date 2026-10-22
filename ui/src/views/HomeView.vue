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
import BlockGrid from '@/components/BlockGrid.vue'
import type { NumberCardBlock, WorkspaceShortcut } from '@/lib/types'
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

/**
 * Les chiffres de l'accueil. Ils ne s'inventent pas : ce sont les cartes que les ESPACES déclarent déjà dans leur YAML, filtrées par
 * le serveur sur les droits du demandeur. Même règle que « Créer » : la première carte de chaque espace d'abord (un chiffre par
 * module), puis les suivantes dans l'ordre, jusqu'à deux rangées pleines. Cliquer une carte ouvre la liste qu'elle vient de compter.
 */
const MAX_FIGURES = 8
const figures = computed<NumberCardBlock[]>(() => {
  const spaces = (workspaces.items ?? []).map((w) => w.blocks.filter((b): b is NumberCardBlock => b.type === 'number_card'))
  const first = spaces.map((cards) => cards[0]).filter((c): c is NumberCardBlock => c !== undefined)
  const rest = spaces.flatMap((cards) => cards.slice(1))
  return [...first, ...rest].slice(0, MAX_FIGURES)
})

/** L'accueil n'a rien à raconter : ni chiffre, ni tâche, ni document repris. Les espaces sont alors le seul contenu, on les montre partout. */
const bare = computed(() => !figures.value.length && !upcoming.value.length && !shown.value.length)

/**
 * « Créer » : six types au plus, et les bons. L'ordre ne peut pas être l'alphabet (il mettait « Devise » devant « Facture »), ni les seuls
 * récents (vides sur tout navigateur neuf, donc l'alphabet gouvernait en pratique). Il vient des ESPACES, qui disent déjà la priorité
 * métier : leur `order` du YAML, puis la place du raccourci dans l'espace. On prend le PREMIER type créable de chaque espace — un par
 * module, donc la facture et le mouvement de stock, pas deux référentiels —, puis les types ouverts récemment, puis le reste par ordre
 * alphabétique. Un document par utilisateur (un profil) n'est jamais proposé : il n'y en a qu'un, et c'est sa page qui le crée.
 */
const creatable = computed(() => {
  const types = (meta.summaries ?? []).filter((d) => d.create && !d.single && !d.virtual && !d.per_user)
  const known = new Set(types.map((d) => d.name))
  const champions: string[] = []
  for (const w of workspaces.items ?? []) {
    const shortcuts = w.blocks.filter((b): b is WorkspaceShortcut => b.type === 'shortcut')
    const first = shortcuts.find((s) => s.create && known.has(s.doctype) && !champions.includes(s.doctype))
    if (first) champions.push(first.doctype)
  }
  const used = [...new Set(recents.items.map((r) => r.doctype))]
  const rank = (name: string) => {
    const champion = champions.indexOf(name)
    if (champion >= 0) return champion
    const recent = used.indexOf(name)
    return champions.length + (recent >= 0 ? recent : used.length)
  }
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

    <!-- Les chiffres d'abord : c'est ce qu'on vient lire en ouvrant l'application. Les cartes sont celles des espaces (BlockGrid), donc
         elles se cliquent vers la liste qu'elles comptent et portent les couleurs déclarées dans les YAML. -->
    <section v-if="figures.length" class="home-section" data-testid="home-figures">
      <h2 class="home-title">{{ t.welcome.figures }}</h2>
      <BlockGrid :blocks="figures" />
    </section>

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

    <!-- Les espaces : la barre latérale les liste déjà. Dès que l'accueil a de quoi parler (des tâches, des documents repris), ces cartes
         ne sont plus qu'une redite du menu visible à côté : `home-spaces` les réserve alors aux écrans où le menu est escamoté (desk.css).
         Quand l'accueil n'a rien d'autre à montrer — compte neuf, navigateur neuf — elles restent partout : c'est la porte d'entrée. -->
    <section v-if="workspaces.items?.length" class="home-section" :class="{ 'home-spaces': !bare }" data-testid="home-spaces">
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
