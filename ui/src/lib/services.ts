import { inject, type InjectionKey } from 'vue'
import type { ApiClient } from './api'
import type { DeskConfig } from './config'
import type { TabSync } from './tabsync'
import type { Transport } from './transport'
import type { XauthClient } from './xauth'
import type { XflowClient } from './xflow'

/** Ce que l'application reçoit de son hôte : transport réseau, configuration, client d'API. Rien n'est global. */
export interface Services {
  transport: Transport
  config: DeskConfig
  api: ApiClient
  /** xauth (comptes, organisation, rôles, sessions, journal, courrier), quand l'adresse de connexion le désigne ; `null` sinon (jeton collé). */
  auth?: XauthClient | null
  /** xflow, l'orchestrateur des automatisations (un plugin à part) : son absence se découvre à la première lecture (`stores/automations`). */
  flows?: XflowClient | null
  /** Ce que les onglets d'une même session se passent (jeton de renouvellement, déconnexion) ; défaut : le navigateur (`lib/tabsync`). */
  tabs?: TabSync
}

export const SERVICES: InjectionKey<Services> = Symbol('desk-services')

export function useServices(): Services {
  const s = inject(SERVICES)
  if (!s) throw new Error('Services du Desk non fournis (app.provide(SERVICES, …))')
  return s
}
