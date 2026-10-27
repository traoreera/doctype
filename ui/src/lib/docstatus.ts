/** L'état d'un document soumettable, côté affichage. Sans interface : testé seul. */
import { t } from '@/i18n'
import { CANCELLED, DRAFT, SUBMITTED, type DocRecord, type DocTypeMeta, type FieldMeta } from './types'

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
  // Un type à WORKFLOW ne se soumet ni ne s'annule directement : le serveur y refuse `/submit` et `/cancel` (409), c'est une
  // transition qui fait passer le document. Offrir ces boutons serait un piège. Amender reste possible : c'est une création.
  if (meta.workflow) {
    return { ...none, amend: statusOf(rec) === CANCELLED && rights.create !== 'none' }
  }
  return {
    submit: statusOf(rec) === DRAFT && rights.submit !== 'none',
    cancel: statusOf(rec) === SUBMITTED && rights.cancel !== 'none',
    // Amender, c'est CRÉER une copie en brouillon d'un document annulé (et on ne l'amende qu'une fois : le serveur le garde).
    amend: statusOf(rec) === CANCELLED && rights.create !== 'none',
  }
}

/** Un document soumis ou annulé est figé : plus de saisie, ni d'enregistrement, ni de suppression. */
export const isFrozen = (rec: DocRecord | null): boolean => !!rec && statusOf(rec) !== DRAFT

const STATE: Record<'draft' | 'submitted' | 'cancelled', number> = { draft: DRAFT, submitted: SUBMITTED, cancelled: CANCELLED }

/**
 * Les états qu'un lien admet pour sa cible (`target_status`, miroir de `FieldDef.allowed_docstatus` côté serveur), triés ; `null` : tous.
 * L'autocomplétion ne propose qu'eux (le serveur filtre, et rejuge de toute façon à l'écriture).
 */
export function allowedDocstatus(field: Pick<FieldMeta, 'target_status'>): number[] | null {
  const ts = field.target_status
  if (!ts) return null
  if (ts === 'not_cancelled') return [DRAFT, SUBMITTED]
  return [...new Set((Array.isArray(ts) ? ts : [ts]).map((s) => STATE[s]))].sort((a, b) => a - b)
}
