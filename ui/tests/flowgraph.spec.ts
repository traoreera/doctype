import { describe, expect, it } from 'vitest'
import {
  addBranch,
  addCase,
  addStep,
  connect,
  disconnect,
  edgeParts,
  flowProblems,
  forgetPositions,
  freeId,
  graphOf,
  kindOf,
  moveNode,
  outletsOf,
  patchStep,
  ranks,
  readFlow,
  removeBranch,
  removeCase,
  removeStep,
  renameCase,
  renameStep,
  stepSummary,
  TRIGGER_NODE,
  writeFlow,
} from '@/lib/flowgraph'

/**
 * Le modèle de l'éditeur d'automatisation : une définition xflow ↔ un graphe. Tout est pur, donc tout se vérifie ici — la page ne
 * fait que donner ces nœuds et ces flèches à Vue Flow.
 *
 * Ce qui compte : rien ne se perd au passage (une définition écrite à la main se rouvre telle quelle), les flèches sont les clés
 * qu'xflow exécute vraiment (`on_success`, `if_true`, `cases`, `branches`, le corps d'un foreach), et une étape retirée ou renommée
 * ne laisse jamais une flèche vers le vide.
 */

/** Un flux de relance réel : une action, une condition, et une branche d'échec. */
const RELANCE = {
  name: 'relance_facture',
  version: '1.0.0',
  description: 'Relance une facture impayée',
  trigger: { type: 'event', event_name: 'doctype.Sales Invoice.after_submit' },
  steps: [
    {
      id: 'lire', type: 'action', plugin: 'xcore-doctype', action: 'doc.get',
      payload: { doctype: 'Sales Invoice', name: '{{ trigger.name }}' },
      on_success: 'reste_du', on_failure: 'alerter', retry: { max_attempts: 5 },
    },
    { id: 'reste_du', type: 'condition', condition: { left: '{{ steps.lire.result.doc.data.outstanding }}', operator: '>', right: '0' }, if_true: 'alerter' },
    { id: 'alerter', type: 'action', plugin: 'mail', action: 'send', payload: { to: '{{ trigger.name }}' } },
  ],
  tags: ['facturation'],
  webhooks: [{ url: 'https://example.test/hook' }],
}

describe('lire et réécrire une définition', () => {
  it('garde tout ce que l’éditeur ne montre pas : le flux revient identique', () => {
    const model = readFlow(RELANCE)
    expect(model.name).toBe('relance_facture')
    expect(model.trigger).toMatchObject({ kind: 'event', event: 'doctype.Sales Invoice.after_submit', cron: '' })
    expect(model.steps.map((s) => [s.id, s.kind])).toEqual([['lire', 'action'], ['reste_du', 'condition'], ['alerter', 'action']])
    const made = writeFlow(model)
    expect(made.problems).toEqual([])
    // Le retry d'une étape, les tags et les webhooks du flux : jamais montrés, jamais perdus.
    expect(made.definition).toMatchObject({ tags: ['facturation'], webhooks: [{ url: 'https://example.test/hook' }] })
    expect((made.definition!.steps as Record<string, unknown>[])[0]).toMatchObject({ retry: { max_attempts: 5 } })
    expect(made.definition).toEqual(RELANCE)
  })

  it('une étape sans type est une action, un type qu’xflow ajouterait n’est pas reconnu mais reste intact', () => {
    expect(kindOf({ id: 'x' })).toBe('action')
    expect(kindOf({ id: 'x', type: 'ai' })).toBe('ai')
    expect(kindOf({ id: 'x', type: 'quantique' })).toBeNull()
    const model = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'bizarre', type: 'quantique', truc: 1 }] })
    expect(outletsOf(model.steps[0]!)).toEqual([]) // on ne devine pas les sorties d'un type inconnu
    expect(writeFlow(model).definition).toMatchObject({ steps: [{ id: 'bizarre', type: 'quantique', truc: 1 }] })
  })

  it('ne refuse jamais de s’ouvrir : une définition vide, nulle, ou aux étapes sans identifiant', () => {
    expect(readFlow(null).steps).toEqual([])
    expect(readFlow({ steps: 'x' }).steps).toEqual([])
    expect(readFlow({ steps: [{ type: 'action' }, { id: 'a.b' }, { id: 'ok' }] }).steps.map((s) => s.id)).toEqual(['ok'])
    expect(readFlow({ trigger: { type: 'inconnu' } }).trigger.kind).toBe('manual')
  })

  it('n’écrit `entry_step` que s’il n’est pas la première étape', () => {
    const model = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a', plugin: 'p', action: 'x' }, { id: 'b', plugin: 'p', action: 'y' }] })
    expect(writeFlow({ ...model, entry: 'a' }).definition).not.toHaveProperty('entry_step')
    expect(writeFlow({ ...model, entry: 'b' }).definition).toMatchObject({ entry_step: 'b' })
  })
})

