<script setup lang="ts">
import { useConfirm } from 'primevue/useconfirm'
import { computed, defineAsyncComponent, reactive, watch, type Component } from 'vue'
import { onBeforeRouteUpdate, useRouter } from 'vue-router'
import { t } from '@/i18n'
import { piIcon } from '@/lib/icons'
import { moduleLabel } from '@/lib/modules'
import type { ThemeMode } from '@/lib/theme'
import { useServices } from '@/lib/services'
import { useMeta } from '@/stores/meta'
import { usePreferences } from '@/stores/preferences'
import { useSession } from '@/stores/session'
import { useWorkspaces } from '@/stores/workspaces'
import { useTheme } from '@/stores/theme'
import FormView from '@/views/FormView.vue'

/**
 * « Paramètres » : UN seul endroit pour tous les réglages. Une rubrique « Général » (les réglages GLOBAUX de l'application : organisation,
 * affichage ; puis le thème et le compte de l'utilisateur), puis, par application, chacun de ses réglages (un document unique), qui
 * s'édite ICI, sur place, avec le formulaire habituel : on ne quitte pas la page pour changer un réglage d'une autre application. L'adresse dit la rubrique (`/settings/<Type>`), donc on peut la partager ou la rouvrir.
 */
const props = defineProps<{ doctype?: string }>()

const meta = useMeta()
const { auth } = useServices()

// Les pages d'administration d'xauth (comptes, membres, rôles, courrier, audit, API) : des rubriques « système », adresse `/settings/~<clé>`. Elles n'existent que
// connecté à xauth ; chacune dit elle-même si l'utilisateur n'a pas le droit de s'en servir.
const SYSTEM: Record<string, Component> = {
  account: defineAsyncComponent(() => import('@/components/admin/AccountSettings.vue')),
  organisation: defineAsyncComponent(() => import('@/components/admin/OrganisationSettings.vue')),
  users: defineAsyncComponent(() => import('@/components/admin/UsersSettings.vue')),
  roles: defineAsyncComponent(() => import('@/components/admin/RolesSettings.vue')),
  permissions: defineAsyncComponent(() => import('@/components/admin/PermissionsCatalog.vue')),
  accounts: defineAsyncComponent(() => import('@/components/admin/PlatformAccounts.vue')),
  mail: defineAsyncComponent(() => import('@/components/admin/MailSettings.vue')),
  audit: defineAsyncComponent(() => import('@/components/admin/AuditSettings.vue')),
  api: defineAsyncComponent(() => import('@/components/admin/ApiSettings.vue')),
}
// Les rubriques réservées à l'administrateur de la plateforme ne sont proposées qu'à lui (confort : xauth refuserait de toute façon aux autres).
const PLATFORM_ONLY = ['permissions', 'accounts', 'mail']
// Quand le plugin `iam` est là, les membres, les rôles et les permissions SONT des DocTypes (listes, filtres, rapports, fiches) : la rubrique ouvre alors leur liste
// au lieu d'une page faite à la main. Sans lui (ou sans le droit de les lire), la page d'administration d'xauth reste le repli.
const BACKED: Record<string, string> = { users: 'Member', roles: 'Role', permissions: 'Permission' }
const readable = computed(() => new Set((meta.summaries ?? []).map((x) => x.name)))
const railItems = computed<{ key: string; doctype: string | null }[]>(() => {
  if (!auth) return []
  return Object.keys(SYSTEM).flatMap((key): { key: string; doctype: string | null }[] => {
    const doctype = BACKED[key]
    if (doctype && readable.value.has(doctype)) return [{ key, doctype }]
    if (doctype && meta.summaries === null) return [] // les types ne sont pas encore connus : pas de lien de repli qui clignote
    if (!session.platformAdmin && PLATFORM_ONLY.includes(key)) return []
    return [{ key, doctype: null }]
  })
})
const systemKeys = computed(() => railItems.value.filter((i) => !i.doctype).map((i) => i.key))
// Tant que mes permissions ne sont pas lues, une adresse de rubrique réservée n'est pas « inconnue » : on attend de savoir (sinon un rechargement de page la nie un instant).
const knownKeys = computed(() => (session.permissions === null && auth ? Object.keys(SYSTEM) : systemKeys.value))
const section = computed(() => (props.doctype?.startsWith('~') ? props.doctype.slice(1) : null))
const session = useSession()
const theme = useTheme()
const workspaces = useWorkspaces()
const confirm = useConfirm()
const prefs = usePreferences()
const router = useRouter()
void meta.loadSummaries().catch(() => {})
void workspaces.load().catch(() => {})

