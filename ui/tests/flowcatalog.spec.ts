import { describe, expect, it } from 'vitest'
import { eventOf, fieldtypeOf, missingFields, nodeOf, nodeStep, outputSources, readCatalog } from '@/lib/flowcatalog'
import { readValue, writeValue } from '@/lib/flowvalues'

// Ce que sert `GET /flow-catalog` pour le plugin de test `mailer` (tests/support/plugins/mailer/plugin.yaml).
const BODY = {
  nodes: [
    {
      plugin: 'mailer',
      action: 'mail.send',
      label: 'Envoyer un e-mail',
      icon: 'envelope',
      fields: [
        { name: 'to', label: 'Destinataire', type: 'email', required: true },
        { name: 'subject', label: 'Objet', type: 'text', required: true },
        { name: 'body', label: 'Message', type: 'longtext' },
      ],
      outputs: [{ path: 'message_id', label: 'Identifiant du message' }],
    },
    { plugin: 'mailer', label: 'sans action' },
    'pas un objet',
  ],
  events: [
    { plugin: 'mailer', name: 'mailer.received', label: 'E-mail reçu', fields: [{ name: 'sender', label: 'Expéditeur' }, { label: 'sans nom' }] },
    { name: '', label: 'vide' },
  ],
}

describe('le catalogue des nœuds et événements de plugin', () => {
  const catalog = readCatalog(BODY)

  it('garde ce qui a la forme attendue et ignore le reste, sans casser l’écran', () => {
    expect(catalog.nodes.map((n) => n.action)).toEqual(['mail.send'])
    expect(catalog.events.map((e) => e.name)).toEqual(['mailer.received'])
    expect(catalog.events[0]!.fields).toEqual([{ name: 'sender', label: 'Expéditeur' }])
    expect(readCatalog(null)).toEqual({ nodes: [], events: [] })
    expect(readCatalog({ nodes: 'x', events: 3 })).toEqual({ nodes: [], events: [] })
  })

  it('reconnaît l’étape qui appelle un nœud, et pose l’appel avec une charge vide', () => {
    const node = catalog.nodes[0]!
    expect(nodeStep(node)).toEqual({ plugin: 'mailer', action: 'mail.send', payload: {} })
    expect(nodeOf(catalog, { plugin: 'mailer', action: 'mail.send' })).toBe(node)
    expect(nodeOf(catalog, { plugin: 'xcore-doctype', action: 'doc.save' })).toBeUndefined()
    expect(eventOf(catalog, 'mailer.received')?.label).toBe('E-mail reçu')
    expect(eventOf(catalog, 'doctype.Customer.after_insert')).toBeUndefined()
  })

  it('nomme les champs obligatoires laissés vides', () => {
    const node = catalog.nodes[0]!
    expect(missingFields(node, { payload: {} })).toEqual(['Destinataire', 'Objet'])
    expect(missingFields(node, { payload: { to: '{{ trigger.sender }}', subject: '' } })).toEqual(['Objet'])
    expect(missingFields(node, { payload: { to: 'a@b.c', subject: 'x' } })).toEqual([])
  })

  it('propose aux étapes suivantes ce que les nœuds AVANT elles rendent', () => {
    const before = [
      { id: 'lire', raw: { plugin: 'xcore-doctype', action: 'doc.get' } },
      { id: 'envoyer', raw: { plugin: 'mailer', action: 'mail.send' } },
    ]
    expect(outputSources(catalog, before)).toEqual([{ step: 'envoyer', path: 'message_id', label: 'Identifiant du message' }])
    expect(outputSources(catalog, [])).toEqual([])
  })

  it('donne à chaque type de champ la saisie du moteur qui lui va', () => {
    expect(fieldtypeOf('number')).toBe('Number')
    expect(fieldtypeOf('boolean')).toBe('Boolean')
    expect(fieldtypeOf('date')).toBe('Date')
    expect(fieldtypeOf('email')).toBeUndefined()
  })
})

describe('les valeurs : une clé d’événement de plugin et le résultat d’une étape', () => {
  it('lit et écrit la clé d’un événement de plugin comme celles du moteur', () => {
    expect(readValue('{{ trigger.sender }}')).toEqual({ kind: 'trigger', key: 'sender' })
    expect(writeValue({ kind: 'trigger', key: 'sender' })).toBe('{{ trigger.sender }}')
  })

  it('lit et écrit le résultat d’un nœud de plugin, que xflow résout tel quel', () => {
    expect(writeValue({ kind: 'output', step: 'envoyer', path: 'message_id' })).toBe('{{ steps.envoyer.result.message_id }}')
    expect(readValue('{{ steps.envoyer.result.message_id }}')).toEqual({ kind: 'output', step: 'envoyer', path: 'message_id' })
    expect(readValue('{{steps.envoyer.result.a.b}}')).toEqual({ kind: 'output', step: 'envoyer', path: 'a.b' })
    // Le document d'une étape du moteur garde sa propre lecture.
    expect(readValue('{{ steps.creer.result.doc.name }}')).toEqual({ kind: 'step', step: 'creer' })
  })
})
