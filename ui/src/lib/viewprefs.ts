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

export function readColumns(doctype: string): string[] | null {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(key(doctype, 'columns')) ?? 'null')
    return Array.isArray(raw) && raw.every((x) => typeof x === 'string') ? (raw as string[]) : null
  } catch {
    return null
  }
}

export function writeColumns(doctype: string, columns: string[]): void {
  try {
    localStorage.setItem(key(doctype, 'columns'), JSON.stringify(columns))
  } catch {
    // idem
  }
}

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
