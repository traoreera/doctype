/**
 * Le Form Builder (« Personnaliser », à la manière de Frappe) : la disposition d'un formulaire comme un TABLEAU qu'on manipule — des onglets,
 * des sections, des colonnes, des champs — et sa traduction exacte en `layout` d'habillage, la seule chose que le serveur reçoit.
 *
 * Tout est pur ici, testé à part (`tests/formbuilder.spec.ts`) : la page ne fait qu'afficher le tableau et appeler ces fonctions, qui rendent
 * toujours un NOUVEAU tableau. Les règles du serveur (`overrides.validate_form_override`, `schema.layout_tab_problems`) y sont tenues AVANT
 * l'enregistrement, pour qu'on ne les découvre pas par un refus :
 * - une colonne n'est jamais vide — le tableau en garde d'éphémères (on vient d'en ajouter une), `toLayout` les retire, et une section qui
 *   n'a plus aucun champ disparaît ;
 * - 1 à 4 colonnes par section, un champ placé une fois ;
 * - des onglets pour toutes les sections ou pour aucune, chaque onglet d'un seul tenant, nommés (1-60 caractères), distincts, 12 au plus.
 * Un champ visible que la disposition ne place pas n'est pas perdu : le serveur l'ajoute à la fin du premier onglet (`complete_layout`).
 */
import type { FieldTweak, FormOverride, LayoutSection } from './types'

export const MAX_COLUMNS = 4
export const MAX_TABS = 12
export const MAX_TAB_NAME = 60

export interface BoardSection {
  /** Clé stable le temps de l'édition (Vue en a besoin pour suivre une section qu'on déplace) ; jamais envoyée. */
  key: string
  section: string | null
  collapsible: boolean
  collapsed: boolean
  columns: string[][]
}

export interface BoardTab {
  key: string
  /** `null` : le formulaire n'a pas d'onglets (un seul « onglet » implicite). */
  name: string | null
  sections: BoardSection[]
}

export interface Board {
  tabs: BoardTab[]
}

/** Où se trouve un champ, ou une place où le déposer (`index` : sa position dans la colonne). */
export interface Slot {
  tab: number
  section: number
  column: number
  index: number
}

let seq = 0
const key = (prefix: string): string => `${prefix}${++seq}`

export const isTabbed = (board: Board): boolean => board.tabs.some((t) => t.name !== null)

/** Le tableau d'une disposition : les sections qui se suivent avec le même onglet forment cet onglet. */
export function fromLayout(layout: readonly LayoutSection[] | null | undefined): Board {
  const tabs: BoardTab[] = []
  for (const s of layout ?? []) {
    const name = s.tab ?? null
    let tab = tabs[tabs.length - 1]
    if (!tab || tab.name !== name) {
      tab = { key: key('t'), name, sections: [] }
      tabs.push(tab)
    }
    tab.sections.push({ key: key('s'), section: s.section ?? null, collapsible: !!s.collapsible, collapsed: !!s.collapsed, columns: s.columns.map((c) => [...c]) })
  }
  if (!tabs.length) tabs.push({ key: key('t'), name: null, sections: [newSection()] })
  return { tabs }
}

export const newSection = (title: string | null = null): BoardSection => ({ key: key('s'), section: title, collapsible: false, collapsed: false, columns: [[]] })

/** La disposition à envoyer : colonnes vides retirées, sections vides retirées, `tab` sur toutes les sections ou sur aucune. */
export function toLayout(board: Board): LayoutSection[] {
  const tabbed = isTabbed(board)
  const out: LayoutSection[] = []
  for (const tab of board.tabs) {
    for (const s of tab.sections) {
      const columns = s.columns.filter((c) => c.length).map((c) => [...c])
      if (!columns.length) continue
      out.push({ ...(tabbed ? { tab: (tab.name ?? '').trim() } : {}), section: s.section?.trim() || null, collapsible: s.collapsible, collapsed: s.collapsible && s.collapsed, columns })
    }
  }
  return out
}

