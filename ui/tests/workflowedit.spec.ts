import { t } from '@/i18n'
import { describe, expect, it } from 'vitest'
import { addState, addTransition, additionProblems, deadEnds, flowRows, hasTweaks, isAddedState, merged, patchAdded, removeAdded, removeState, setStateDocstatus, withTweak } from '@/lib/workflowedit'
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
    { action: 'Approuver', from: 'À approuver', to: 'Approuvé', not_owner: true, flow: 'commande_fournisseur' },
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

  it('disent quel flux xflow suit une transition (le YAML seul le décide : l’organisation ne le change pas)', () => {
    const rows = flowRows(DEF, { transitions: { Approuver: { closed: true } } })
    expect(rows.map((r) => r.flow)).toEqual([null, 'commande_fournisseur', null])
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
    expect(withCond.transitions?.['Soumettre']?.condition).toBe('objet == 1') // rognée
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

describe('ce que l’organisation AJOUTE au circuit', () => {
  const APP: WorkflowDef = {
    states: [{ name: 'Brouillon' }, { name: 'À approuver' }, { name: 'Approuvée', docstatus: 'submitted' }],
    transitions: [
      { action: 'Soumettre', from: 'Brouillon', to: 'À approuver' },
      { action: 'Approuver', from: 'À approuver', to: 'Approuvée' },
    ],
  }
  const L = t.workflowBuilder.problems

  it('ajoute un état et la transition qui y mène ; le circuit de l’organisation les contient', () => {
    let c = addState({}, ' Visée ')
    expect(additionProblems(APP, c, L)).toEqual([L.unreachable('Visée')])
    c = addTransition(c, { action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'hr:x:write', not_owner: true, condition: '  ' })
    expect(c).toEqual({ states: [{ name: 'Visée', docstatus: 'draft' }], added: [{ action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'hr:x:write', not_owner: true }] })
    expect(additionProblems(APP, c, L)).toEqual([])
    expect(merged(APP, c)!.states.map((s) => s.name)).toEqual(['Brouillon', 'À approuver', 'Approuvée', 'Visée'])
    const rows = flowRows(APP, c)
    expect(rows.map((r) => [r.action, r.added])).toEqual([['Soumettre', false], ['Approuver', false], ['Viser', true]])
    expect(rows[2]!.grant).toBe('hr:x:write')
    expect(isAddedState(c, 'Visée') && !isAddedState(c, 'Brouillon')).toBe(true)
  })

  it('une retouche ne fait plus disparaître les ajouts (défaut corrigé : `withTweak` rendait `{transitions}` seul)', () => {
    const c = addTransition(addState({}, 'Visée'), { action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'g:x:y' })
    const tightened = withTweak(c, 'Approuver', 'closed', true)
    expect(tightened.added).toHaveLength(1)
    expect(tightened.transitions).toEqual({ Approuver: { closed: true } })
    expect(withTweak(tightened, 'Approuver', 'closed', false)).toEqual(c)
    expect(hasTweaks(c)).toBe(true)
  })

  it('retirer un état ajouté retire les transitions ajoutées qui le touchent ; un habillage vide n’a plus de clé', () => {
    let c = addTransition(addState({}, 'Visée'), { action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'g:x:y' })
    c = addTransition(c, { action: 'Valider', from: 'Visée', to: 'Approuvée', grant: 'g:x:y' })
    expect(removeState(c, 'Visée')).toEqual({})
    expect(removeAdded(c, 'Valider').added!.map((t) => t.action)).toEqual(['Viser'])
  })

  it('modifier un ajout retire ce qui est vide, et dit les noms en double et le droit manquant', () => {
    let c = addTransition(addState({}, 'Visée'), { action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'g:x:y' })
    c = patchAdded(c, 'Viser', { condition: ' montant > 0 ', not_owner: true })
    expect(c.added![0]).toMatchObject({ condition: 'montant > 0', not_owner: true })
    c = patchAdded(c, 'Viser', { condition: '', not_owner: false, grant: '' })
    expect(c.added![0]).toEqual({ action: 'Viser', from: 'À approuver', to: 'Visée', grant: '' })
    expect(additionProblems(APP, c, L)).toEqual([L.noGrant('Viser')])
    const twice = addTransition(c, { action: 'Approuver', from: 'Visée', to: 'Approuvée', grant: 'g:x:y' })
    expect(additionProblems(APP, addState(twice, 'Brouillon'), L)).toEqual([L.duplicateState('Brouillon'), L.duplicateAction('Approuver'), L.noGrant('Viser')])
    expect(setStateDocstatus(c, 'Visée', 'submitted').states).toEqual([{ name: 'Visée', docstatus: 'submitted' }])
  })

  it('un cul-de-sac se juge sur le circuit de l’organisation, ajouts compris', () => {
    const c = addTransition(addState({}, 'Visée'), { action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'g:x:y' })
    // « Visée » n'a aucune sortie : c'est une fin, pas un cul-de-sac (rien n'en partait).
    expect(deadEnds(APP, c)).toEqual([])
    expect(deadEnds(APP, withTweak(withTweak(c, 'Approuver', 'closed', true), 'Soumettre', 'closed', true))).toEqual(['Brouillon'])
  })

  it('une automatisation se pose sur une transition de l’application, et se retire en la vidant', () => {
    const c = withTweak({}, 'Soumettre', 'flow', ' accuse_reception ')
    expect(c).toEqual({ transitions: { Soumettre: { flow: 'accuse_reception' } } })
    expect(flowRows(APP, c)[0]).toMatchObject({ flow: 'accuse_reception', yamlFlow: null })
    expect(withTweak(c, 'Soumettre', 'flow', '')).toEqual({})
    const added = addTransition(addState({}, 'Visée'), { action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'g:x:y' })
    expect(patchAdded(added, 'Viser', { flow: 'prevenir' }).added![0]!.flow).toBe('prevenir')
    expect(patchAdded(patchAdded(added, 'Viser', { flow: 'prevenir' }), 'Viser', { flow: '' }).added![0]).not.toHaveProperty('flow')
  })
})
