import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { brandCss } from '@/lib/brand'
import { setDateFormat } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { OrgPreferences } from '@/lib/types'
import { t } from '@/i18n'

const STYLE_ID = 'desk-brand'

/**
 * Les préférences d'affichage de l'organisation (`GET /preferences`) : nom et logo de la barre du haut, couleur des boutons, format de date.
 * Lues une fois à l'ouverture du Desk (et après un enregistrement des Paramètres) ; un serveur sans le plugin `system`, ou un échec, laisse
 * le Desk tel qu'il est : ce sont des préférences, jamais un obstacle.
 */
export const usePreferences = defineStore('preferences', () => {
  const { api, config } = useServices()
  const items = ref<OrgPreferences>({})
  let loading: Promise<void> | null = null

  /** Le nom affiché en haut : celui de l'organisation, sinon « Desk ». */
  const name = computed(() => items.value.organization_name?.trim() || t.appName)
  /** Le titre de la page courante (le coque le pose à chaque navigation) : « Visite · hunters » dans l'onglet et l'historique du navigateur. */
  const page = ref('')
  const documentTitle = computed(() => (page.value ? `${page.value} · ${name.value}` : name.value))
  watch(documentTitle, (v) => {
    if (typeof document !== 'undefined') document.title = v
  })
  /** L'adresse signée du logo (courte durée : voir `reload` si l'image échoue), ou `null`. */
  const logoUrl = computed(() => (items.value.logo ? `${config.baseUrl}/${items.value.logo.path}` : null))

  function apply(prefs: OrgPreferences): void {
    items.value = prefs
    setDateFormat(prefs.date_format)
    if (typeof document === 'undefined') return
    document.title = documentTitle.value
    let style = document.getElementById(STYLE_ID) as HTMLStyleElement | null
    const css = brandCss(prefs.brand_color)
    if (!css) return void style?.remove()
    if (!style) {
      style = document.createElement('style')
      style.id = STYLE_ID
      document.head.appendChild(style)
    }
    style.textContent = css
  }

  function load(force = false): Promise<void> {
    if (loading && !force) return loading
    loading = Promise.resolve()
      .then(() => api.preferences())
      .then(apply)
      .catch(() => apply({}))
      .finally(() => (loading = null))
    return loading
  }

  /** Déconnexion : l'organisation suivante ne doit pas hériter du nom, de la couleur ni du logo de la précédente. */
  function reset(): void {
    loading = null
    apply({}) // remet aussi le titre de l'onglet
  }

  return { items, name, page, documentTitle, logoUrl, load, reset }
})