/** Les réglages globaux, que « Général » montre : `System Settings` (plugin `system`) d'abord, puis ceux que d'autres plugins y ajoutent. */
const general = computed(() =>
  (meta.summaries ?? [])
    .filter((x) => x.single && x.settings_group === 'general')
    .sort((a, b) => Number(b.name === 'System Settings') - Number(a.name === 'System Settings') || a.label.localeCompare(b.label, 'fr')),
)

const groups = computed(() => {
  const by = new Map<string, { module: string; label: string; items: { name: string; label: string; icon: string | null | undefined }[] }>()
  for (const d of (meta.summaries ?? []).filter((x) => x.single && !x.settings_group)) {
    const key = d.module ?? 'other'
    // Le nom du module est un identifiant (`hr`) : l'espace de travail de ce module, quand il existe, en donne le libellé (« RH »).
    if (!by.has(key)) by.set(key, { module: key, label: workspaces.byName(key)?.label ?? moduleLabel(d.module), items: [] })
    by.get(key)!.items.push({ name: d.name, label: d.label, icon: d.icon })
  }
  const list = [...by.values()].sort((a, b) => a.label.localeCompare(b.label, 'fr'))
  for (const g of list) g.items.sort((a, b) => a.label.localeCompare(b.label, 'fr'))
  return list
})

// Les LISTES de l'application elle-même (plugin `system`) qu'une organisation règle, sans être un réglage unique : les règles de notification.
// Un lien sous « Général » quand on peut les lire (elles s'ouvrent dans leur liste, comme les membres et les rôles).
const APP_LISTS = ['Notification Rule']
const appLists = computed(() => APP_LISTS.flatMap((name) => (meta.summaries ?? []).filter((x) => x.name === name && !x.single)))

const selected = computed(() => (meta.summaries ?? []).find((d) => d.single && !d.settings_group && d.name === props.doctype))
// Un réglage global n'a pas de rubrique à lui (ancienne adresse, lien partagé) : c'est « Général ».
watch([() => props.doctype, general], () => {
  if (props.doctype && general.value.some((d) => d.name === props.doctype)) void router.replace({ name: 'settings' })
})
/** Une rubrique demandée qui n'est pas un réglage lisible (adresse périmée, droits retirés) : on le dit, on ne montre pas un formulaire. */
const unknown = computed(() =>
  section.value !== null ? !knownKeys.value.includes(section.value) : !!props.doctype && !!meta.summaries && !selected.value && !general.value.some((d) => d.name === props.doctype),
)

const modes: { value: ThemeMode; label: string; icon: string }[] = [
  { value: 'light', label: t.nav.themeLight, icon: 'pi-sun' },
  { value: 'dark', label: t.nav.themeDark, icon: 'pi-moon' },
  { value: 'system', label: t.nav.themeSystem, icon: 'pi-desktop' },
]

// Changer de rubrique garde cette page montée : le garde « quitter la page » du formulaire ne se déclenche pas, on garde donc nous-mêmes.
const unsaved = reactive(new Set<string>())
const markDirty = (name: string, on: boolean): void => void (on ? unsaved.add(name) : unsaved.delete(name))
// Un réglage global enregistré : le nom, le logo, la couleur et le format de date s'appliquent tout de suite, sans recharger la page.
const saved = (doctype: string): void => void (doctype === 'System Settings' && prefs.load(true))
onBeforeRouteUpdate((to, from) => {
  if (!unsaved.size || to.params.doctype === from.params.doctype) return true
  return new Promise<boolean>((resolve) => {
    confirm.require({
      header: t.form.unsavedTitle,
      message: t.settings.leave,
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: t.form.leave,
      rejectLabel: t.form.stay,
      rejectProps: { severity: 'secondary', text: true },
      accept: () => {
        unsaved.clear()
        resolve(true)
      },
      reject: () => resolve(false),
      onHide: () => resolve(false),
    })
  })
})
</script>

