/**
 * Import CSV côté Desk : lire le fichier choisi et proposer un modèle. Le SERVEUR comprend l'en-tête, convertit les cellules et passe chaque
 * ligne par la porte ordinaire (`POST /import/<Type>`, `csv_import.py`) ; ce module ne fait que préparer le texte.
 */
import { buildCsv } from './csv'
import type { DocTypeMeta, FieldMeta } from './types'

/** Ce que le serveur refuse en colonne (`csv_import.NOT_IMPORTED` et `fetch_from`) : le modèle ne les propose pas. */
const NOT_IMPORTED = new Set(['Table', 'Password', 'Image', 'Attach'])
export const importable = (f: FieldMeta): boolean => !NOT_IMPORTED.has(f.fieldtype) && !f.fetch_from && !f.read_only && !f.hidden

export type ImportMode = 'insert' | 'update'

/** 0x80-0x9F en Windows-1252 (€, ’, œ…) : écrit ici parce que tous les moteurs ne le décodent pas (Node lit ces octets en Latin-1). */
const CP1252_HIGH = '\u20ac\u0081\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152\u008d\u017d\u008f\u0090\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\u009d\u017e\u0178'

/** Le texte d'un fichier : UTF-8 (avec ou sans BOM) d'abord ; un octet qui n'en est pas -> Windows-1252, ce qu'Excel écrit en « CSV » simple. */
export function decodeCsv(bytes: ArrayBuffer | Uint8Array): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    let text = ''
    for (const b of bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)) text += b >= 0x80 && b < 0xa0 ? CP1252_HIGH[b - 0x80] : String.fromCharCode(b)
    return text
  }
}

/** L'en-tête d'un modèle : le libellé de chaque champ importable (son nom quand deux champs partagent un libellé), précédé de `name` quand le
 * fichier doit désigner le document (mise à jour, ou type `naming: prompt`). */
export function templateHeaders(meta: DocTypeMeta, mode: ImportMode): string[] {
  const fields = meta.fields.filter(importable)
  const count = new Map<string, number>()
  for (const f of meta.fields) if (f.label) count.set(fold(f.label), (count.get(fold(f.label)) ?? 0) + 1)
  const names = new Set(meta.fields.map((f) => f.fieldname))
  // Le libellé seulement s'il désigne CE champ et lui seul pour le serveur : unique, pas le nom d'un autre champ (le serveur lit les noms
  // d'abord), pas « name »/« Nom » (le nom du document) ; sinon le nom du champ, toujours compris.
  const usable = (f: FieldMeta) => !!f.label && count.get(fold(f.label)) === 1 && !names.has(f.label.trim()) && !['name', 'nom'].includes(fold(f.label))
  const heads = fields.map((f) => (usable(f) ? f.label! : f.fieldname))
  return mode === 'update' || meta.naming === 'prompt' ? ['name', ...heads] : heads
}

/** Au plus près du `str.casefold` du serveur : `toUpperCase` déplie « ß » en « SS », puis tout en minuscules (« Straße » = « STRASSE »). */
export const fold = (text: string): string => text.trim().toUpperCase().toLowerCase()

export const templateCsv = (meta: DocTypeMeta, mode: ImportMode): string => buildCsv(templateHeaders(meta, mode), [], [])
