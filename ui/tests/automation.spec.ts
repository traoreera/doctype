import { describe, expect, it } from 'vitest'
import {
  TRIGGER_ID, addStep, arrange, blankStep, connect, disconnect, docActionOf, docEventName, docStep, edgesOf, emptyFlow, firstStep, forDeploy,
  freeSpot, moveNode, parseDocEvent, patchPayload, portsOf, positions, problems, removeStep, renameStep, setCase, targetOf, triggerFields, uniqueId,
  variablesFor, type FlowDefinition,
} from '@/lib/automation'

const FLOW: FlowDefinition = {
  name: 'approuver_petites_demandes',
  trigger: { type: 'event', event_name: 'doctype.Demande.after_transition' },
  steps: [
    { id: 'lire', type: 'action', plugin: 'xcore-doctype', action: 'doc.get', payload: { doctype: 'Demande', name: '{{ trigger.name }}' }, on_success: 'petite' },
    { id: 'petite', type: 'condition', condition: { left: '{{ steps.lire.result.doc.data.jours }}', operator: '<=', right: '2' }, if_true: 'approuver', if_false: 'prevenir' },
    { id: 'approuver', type: 'action', plugin: 'xcore-doctype', action: 'doc.transition', payload: { doctype: 'Demande', name: '{{ steps.lire.result.doc.name }}', action: 'Approuver', version: '{{ steps.lire.result.doc.version }}' } },
    { id: 'prevenir', type: 'action', plugin: 'mailer', action: 'send', payload: { to: 'rh@example.com' } },
  ],
  tags: ['rh'],
}

