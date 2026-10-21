import '@fontsource-variable/inter'
import 'primeicons/primeicons.css'
import '@/theme/desk.css'
import { loadConfig } from '@/lib/config'
import { FetchTransport } from '@/lib/transport'
import { createDesk } from '@/desk'

async function start(): Promise<void> {
  const transport = new FetchTransport()
  const config = await loadConfig(transport, { pathname: location.pathname, href: location.href }, { dev: import.meta.env.DEV })
  const desk = createDesk(transport, config)
  const { app, router } = desk
  await desk.loadExtensions()
  await router.isReady()
  // La première navigation part dès `app.use(router)`, avant que les pages des extensions existent : un lien direct vers `#/x/…` est
  // alors tombé sur « introuvable ». On le rejoue une fois qu'elles sont là.
  if (router.currentRoute.value.name === 'not-found' && desk.extensions.loaded.length) await router.replace(router.currentRoute.value.fullPath)
  app.mount('#app')
}

void start()
