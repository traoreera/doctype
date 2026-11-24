import { fileURLToPath, URL } from 'node:url'
import { mergeConfig, type Plugin } from 'vite'
import web from './vite.config'

/**
 * Le build de l'application de BUREAU (Tauri, `src-tauri/`) : le même Desk, la même page, un autre point d'entrée.
 *
 * `index.html` reste celui du web ; seul son script change (`src/main.ts` → `src/desktop/main.ts`), si bien que le build web ne
 * contient rien de Tauri et que celui-ci ne contient pas `FetchTransport` comme transport. Le résultat part dans `dist-desktop/`
 * (ignoré par git), que `tauri.conf.json` désigne (`frontendDist`) ; en développement, Tauri ouvre ce serveur-ci (port 5174).
 */
const desktopEntry = (): Plugin => ({
  name: 'desk-desktop-entry',
  transformIndexHtml: {
    order: 'pre',
    handler(html) {
      if (!html.includes('/src/main.ts')) throw new Error("index.html ne désigne plus /src/main.ts : le point d'entrée de bureau ne sait plus où se brancher")
      return html.replace('/src/main.ts', '/src/desktop/main.ts')
    },
  },
})

export default mergeConfig(web, {
  plugins: [desktopEntry()],
  build: { outDir: fileURLToPath(new URL('./dist-desktop', import.meta.url)) },
  // Tauri attend ce port précis (`devUrl`) et affiche ses propres journaux Rust dans le même terminal.
  server: { port: 5174, strictPort: true },
  clearScreen: false,
})
