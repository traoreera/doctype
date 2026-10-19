/**
 * L'unité qu'on affiche à côté d'une quantité (`unit_from`), sans interface : testé seul.
 *
 * Deux formes, comme côté serveur (schema.FieldDef.unit_from) : `uom` (un champ texte du même document ou de la même ligne) et
 * `item.uom` (le champ `uom` du document que le champ Link `item` désigne). C'est de l'AFFICHAGE : rien n'est stocké ni envoyé.
 */
import type { FieldMeta } from './types'

/** Où chercher la valeur d'un Link : `(DocType cible, nom du document, champ)` -> texte, ou `undefined` tant qu'on ne l'a pas lue. */
export type LinkReader = (target: string, name: string, fieldname: string) => unknown

export interface UnitSource {
  /** Le champ Link à suivre (forme `item.uom`), ou `null` (forme `uom` : un champ du même document). */
  link: FieldMeta | null
  /** Le champ qui porte l'unité : du même document, ou de la cible du Link. */
  field: string
}

export function unitSource(field: FieldMeta, siblings: readonly FieldMeta[]): UnitSource | null {
  if (!field.unit_from) return null
  const [first = '', rest] = field.unit_from.split('.')
  if (rest === undefined) return { link: null, field: first }
  const holder = siblings.find((f) => f.fieldname === first && f.fieldtype === 'Link' && typeof f.options === 'string')
  return holder ? { link: holder, field: rest } : null
}

/** Le texte de l'unité d'un champ pour des valeurs données, ou `null` (pas d'unité, valeur absente ou pas encore lue). */
export function unitOf(field: FieldMeta, siblings: readonly FieldMeta[], values: Record<string, unknown>, read: LinkReader): string | null {
  const src = unitSource(field, siblings)
  if (!src) return null
  let raw: unknown
  if (src.link) {
    const name = values[src.link.fieldname]
    raw = typeof name === 'string' && name ? read(src.link.options as string, name, src.field) : undefined
  } else raw = values[src.field]
  return typeof raw === 'string' && raw ? raw : null
}

/** Les valeurs de Link à lire pour afficher les unités : DocType cible -> noms (de quoi les résoudre d'un coup). */
export function unitLinks(fields: readonly FieldMeta[], rows: readonly Record<string, unknown>[]): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  for (const f of fields) {
    const src = unitSource(f, fields)
    if (!src?.link) continue
    const target = src.link.options as string
    for (const row of rows) {
      const v = row[src.link.fieldname]
      if (typeof v === 'string' && v) (out.get(target) ?? out.set(target, new Set()).get(target)!).add(v)
    }
  }
  return out
}
