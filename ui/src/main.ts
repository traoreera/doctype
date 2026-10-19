import '@fontsource-variable/inter'
import 'primeicons/primeicons.css'
import '@/theme/desk.css'
import { loadConfig } from '@/lib/config'
import { FetchTransport } from '@/lib/transport'
import { createDesk } from '@/desk'

async function start(): Promise<void> {
  const transport = new FetchTransport()
  const config = await loadConfig(transport, { pathname: location.pathname, href: location.href }, { dev: import.meta.env.DEV })
  const { app, router } = createDesk(transport, config)
  await router.isReady()
  app.mount('#app')
}

void start()