/** Ce que le serveur refuserait, dit avant d'enregistrer (vide : rien). */
export function boardProblems(board: Board): string[] {
  const out: string[] = []
  if (isTabbed(board)) {
    const names = board.tabs.map((t) => (t.name ?? '').trim())
    if (names.some((n) => !n)) out.push('Chaque onglet doit avoir un nom.')
    if (names.some((n) => n.length > MAX_TAB_NAME)) out.push(`Un nom d’onglet fait au plus ${MAX_TAB_NAME} caractères.`)
    if (new Set(names.map((n) => n.toLowerCase())).size !== names.length) out.push('Deux onglets portent le même nom.')
    if (board.tabs.length > MAX_TABS) out.push(`${MAX_TABS} onglets au plus.`)
  }
  if (!toLayout(board).length) out.push('Placez au moins un champ.')
  return out
}

/** Les champs placés, dans l'ordre de lecture. */
export function placed(board: Board): string[] {
  return board.tabs.flatMap((t) => t.sections.flatMap((s) => s.columns.flat()))
}

/** Où est ce champ, ou `null` s'il n'est pas placé. */
export function locate(board: Board, field: string): Slot | null {
  for (const [ti, tab] of board.tabs.entries())
    for (const [si, s] of tab.sections.entries())
      for (const [ci, col] of s.columns.entries()) {
        const index = col.indexOf(field)
        if (index >= 0) return { tab: ti, section: si, column: ci, index }
      }
  return null
}

const clone = (board: Board): Board => ({ tabs: board.tabs.map((t) => ({ ...t, sections: t.sections.map((s) => ({ ...s, columns: s.columns.map((c) => [...c]) })) })) })

/** Retire un champ de la disposition (il retourne dans la palette des champs non placés). */
export function unplace(board: Board, field: string): Board {
  const out = clone(board)
  for (const t of out.tabs) for (const s of t.sections) s.columns = s.columns.map((c) => c.filter((n) => n !== field))
  return out
}

/** Dépose un champ à cette place (d'où qu'il vienne : une autre colonne, ou la palette). `index` au-delà de la colonne : à la fin. */
export function moveField(board: Board, field: string, to: Slot): Board {
  const from = locate(board, field)
  const out = unplace(board, field)
  const col = out.tabs[to.tab]?.sections[to.section]?.columns[to.column]
  if (!col) return board
  // Dans la même colonne, plus bas : retirer le champ a décalé d'une place ce qui le suivait.
  const sameColumn = from && from.tab === to.tab && from.section === to.section && from.column === to.column
  const index = Math.max(0, Math.min(sameColumn && from.index < to.index ? to.index - 1 : to.index, col.length))
  col.splice(index, 0, field)
  return out
}

export type Nudge = 'up' | 'down' | 'left' | 'right' | 'prevSection' | 'nextSection'

/** Le déplacement au CLAVIER d'un champ (l'équivalent du glisser-déposer) : d'une place, d'une colonne, ou d'une section. */
export function nudge(board: Board, field: string, how: Nudge): Board {
  const at = locate(board, field)
  if (!at) return board
  const section = board.tabs[at.tab]!.sections[at.section]!
  const col = section.columns[at.column]!
  switch (how) {
    case 'up':
      return at.index > 0 ? moveField(board, field, { ...at, index: at.index - 1 }) : board
    case 'down':
      return at.index < col.length - 1 ? moveField(board, field, { ...at, index: at.index + 2 }) : board
    case 'left':
      return at.column > 0 ? moveField(board, field, { ...at, column: at.column - 1, index: Infinity }) : board
    case 'right':
      return at.column < section.columns.length - 1 ? moveField(board, field, { ...at, column: at.column + 1, index: Infinity }) : board
    case 'prevSection':
    case 'nextSection': {
      const flat = board.tabs.flatMap((t, ti) => t.sections.map((_, si) => ({ tab: ti, section: si })))
      const here = flat.findIndex((p) => p.tab === at.tab && p.section === at.section)
      const there = flat[here + (how === 'prevSection' ? -1 : 1)]
      return there ? moveField(board, field, { ...there, column: 0, index: how === 'prevSection' ? Infinity : 0 }) : board
    }
  }
}

export function addSection(board: Board, tab: number, after: number | null = null, title: string | null = null): Board {
  const out = clone(board)
  const sections = out.tabs[tab]?.sections
  if (!sections) return board
  sections.splice(after === null ? sections.length : after + 1, 0, newSection(title))
  return out
}