describe('les fautes dites avant d’appeler xflow', () => {
  const base = readFlow({ name: 'bon_nom', trigger: { type: 'manual' }, steps: [{ id: 'a', plugin: 'p', action: 'act' }] })

  it('le nom, le déclencheur, les étapes', () => {
    expect(flowProblems({ ...base, name: 'deux mots' })[0]).toContain('Nom')
    expect(flowProblems({ ...base, steps: [] })).toContain('Ajoutez au moins une étape.')
    expect(flowProblems({ ...base, trigger: { ...base.trigger, kind: 'event', event: '' } })[0]).toContain('événement')
    expect(flowProblems({ ...base, trigger: { ...base.trigger, kind: 'schedule', cron: '0 9 * *' } })[0]).toContain('cron')
    expect(flowProblems({ ...base, trigger: { ...base.trigger, kind: 'schedule', cron: '0 9 * * 1-5' } })).toEqual([])
    expect(flowProblems(base)).toEqual([])
  })

  it('ce que chaque type d’étape exige', () => {
    const one = (step: Record<string, unknown>) => flowProblems(readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a', ...step }] }))
    expect(one({ type: 'action', plugin: '', action: '' }).length).toBe(2)
    // Un geste du catalogue : c'est ce qu'il DEMANDE qui doit être rempli, pas un nom d'action.
    expect(one({ type: 'action', plugin: 'xcore-doctype', action: 'doc.save', payload: {} })[0]).toContain('type de document')
    expect(one({ type: 'action', plugin: 'xcore-doctype', action: 'doc.submit', payload: { doctype: 'X' } })[0]).toContain('quel document')
    expect(one({ type: 'action', plugin: 'xcore-doctype', action: 'doc.submit', payload: { doctype: 'X', name: 'X-1' } })).toEqual([])
    expect(one({ type: 'condition', condition: { left: '' } })[0]).toContain('valeur à comparer')
    expect(one({ type: 'switch', expression: '' })[0]).toContain('expression')
    expect(one({ type: 'foreach', items: '' })[0]).toContain('liste')
    expect(one({ type: 'parallel', branches: [[], []] })[0]).toContain('branche')
    expect(one({ type: 'wait', delay_seconds: 60 })).toEqual([]) // un délai suffit
    expect(one({ type: 'transform', query: 'x' })).toEqual([])
  })

  it('une flèche vers une étape disparue, et un départ qui n’existe pas (la vue JSON peut en laisser)', () => {
    const broken = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a', plugin: 'p', action: 'x', on_success: 'parti' }] })
    expect(flowProblems(broken)[0]).toContain('« parti »')
    expect(flowProblems({ ...base, entry: 'parti' })[0]).toContain('« parti »')
  })

  it('deux étapes du même nom', () => {
    const twice = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a', plugin: 'p', action: 'x' }, { id: 'a', plugin: 'p', action: 'y' }] })
    expect(flowProblems(twice).join(' ')).toContain('« a »')
  })
})

describe('les sorties d’une étape : ce qu’xflow exécute vraiment', () => {
  const step = (raw: Record<string, unknown>) => readFlow({ steps: [{ id: 'x', ...raw }] }).steps[0]!

  it('une par type, dans l’ordre où le nœud les dessine', () => {
    expect(outletsOf(step({ type: 'action', on_success: 'b' })).map((o) => [o.key, o.tone, o.target])).toEqual([
      ['on_success', 'ok', 'b'],
      ['on_failure', 'fail', null],
    ])
    expect(outletsOf(step({ type: 'condition', if_true: 'b', if_false: 'c' })).map((o) => [o.key, o.target])).toEqual([['if_true', 'b'], ['if_false', 'c']])
    expect(outletsOf(step({ type: 'switch', cases: { pro: 'b', starter: 'c' }, default: 'd' })).map((o) => [o.key, o.text, o.target])).toEqual([
      ['cases.pro', 'pro', 'b'],
      ['cases.starter', 'starter', 'c'],
      ['default', null, 'd'],
    ])
    // Le moteur ne part que du PREMIER identifiant d'une branche (`_run_parallel`) : c'est lui que la flèche montre.
    expect(outletsOf(step({ type: 'parallel', branches: [['b', 'suite'], []] })).map((o) => [o.key, o.target])).toEqual([
      ['branches.0', 'b'],
      ['branches.1', null],
      ['on_success', null],
      ['on_failure', null],
    ])
    expect(outletsOf(step({ type: 'foreach', steps: ['corps'] })).map((o) => [o.key, o.target])).toEqual([
      ['body', 'corps'],
      ['on_success', null],
      ['on_failure', null],
    ])
    expect(outletsOf(step({ type: 'template' })).map((o) => o.key)).toEqual(['on_success'])
  })

  it('relier écrit la clé de la définition, détacher la retire', () => {
    const model = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a' }, { id: 'b', type: 'switch', cases: { pro: '' } }, { id: 'c', type: 'foreach', steps: [] }] })
    const linked = connect(connect(connect(model, 'a', 'on_success', 'b'), 'b', 'cases.pro', 'c'), 'c', 'body', 'a')
    expect(linked.steps.map((s) => s.raw)).toMatchObject([{ on_success: 'b' }, { cases: { pro: 'c' } }, { steps: ['a'] }])
    const cut = disconnect(disconnect(linked, 'a', 'on_success'), 'b', 'cases.pro')
    expect(cut.steps[0]!.raw).not.toHaveProperty('on_success')
    expect(cut.steps[1]!.raw).toMatchObject({ cases: {} })
  })

  it('relier la sortie du déclencheur change l’étape de départ ; une étape ne s’enchaîne pas à elle-même', () => {
    const model = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a' }, { id: 'b' }] })
    expect(connect(model, TRIGGER_NODE, 'start', 'b').entry).toBe('b')
    expect(connect(model, 'a', 'on_success', 'a')).toBe(model)
  })
})

describe('poser, retirer et renommer une étape', () => {
  const two = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a', on_success: 'b' }, { id: 'b' }] })

  it('une étape posée est RELIÉE à la première sortie libre : sans flèche, xflow ne l’exécuterait jamais', () => {
    const { model, id } = addStep(two, 'action', { x: 10, y: 20 }, 'b')
    expect(id).toBe('action_1')
    expect(model.steps.find((s) => s.id === 'b')!.raw).toMatchObject({ on_success: 'action_1' })
    expect(model.steps.find((s) => s.id === id)!.raw).toMatchObject({ _x: 10, _y: 20, plugin: 'xcore-doctype' })
    // La première étape d'un flux vide devient son départ.
    const fresh = addStep(readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [] }), 'condition')
    expect([fresh.id, fresh.model.entry]).toEqual(['condition_1', 'condition_1'])
  })

  it('un identifiant libre ne prend jamais celui d’une autre', () => {
    const taken = readFlow({ steps: [{ id: 'action_1' }, { id: 'action_2' }] })
    expect(freeId(taken, 'action')).toBe('action_3')
  })

  it('retirer une étape retire aussi toute flèche qui la visait, et déplace le départ', () => {
    const left = removeStep(two, 'b')
    expect(left.steps.map((s) => s.id)).toEqual(['a'])
    expect(left.steps[0]!.raw).not.toHaveProperty('on_success')
    expect(removeStep({ ...two, entry: 'a' }, 'a').entry).toBe('b')
  })

  it('renommer une étape renomme partout : les flèches, les cas, les branches, le départ', () => {
    const model = readFlow({
      name: 'f',
      trigger: { type: 'manual' },
      entry_step: 'cible',
      steps: [
        { id: 'cible' },
        { id: 'sw', type: 'switch', expression: 'x', cases: { pro: 'cible' }, default: 'cible' },
        { id: 'par', type: 'parallel', branches: [['cible']] },
        { id: 'fe', type: 'foreach', items: 'x', steps: ['cible'] },
        { id: 'act', on_success: 'cible', on_failure: 'cible' },
      ],
    })
    const renamed = renameStep(model, 'cible', 'arrivee')
    expect(JSON.stringify(writeFlow(renamed).definition)).not.toContain('cible')
    expect(renamed.entry).toBe('arrivee')
    expect(renamed.steps.map((s) => s.raw)).toMatchObject([
      { id: 'arrivee' },
      { cases: { pro: 'arrivee' }, default: 'arrivee' },
      { branches: [['arrivee']] },
      { steps: ['arrivee'] },
      { on_success: 'arrivee', on_failure: 'arrivee' },
    ])
  })

  it('le panneau ne change que ce qu’il montre ; une clé vidée disparaît de la définition', () => {
    const patched = patchStep(two, 'a', { description: 'Lire', on_success: undefined })
    expect(patched.steps[0]!.raw).toMatchObject({ description: 'Lire' })
    expect(patched.steps[0]!.raw).not.toHaveProperty('on_success')
  })
})

