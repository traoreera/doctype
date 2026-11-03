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

export class XlicenseClient extends ApiClient {
  constructor(transport: Transport, context: () => ClientContext, onUnauthorized?: () => boolean | Promise<boolean> | void) {
    super(transport, context, onUnauthorized)
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
