import type { DocTypeMeta, FieldMeta } from './types'

const GROUPABLE = ['Select', 'Link', 'Boolean']
export const MAX_SIDEBAR_GROUPS = 3

/**
 * Les champs de la barre latérale d'une liste : quelques champs à choix (Select, Link, Booléen), ceux des colonnes de la liste d'abord (ce que l'utilisateur regarde), au plus trois.
 * Vide : le type n'a rien à proposer, et la liste n'a alors PAS de colonne de barre latérale (sinon son tableau se retrouverait dans cette colonne étroite).
 */
export function sidebarFields(meta: DocTypeMeta): FieldMeta[] {
  const usable = meta.fields.filter((f) => GROUPABLE.includes(f.fieldtype) && !f.hidden)
  const shown = meta.list_columns.map((n) => usable.find((f) => f.fieldname === n)).filter((f): f is FieldMeta => !!f)
  return [...new Set([...shown, ...usable])].slice(0, MAX_SIDEBAR_GROUPS)
}
