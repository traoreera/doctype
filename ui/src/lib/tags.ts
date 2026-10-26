import type { DocTypeMeta, Filters } from './types'

/**
 * Étiquettes d'un document : les règles d'un NOM, jumelles de `tags.py` côté serveur (qui reste l'autorité), et le filtre `_tag` de la liste.
 * Le Desk normalise avant d'envoyer pour montrer tout de suite ce qui sera gardé (« Salon   2026 » devient « Salon 2026 ») et refuser sans
 * aller-retour ce que le serveur refuserait.
 */
export const TAG_FILTER = '_tag'
export const MAX_TAG = 50
export const MAX_TAGS_PER_DOC = 20

// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/

/** Le nom gardé (blancs de bord retirés, blancs intérieurs réduits à un espace), ou `null` s'il est refusé (vide, trop long, virgule, contrôle). */
export function normalizeTag(text: string): string | null {
  const tag = text.replace(/\s+/gu, ' ').trim()
  if (!tag || [...tag].length > MAX_TAG || tag.includes(',') || CONTROL.test(tag)) return null
  return tag
}

/** Le même nom quelle que soit la casse : la clé du serveur (`casefold`), approchée ici par `toLowerCase` (le serveur tranche). */
export const sameTag = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase()

/** Un document unique (un réglage) et un type virtuel (ses documents vivent ailleurs) ne portent pas d'étiquettes. */
export const supportsTags = (meta: DocTypeMeta | null | undefined): boolean => !!meta && !meta.single && !meta.virtual

/** L'étiquette filtrée par `=` dans ces filtres, s'il y en a une. */
export function tagFilterOf(filters: Filters): string | null {
  const v = filters[TAG_FILTER]
  return typeof v === 'string' ? v : null
}

/** Les filtres sans celui des étiquettes (la barre latérale compte les étiquettes dans les AUTRES filtres). */
export function withoutTag(filters: Filters): Filters {
  const { [TAG_FILTER]: _own, ...rest } = filters
  void _own
  return rest
}
