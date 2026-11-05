import { describe, expect, it } from 'vitest'
import {
  ACTIONS,
  DEFAULT_SCHEDULE,
  ENGINE,
  actionOf,
  readCron,
  readEvent,
  withAction,
  writeCron,
  writeEvent,
  type ActionId,
} from '@/lib/flowactions'
import { fixedValue, readValue, writeValue } from '@/lib/flowvalues'

/**
 * Ce qui permet d'automatiser SANS écrire de code : un geste métier ↔ un appel du moteur, une valeur choisie ↔ un jeton `{{ … }}`,
 * une planification dite en français ↔ une expression cron.
 *
 * Ce qui compte : les charges utiles sont exactement celles que `plugin.py` attend (un `doc.save` qui modifie exige la version),
 * changer de geste ne laisse jamais traîner une clé du geste précédent, et ce que l'écran ne sait pas dire (une action d'un autre
 * plugin, un cron savant) est RECONNU comme tel au lieu d'être réécrit de travers.
 */

describe('les gestes : ce que le flux fait, dit sans jargon', () => {
  it('reconnaît un geste dans un appel du moteur, et une action étrangère comme telle', () => {
    const call = (action: string, payload: Record<string, unknown> = {}) => ({ id: 'x', plugin: ENGINE, action, payload })
    expect(actionOf(call('doc.save', { doctype: 'Customer', data: {} }))).toBe('create')
    expect(actionOf(call('doc.save', { doctype: 'Customer', name: 'C-1', data: {} }))).toBe('update')
    expect(actionOf(call('doc.submit'))).toBe('submit')
    expect(actionOf(call('doc.assign'))).toBe('assign')
    expect(actionOf(call('doc.comment.add'))).toBe('comment')
    expect(actionOf(call('doc.get'))).toBe('read')
    expect(actionOf({ id: 'x', plugin: 'mail', action: 'send', payload: {} })).toBeNull() // un autre plugin : montré, pas réglé
    expect(actionOf(call('doc.import'))).toBeNull() // une action du moteur hors catalogue
  })

  it('chaque geste vise par défaut le document déclencheur : rien à saisir pour le cas courant', () => {
    const made = withAction({ id: 'x', type: 'action' }, 'submit')
    expect(made).toMatchObject({
      plugin: ENGINE,
      action: 'doc.submit',
      payload: { doctype: '{{ trigger.doctype }}', name: '{{ trigger.name }}', version: '{{ trigger.version }}' },
    })
  })

  it('une création n’a ni cible ni version, une modification les a (le moteur exige la version, D15)', () => {
    expect(withAction({ id: 'x' }, 'create').payload).toEqual({ doctype: '', data: {} })
    expect(withAction({ id: 'x' }, 'update').payload).toMatchObject({ name: '{{ trigger.name }}', version: '{{ trigger.version }}', data: {} })
    // Supprimer, assigner, commenter et lire n'ont pas de version à donner.
    for (const id of ['delete', 'assign', 'comment', 'read'] as ActionId[]) {
      expect(withAction({ id: 'x' }, id).payload).not.toHaveProperty('version')
    }
  })

  it('changer de geste garde ce qui a du sens et jette le reste : xflow ne reçoit jamais une clé du geste précédent', () => {
    const before = { id: 'x', plugin: ENGINE, action: 'doc.save', payload: { doctype: 'Customer', name: 'C-1', version: 3, data: { nom: 'Alice' } }, retry: { max_attempts: 2 } }
    const after = withAction(before, 'comment')
    expect(after.payload).toEqual({ doctype: 'Customer', name: 'C-1', content: '' })
    expect(after).toMatchObject({ retry: { max_attempts: 2 } }) // ce qui n'est pas la charge utile n'est pas touché
    // …et revenir en arrière retrouve le type et la cible.
    expect(withAction(after, 'update').payload).toMatchObject({ doctype: 'Customer', name: 'C-1', data: {} })
  })

  it('le catalogue ne nomme que des actions du moteur, chacune une fois', () => {
    const calls = ACTIONS.map((a) => `${a.id}:${a.action}`)
    expect(new Set(calls).size).toBe(ACTIONS.length)
    expect(ACTIONS.every((a) => a.action.startsWith('doc.'))).toBe(true)
  })
})

