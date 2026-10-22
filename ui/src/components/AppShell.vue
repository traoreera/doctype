<script setup lang="ts">
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CommandPalette from '@/components/CommandPalette.vue'
import { useNotify } from '@/composables/useNotify'
import { useExtensions } from '@/extensions/host'
import { t } from '@/i18n'
import { piIcon } from '@/lib/icons'
import { initials, readNav, writeNav } from '@/lib/navstate'
import { useServices } from '@/lib/services'
import type { ThemeMode } from '@/lib/theme'
import type { Workspace } from '@/lib/types'
import { useMeta } from '@/stores/meta'
import { usePreferences } from '@/stores/preferences'
import { useRecents } from '@/stores/recents'
import NotificationsMenu from '@/components/NotificationsMenu.vue'
import FileThumb from '@/components/FileThumb.vue'
import { useProfile } from '@/stores/profile'
import { useTasks } from '@/stores/tasks'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { useWorkspaces } from '@/stores/workspaces'

const session = useSession()
const meta = useMeta()
const workspaces = useWorkspaces()
const recents = useRecents()
const prefs = usePreferences()
const tasks = useTasks()
const profile = useProfile()
const theme = useTheme()
const router = useRouter()
const route = useRoute()
const notify = useNotify()
const extensions = useExtensions()
// D28 : les liens que les extensions ajoutent sous un espace, ou dans un groupe à part quand elles n'en nomment aucun affiché.
const spaceNames = computed(() => (workspaces.items ?? []).map((w) => w.name))
const extLinks = (w: Workspace) => extensions.sidebarLinks(w.name)
const looseLinks = computed(() => extensions.sidebarLinks(null, spaceNames.value))
const services = useServices()

// Le logo est servi par un lien signé de courte durée : s'il a expiré pendant que la page restait ouverte, on le redemande UNE fois.
const logoFailed = ref(false)
let logoRetried = false
function onLogoError(): void {
  if (logoRetried) {
    logoFailed.value = true
    return
  }
  logoRetried = true
  void prefs.load(true)
}
watch(() => prefs.logoUrl, () => (logoFailed.value = false))

onMounted(() => {
  // Une extension écartée au démarrage : une seule notification, le Desk fonctionne sans elle (le détail est dans la console).
  if (extensions.failures.length) notify.warn(t.extensions.failed(extensions.failures.map((f) => f.name).join(', ')))
})
onMounted(async () => {
  recents.load()
  void tasks.refresh() // la pastille de « Mes tâches »
  void prefs.load() // nom, logo, couleur et format de date de l'organisation : sans bloquer le reste
  try {
    // Les espaces donnent la barre latérale ; les types servent la barre de commande et l'accueil.
    await Promise.all([meta.loadSummaries(), workspaces.load()])
  } catch (e) {
    notify.error(e)
  }
  void profile.load() // mon nom affiché et ma photo, si le DocType existe pour moi
})

// ── Barre latérale : tiroir sur écran étroit, repliable sur grand écran ─────────────────────────────────────────
const navOpen = ref(false)
const nav = reactive(readNav())
const narrow = ref(typeof matchMedia === 'function' && matchMedia('(max-width: 900px)').matches)
let mq: MediaQueryList | null = null
const onMq = (e: MediaQueryListEvent) => (narrow.value = e.matches)

function toggleSidebar(): void {
  if (narrow.value) navOpen.value = !navOpen.value
  else {
    nav.collapsed = !nav.collapsed
    writeNav({ collapsed: nav.collapsed, expanded: { ...nav.expanded } })
  }
}
watch(() => route.fullPath, () => (navOpen.value = false))

// ── Le tiroir (écran étroit) est une fenêtre modale pour le clavier et le lecteur d'écran (C3.2) ───────────────────────────
// Ouvert : le focus entre dans le tiroir, Tab tourne entre le bouton du menu (qui le referme) et le tiroir, le contenu de la page est `inert`.
// Fermé par Échap ou le fond : le focus revient au bouton du menu. Fermé par une navigation : il va au titre de la nouvelle page
// (`landOnPage`), comme toute navigation. Fermé, le tiroir hors écran est `inert` lui aussi : Tab ne s'y perd plus.
const drawer = ref<HTMLElement | null>(null)
const drawerModal = computed(() => narrow.value && navOpen.value)
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
const toggleButton = (): HTMLElement | null => document.querySelector<HTMLElement>('[data-testid="nav-toggle"]')
const drawerItems = (): HTMLElement[] => [...(drawer.value?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])]
const focusables = (): HTMLElement[] => [toggleButton(), ...drawerItems()].filter((x): x is HTMLElement => !!x)
watch(drawerModal, async (open) => {
  if (!open) return
  await nextTick()
  drawerItems()[0]?.focus()
})
/** Fermer le tiroir et rendre le focus au bouton qui l'a ouvert (Échap, clic sur le fond). */
function closeDrawer(): void {
  navOpen.value = false
  void nextTick(() => toggleButton()?.focus())
}
function trapTab(e: KeyboardEvent): void {
  if (!drawerModal.value || e.key !== 'Tab') return
  const items = focusables()
  if (!items.length) return
  const first = items[0]!
  const last = items[items.length - 1]!
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault()
    first.focus()
  }
}

