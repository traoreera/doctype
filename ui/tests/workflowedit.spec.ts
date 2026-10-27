import { describe, expect, it } from 'vitest'
import { deadEnds, flowRows, hasTweaks, withTweak } from '@/lib/workflowedit'
import type { WorkflowDef } from '@/lib/types'

const DEF: WorkflowDef = {
  states: [
    { name: 'Brouillon' },
    { name: 'À approuver' },
    { name: 'Approuvé', docstatus: 'submitted' },
    { name: 'Refusé', docstatus: 'cancelled' },
  ],
  transitions: [
    { action: 'Soumettre', from: 'Brouillon', to: 'À approuver' },
    { action: 'Approuver', from: 'À approuver', to: 'Approuvé', not_owner: true },
    { action: 'Refuser', from: 'À approuver', to: 'Refusé', condition: 'motif' },
  ],
}

describe('les lignes de l’éditeur d’automate', () => {
  it('montrent l’automate du YAML, habillé de ce que l’organisation a enregistré', () => {
    const rows = flowRows(DEF, { transitions: { Refuser: { closed: true }, Soumettre: { condition: 'objet', not_owner: true } } })
    expect(rows.map((r) => r.action)).toEqual(['Soumettre', 'Approuver', 'Refuser']) // l’ordre du YAML
    expect(rows[0]).toMatchObject({ closed: false, condition: 'objet', notOwner: true, locked: false })
    expect(rows[2]).toMatchObject({ closed: true, condition: '', yamlCondition: 'motif' })
  })

  it('montrent coché et VERROUILLÉ ce que le YAML impose déjà (le serveur refuse qu’on y touche)', () => {
    const [, approve] = flowRows(DEF, {})
    expect(approve).toMatchObject({ action: 'Approuver', locked: true, notOwner: true })
  })

  it('sans habillage ni automate, il n’y a rien à montrer', () => {
    expect(flowRows(DEF, null).every((r) => !r.closed && !r.condition)).toBe(true)
    expect(flowRows(null, null)).toEqual([])
  })
})

describe('poser et retirer une retouche', () => {
  it('pose une fermeture, puis la retire quand on la décoche', () => {
    const closed = withTweak({}, 'Refuser', 'closed', true)
    expect(closed).toEqual({ transitions: { Refuser: { closed: true } } })
    // `false` RETIRE la clé : le serveur refuse `closed: false`, qui serait un desserrage.
    expect(withTweak(closed, 'Refuser', 'closed', false)).toEqual({})
  })

  it('une condition vide (ou blanche) retire la clé au lieu d’en poser une vide', () => {
    const withCond = withTweak({}, 'Soumettre', 'condition', '  objet == 1  ')
    expect(withCond.transitions?.Soumettre.condition).toBe('objet == 1') // rognée
    expect(withTweak(withCond, 'Soumettre', 'condition', '   ')).toEqual({})
  })

  it('garde les autres retouches de la même transition et des autres', () => {
    let config = withTweak({}, 'Soumettre', 'closed', true)
    config = withTweak(config, 'Soumettre', 'not_owner', true)
    config = withTweak(config, 'Refuser', 'condition', 'motif')
    expect(config).toEqual({ transitions: { Soumettre: { closed: true, not_owner: true }, Refuser: { condition: 'motif' } } })
    config = withTweak(config, 'Soumettre', 'closed', false)
    expect(config.transitions?.Soumettre).toEqual({ not_owner: true })
  })

  it('dit s’il reste quelque chose à enregistrer', () => {
    expect(hasTweaks({})).toBe(false)
    expect(hasTweaks({ transitions: {} })).toBe(false)
    expect(hasTweaks({ transitions: { Refuser: { closed: true } } })).toBe(true)
  })
})

describe('les culs-de-sac, dits AVANT l’enregistrement', () => {
  it('un état dont toutes les sorties seraient fermées est nommé', () => {
    expect(deadEnds(DEF, { transitions: { Soumettre: { closed: true } } })).toEqual(['Brouillon'])
    expect(deadEnds(DEF, { transitions: { Approuver: { closed: true } } })).toEqual([])
    expect(deadEnds(DEF, { transitions: { Approuver: { closed: true }, Refuser: { closed: true } } })).toEqual(['À approuver'])
  })

  it('une condition ne compte pas pour un cul-de-sac (c’est une règle métier, pas une configuration fautive)', () => {
    expect(deadEnds(DEF, { transitions: { Soumettre: { condition: 'jamais == 1' } } })).toEqual([])
  })
})