describe('le modèle d’une automatisation xflow', () => {
  it('les liens SONT les champs des steps : chaque sortie, le déclencheur vers le premier step', () => {
    expect(edgesOf(FLOW).map((e) => `${e.source}.${e.port}>${e.target}`)).toEqual([
      `${TRIGGER_ID}.start>lire`, 'lire.on_success>petite', 'petite.if_true>approuver', 'petite.if_false>prevenir',
    ])
    expect(portsOf(FLOW.steps[1]!).map((p) => p.label)).toEqual(['vrai', 'faux'])
  })

  it('relier et délier écrivent le champ de la sortie ; depuis le déclencheur, le premier step', () => {
    let def = connect(FLOW, 'approuver', 'on_failure', 'prevenir')
    expect(def.steps[2]!.on_failure).toBe('prevenir')
    def = disconnect(def, 'petite', 'if_false')
    expect('if_false' in def.steps[1]!).toBe(false)
    def = connect(def, TRIGGER_ID, 'start', 'petite')
    expect(def.entry_step).toBe('petite')
    expect(firstStep(def)).toBe('petite')
    expect(connect(FLOW, 'lire', 'on_success', 'lire')).toBe(FLOW) // pas de boucle immédiate
    expect(connect(FLOW, 'lire', 'on_success', 'inconnu')).toBe(FLOW)
  })

  it('aiguillage, parallèle, boucle : leurs sorties sont des cases, des branches, un corps', () => {
    let def = addStep(FLOW, blankStep('switch', 'switch')).def
    def = setCase(setCase(def, 'switch', null, 'urgent'), 'switch', null, 'normal')
    def = connect(def, 'switch', 'case:urgent', 'approuver')
    def = setCase(def, 'switch', 'urgent', 'tres_urgent')
    expect(targetOf(def.steps.at(-1)!, 'case:tres_urgent')).toBe('approuver') // renommer une case garde sa cible
    expect(portsOf(def.steps.at(-1)!).map((p) => p.id)).toEqual(['case:tres_urgent', 'case:normal', 'default'])

    def = addStep(def, blankStep('parallel', 'parallel')).def
    def = connect(connect(def, 'parallel', 'branch:0', 'lire'), 'parallel', 'branch:1', 'prevenir')
    expect(def.steps.at(-1)!.branches).toEqual([['lire'], ['prevenir']])
    expect(portsOf(def.steps.at(-1)!).filter((p) => p.id.startsWith('branch:')).map((p) => p.label)).toEqual(['branche 1', 'branche 2', '+ branche'])
    def = disconnect(def, 'parallel', 'branch:0')
    expect(def.steps.at(-1)!.branches).toEqual([['prevenir']])

    def = addStep(def, blankStep('foreach', 'foreach')).def
    def = connect(def, 'foreach', 'body', 'approuver')
    expect(def.steps.at(-1)!.steps).toEqual(['approuver']) // xflow lance le corps depuis steps[0]
  })

  it('retirer un step vide les sorties qui le visaient et le premier step', () => {
    const def = removeStep({ ...FLOW, entry_step: 'lire' }, 'lire')
    expect(def.steps.map((s) => s.id)).toEqual(['petite', 'approuver', 'prevenir'])
    expect(def.entry_step).toBeNull()
    const cut = removeStep(FLOW, 'approuver')
    expect('if_true' in cut.steps[1]!).toBe(false)
  })

  it('renommer un step le renomme PARTOUT, gabarits compris ; un nom pris ou invalide est refusé', () => {
    const def = renameStep({ ...FLOW, entry_step: 'lire' }, 'lire', 'demande')!
    expect(def.entry_step).toBe('demande')
    expect(def.steps[0]!.id).toBe('demande')
    expect((def.steps[1]!.condition as { left: string }).left).toBe('{{ steps.demande.result.doc.data.jours }}')
    expect(def.steps[2]!.payload).toMatchObject({ name: '{{ steps.demande.result.doc.name }}', version: '{{ steps.demande.result.doc.version }}' })
    expect(renameStep(FLOW, 'lire', 'petite')).toBeNull()
    expect(renameStep(FLOW, 'lire', 'Lire!')).toBeNull()
    // `steps.lire_x` n'est pas `steps.lire` : seul le step renommé change
    const other = renameStep(patchPayload(FLOW, 'prevenir', { to: '{{ steps.lire_x.result }}' }), 'lire', 'demande')!
    expect(other.steps[3]!.payload).toEqual({ to: '{{ steps.lire_x.result }}' })
  })

  it('les nœuds Document sont des actions vers le moteur, avec le document de l’événement par défaut', () => {
    const step = docStep('doc.transition', 'Demande')
    expect(step).toMatchObject({ type: 'action', plugin: 'xcore-doctype', action: 'doc.transition', payload: { doctype: 'Demande', name: '{{ trigger.name }}', version: '{{ trigger.version }}' } })
    expect(docActionOf(FLOW.steps[0]!)).toBe('doc.get')
    expect(docActionOf(FLOW.steps[3]!)).toBeNull()
    expect(docActionOf({ id: 'x', type: 'action', plugin: 'xcore-doctype', action: 'doc.devtools.check' })).toBeNull()
    expect(docStep('doc.patch', 'Demande').payload).toEqual({ doctype: 'Demande', name: '{{ trigger.name }}', data: {} }) // jamais doc.save pour modifier
  })

  it('les événements de document se nomment et se relisent', () => {
    expect(docEventName('Stock Entry', 'submit')).toBe('doctype.Stock Entry.after_submit')
    expect(parseDocEvent('doctype.Stock Entry.after_submit')).toEqual({ doctype: 'Stock Entry', event: 'submit' })
    expect(parseDocEvent('user.registered')).toBeNull()
    expect(parseDocEvent('doctype.X.after_print')).toBeNull()
    expect(triggerFields(FLOW.trigger)).toEqual(['doctype', 'name', 'tenant_id', 'user', 'version', 'action', 'from', 'to', 'docstatus'])
    expect(triggerFields({ type: 'manual' })).toEqual([])
  })

  it('les variables : le déclencheur, puis les champs du document qu’un step a lu', () => {
    const vars = variablesFor(FLOW, 'approuver', (dt) => (dt === 'Demande' ? ['objet', 'jours'] : null))
    expect(vars.map((v) => v.token)).toEqual(expect.arrayContaining(['{{ trigger.name }}', '{{ steps.lire.result.doc.data.jours }}', '{{ steps.lire.result.doc.version }}', '{{ steps.prevenir.result }}']))
    expect(vars.some((v) => v.group === 'approuver')).toBe(false) // jamais son propre résultat
  })

  it('les positions : celles de la définition, sinon une rangée par distance au déclencheur (de haut en bas) ; « Réorganiser » les oublie', () => {
    const at = positions(FLOW)
    expect([at[TRIGGER_ID]!.y, at.lire!.y, at.petite!.y, at.approuver!.y]).toEqual([0, 170, 340, 510])
    expect(at.approuver!.y).toBe(at.prevenir!.y) // les deux branches d'une condition, côte à côte
    expect(at.approuver!.x).not.toBe(at.prevenir!.x)
    const moved = moveNode(FLOW, 'lire', 12.4, 30.6)
    expect(positions(moved).lire).toEqual({ x: 12, y: 31 })
    expect(positions(arrange(moved)).lire).toEqual(at.lire)
    const lost = addStep(FLOW, blankStep('wait', 'wait')).def // injoignable : la dernière colonne, pour qu'on le voie
    expect(positions(lost).wait!.y).toBe(680)
  })

  it('ce qui empêche d’enregistrer est dit avant le serveur', () => {
    expect(problems(FLOW)).toEqual([])
    const bad: FlowDefinition = {
      ...FLOW, name: 'Mon flux',
      trigger: { type: 'event' },
      steps: [...FLOW.steps, { id: 'orphelin', type: 'action', plugin: 'xcore-doctype', action: 'doc.transition', payload: { doctype: '' } }],
    }
    expect(problems(bad)).toEqual([
      { code: 'name' }, { code: 'event' },
      { code: 'missing', step: 'orphelin', field: 'doctype' }, { code: 'missing', step: 'orphelin', field: 'transition' },
      { code: 'unreachable', step: 'orphelin' },
    ])
    expect(problems(emptyFlow('x'))).toContainEqual({ code: 'no_steps' })
    expect(problems({ ...emptyFlow('x'), trigger: { type: 'schedule' }, steps: [blankStep('wait', 'w')] })).toEqual([{ code: 'schedule' }])
  })

  it('une carte ajoutée ne tombe jamais sur une autre : elle se décale d’une colonne', () => {
    const at = positions(FLOW)
    expect(freeSpot(FLOW, at.lire!)).toEqual({ x: at.lire!.x + 260, y: at.lire!.y })
    expect(freeSpot(FLOW, { x: 0, y: 2000 })).toEqual({ x: 0, y: 2000 })
  })

  it('un identifiant se tire d’un libellé ; qui a déployé n’est jamais renvoyé par le client', () => {
    expect(uniqueId('Faire avancer', [])).toBe('faire_avancer')
    expect(uniqueId('lire', ['lire', 'lire_2'])).toBe('lire_3')
    expect(uniqueId('2e étape', [])).toBe('e_2e_etape')
    expect('deployed_by' in forDeploy({ ...FLOW, deployed_by: 'alice' })).toBe(false)
  })
})

describe('les valeurs tapées dans un nœud Document', () => {
  it('prennent le type du champ ; un gabarit entier reste un gabarit (xflow le remplace par la valeur typée)', async () => {
    const { typedValue, shownValue } = await import('@/lib/automation')
    expect(typedValue('Int', ' 12 ')).toBe(12)
    expect(typedValue('Number', '2.5')).toBe(2.5)
    expect(typedValue('Boolean', 'true')).toBe(true)
    expect(typedValue('Decimal', '12.50')).toBe('12.50')
    expect(typedValue('Int', '{{ steps.lire.result.doc.data.jours }}')).toBe('{{ steps.lire.result.doc.data.jours }}')
    expect(typedValue('Int', 'deux')).toBe('deux') // le moteur dira pourquoi
    expect(typedValue('Text', 'Bonjour {{ trigger.name }}')).toBe('Bonjour {{ trigger.name }}')
    expect([shownValue(12), shownValue(true), shownValue('x'), shownValue(undefined)]).toEqual(['12', 'true', 'x', ''])
  })
})