// ── Changement de page : le titre de l'onglet, et le focus sur le titre de la page (lecteur d'écran, clavier) ─────────────
const main = ref<HTMLElement | null>(null)
let pageTicket = 0
/** Les pages se remplissent après un appel au serveur : on cherche leur titre (h1) pendant ~1,5 s, puis on y pose le focus. Un simple changement de filtre (la requête) n'y touche pas. */
async function landOnPage(focus: boolean): Promise<void> {
  const mine = ++pageTicket
  await nextTick()
  for (let i = 0; i < 10; i++) {
    const heading = main.value?.querySelector('h1')
    if (heading?.textContent?.trim()) {
      if (mine !== pageTicket) return
      prefs.page = heading.textContent.trim()
      if (focus) {
        heading.setAttribute('tabindex', '-1')
        heading.focus({ preventScroll: true })
      }
      return
    }
    await new Promise((r) => setTimeout(r, 150))
    if (mine !== pageTicket || !main.value) return
  }
  prefs.page = ''
  if (focus) main.value?.focus({ preventScroll: true })
}
watch(() => route.path, () => void landOnPage(true))
onMounted(() => void landOnPage(false)) // la première page : son titre, sans voler le focus
function skipToContent(): void {
  const heading = main.value?.querySelector('h1') as HTMLElement | null
  if (heading) {
    heading.setAttribute('tabindex', '-1')
    heading.focus()
  } else main.value?.focus()
}

/** L'espace de la page courante : celui qu'on déplie d'office, et celui qu'on met en évidence. */
/** Un compte qui peut lire au moins un document unique voit « Paramètres ». */
const hasSettings = computed(() => (meta.summaries ?? []).some((d) => d.single))

const currentSpace = computed(() => {
  if (route.name === 'workspace') return String(route.params.name)
  const doctype = typeof route.params.doctype === 'string' ? route.params.doctype : null
  return doctype ? workspaces.owning(doctype)?.name : undefined
})
const isOpen = (w: Workspace) => nav.expanded[w.name] ?? currentSpace.value === w.name
const sectionsOf = (w: Workspace) => workspaces.sections(w)
/** Un sous-module est déplié tant que l'utilisateur ne l'a pas replié (choix gardé sur cet appareil, comme celui des espaces). */
const sectionKey = (w: Workspace, title: string) => `${w.name}/${title}`
const sectionOpen = (w: Workspace, title: string) => nav.expanded[sectionKey(w, title)] ?? true
function toggleSection(w: Workspace, title: string): void {
  nav.expanded[sectionKey(w, title)] = !sectionOpen(w, title)
  writeNav({ collapsed: nav.collapsed, expanded: { ...nav.expanded } })
}
function toggleSpace(w: Workspace): void {
  nav.expanded[w.name] = !isOpen(w)
  writeNav({ collapsed: nav.collapsed, expanded: { ...nav.expanded } })
}

// ── Raccourcis clavier ──────────────────────────────────────────────────────────────────────────────────────────
const paletteOpen = ref(false)
function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape' && navOpen.value) closeDrawer()
  trapTab(e)
  // Ctrl+G, comme dans Frappe ; Ctrl+K, l'usage ailleurs. Le navigateur y met « suivant » et « recherche » : on les reprend.
  if ((e.ctrlKey || e.metaKey) && !e.altKey && ['g', 'k'].includes(e.key.toLowerCase())) {
    e.preventDefault()
    paletteOpen.value = !paletteOpen.value
  }
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  if (typeof matchMedia === 'function') {
    mq = matchMedia('(max-width: 900px)')
    mq.addEventListener('change', onMq)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  mq?.removeEventListener('change', onMq)
})
// Un jeton de développement ne se renouvelle pas : on prévient avant qu'il expire au milieu d'une saisie.
watch(() => session.expiringSoon, (soon) => soon && notify.warn(t.login.expiresSoon))

