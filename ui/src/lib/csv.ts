/**
 * Export CSV d'une liste ou d'un rapport. Pensé pour s'ouvrir tel quel dans un tableur francophone : séparateur « ; », virgule décimale,
 * UTF-8 avec marque d'ordre (BOM), fins de ligne CRLF.
 *
 * Un document peut contenir n'importe quel texte saisi par n'importe qui. Un tableur interprète « =… », « +… », « -… », « @… » comme une
 * FORMULE : un export doit donc neutraliser une cellule TEXTE qui commence ainsi (apostrophe devant), sans toucher aux nombres (un solde
 * négatif reste un nombre). C'est une défense en profondeur, et elle est testée.
 */
import { statusLabel } from './docstatus'
import { clockDuration } from './duration'
import { plainText } from './richtext'
import type { DocRecord, FieldMeta } from './types'

const SEP = ';'
const DANGEROUS = /^[=+\-@\t\r]/

/** Une cellule prête à écrire : neutralisée si elle est du texte, entre guillemets si elle contient un séparateur, un guillemet ou un saut de ligne. */
export function csvCell(text: string, textual: boolean): string {
  const safe = textual && DANGEROUS.test(text) ? `'${text}` : text
  return /[;"\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe
}

/** Le fichier : en-têtes puis lignes. `textual[i]` dit si la colonne i est du texte (à neutraliser) ou un nombre. */
export function buildCsv(headers: readonly string[], rows: readonly (readonly string[])[], textual: readonly boolean[]): string {
  const line = (cells: readonly string[], isHeader: boolean) => cells.map((c, i) => csvCell(c, isHeader || (textual[i] ?? true))).join(SEP)
  return `﻿${[line(headers, true), ...rows.map((r) => line(r, false))].join('\r\n')}\r\n`
}

/** Les colonnes système que le rapport sait montrer, en plus des champs du type. */
export const SYSTEM_COLUMNS = ['name', 'owner', 'creation', 'modified', 'docstatus'] as const
export type SystemColumn = (typeof SYSTEM_COLUMNS)[number]
export const isSystemColumn = (c: string): c is SystemColumn => (SYSTEM_COLUMNS as readonly string[]).includes(c)

/** Une valeur sous la forme qu'un tableur francophone lit : virgule décimale, Oui/Non, date ISO. */
export function exportValue(field: FieldMeta | undefined, column: string, rec: DocRecord): { text: string; textual: boolean } {
  if (isSystemColumn(column)) {
    if (column === 'docstatus') return { text: statusLabel(rec.docstatus), textual: true }
    return { text: String(rec[column] ?? ''), textual: true }
  }
  const v = rec.data[column]
  if (v === null || v === undefined) return { text: '', textual: true }
  switch (field?.fieldtype) {
    case 'Number':
      return { text: String(v).replace('.', ','), textual: false }
    case 'Decimal':
    case 'Currency':
      return { text: String(v).replace('.', ','), textual: false }
    case 'Rating':
    case 'Int':
      return { text: String(v), textual: false }
    case 'Percent':
      return { text: String(v).replace('.', ','), textual: false }
    case 'Duration':
      return { text: typeof v === 'number' ? clockDuration(v) : String(v), textual: true } // « 1:30:00 » : un tableur le lit comme une durée
    case 'Password':
      return { text: '', textual: true } // jamais dans un export
    case 'TextEditor':
      return { text: plainText(v), textual: true }
    case 'JSON':
      return { text: typeof v === 'string' ? v : JSON.stringify(v), textual: true }
    case 'Boolean':
      return { text: v ? 'Oui' : 'Non', textual: true }
    default:
      return { text: String(v), textual: true }
  }
}

/** Une colonne est du texte (à neutraliser) sauf si c'est un Number ou un Decimal. Les colonnes système sont du texte. */
export const isTextualColumn = (field: FieldMeta | undefined): boolean => !(field && ['Number', 'Decimal', 'Currency', 'Rating', 'Int', 'Percent'].includes(field.fieldtype))

/** Un nom de fichier sûr : « Stock Entry » -> « stock-entry-2026-10-17.csv ». */
export const csvFilename = (doctype: string, today: string): string => `${doctype.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'export'}-${today}.csv`
