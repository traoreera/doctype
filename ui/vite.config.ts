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
    server.middlewares.use('/config.json', (_req, res) => {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ api_base: '/app/xcore-doctype', login_url: process.env.DESK_LOGIN_URL || null }))
    })
  },
})

export default defineConfig({
  // Chemins relatifs : le Desk est monté sous un préfixe que l'opérateur choisit (/app/xcore-doctype/desk/),
  // et plus tard sous un protocole d'application (Tauri). Le routeur est en mode « hash » pour la même raison.
  base: './',
  plugins: [vue(), devConfig()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: { outDir: dist, emptyOutDir: true, target: 'es2022', sourcemap: false },
  // En développement, Vite sert l'interface et relaie l'API vers un hôte xcore lancé à part (make dev / uvicorn).
  // `make dev-frontend` passe DESK_API_URL quand le serveur n'est pas sur le port 8000.
  server: { port: 5173, proxy: { '/app': process.env.DESK_API_URL ?? 'http://127.0.0.1:8000' } },
  test: {
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