/** Retire une section : ses champs retournent dans la palette (un formulaire garde au moins une section). */
export function removeSection(board: Board, tab: number, section: number): Board {
  const out = clone(board)
  const t = out.tabs[tab]
  if (!t || !t.sections[section]) return board
  t.sections.splice(section, 1)
  if (!t.sections.length) t.sections.push(newSection())
  return out
}

export function updateSection(board: Board, tab: number, section: number, patch: Partial<Pick<BoardSection, 'section' | 'collapsible' | 'collapsed'>>): Board {
  const out = clone(board)
  const s = out.tabs[tab]?.sections[section]
  if (!s) return board
  Object.assign(s, patch)
  if (!s.collapsible) s.collapsed = false
  return out
}

/** Change le nombre de colonnes (1 à 4). Une colonne retirée verse ses champs dans la dernière qui reste : rien ne se perd. */
export function setColumns(board: Board, tab: number, section: number, count: number): Board {
  const out = clone(board)
  const s = out.tabs[tab]?.sections[section]
  if (!s) return board
  const n = Math.max(1, Math.min(MAX_COLUMNS, Math.round(count)))
  if (n > s.columns.length) while (s.columns.length < n) s.columns.push([])
  else if (n < s.columns.length) {
    const extra = s.columns.splice(n).flat()
    s.columns[n - 1]!.push(...extra)
  }
  return out
}

/** Déplace une section (même onglet ou un autre) : `index` est sa nouvelle place dans l'onglet d'arrivée. */
export function moveSection(board: Board, from: { tab: number; section: number }, to: { tab: number; index: number }): Board {
  const out = clone(board)
  const source = out.tabs[from.tab]?.sections
  const target = out.tabs[to.tab]?.sections
  if (!source || !target || !source[from.section]) return board
  const [moved] = source.splice(from.section, 1)
  const index = from.tab === to.tab && from.section < to.index ? to.index - 1 : to.index
  target.splice(Math.max(0, Math.min(index, target.length)), 0, moved!)
  if (!source.length) source.push(newSection())
  return out
}

/** Ajoute un onglet. Le premier fait passer le formulaire en onglets : ce qui existait devient l'onglet `firstName`. */
export function addTab(board: Board, name: string, firstName = 'Détails'): Board {
  const out = clone(board)
  if (!isTabbed(out)) out.tabs[0]!.name = firstName
  out.tabs.push({ key: key('t'), name, sections: [newSection()] })
  return out
}

export function renameTab(board: Board, tab: number, name: string): Board {
  const out = clone(board)
  if (!out.tabs[tab] || !isTabbed(out)) return board
  out.tabs[tab]!.name = name
  return out
}

/** Retire un onglet : ses sections rejoignent l'onglet d'avant (ou d'après). Le dernier retiré, le formulaire n'a plus d'onglets. */
export function removeTab(board: Board, tab: number): Board {
  const out = clone(board)
  const gone = out.tabs[tab]
  if (!gone || out.tabs.length < 2) {
    if (gone && out.tabs.length === 1) gone.name = null
    return out
  }
  out.tabs.splice(tab, 1)
  const into = out.tabs[Math.max(0, tab - 1)]!
  if (tab === 0) into.sections.unshift(...gone.sections.filter((s) => s.columns.some((c) => c.length)))
  else into.sections.push(...gone.sections.filter((s) => s.columns.some((c) => c.length)))
  if (out.tabs.length === 1) out.tabs[0]!.name = null
  return out
}

/** Le libellé ou la description d'un champ du TYPE, retouchés par l'habillage (affichage seulement) ; vide : retiré, le YAML reprend la main. */
export function withDisplay(config: FormOverride | null | undefined, fieldname: string, key: 'label' | 'description', value: string): FormOverride {
  const out: FormOverride = { ...(config ?? {}) }
  const fields: Record<string, FieldTweak> = { ...(out.fields ?? {}) }
  const tw: FieldTweak = { ...(fields[fieldname] ?? {}) }
  if (value.trim()) tw[key] = value.trim()
  else delete tw[key]
  if (Object.keys(tw).length) fields[fieldname] = tw
  else delete fields[fieldname]
  if (Object.keys(fields).length) out.fields = fields
  else delete out.fields
  return out
}
