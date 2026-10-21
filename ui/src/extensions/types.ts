/**
 * Le contrat des extensions JavaScript du Desk, version 1 (D28, `doc/extensions.md`). C'est la SEULE surface publique : une extension
 * reçoit un `DeskHandle` dans `setup` et n'importe rien d'autre du Desk que `@xcore-doctype/desk` (le SDK, `src/shared/sdk.ts`).
 */
import type { Component } from 'vue'
import type { Router } from 'vue-router'
import type { ApiClient } from '@/lib/api'
import type { DocRecord, DocTypeMeta } from '@/lib/types'

export const EXTENSION_API_VERSION = 1

/** Ce que le module d'une extension exporte par défaut (`export default defineExtension({...})`). */
export interface ExtensionDefinition {
  /** Le nom du plugin qui la livre : une extension qui se nomme autrement est refusée. */
  name: string
  apiVersion: 1
  setup(desk: DeskHandle): void | Promise<void>
}

/** Les routes HTTP du plugin lui-même (`<préfixe>/<plugin>/…`), avec la session du Desk (jeton, organisation, renouvellement). */
export interface PluginClient {
  get<T = unknown>(path: string, query?: Record<string, string | number | boolean>): Promise<T>
  post<T = unknown>(path: string, body?: unknown): Promise<T>
  put<T = unknown>(path: string, body?: unknown): Promise<T>
  patch<T = unknown>(path: string, body?: unknown): Promise<T>
  delete<T = unknown>(path: string): Promise<T>
}

export interface Notifier {
  success(message: string): void
  info(message: string): void
  warn(message: string): void
  /** Une erreur d'API devient un message lisible (comme partout dans le Desk). */
  error(error: unknown): void
}

/** Ce qu'une action reçoit en plus de ce qu'elle regarde : de quoi parler au serveur et à l'utilisateur. */
export interface Tools {
  api: ApiClient
  plugin: PluginClient
  notify: Notifier
  router: Router
}

/** Ce qu'un composant d'extension obtient par `useDesk()` (SDK) : les outils, au nom de son extension. */
export interface ExtensionContext extends Tools {
  readonly name: string
}

/** Une fiche : le type, le document enregistré (`null` pour un nouveau) et ce qui est saisi. */
export interface FormContext {
  doctype: string
  name: string | null
  record: DocRecord | null
  meta: DocTypeMeta
  values: Readonly<Record<string, unknown>>
}

export interface FormAction {
  /** Le type de document visé, ou `*` pour tous. */
  doctype: string
  label: string
  icon?: string
  /** Rangée dans la barre d'en-tête plutôt que dans le menu « ⋯ ». */
  primary?: boolean
  danger?: boolean
  /** Montrer ou cacher : ne protège rien, le serveur juge chaque appel. */
  when?(ctx: FormContext): boolean
  run(ctx: FormContext & Tools & { reload(): Promise<void> }): unknown
}

export interface FormPanel {
  doctype: string
  title?: string
  /** Reçoit les props `doctype`, `name`, `record`, `meta`. */
  component: Component
  when?(ctx: FormContext): boolean
}

export interface ListContext {
  doctype: string
  meta: DocTypeMeta
  /** Les documents cochés. */
  names: readonly string[]
}

export interface ListAction {
  doctype: string
  label: string
  icon?: string
  when?(ctx: ListContext): boolean
  run(ctx: ListContext & Tools & { reload(): void }): unknown
}

export interface RouteContribution {
  /** Sous `#/x/<plugin>/` : `pipeline`, `deal/:id`. */
  path: string
  /** Le titre `h1` de la page (et de l'onglet) ; sans titre, le composant donne le sien. */
  title?: string
  component: Component
}

export interface SidebarLink {
  /** L'espace sous lequel le lien se range (sinon : un groupe « Extensions »). */
  workspace?: string
  label: string
  icon?: string
  /** Un chemin interne du Desk (`/x/sales/pipeline`). */
  to: string
}

export interface CommandContribution {
  label: string
  icon?: string
  keywords?: string
  run(ctx: Tools): unknown
}

export interface DeskHandle {
  readonly name: string
  readonly apiVersion: 1
  readonly api: ApiClient
  readonly plugin: PluginClient
  readonly router: Router
  readonly notify: Notifier
  routes: { add(route: RouteContribution): void }
  sidebar: { add(link: SidebarLink): void }
  form: { action(action: FormAction): void; panel(panel: FormPanel): void }
  list: { action(action: ListAction): void }
  /** Un bloc d'espace `{ type: extension, component: <plugin>.<nom> }` ; le composant reçoit `props` (et `label`). */
  blocks: { add(name: string, component: Component): void }
  commands: { add(command: CommandContribution): void }
}
