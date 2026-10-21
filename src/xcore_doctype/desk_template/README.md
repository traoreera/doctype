# Extension du Desk de `__PLUGIN__`

`index.js` est le point d'entrée que le Desk charge (contrat : `doc/extensions.md` d'xcore-doctype, D28). Il fonctionne tel quel.

Pour écrire des composants `.vue` (ou en TypeScript), construisez l'extension en bibliothèque ES, avec `vue` et `@xcore-doctype/desk`
laissés **externes** (le Desk les fournit par son import map : une seule copie de Vue dans la page) :

```js
// vite.config.js, dans un dossier `desk-src/` du plugin
import vue from '@vitejs/plugin-vue'

export default {
  plugins: [vue()],
  build: {
    lib: { entry: 'src/index.js', formats: ['es'], fileName: () => 'index.js' },
    outDir: '../desk',
    emptyOutDir: true,
    rollupOptions: { external: ['vue', '@xcore-doctype/desk'] },
  },
}
```

Ce que le SDK `@xcore-doctype/desk` exporte : `defineExtension`, `useDesk` (dans un composant : `api`, `plugin`, `notify`, `router`),
`CellValue`, `FieldControl`, `LinkField`, `describeError`, `t`, `piIcon`, et une sélection de PrimeVue (`Button`, `Card`, `Column`,
`DataTable`, `Dialog`, `InputNumber`, `InputText`, `Message`, `ProgressSpinner`, `Select`, `Tag`, `Textarea`).

Vérifier le plugin (CI) : `xcore-doctype check <plugin>/doctypes --owner __PLUGIN__` valide aussi `desk/`.