describe('les valeurs : une liste, jamais des accolades à taper', () => {
  it('va du choix au jeton et du jeton au choix', () => {
    expect(writeValue({ kind: 'trigger', key: 'name' })).toBe('{{ trigger.name }}')
    expect(writeValue({ kind: 'step', step: 'creer' })).toBe('{{ steps.creer.result.doc.name }}')
    expect(readValue('{{ trigger.version }}')).toEqual({ kind: 'trigger', key: 'version' })
    expect(readValue('{{ steps.creer.result.doc.name }}')).toEqual({ kind: 'step', step: 'creer' })
    expect(readValue('Bonjour')).toEqual({ kind: 'fixed', value: 'Bonjour' })
    expect(readValue(12)).toEqual({ kind: 'fixed', value: 12 })
  })

  it('une expression écrite ailleurs est reconnue comme telle, et rendue intacte', () => {
    const odd = '{{ steps.lire.result.doc.data.total | round }}'
    expect(readValue(odd)).toEqual({ kind: 'raw', text: odd })
    expect(writeValue(readValue(odd))).toBe(odd)
    expect(readValue('{{ trigger.inconnu }}').kind).toBe('raw') // une clé que le déclencheur ne porte pas
  })

  it('une valeur fixe garde son type : le moteur refuse « 12 » pour un nombre', () => {
    expect(fixedValue('12', 'Number')).toBe(12)
    expect(fixedValue('12', 'Text')).toBe('12')
    expect(fixedValue('12.50', 'Currency')).toBe('12.50') // un montant exact reste une chaîne canonique
    expect(fixedValue('oui', 'Boolean')).toBe(true)
    expect(fixedValue('', 'Boolean')).toBe(false)
  })
})

describe('le déclencheur : deux listes au lieu d’un nom d’événement, une fréquence au lieu d’un cron', () => {
  it('compose et relit un événement de document', () => {
    expect(writeEvent('Sales Invoice', 'after_submit')).toBe('doctype.Sales Invoice.after_submit')
    expect(readEvent('doctype.Sales Invoice.after_submit')).toEqual({ doctype: 'Sales Invoice', moment: 'after_submit' })
    expect(readEvent('xauth.login')).toBeNull() // un événement d'un autre plugin : gardé, pas réécrit
    expect(readEvent('doctype.Customer.after_lune')).toBeNull()
  })

  it('compose un cron depuis « tous les jours à 9 h »', () => {
    expect(writeCron({ ...DEFAULT_SCHEDULE, every: 'day', hour: 9, minute: 0 })).toBe('0 9 * * *')
    expect(writeCron({ ...DEFAULT_SCHEDULE, every: 'week', hour: 8, minute: 30, weekday: 1 })).toBe('30 8 * * 1')
    expect(writeCron({ ...DEFAULT_SCHEDULE, every: 'month', hour: 7, minute: 5, day: 15 })).toBe('5 7 15 * *')
    // Un jour au-delà du 28 sauterait les mois courts : on le ramène.
    expect(writeCron({ ...DEFAULT_SCHEDULE, every: 'month', day: 31 })).toBe('0 9 28 * *')
  })

  it('relit un cron simple, et laisse les autres tels quels', () => {
    expect(readCron('0 9 * * *')).toMatchObject({ every: 'day', hour: 9, minute: 0 })
    expect(readCron('30 8 * * 1')).toMatchObject({ every: 'week', weekday: 1, hour: 8, minute: 30 })
    expect(readCron('5 7 15 * *')).toMatchObject({ every: 'month', day: 15 })
    expect(readCron('0 9 * * 1-5')).toBeNull() // une plage : plus que ce que les listes disent
    expect(readCron('*/5 * * * *')).toBeNull()
    expect(readCron('0 9 * *')).toBeNull()
  })
})
