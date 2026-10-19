/**
 * Les derniers documents ouverts, pour l'accueil. Sans interface : testé seul.
 *
 * Une commodité de CET appareil (localStorage), propre à chaque utilisateur ET organisation : sur un poste partagé, l'accueil de l'un
 * ne montre pas les documents de l'autre. Tout accès au stockage est enveloppé (navigation privée) : sans lui, la liste est vide.
 */
export interface Recent {
  doctype: string
  name: string
  /** Ce que l'utilisateur reconnaît (le titre du document, sinon son nom). */
  title: string
  /** Quand il l'a ouvert (ms depuis 1970). */
  at: number
}

export const MAX_RECENTS = 8

export const recentsKey = (tenant: string | null, user: string): string => `xdt.desk.recent.${tenant ?? '-'}.${user || '-'}`

function isRecent(v: unknown): v is Recent {
  const r = v as Partial<Recent> | null
  return !!r && typeof r.doctype === 'string' && typeof r.name === 'string' && typeof r.title === 'string' && typeof r.at === 'number'
}

export function readRecents(key: string): Recent[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? '[]')
    return Array.isArray(raw) ? raw.filter(isRecent).slice(0, MAX_RECENTS) : []
  } catch {
    return []
  }
}

/** Le même document ne figure qu'une fois (le plus récent), et la liste est bornée. */
export function pushRecent(list: readonly Recent[], entry: Recent): Recent[] {
  return [entry, ...list.filter((r) => !(r.doctype === entry.doctype && r.name === entry.name))].slice(0, MAX_RECENTS)
}

export function writeRecents(key: string, list: readonly Recent[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(list))
  } catch {
    // stockage indisponible : l'accueil ne se souviendra pas, rien de plus
  }
}
