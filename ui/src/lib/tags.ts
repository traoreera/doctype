import { t } from '@/i18n'
import type { DocTypeMeta, Filters } from './types'

/**
 * Étiquettes d'un document : les règles d'un NOM, jumelles de `tags.py` côté serveur (qui reste l'autorité), et le filtre `_tag` de la liste.
 * Le Desk normalise avant d'envoyer pour montrer tout de suite ce qui sera gardé (« Salon   2026 » devient « Salon 2026 ») et refuser sans
 * aller-retour ce que le serveur refuserait.
 */
export const TAG_FILTER = '_tag'
export const MAX_TAG = 50
export const MAX_TAGS_PER_DOC = 20

// Les blancs, écrits un à un comme côté serveur (`\s` n'a pas le même sens en JavaScript et en Python) ; contrôles, invisibles de format et
// demi-codets refusés. Vecteurs communs : `tests/vectors/tag_cases.json`.
const SPACES = /[ \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/gu
const FORBIDDEN = /[\p{Cc}\p{Cf}\p{Cs}]/u

/** Le nom gardé (forme composée NFC, blancs de bord retirés, blancs intérieurs réduits à un espace), ou `null` s'il est refusé (vide, trop long,
 * virgule, caractère de contrôle ou invisible). La longueur se compte en points de code, comme en Python. */
export function normalizeTag(text: string): string | null {
  const tag = text.normalize('NFC').replace(SPACES, ' ').replace(/^ +| +$/g, '')
  if (!tag || [...tag].length > MAX_TAG || tag.includes(',') || FORBIDDEN.test(tag)) return null
  return tag
}

/** Le même nom quelle que soit la casse : la clé du serveur (`casefold`), approchée ici par `toLowerCase` (le serveur tranche). */
export const sameTag = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase()

/** Un document unique (un réglage) et un type virtuel (ses documents vivent ailleurs) ne portent pas d'étiquettes. */
export const supportsTags = (meta: DocTypeMeta | null | undefined): boolean => !!meta && !meta.single && !meta.virtual

/**
 * Le filtre `_tag` tel que la liste le tient : une étiquette (`=`), `['!=', étiquette]` (ne l'a pas) ou `['in', [...]]` (en a une). Le serveur
 * accepte les trois dans une vue enregistrée ou un bloc d'espace : la liste qu'ils ouvrent doit filtrer pareil, et le montrer.
 */
export type TagFilter = string | ['!=', string] | ['in', string[]]

/** Le filtre `_tag` d'une adresse, d'une vue ou d'un bloc, normalisé ; `null` s'il n'est pas lisible (le serveur le refuserait). */
export function parseTagFilter(spec: unknown): TagFilter | null {
  if (typeof spec === 'string') return normalizeTag(spec)
  if (!Array.isArray(spec) || spec.length !== 2) return null
  const [op, value] = spec as [unknown, unknown]
  if ((op === '=' || op === '!=') && typeof value === 'string') {
    const tag = normalizeTag(value)
    return tag === null ? null : op === '=' ? tag : ['!=', tag]
  }
  if (op === 'in' && Array.isArray(value) && value.length > 0 && value.length <= 100) {
    const tags = value.map((v) => (typeof v === 'string' ? normalizeTag(v) : null))
    return tags.every((x): x is string => x !== null) ? ['in', tags] : null
  }
  return null
}

/** Ce que la pastille du filtre dit : « Urgent », « sauf Archivé », « Urgent ou Client ». */
export function tagFilterLabel(filter: TagFilter): string {
  if (typeof filter === 'string') return filter
  return filter[0] === '!=' ? t.tags.except(filter[1]) : t.tags.anyOf(filter[1])
}

/** L'étiquette filtrée par égalité dans ces filtres (celle qu'on met en évidence), s'il y en a une. */
export function tagFilterOf(filters: Filters): string | null {
  const f = parseTagFilter(filters[TAG_FILTER])
  return typeof f === 'string' ? f : null
}

/** Les filtres sans celui des étiquettes (la barre latérale compte les étiquettes dans les AUTRES filtres). */
export function withoutTag(filters: Filters): Filters {
  const { [TAG_FILTER]: _own, ...rest } = filters
  void _own
  return rest
}
