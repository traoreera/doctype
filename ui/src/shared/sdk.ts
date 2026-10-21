/**
 * `@xcore-doctype/desk` : ce qu'une extension du Desk importe (D28, `doc/extensions.md`). Servi sous un nom FIXE (`shared/sdk.js`) que
 * l'import map d'`index.html` désigne. Tout ce qui n'est pas exporté ici est privé : il peut changer sans prévenir.
 */
import { inject } from 'vue'
import { EXTENSION_CONTEXT } from '@/extensions/keys'
import type { ExtensionContext, ExtensionDefinition } from '@/extensions/types'

export { EXTENSION_API_VERSION } from '@/extensions/types'
export type {
  CommandContribution,
  DeskHandle,
  ExtensionContext,
  ExtensionDefinition,
  FormAction,
  FormContext,
  FormPanel,
  ListAction,
  ListContext,
  Notifier,
  PluginClient,
  RouteContribution,
  SidebarLink,
  Tools,
} from '@/extensions/types'
export type { DocRecord, DocTypeMeta, FieldMeta } from '@/lib/types'

/** L'export par défaut d'une extension : `export default defineExtension({ name, apiVersion: 1, setup(desk) { … } })`. */
export function defineExtension(definition: ExtensionDefinition): ExtensionDefinition {
  return definition
}

/** Dans un composant d'extension (page, panneau, bloc) : les outils de SON extension (`api`, `plugin`, `notify`, `router`). */
export function useDesk(): ExtensionContext {
  const ctx = inject(EXTENSION_CONTEXT, null)
  if (!ctx) throw new Error('useDesk() : à appeler dans un composant affiché par le Desk (page, panneau ou bloc d’extension)')
  return ctx
}

// Les composants du Desk qu'une extension peut réutiliser : rendu d'une valeur, saisie d'un champ, choix d'un document lié.
export { default as CellValue } from '@/components/CellValue.vue'
export { default as FieldControl } from '@/components/FieldControl.vue'
export { default as LinkField } from '@/components/LinkField.vue'
export { describeError } from '@/composables/useNotify'
export { t } from '@/i18n'
export { piIcon } from '@/lib/icons'

// Une sélection de PrimeVue, configurée comme le reste du Desk (thème, langue) : la MÊME copie que celle de la page.
export { default as Button } from 'primevue/button'
export { default as Card } from 'primevue/card'
export { default as Column } from 'primevue/column'
export { default as DataTable } from 'primevue/datatable'
export { default as Dialog } from 'primevue/dialog'
export { default as InputNumber } from 'primevue/inputnumber'
export { default as InputText } from 'primevue/inputtext'
export { default as Message } from 'primevue/message'
export { default as ProgressSpinner } from 'primevue/progressspinner'
export { default as Select } from 'primevue/select'
export { default as Tag } from 'primevue/tag'
export { default as Textarea } from 'primevue/textarea'
