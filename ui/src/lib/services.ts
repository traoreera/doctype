import { inject, type InjectionKey } from 'vue'
import type { ApiClient } from './api'
import type { DeskConfig } from './config'
import type { RealtimeClient } from './realtime'
import type { TabSync } from './tabsync'
import type { Transport } from './transport'
import type { XauthClient } from './xauth'
import type { XlicenseClient } from './xlicense'

/** Ce que l'application reçoit de son hôte : transport réseau, configuration, client d'API. Rien n'est global. */
export interface Services {
  transport: Transport
  config: DeskConfig
  api: ApiClient
  /** xauth (comptes, organisation, rôles, sessions, journal, courrier), quand l'adresse de connexion le désigne ; `null` sinon (jeton collé). */
  auth?: XauthClient | null
  /** xlicense (ouvrir une caisse, confirmer un paiement, le portail du prestataire), quand le plugin est installé ;
   * `null` sinon. Lire le catalogue, ses modules et sa licence passe par les DocTypes, donc par `api`. */
  license?: XlicenseClient | null
  /** Le temps réel par xpulse (`lib/realtime.ts`) : cloche et documents en direct ; `null`/absent = le Desk relit comme avant. */
  realtime?: RealtimeClient | null
  /** Ce que les onglets d'une même session se passent (jeton de renouvellement, déconnexion) ; défaut : le navigateur (`lib/tabsync`). */
  tabs?: TabSync
}

export const SERVICES: InjectionKey<Services> = Symbol('desk-services')

export function useServices(): Services {
  const s = inject(SERVICES)
  if (!s) throw new Error('Services du Desk non fournis (app.provide(SERVICES, …))')
  return s
}
