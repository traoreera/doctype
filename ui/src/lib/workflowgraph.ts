/**
 * Le circuit d'un type dessiné comme un graphe (l'éditeur de workflow, façon Frappe) : des ÉTATS en nœuds, des TRANSITIONS en flèches.
 *
 * Tout est pur ici, testé à part (`tests/workflowgraph.spec.ts`) : la page ne fait que donner ces nœuds et ces flèches à Vue Flow.
 * Le graphe montre l'automate du YAML (`definition`) habillé de ce que l'organisation RESSERRE (`flowRows` de `lib/workflowedit`) :
 * il n'invente ni état ni transition (doc/programme.md §3.6), il dit seulement lesquelles sont fermées, conditionnées ou interdites
 * à l'auteur.
 *
 * Placement : de gauche à droite, chaque état dans la colonne de sa distance au PREMIER état (celui d'un document neuf) ; dans une
 * colonne, l'ordre du YAML. Une flèche qui revient en arrière (ou reste dans sa colonne) passe par-dessous, pour ne pas croiser les
 * autres. Un état que rien n'atteint se range dans une dernière colonne : le YAML l'interdit, mais le dessin ne doit pas casser.
 */
import { deadEnds, flowRows, type FlowRow } from './workflowedit'
import type { WorkflowDef, WorkflowOverride } from './types'

export const COLUMN_GAP = 280
export const ROW_GAP = 150

export type Docstatus = 'draft' | 'submitted' | 'cancelled'

export interface StateData {
  name: string
  docstatus: Docstatus
  /** Le premier état : celui d'un document qui vient d'être créé. */
  initial: boolean
  /** Aucune transition n'en part dans le YAML : un état final (approuvé, refusé…). */
  final: boolean
  /** Il AVAIT une sortie, et toutes sont fermées par l'habillage : un document s'y échouerait (le serveur refuse d'enregistrer ça). */
  deadEnd: boolean
}

export interface StateNode {
  id: string
  type: 'state'
  position: { x: number; y: number }
  data: StateData
}

export interface TransitionEdge {
  id: string
  type: 'transition'
  source: string
  target: string
  sourceHandle: 'out' | 'loop-out'
  targetHandle: 'in' | 'loop-in'
  data: FlowRow & { back: boolean }
}

/** La colonne de chaque état : sa distance (en transitions du YAML) au premier état. */
export function ranks(definition: WorkflowDef | null | undefined): Map<string, number> {
  const states = definition?.states ?? []
  const out = new Map<string, number>()
  if (!states.length) return out
  const next = new Map<string, string[]>()
  for (const t of definition?.transitions ?? []) next.set(t.from, [...(next.get(t.from) ?? []), t.to])
  const queue = [states[0]!.name]
  out.set(states[0]!.name, 0)
  while (queue.length) {
    const here = queue.shift()!
    for (const to of next.get(here) ?? []) {
      if (out.has(to)) continue
      out.set(to, out.get(here)! + 1)
      queue.push(to)
    }
  }
  const last = Math.max(0, ...out.values()) + 1
  for (const st of states) if (!out.has(st.name)) out.set(st.name, last)
  return out
}

/** Les nœuds et les flèches du circuit, prêts pour Vue Flow. `positions` : celles que l'utilisateur a déplacées (elles gagnent). */
export function workflowGraph(
  definition: WorkflowDef | null | undefined,
  config: WorkflowOverride | null | undefined,
  positions: Record<string, { x: number; y: number }> = {},
): { nodes: StateNode[]; edges: TransitionEdge[] } {
  const states = definition?.states ?? []
  const column = ranks(definition)
  const stuck = new Set(deadEnds(definition, config))
  const leaving = new Set((definition?.transitions ?? []).map((t) => t.from))
  const byColumn = new Map<number, string[]>()
  for (const st of states) byColumn.set(column.get(st.name)!, [...(byColumn.get(column.get(st.name)!) ?? []), st.name])

  const nodes: StateNode[] = states.map((st, i) => {
    const col = column.get(st.name)!
    const siblings = byColumn.get(col)!
    const row = siblings.indexOf(st.name)
    return {
      id: st.name,
      type: 'state',
      position: positions[st.name] ?? { x: col * COLUMN_GAP, y: (row - (siblings.length - 1) / 2) * ROW_GAP },
      data: { name: st.name, docstatus: st.docstatus ?? 'draft', initial: i === 0, final: !leaving.has(st.name), deadEnd: stuck.has(st.name) },
    }
  })
  const edges: TransitionEdge[] = flowRows(definition, config).map((row) => {
    const back = (column.get(row.to) ?? 0) <= (column.get(row.from) ?? 0)
    return {
      id: row.action,
      type: 'transition',
      source: row.from,
      target: row.to,
      sourceHandle: back ? 'loop-out' : 'out',
      targetHandle: back ? 'loop-in' : 'in',
      data: { ...row, back },
    }
  })
  return { nodes, edges }
}

/** Ce qui part d'un état et ce qui y arrive : le panneau d'un état les liste, cliquables. */
export function around(definition: WorkflowDef | null | undefined, state: string): { leaving: string[]; arriving: string[] } {
  const transitions = definition?.transitions ?? []
  return { leaving: transitions.filter((t) => t.from === state).map((t) => t.action), arriving: transitions.filter((t) => t.to === state).map((t) => t.action) }
}
