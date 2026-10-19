/** L'état d'un document soumettable, côté affichage. Sans interface : testé seul. */
import { t } from '@/i18n'
import { CANCELLED, DRAFT, SUBMITTED, type DocRecord, type DocTypeMeta } from './types'

export type StatusTone = 'draft' | 'submitted' | 'cancelled'

export const statusLabel = (docstatus: number): string =>
  docstatus === SUBMITTED ? t.status.submitted : docstatus === CANCELLED ? t.status.cancelled : t.status.draft

export const statusTone = (docstatus: number): StatusTone => (docstatus === SUBMITTED ? 'submitted' : docstatus === CANCELLED ? 'cancelled' : 'draft')

/** Les actions de transition qui s'offrent à l'utilisateur sur ce document (le serveur reste l'autorité). */
export interface Transitions {
  submit: boolean
  cancel: boolean
  amend: boolean
}

/** Un enregistrement venu d'un serveur sans documents soumettables n'a pas de `docstatus` : c'est un brouillon. */
const statusOf = (rec: DocRecord): number => rec.docstatus ?? DRAFT

export function transitionsFor(meta: DocTypeMeta | null, rec: DocRecord | null): Transitions {
  const none = { submit: false, cancel: false, amend: false }
  if (!meta?.submittable || !rec) return none
  const rights = meta.rights
  return {
    submit: statusOf(rec) === DRAFT && rights.submit !== 'none',
    cancel: statusOf(rec) === SUBMITTED && rights.cancel !== 'none',
    // Amender, c'est CRÉER une copie en brouillon d'un document annulé (et on ne l'amende qu'une fois : le serveur le garde).
    amend: statusOf(rec) === CANCELLED && rights.create !== 'none',
  }
}

/** Un document soumis ou annulé est figé : plus de saisie, ni d'enregistrement, ni de suppression. */
export const isFrozen = (rec: DocRecord | null): boolean => !!rec && statusOf(rec) !== DRAFT