<template>
  <div class="desk-page" data-testid="settings">
    <div class="desk-page-head"><i class="pi pi-cog ws-icon" /><h1 data-testid="settings-title">{{ t.settings.title }}</h1></div>
    <p class="desk-muted ws-description">{{ t.settings.intro }}</p>

    <div class="settings-layout">
      <nav class="settings-rail" :aria-label="t.settings.menu" data-testid="settings-rail">
        <RouterLink class="settings-item" :to="{ name: 'settings' }" exact-active-class="active" data-testid="settings-nav-general"><i class="pi pi-sliders-h" />{{ t.settings.general }}</RouterLink>
        <RouterLink
          v-for="d in appLists"
          :key="d.name"
          class="settings-item"
          :to="{ name: 'list', params: { doctype: d.name } }"
          :data-testid="`settings-list-${d.name}`"
        >
          <i :class="piIcon(d.icon, 'list')" />{{ t.settings.lists[d.name] ?? d.label }}<i class="pi pi-arrow-up-right settings-out" :title="t.admin.opensList" />
        </RouterLink>
        <div v-for="g in groups" :key="g.module" class="settings-group" :data-testid="`settings-group-${g.module}`">
          <h4 class="settings-module">{{ g.label }}</h4>
          <RouterLink
            v-for="it in g.items"
            :key="it.name"
            class="settings-item"
            :to="{ name: 'settings', params: { doctype: it.name } }"
            exact-active-class="active"
            :title="t.settings.open(it.label)"
            :data-testid="`setting-${it.name}`"
          >
            <i :class="piIcon(it.icon, 'sliders-h')" />{{ it.label }}
          </RouterLink>
        </div>
        <div v-if="railItems.length" class="settings-group" data-testid="settings-group-system">
          <h4 class="settings-module">{{ t.admin.group }}</h4>
          <RouterLink
            v-for="item in railItems"
            :key="item.key"
            class="settings-item"
            :to="item.doctype ? { name: 'list', params: { doctype: item.doctype } } : { name: 'settings', params: { doctype: `~${item.key}` } }"
            exact-active-class="active"
            :data-testid="`system-${item.key}`"
          >
            <i :class="`pi pi-${t.admin.icons[item.key]}`" />{{ t.admin.sections[item.key] }}<i v-if="item.doctype" class="pi pi-arrow-up-right settings-out" :title="t.admin.opensList" />
          </RouterLink>
        </div>
        <p v-if="meta.summaries && !groups.length && !railItems.length" class="desk-muted settings-none" data-testid="settings-empty">{{ t.settings.none }}</p>
      </nav>

      <div class="settings-pane" data-testid="settings-pane">
        <p v-if="unknown" class="desk-card settings-unknown" data-testid="settings-unknown">{{ t.settings.unknown }}</p>

        <template v-else-if="section !== null">
          <div class="desk-page-head"><h1 data-testid="system-title">{{ t.admin.sections[section] }}</h1></div>
          <component :is="SYSTEM[section]" :key="section" />
        </template>

        <FormView v-else-if="selected" :key="selected.name" :doctype="selected.name" :name="selected.name" embedded data-testid="settings-form" @dirty="markDirty(selected.name, $event)" />

        <template v-else-if="!doctype">
          <FormView
            v-for="g in general"
            :key="g.name"
            :doctype="g.name"
            :name="g.name"
            embedded
            class="settings-general"
            :data-testid="`settings-general-${g.name}`"
            @dirty="markDirty(g.name, $event)"
            @saved="saved(g.name)"
          />
          <section class="home-section">
            <h3 class="home-title">{{ t.settings.appearance }}</h3>
            <div class="view-switcher" role="radiogroup" :aria-label="t.settings.theme">
              <button v-for="m in modes" :key="m.value" type="button" role="radio" :aria-checked="theme.mode === m.value" :class="['view-btn', { active: theme.mode === m.value }]" :data-testid="`theme-${m.value}`" @click="theme.setMode(m.value)">
                <i :class="['pi', m.icon]" aria-hidden="true" /><span>{{ m.label }}</span>
              </button>
            </div>
          </section>

          <section class="home-section">
            <h3 class="home-title">{{ t.settings.account }}</h3>
            <dl class="settings-account">
              <dt class="desk-muted">{{ t.settings.user }}</dt><dd data-testid="settings-user">{{ session.user || '—' }}</dd>
              <dt class="desk-muted">{{ t.settings.tenant }}</dt><dd data-testid="settings-tenant">{{ session.tenant || '—' }}</dd>
            </dl>
          </section>
        </template>
      </div>
    </div>
  </div>
</template>
