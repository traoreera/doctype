import { describe, expect, it } from 'vitest'
import { COLUMN_GAP, around, ranks, workflowGraph } from '@/lib/workflowgraph'
import type { WorkflowDef } from '@/lib/types'

// Le circuit de la demande de congé (tests/support/plugins/hr/doctypes/leave_request.yaml).
const LEAVE: WorkflowDef = {
  states: [{ name: 'Brouillon' }, { name: 'À approuver' }, { name: 'Approuvée', docstatus: 'submitted' }, { name: 'Refusée', docstatus: 'cancelled' }],
  transitions: [
    { action: 'Soumettre', from: 'Brouillon', to: 'À approuver' },
    { action: 'Approuver', from: 'À approuver', to: 'Approuvée', not_owner: true },
    { action: 'Refuser', from: 'À approuver', to: 'Refusée', not_owner: true },
    { action: 'Reprendre', from: 'À approuver', to: 'Brouillon', condition: 'comment' },
  ],
}

describe('le circuit en graphe', () => {
  it('range chaque état dans la colonne de sa distance au premier état', () => {
    expect(Object.fromEntries(ranks(LEAVE))).toEqual({ Brouillon: 0, 'À approuver': 1, Approuvée: 2, Refusée: 2 })
    const { nodes } = workflowGraph(LEAVE, {})
    const at = Object.fromEntries(nodes.map((n) => [n.id, n.position]))
    expect(at.Brouillon!.x).toBe(0)
    expect(at['À approuver']!.x).toBe(COLUMN_GAP)
    expect(at.Approuvée!.x).toBe(2 * COLUMN_GAP)
    expect(at.Approuvée!.y).toBeLessThan(at.Refusée!.y) // l'ordre du YAML dans une colonne
    expect(at.Approuvée!.y).toBe(-at.Refusée!.y) // centrés autour de la ligne du premier état
  })

  it('un nœud dit son état de document, s’il est le premier, final, ou un cul-de-sac', () => {
    const { nodes } = workflowGraph(LEAVE, {})
    const data = Object.fromEntries(nodes.map((n) => [n.id, n.data]))
    expect(data.Brouillon).toMatchObject({ docstatus: 'draft', initial: true, final: false, deadEnd: false })
    expect(data.Approuvée).toMatchObject({ docstatus: 'submitted', initial: false, final: true })
    expect(data.Refusée!.docstatus).toBe('cancelled')
    const closedAll = workflowGraph(LEAVE, { transitions: { Approuver: { closed: true }, Refuser: { closed: true }, Reprendre: { closed: true } } })
    expect(closedAll.nodes.find((n) => n.id === 'À approuver')!.data.deadEnd).toBe(true)
  })

  it('une flèche par transition, habillée de ce que l’organisation resserre ; un retour en arrière passe par-dessous', () => {
    const { edges } = workflowGraph(LEAVE, { transitions: { Refuser: { closed: true }, Soumettre: { condition: 'reason' } } })
    const by = Object.fromEntries(edges.map((e) => [e.id, e]))
    expect(by.Soumettre).toMatchObject({ source: 'Brouillon', target: 'À approuver', sourceHandle: 'out', targetHandle: 'in' })
    expect(by.Soumettre!.data.condition).toBe('reason')
    expect(by.Refuser!.data.closed).toBe(true)
    expect(by.Approuver!.data).toMatchObject({ notOwner: true, locked: true })
    expect(by.Reprendre).toMatchObject({ sourceHandle: 'loop-out', targetHandle: 'loop-in' })
    expect(by.Reprendre!.data).toMatchObject({ back: true, yamlCondition: 'comment' })
  })

  it('une position déplacée par l’utilisateur gagne sur le placement calculé', () => {
    const { nodes } = workflowGraph(LEAVE, {}, { Refusée: { x: 10, y: 20 } })
    expect(nodes.find((n) => n.id === 'Refusée')!.position).toEqual({ x: 10, y: 20 })
  })

  it('un état que rien n’atteint ne casse pas le dessin : dernière colonne', () => {
    const odd: WorkflowDef = { states: [...LEAVE.states, { name: 'Orphelin' }], transitions: LEAVE.transitions }
    expect(ranks(odd).get('Orphelin')).toBe(3)
    expect(workflowGraph(null, null)).toEqual({ nodes: [], edges: [] })
  })

  it('ce qui part d’un état et ce qui y arrive', () => {
    expect(around(LEAVE, 'À approuver')).toEqual({ leaving: ['Approuver', 'Refuser', 'Reprendre'], arriving: ['Soumettre'] })
    expect(around(LEAVE, 'Brouillon')).toEqual({ leaving: ['Soumettre'], arriving: ['Reprendre'] })
  })
})