describe('les cas d’un aiguillage et les branches d’un parallèle', () => {
  const model = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'sw', type: 'switch', expression: 'x', cases: { pro: 'a' } }, { id: 'par', type: 'parallel', branches: [[]] }, { id: 'a' }] })

  it('nommer, renommer et retirer un cas, sans mélanger l’ordre ni perdre sa cible', () => {
    const more = addCase(addCase(model, 'sw', 'starter'), 'sw', 'pro') // un cas déjà nommé n'est pas doublé
    expect(Object.keys(more.steps[0]!.raw.cases as object)).toEqual(['pro', 'starter'])
    const renamed = renameCase(more, 'sw', 'pro', 'entreprise')
    expect(renamed.steps[0]!.raw.cases).toEqual({ entreprise: 'a', starter: '' })
    expect(renameCase(renamed, 'sw', 'entreprise', 'starter').steps[0]!.raw.cases).toEqual({ entreprise: 'a', starter: '' }) // un nom pris : rien
    expect(removeCase(renamed, 'sw', 'entreprise').steps[0]!.raw.cases).toEqual({ starter: '' })
  })

  it('ajouter et retirer une branche', () => {
    const more = addBranch(addBranch(model, 'par'), 'par')
    expect(outletsOf(more.steps[1]!).filter((o) => o.tone === 'branch')).toHaveLength(3)
    const linked = connect(more, 'par', 'branches.1', 'a')
    expect(removeBranch(linked, 'par', 1).steps[1]!.raw.branches).toEqual([[], []])
  })
})

