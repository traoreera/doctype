import type { ViewName, ViewsMeta } from './types'

/**
 * La vue que cet appareil a choisie pour chaque type (Liste, Rapport, Kanban…) : une commodité, pas une donnée du compte. localStorage,
 * enveloppé (navigation privée) ; sans lui, c'est la vue par défaut du type. Les colonnes d'un rapport suivent la même règle.
 */
const key = (doctype: string, what: string): string => `xdt.desk.${what}.${doctype}`

export function readView(doctype: string): string | null {
  try {
    return localStorage.getItem(key(doctype, 'view'))
  } catch {
    return null
  }
}

export function writeView(doctype: string, view: string): void {
  try {
    localStorage.setItem(key(doctype, 'view'), view)
  } catch {
    // le choix vaut pour cette session seulement
  }
}

function readNames(doctype: string, what: string): string[] | null {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(key(doctype, what)) ?? 'null')
    return Array.isArray(raw) && raw.every((x) => typeof x === 'string') ? (raw as string[]) : null
  } catch {
    return null
  }
}

function writeNames(doctype: string, what: string, names: string[]): void {
  try {
    // Une liste VIDE veut dire « oublie mon choix » : la clé part, et on retombe sur les colonnes du type.
    if (names.length) localStorage.setItem(key(doctype, what), JSON.stringify(names))
    else localStorage.removeItem(key(doctype, what))
  } catch {
    // idem
  }
}

/** Les colonnes du RAPPORT. */
export const readColumns = (doctype: string): string[] | null => readNames(doctype, 'columns')
export const writeColumns = (doctype: string, columns: string[]): void => writeNames(doctype, 'columns', columns)

/**
 * Les colonnes de la LISTE, sous leur propre clé : le rapport et la liste ne montrent pas les mêmes choses (le rapport
 * sait aussi tenir `owner`, `creation`…), et un choix fait dans l'un n'a pas à déplacer l'autre.
 */
export const readListColumns = (doctype: string): string[] | null => readNames(doctype, 'listcolumns')
export const writeListColumns = (doctype: string, columns: string[]): void => writeNames(doctype, 'listcolumns', columns)

/**
 * La vue à ouvrir : celle de l'adresse si le type la propose, sinon celle que l'appareil a choisie en dernier, sinon la vue par défaut du type.
 * Une vue qui n'existe pas (ou plus) retombe sur la liste : jamais un écran vide.
 */
export function pickView(views: ViewsMeta | undefined, asked: unknown, remembered: string | null): ViewName {
  const available = views?.available ?? (['list'] as ViewName[])
  for (const candidate of [asked, remembered, views?.default]) {
    if (typeof candidate === 'string' && (available as string[]).includes(candidate)) return candidate as ViewName
  }
  return 'list'
}
