import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { createPinia } from 'pinia'
import { createApp, watch, type App as VueApp } from 'vue'
import type { Router } from 'vue-router'
import App from '@/App.vue'
import { useNotify } from '@/composables/useNotify'
import { EXTENSIONS, ExtensionHost } from '@/extensions/host'
import { loadExtensions, type Importer } from '@/extensions/loader'
import { ApiClient, PluginClient, pluginBase } from '@/lib/api'
import type { DeskConfig } from '@/lib/config'
import { SERVICES, type Services } from '@/lib/services'
import type { Transport } from '@/lib/transport'
import { createDeskRouter } from '@/router'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { usePreferences } from '@/stores/preferences'
import { useTasks } from '@/stores/tasks'
import { useUsers } from '@/stores/users'
import { authBase, XauthClient } from '@/lib/xauth'
import { useWorkspaces } from '@/stores/workspaces'
import { primeVueOptions } from '@/theme/frappe'
import { frLocale } from '@/theme/locale'

export interface Desk {
  app: VueApp
  router: Router
  services: Services
  extensions: ExtensionHost
  /** Charge les extensions que `config.json` annonce (D28) : AVANT la première navigation, pour que leurs pages répondent aux liens directs. */
  loadExtensions(importer?: Importer): Promise<void>
}

/**
 * Assemble l'application à partir de ce que l'hôte fournit : un transport réseau et une configuration. Le navigateur lui
 * donne `FetchTransport` et `config.json` ; une application desktop lui donnera un transport Tauri et l'URL de son serveur.
 */
export function createDesk(transport: Transport, config: DeskConfig): Desk {
  const pinia = createPinia()
  const api = new ApiClient(
    transport,
    () => {
      const s = useSession(pinia)
      return { baseUrl: config.baseUrl, token: s.token, tenant: s.tenant }
    },
    async () => {
      const s = useSession(pinia)
      if ((await s.refresh()) === 'ok') return true
      s.expire()
      return false
    },
  )
  // xauth : même transport, même jeton, même renouvellement ; racine déduite de `login_url`.
  const base = authBase(config.loginUrl)
  const auth = base
    ? new XauthClient(
        transport,
        () => {
          const s = useSession(pinia)
          return { baseUrl: base, token: s.token, tenant: s.tenant }
        },
        async () => {
          const s = useSession(pinia)
          if ((await s.refresh()) === 'ok') return true
          s.expire()
          return false
        },
      )
    : null
  const services: Services = { transport, config, api, auth }

  const router = createDeskRouter(pinia)
  const app = createApp(App)
  app.provide(SERVICES, services)
  app.use(pinia)
  app.use(router)
  app.use(PrimeVue, { ...primeVueOptions, locale: frLocale })
  app.use(ToastService)
  app.use(ConfirmationService)

  // Les extensions des plugins (D28) : leurs appels passent par la même session que le Desk, leurs notifications par la même file.
  const plugins = new Map<string, PluginClient>()
  const extensions = new ExtensionHost({
    api,
    router,
    notify: app.runWithContext(() => useNotify()),
    plugin(name) {
      let client = plugins.get(name)
      if (!client) {
        client = new PluginClient(
          transport,
          () => {
            const s = useSession(pinia)
            return { baseUrl: pluginBase(config.baseUrl, name), token: s.token, tenant: s.tenant }
          },
          async () => {
            const s = useSession(pinia)
            if ((await s.refresh()) === 'ok') return true
            s.expire()
            return false
          },
        )
        plugins.set(name, client)
      }
      return client
    },
  })
  app.provide(EXTENSIONS, extensions)

  useSession(pinia).start() // renouvelle le jeton avant son expiration (xauth)
  useTheme(pinia).start() // avant le premier affichage : pas d'éclair de thème clair pour qui préfère le sombre

  // Une session qui prend fin (déconnexion, jeton expiré, 401) ramène à l'écran de connexion et vide les caches.
  const session = useSession(pinia)
  watch(
    () => session.authenticated,
    (ok) => {
      if (ok) return
      useMeta(pinia).reset()
      useLinks(pinia).reset()
      useWorkspaces(pinia).reset()
      usePreferences(pinia).reset()
      useTasks(pinia).reset()
      useUsers(pinia).reset()
      const here = router.currentRoute.value
      if (!here.meta.public) void router.replace({ name: 'login', query: here.fullPath !== '/' ? { next: here.fullPath } : {} })
    },
  )
  return {
    app,
    router,
    services,
    extensions,
    loadExtensions: (importer) => loadExtensions(extensions, config.extensions ?? [], importer ? { importer } : {}),
  }
}