describe('le graphe donné à Vue Flow', () => {
  const model = readFlow(RELANCE)

  it('un nœud par étape plus le déclencheur, une flèche par sortie remplie', () => {
    const { nodes, edges } = graphOf(model)
    expect(nodes.map((n) => n.id)).toEqual([TRIGGER_NODE, 'lire', 'reste_du', 'alerter'])
    expect(nodes[0]!.data).toMatchObject({ kind: 'event', detail: 'doctype.Sales Invoice.after_submit' })
    expect(edges.map((e) => [e.id, e.source, e.target, e.sourceHandle])).toEqual([
      [`${TRIGGER_NODE}::start`, TRIGGER_NODE, 'lire', 'start'],
      ['lire::on_success', 'lire', 'reste_du', 'on_success'],
      ['lire::on_failure', 'lire', 'alerter', 'on_failure'],
      ['reste_du::if_true', 'reste_du', 'alerter', 'if_true'],
    ])
    expect(edgeParts('lire::cases.pro')).toEqual({ source: 'lire', key: 'cases.pro' })
    expect(edgeParts('sans')).toBeNull()
  })

  it('place de gauche à droite par distance au départ ; ce qu’on n’atteint pas se range après', () => {
    const seul = readFlow({ name: 'f', trigger: { type: 'manual' }, steps: [{ id: 'a', on_success: 'b' }, { id: 'b' }, { id: 'perdue' }] })
    const column = ranks(seul)
    expect([column.get(TRIGGER_NODE), column.get('a'), column.get('b')]).toEqual([0, 1, 2])
    expect(column.get('perdue')).toBe(3)
    const node = graphOf(seul).nodes.find((n) => n.id === 'perdue')!
    expect(node.data).toMatchObject({ orphan: true }) // aucune flèche n'y arrive : le nœud le dit
    expect(graphOf(seul).nodes.find((n) => n.id === 'b')!.data).toMatchObject({ orphan: false })
  })

  it('une position gardée dans la définition gagne sur le placement calculé, et « Réorganiser » les oublie', () => {
    const moved = moveNode(moveNode(model, 'alerter', { x: 640.4, y: -12.6 }), TRIGGER_NODE, { x: 5, y: 5 })
    expect(moved.steps.find((s) => s.id === 'alerter')!.raw).toMatchObject({ _x: 640, _y: -13 })
    expect(graphOf(moved).nodes.find((n) => n.id === 'alerter')!.position).toEqual({ x: 640, y: -13 })
    // Les positions voyagent avec le flux : xflow laisse passer les clés qu'il ne connaît pas.
    expect(JSON.stringify(writeFlow(moved).definition)).toContain('_x')
    const flat = forgetPositions(moved)
    expect(JSON.stringify(writeFlow(flat).definition)).not.toContain('_x')
    // `alerter` est à deux flèches du départ (l'échec de `lire`), donc dans la troisième colonne du dessin calculé.
    expect(graphOf(flat).nodes.find((n) => n.id === 'alerter')!.position.x).toBe(600)
  })

  it('le nœud dit en français ce que l’étape fait : un geste, jamais un nom d’action ni une accolade', () => {
    const summaries = model.steps.map(stepSummary)
    expect(summaries[0]).toBe('Lire un document · Sales Invoice') // et non « xcore-doctype.doc.get »
    expect(summaries[1]).toBe('lire › outstanding est plus grand que 0') // et non « {{ steps.lire.result.doc.data.outstanding }} > 0 »
    expect(summaries[2]).toBe('mail.send') // une action étrangère : on ne sait pas la nommer autrement
    const step = (raw: Record<string, unknown>) => readFlow({ steps: [{ id: 'x', ...raw }] }).steps[0]!
    expect(stepSummary(step({ type: 'wait', delay_seconds: 90 }))).toBe('90 s')
    expect(stepSummary(step({ type: 'parallel', branches: [[], []] }))).toBe('2 branches')
    expect(stepSummary(step({ type: 'condition', condition: { left: 'x', operator: 'is_null' } }))).toBe('x est vide')
    // Un geste sur le document déclencheur se lit sans qu'aucun jeton ne paraisse.
    expect(stepSummary(step({ type: 'action', plugin: 'xcore-doctype', action: 'doc.submit', payload: { doctype: '{{ trigger.doctype }}' } }))).toBe(
      'Soumettre un document · Du déclencheur',
    )
  })
})
