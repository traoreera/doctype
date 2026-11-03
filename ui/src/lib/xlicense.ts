import { ApiClient, type ClientContext } from './api'
import type { Transport } from './transport'

/**
 * Le client de xlicense (l'abonnement : modules pris, licence, paiement). Même transport, même jeton, même
 * renouvellement sur un 401 que le client des documents ; seule la racine change (`/app/xlicense`).
 *
 * Ce qu'on NE met pas ici : lire le catalogue, lire ses modules, lire sa licence. Ce sont des DocTypes
 * (`Module`, `Tenant Module`, `License`) et le Desk les sert déjà avec ses listes, ses filtres et ses formulaires.
 * Il ne reste que ce qu'aucun DocType ne sait faire : ouvrir une caisse, confirmer un encaissement, ouvrir le
 * portail du prestataire.
 */

export interface Checkout {
  /** L'adresse du prestataire : le navigateur y est envoyé. */
  url: string
  session_id: string
  module: string
  price: number
  currency: string
}

export interface ModuleTaken {
  code: string
  taken: boolean
  source: string
}

/** Un module du catalogue vu par MON organisation (`GET /me/modules`). */
export interface MarketModule {
  code: string
  label: string
  description: string | null
  free: boolean
  price: number
  currency: string | null
  period: 'month' | 'year' | 'once' | null
  active: boolean
  /** Payant sans tarif chez le prestataire : affiché, pas achetable. */
  sellable: boolean
  taken: boolean
  source: 'free' | 'paid' | 'granted' | null
  expires_at: string | null
  /** Utilisable MAINTENANT (pris et courant, ou tout est ouvert pendant l'essai). */
  usable: boolean
}

export interface MyModules {
  state: string
  valid: boolean
  trial: boolean
  all_modules: boolean
  /** Prendre, rendre, payer : l'administrateur de l'organisation seulement. Le serveur le rejuge de toute façon. */
  can_manage: boolean
  modules: MarketModule[]
}

export class XlicenseClient extends ApiClient {
  constructor(transport: Transport, context: () => ClientContext, onUnauthorized?: () => boolean | Promise<boolean> | void) {
    super(transport, context, onUnauthorized)
  }

  /** Le catalogue de l'instance et ce que MON organisation en a pris. */
  modules(): Promise<MyModules> {
    return this.call('GET', '/me/modules')
  }

  /** Prendre un module GRATUIT (un payant répond 402 `payment_required` : passer par `checkout`). */
  take(code: string): Promise<ModuleTaken> {
    return this.call('POST', `/me/modules/${encodeURIComponent(code)}`)
  }

  /** Rendre un module gratuit ou accordé (un module payé se résilie au portail : 409 `subscription_active`). */
  drop(code: string): Promise<{ code: string; taken: false }> {
    return this.call('DELETE', `/me/modules/${encodeURIComponent(code)}`)
  }

  /** Ouvre le paiement d'un module payant. Rien n'est activé avant l'encaissement. */
  checkout(code: string): Promise<Checkout> {
    return this.call('POST', `/me/modules/${encodeURIComponent(code)}/checkout`)
  }

  /**
   * Confirme au retour du prestataire. L'identifiant de session n'est pas une preuve : le serveur le revérifie
   * et n'accepte que si la session désigne bien cette organisation et ce module.
   */
  confirm(code: string, sessionId: string): Promise<ModuleTaken> {
    return this.call('POST', `/me/modules/${encodeURIComponent(code)}/confirm`, { body: { session_id: sessionId } })
  }

  /** L'espace du prestataire : changer de carte, résilier. La résiliation a lieu là où l'argent est pris. */
  async portal(): Promise<string> {
    return (await this.call<{ url: string }>('POST', '/me/billing/portal')).url
  }
}

/**
 * La racine de xlicense, déduite de celle de l'API (`…/xcore-doctype` -> `…/xlicense`) : les deux plugins vivent
 * sous le même préfixe d'hôte, lequel est configurable (`app.plugin_prefix`), donc rien n'est codé en dur.
 * `null` si l'adresse n'a pas cette forme — on ne devine pas une racine de facturation.
 */
export function licenseBase(apiBase: string): string | null {
  const base = apiBase.replace(/\/+$/, '')
  return /\/xcore-doctype$/.test(base) ? base.replace(/\/xcore-doctype$/, '/xlicense') : null
}

/** Ce que l'écran propose pour un module, selon son état et mon pouvoir. Pur : c'est la règle de l'écran, testée seule. */
export type ModuleAction = 'take' | 'subscribe' | 'drop' | 'portal' | null

export function moduleAction(m: MarketModule, canManage: boolean): ModuleAction {
  if (!canManage) return null
  if (m.taken && m.source === 'paid') return 'portal' // résilier se fait là où l'argent est pris
  if (m.taken) return m.expires_at ? null : 'drop' // pris jusqu'à une échéance (résilié) : il court jusqu'au bout, rien à faire
  if (!m.active) return null // retiré du catalogue : plus proposé, même à reprendre
  if (m.free) return 'take'
  return m.sellable ? 'subscribe' : null
}
