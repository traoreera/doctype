/**
 * Le point d'entrée de l'application de BUREAU (Tauri). Le pendant de `src/main.ts`, qui reste celui du navigateur et n'a pas changé de
 * comportement : même `createDesk`, mais un transport natif, une configuration lue chez le serveur configuré, et l'hôte de bureau.
 *
 * Ordre du démarrage, chaque étape pouvant REFUSER avec sa cause (affichée, jamais avalée) :
 * 1. `desk.json` (l'adresse du serveur) — `desktop/config.readDesktopConfig` ;
 * 2. le coffre de session du processus — avant le Desk, dont le magasin de session lit ses jetons à sa création ;
 * 3. `config.json` du serveur, par le transport natif — `desktop/config.loadServerConfig` ;
 * 4. le Desk, avec ce que l'hôte ne sait pas faire désactivé et annoncé ; puis la garde des liens et les liens profonds.
 */
import '@fontsource-variable/inter'
import 'primeicons/primeicons.css'
import '@/theme/desk.css'
import { createDesk } from '@/desk'
import { useSession } from '@/stores/session'
import { allowedOrigins, disabledByConfig, loadServerConfig, readDesktopConfig } from './config'
import { readDeepLink } from './deeplink'
import { desktopHost } from './host'
import { installLinkGuard } from './links'
import { nativeFetch, readHostConfig, shell, vaultBackend, watchDeepLinks, windowLabel } from './tauri'
import { NativeTransport } from './transport'
import { Vault } from './vault'

async function start(): Promise<void> {
  const desktop = readDesktopConfig(await readHostConfig())
  const vault = await Vault.open(vaultBackend(), windowLabel())
  // Le premier transport ne connaît que le serveur configuré ; l'API et la connexion qu'il ANNONCE s'y ajoutent ensuite (souvent les mêmes).
  const config = await loadServerConfig(new NativeTransport(nativeFetch, allowedOrigins(desktop)), desktop)
  const transport = new NativeTransport(nativeFetch, allowedOrigins(desktop, config))
  const host = desktopHost({ vault, shell, transport, unavailable: disabledByConfig(desktop) })

  const desk = createDesk(transport, config, host)
  const { app, router } = desk
  for (const u of desk.unavailable) console.info(`[bureau] ${u.capability} : ${u.reason}`)
  await desk.loadExtensions()
  await router.isReady()
  if (router.currentRoute.value.name === 'not-found' && desk.extensions.loaded.length) await router.replace(router.currentRoute.value.fullPath)
  app.mount('#app')

  // Le test de fumée (`e2e/desktop.smoke.mjs`, build `--mode smoke`) pilote le VRAI transport et le VRAI hôte de l'application :
  // cette poignée n'existe dans aucun autre build (la branche est retirée à la compilation).
  if (import.meta.env.MODE === 'smoke') Object.assign(window, { __DESK_SMOKE__: { desk, transport, host, session: app.runWithContext(() => useSession()) } })

  installLinkGuard(document, window, { host, page: () => location.href, report: (message, cause) => console.warn(`[bureau] ${message}`, cause ?? '') })

  const session = app.runWithContext(() => useSession())
  await watchDeepLinks((raw) => {
    const link = readDeepLink(raw)
    if (link.kind === 'refused') return void console.warn(`[bureau] lien profond ignoré : ${link.reason}`)
    if (link.kind === 'route') return void router.push({ path: link.path, query: link.query })
    // Un retour OAuth : seulement dans la fenêtre qui a ouvert le fournisseur. Ailleurs (ou sans tentative en cours : un lien forgé), rien.
    if (!session.oauthPending()) return
    // L'écran `#/oauth` traite le retour à son montage (nonce compris) ; s'il est déjà affiché (un retour précédent en erreur), on le remonte.
    void (async () => {
      if (router.currentRoute.value.name === 'oauth') await router.replace({ name: 'login' })
      await router.replace({ name: 'oauth', query: link.query })
    })()
  })
}

/** Un démarrage refusé se DIT, à l'écran : sans cela l'application resterait une fenêtre blanche. */
function refuse(e: unknown): void {
  console.error('[bureau] démarrage refusé', e)
  const root = document.getElementById('app')
  if (!root) return
  root.textContent = ''
  const box = document.createElement('div')
  box.setAttribute('data-testid', 'desktop-refusal')
  box.style.cssText = 'max-width:38rem;margin:15vh auto;padding:1.5rem;font:15px/1.5 system-ui,sans-serif'
  const title = document.createElement('h1')
  title.style.cssText = 'font-size:1.2rem;margin:0 0 .75rem'
  title.textContent = "L'application ne peut pas démarrer"
  const cause = document.createElement('p')
  cause.textContent = e instanceof Error ? e.message : String(e)
  const hint = document.createElement('p')
  hint.style.color = '#666'
  hint.textContent = "L'adresse du serveur se règle dans desk.json (voir docs/desktop.md)."
  const retry = document.createElement('button')
  retry.textContent = 'Réessayer'
  retry.addEventListener('click', () => location.reload())
  box.append(title, cause, hint, retry)
  root.append(box)
}

start().catch(refuse)