// ── Menu de l'utilisateur ───────────────────────────────────────────────────────────────────────────────────────
const userMenu = ref()
// Les autres organisations du compte : on les demande au serveur à l'ouverture de la coquille (jamais devinées du jeton).
onMounted(() => {
  if (services.auth && session.authenticated) {
    void session.loadTenants()
    void session.loadPermissions()
  }
})
const otherTenants = computed(() => session.tenants.filter((tenant) => tenant.id !== session.tenant))
const switching = ref(false)
async function switchTenant(tenantId: string): Promise<void> {
  if (switching.value) return
  switching.value = true
  try {
    const out = await session.switchTenant(tenantId)
    if (out.status === 'ok') globalThis.location.reload()
    else notify.error(out.message)
  } finally {
    switching.value = false
  }
}
const items = computed(() => [
  { label: profile.label || '—', disabled: true },
  ...(profile.fullName ? [{ label: session.user, disabled: true, class: 'desk-menu-title' }] : []),
  ...(session.tenant ? [{ label: `${t.nav.tenant} : ${session.tenant}`, disabled: true }] : []),
  ...(otherTenants.value.length
    ? [
        {
          label: t.login.switchTenant,
          icon: 'pi pi-building',
          items: otherTenants.value.map((tenant) => ({
            label: tenant.name ?? tenant.id,
            command: () => void switchTenant(tenant.id),
          })),
        },
      ]
    : []),
  ...(profile.available ? [{ label: t.profile.mine, icon: 'pi pi-id-card', command: () => void router.push({ name: 'profile' }) }] : []),
  ...(services.auth ? [{ label: t.admin.sections.account, icon: 'pi pi-user', command: () => void router.push({ name: 'settings', params: { doctype: '~account' } }) }] : []),
  { separator: true },
  { label: t.nav.theme, disabled: true, class: 'desk-menu-title' },
  ...(['light', 'dark', 'system'] as ThemeMode[]).map((m) => ({
    label: { light: t.nav.themeLight, dark: t.nav.themeDark, system: t.nav.themeSystem }[m],
    icon: theme.mode === m ? 'pi pi-check' : 'pi pi-circle',
    command: () => theme.setMode(m),
  })),
  { separator: true },
  { label: t.nav.logout, icon: 'pi pi-sign-out', command: () => session.logout() },
])
</script>

