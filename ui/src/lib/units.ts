/**
 * L'unité qu'on affiche à côté d'une quantité (`unit_from`), sans interface : testé seul.
 *
 * Deux formes, comme côté serveur (schema.FieldDef.unit_from) : `uom` (un champ texte du même document ou de la même ligne) et
 * `item.uom` (le champ `uom` du document que le champ Link `item` désigne). C'est de l'AFFICHAGE : rien n'est stocké ni envoyé.
 */
import { currencySymbol } from './format'
import type { FieldMeta } from './types'

/** Le préfixe par lequel un champ de LIGNE lit le document parent (`parent_currency`), comme les conditions de ligne. */
export const PARENT_PREFIX = 'parent_'

/** Où chercher la valeur d'un Link : `(DocType cible, nom du document, champ)` -> texte, ou `undefined` tant qu'on ne l'a pas lue. */
export type LinkReader = (target: string, name: string, fieldname: string) => unknown

/** Le document qui porte une table : ses champs et ses valeurs, pour une ligne qui lit `parent_…`. */
export interface ParentSide {
  fields: readonly FieldMeta[]
  values: Record<string, unknown>
}

export interface UnitSource {
  /** Le champ Link à suivre (forme `item.uom`), ou `null` (forme `uom` : un champ du même document). */
  link: FieldMeta | null
  /** Le champ qui porte l'unité : du même document, ou de la cible du Link. */
  field: string
  /** La source est lue sur le document PARENT (ligne, `parent_…`). */
  parent: boolean
  /** C'est une devise (`currency_from`) : on affiche son symbole (« € »), pas le code brut. */
  currency: boolean
}

/**
 * La source de l'unité d'un champ : `unit_from` (une quantité) ou `currency_from` (un montant, M3). `parentFields` : les champs du
 * document qui porte la table, quand `field` est une colonne de ligne (sans eux, une source `parent_…` n'a pas de sens).
 */
export function unitSource(field: FieldMeta, siblings: readonly FieldMeta[], parentFields?: readonly FieldMeta[]): UnitSource | null {
  let from = field.unit_from ?? (field.fieldtype === 'Currency' ? field.currency_from : undefined)
  if (!from) return null
  const currency = !field.unit_from
  let parent = false
  let where = siblings
  if (currency && parentFields && from.startsWith(PARENT_PREFIX)) {
    parent = true
    where = parentFields
    from = from.slice(PARENT_PREFIX.length)
  }
  const [first = '', rest] = from.split('.')
  if (rest === undefined) return { link: null, field: first, parent, currency }
  const holder = where.find((f) => f.fieldname === first && f.fieldtype === 'Link' && typeof f.options === 'string')
  return holder ? { link: holder, field: rest, parent, currency } : null
}

/** Le texte de l'unité d'un champ pour des valeurs données, ou `null` (pas d'unité, valeur absente ou pas encore lue). */
export function unitOf(
  field: FieldMeta,
  siblings: readonly FieldMeta[],
  values: Record<string, unknown>,
  read: LinkReader,
  parentSide?: ParentSide,
): string | null {
  const src = unitSource(field, siblings, parentSide?.fields)
  if (!src) return null
  const from = src.parent ? parentSide!.values : values
  let raw: unknown
  if (src.link) {
    const name = from[src.link.fieldname]
    raw = typeof name === 'string' && name ? read(src.link.options as string, name, src.field) : undefined
  } else raw = from[src.field]
  if (typeof raw !== 'string' || !raw) return null
  return src.currency ? currencySymbol(raw) : raw
}

/** Les valeurs de Link à lire pour afficher les unités : DocType cible -> noms (de quoi les résoudre d'un coup). */
export function unitLinks(fields: readonly FieldMeta[], rows: readonly Record<string, unknown>[], parentSide?: ParentSide): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  const add = (target: string, v: unknown) => {
    if (typeof v === 'string' && v) (out.get(target) ?? out.set(target, new Set()).get(target)!).add(v)
  }
  for (const f of fields) {
    const src = unitSource(f, fields, parentSide?.fields)
    if (!src?.link) continue
    const target = src.link.options as string
    if (src.parent) add(target, parentSide!.values[src.link.fieldname])
    else for (const row of rows) add(target, row[src.link.fieldname])
  }
  return out
}
