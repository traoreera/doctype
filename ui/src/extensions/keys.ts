import type { InjectionKey } from 'vue'
import type { ExtensionHost } from './host'
import type { ExtensionContext } from './types'

/** Les clés d'injection, à part : `ExtensionSlot` les lit sans importer l'hôte (qui importe les pages : pas de cycle). */
export const EXTENSIONS: InjectionKey<ExtensionHost> = Symbol('desk-extensions')
/** Le contexte d'un composant d'extension (fourni par `ExtensionSlot`), lu par `useDesk()` du SDK. */
export const EXTENSION_CONTEXT: InjectionKey<ExtensionContext> = Symbol('desk-extension-context')