<template>
  <div class="desk-shell" :class="{ collapsed: nav.collapsed && !narrow }">
    <a class="skip-link" href="#desk-main" data-testid="skip-link" @click.prevent="skipToContent">{{ t.nav.skip }}</a>
    <header class="desk-navbar">
      <Button
        class="desk-burger"
        text
        severity="secondary"
        size="small"
        icon="pi pi-bars"
        :aria-label="t.nav.menu"
        :aria-expanded="narrow ? navOpen : !nav.collapsed"
        aria-controls="desk-sidebar"
        data-testid="nav-toggle"
        @click="toggleSidebar"
      />
      <RouterLink class="desk-brand" :to="{ name: 'home' }" :aria-label="prefs.name" data-testid="brand">
        <img v-if="prefs.logoUrl && !logoFailed" class="desk-brand-logo" :src="prefs.logoUrl" alt="" data-testid="brand-logo" @error="onLogoError" />
        <i v-else class="pi pi-th-large" />
        <span class="desk-brand-name" data-testid="brand-name">{{ prefs.name }}</span>
      </RouterLink>
      <button type="button" class="desk-search" data-testid="search" @click="paletteOpen = true">
        <i class="pi pi-search" />
        <span class="desk-search-text">{{ t.palette.placeholder }}</span>
        <kbd>{{ t.palette.hint }}</kbd>
      </button>
      <div class="desk-spacer" />
      <NotificationsMenu v-if="services.auth" />
      <button type="button" class="desk-avatar" data-testid="user-menu" :aria-label="profile.label" :title="profile.label" @click="userMenu.toggle($event)">
        <FileThumb v-if="profile.photo" :id="profile.photo" :size="24" round :alt="profile.label" />
        <template v-else>{{ initials(profile.label) }}</template>
      </button>
      <Menu ref="userMenu" :model="items" popup />
    </header>

    <div v-if="navOpen" class="desk-backdrop" data-testid="nav-backdrop" @click="closeDrawer" />
    <nav
      id="desk-sidebar"
      ref="drawer"
      class="desk-sidebar"
      :class="{ open: navOpen }"
      :data-open="narrow ? navOpen : !nav.collapsed"
      aria-label="Espaces de travail"
      :role="drawerModal ? 'dialog' : undefined"
      :aria-modal="drawerModal ? 'true' : undefined"
      :inert="(narrow && !navOpen) || undefined"
    >
      <RouterLink class="desk-link" :to="{ name: 'home' }" exact-active-class="router-link-active"><i class="pi pi-home" />{{ t.home }}</RouterLink>
      <RouterLink class="desk-link" :to="{ name: 'tasks' }" data-testid="nav-tasks">
        <i class="pi pi-check-square" />{{ t.collab.tasks.title }}
        <span v-if="tasks.openCount" class="nav-badge" data-testid="tasks-count" :aria-label="`${tasks.openCount}`">{{ tasks.openCount }}</span>
      </RouterLink>
      <h6>{{ t.workspace.spaces }}</h6>
      <div v-for="w in workspaces.items ?? []" :key="w.name" class="ws-item" :data-testid="`ws-${w.name}`">
        <div class="desk-link ws-row" :class="{ 'router-link-active': currentSpace === w.name }">
          <RouterLink class="ws-main" :to="{ name: 'workspace', params: { name: w.name } }"><i :class="piIcon(w.icon, 'folder')" /><span>{{ w.label }}</span></RouterLink>
          <button
            type="button"
            class="ws-toggle"
            :aria-expanded="isOpen(w)"
            :aria-label="isOpen(w) ? t.workspace.collapse(w.label) : t.workspace.expand(w.label)"
            :data-testid="`ws-toggle-${w.name}`"
            @click="toggleSpace(w)"
          >
            <i :class="['pi', isOpen(w) ? 'pi-chevron-down' : 'pi-chevron-right']" />
          </button>
        </div>
        <div v-if="isOpen(w)" class="ws-sub">
          <template v-for="(sec, i) in sectionsOf(w)" :key="sec.title ?? i">
            <!-- Des sous-modules : leur titre n'a de sens que s'il y en a plusieurs ; un seul groupe se lit comme une liste. -->
            <button
              v-if="sectionsOf(w).length > 1 && sec.title"
              type="button"
              class="ws-section"
              :aria-expanded="sectionOpen(w, sec.title)"
              :data-testid="`ws-section-${w.name}-${sec.title}`"
              @click="toggleSection(w, sec.title)"
            >
              <i :class="['pi', sectionOpen(w, sec.title) ? 'pi-chevron-down' : 'pi-chevron-right']" aria-hidden="true" />{{ sec.title }}
            </button>
            <template v-if="!sec.title || sectionsOf(w).length === 1 || sectionOpen(w, sec.title)">
              <RouterLink v-for="s in sec.items" :key="s.doctype" class="desk-link ws-sublink" :to="{ name: 'list', params: { doctype: s.doctype } }" :data-testid="`nav-${s.doctype}`">
                <i :class="piIcon(s.icon, 'file')" aria-hidden="true" />{{ s.label }}
              </RouterLink>
            </template>
          </template>
          <RouterLink v-for="l in extLinks(w)" :key="`${l.extension}:${l.to}`" class="desk-link ws-sublink" :to="l.to" :data-testid="`nav-ext-${l.extension}-${l.label}`">
            <i :class="piIcon(l.icon, 'bolt')" aria-hidden="true" />{{ l.label }}
          </RouterLink>
        </div>
      </div>
      <template v-if="looseLinks.length">
        <h6>{{ t.extensions.group }}</h6>
        <RouterLink v-for="l in looseLinks" :key="`${l.extension}:${l.to}`" class="desk-link" :to="l.to" :data-testid="`nav-ext-${l.extension}-${l.label}`">
          <i :class="piIcon(l.icon, 'bolt')" aria-hidden="true" />{{ l.label }}
        </RouterLink>
      </template>
      <div class="desk-sidebar-foot" v-if="hasSettings">
        <RouterLink class="desk-link" :to="{ name: 'settings' }" data-testid="nav-settings"><i class="pi pi-cog" />{{ t.settings.title }}</RouterLink>
      </div>
    </nav>

    <main id="desk-main" ref="main" class="desk-main" tabindex="-1" :inert="drawerModal || undefined"><slot /></main>
    <CommandPalette v-model:open="paletteOpen" />
  </div>
</template>
