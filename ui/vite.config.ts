import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// Le build part dans le paquet Python : le wheel embarque le Desk et le plugin le sert (src/xcore_doctype/ui/dist).
const dist = fileURLToPath(new URL('../src/xcore_doctype/ui/dist', import.meta.url))

/**
 * En développement, le plugin xcore-doctype ne sert pas l'interface : Vite, si. Il répond donc lui-même à `config.json`, avec le
 * contenu que donnerait le plugin (voir api/desk.py). `DESK_LOGIN_URL` pointe sur un xauth réel pour essayer la vraie connexion.
 */
const devConfig = (): Plugin => ({
  name: 'desk-dev-config',
  apply: 'serve',
  configureServer(server) {
    server.middlewares.use('/config.json', async (_req, res) => {
      // Les extensions (D28) : celles que le backend sert, relayées par le proxy (`/app/…`). Backend absent : aucune.
      let extensions: { name: string; url: string }[] = []
      try {
        const backend = await fetch(`${process.env.DESK_API_URL ?? 'http://127.0.0.1:8000'}/app/xcore-doctype/desk/config.json`)
        const body = (await backend.json()) as { extensions?: { name: string; url: string }[] }
        extensions = (body.extensions ?? []).map((e) => ({ name: e.name, url: `/app/xcore-doctype/desk/${e.url}` }))
      } catch {
        // backend pas encore démarré
      }
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ api_base: '/app/xcore-doctype', login_url: process.env.DESK_LOGIN_URL || null, extensions }))
    })
  },
})

/**
 * D28 : une extension de plugin importe `vue` et `@xcore-doctype/desk` par leur NOM ; l'import map les envoie vers deux points d'entrée
 * du build aux noms FIXES (`shared/vue.js`, `shared/sdk.js`), la même copie que celle du Desk. En développement, vers les sources que Vite
 * transforme (elles réimportent la même `vue` pré-optimisée que l'application). Le serveur autorise ce script en ligne par son empreinte.
 */
const SHARED = { vue: 'src/shared/vue.ts', '@xcore-doctype/desk': 'src/shared/sdk.ts' } as const
const importMap = (): Plugin => {
  let dev = false
  return {
    name: 'desk-import-map',
    configResolved(c) {
      dev = c.command === 'serve'
    },
    transformIndexHtml() {
      const imports = dev
        ? { vue: `/${SHARED.vue}`, '@xcore-doctype/desk': `/${SHARED['@xcore-doctype/desk']}` }
        : { vue: './shared/vue.js', '@xcore-doctype/desk': './shared/sdk.js' }
      return [{ tag: 'script', attrs: { type: 'importmap' }, children: JSON.stringify({ imports }), injectTo: 'head-prepend' }]
    },
  }
}

export default defineConfig({
  // Chemins relatifs : le Desk est monté sous un préfixe que l'opérateur choisit (/app/xcore-doctype/desk/),
  // et plus tard sous un protocole d'application (Tauri). Le routeur est en mode « hash » pour la même raison.
  base: './',
  plugins: [vue(), devConfig(), importMap()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: dist,
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./index.html', import.meta.url)),
        'shared/vue': fileURLToPath(new URL(`./${SHARED.vue}`, import.meta.url)),
        'shared/sdk': fileURLToPath(new URL(`./${SHARED['@xcore-doctype/desk']}`, import.meta.url)),
      },
      // Les deux modules partagés gardent leur nom ET leurs exports (une extension les importe) ; le reste garde ses noms par empreinte.
      preserveEntrySignatures: 'strict',
      output: { entryFileNames: (chunk) => (chunk.name.startsWith('shared/') ? '[name].js' : 'assets/[name]-[hash].js') },
    },
  },
  // En développement, Vite sert l'interface et relaie l'API vers un hôte xcore lancé à part (make dev / uvicorn).
  // `make dev-frontend` passe DESK_API_URL quand le serveur n'est pas sur le port 8000.
  server: { port: 5173, proxy: { '/app': process.env.DESK_API_URL ?? 'http://127.0.0.1:8000' } },
  test: {
    // Une extension de plugin importe le SDK par son nom nu (D28) : dans les tests, il se résout vers la source (au build : l'import map).
    alias: { '@xcore-doctype/desk': fileURLToPath(new URL(`./${SHARED['@xcore-doctype/desk']}`, import.meta.url)) },
    environment: 'happy-dom',
    include: ['tests/**/*.spec.ts'],
    // Le premier test d'un fichier qui monte PrimeVue paie la transformation du module : plus de 5 s quand la machine est chargée
    // (suite Python en parallèle, build…). Ce n'est pas une erreur de logique, et un test réellement bloqué échoue toujours.
    testTimeout: 20_000,
    // Fuseau loin de l'UTC : une date locale à minuit y tombe la veille en UTC, ce qui révèle tout `toISOString()` oublié.
    env: { TZ: 'Pacific/Auckland', LC_ALL: 'fr_FR.UTF-8' },
    // Les vecteurs de test partagés avec pytest vivent hors de ui/ : ils doivent rester la source unique.
    server: { deps: { inline: [/primevue/] } },
  },
})
