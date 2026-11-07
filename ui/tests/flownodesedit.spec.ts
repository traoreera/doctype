import { describe, expect, it } from 'vitest'
import { t } from '@/i18n'
import { addField, emptyNode, fieldName, nodeProblems, patchField, toDraft, toPayload } from '@/lib/flownodesedit'

const P = t.flowNodes.problems

describe('décrire un nœud à l’écran', () => {
  it('tire la clé d’un champ de son libellé, sans accent ni espace', () => {
    expect(fieldName('Date d’échéance')).toBe('date_d_echeance')
    expect(fieldName('  Numéro de téléphone ')).toBe('numero_de_telephone')
    expect(fieldName('2e adresse')).toBe('champ_2e_adresse')
    expect(fieldName('???')).toBe('')
  })

  it('ajoute des champs aux clés distinctes', () => {
    let node = addField(emptyNode())
    node = addField(node)
    expect(node.fields.map((f) => f.name)).toEqual(['champ', 'champ_2'])
  })

  it('dit les fautes avant le serveur, une par phrase', () => {
    expect(nodeProblems(emptyNode(), P)).toEqual([P.plugin, P.action, P.label])
    let node = { ...emptyNode(), plugin: 'mailer', action: 'mail.log', label: 'Noter' }
    expect(nodeProblems(node, P)).toEqual([])
    node = addField(node, 'Canal')
    node = patchField(node, 0, { type: 'select', options: ['', ' '] })
    node = addField(node, 'Canal')
    node = patchField(node, 1, { name: 'canal' })
    node = { ...node, outputs: [{ path: 'a..b', label: '' }] }
    expect(nodeProblems(node, P)).toEqual([P.options('Canal'), P.duplicate('canal'), P.outputPath(1), P.outputLabel(1)])
  })

  it('n’envoie que ce qui compte : options d’une liste seulement, rien de vide', () => {
    let node = { ...emptyNode(), plugin: ' mailer ', action: 'mail.log', label: 'Noter ', description: '  ' }
    node = addField(node, 'Note')
    node = patchField(node, 0, { required: true, options: ['ignorée'] })
    node = addField(node, 'Canal')
    node = patchField(node, 1, { type: 'select', options: ['SMS', ' ', 'E-mail '] })
    expect(toPayload(node)).toEqual({
      plugin: 'mailer', action: 'mail.log', label: 'Noter', icon: 'bolt',
      fields: [
        { name: 'note', label: 'Note', type: 'text', required: true },
        { name: 'canal', label: 'Canal', type: 'select', required: false, options: ['SMS', 'E-mail'] },
      ],
      outputs: [],
    })
  })

  it('rouvre un nœud enregistré tel quel', () => {
    const stored = { plugin: 'mailer', action: 'mail.log', label: 'Noter', icon: 'book', fields: [{ name: 'note', label: 'Note', type: 'text' as const }], outputs: [] }
    const draft = toDraft(stored)
    expect(draft.description).toBe('')
    expect(toPayload(draft)).toEqual({ ...stored, fields: [{ name: 'note', label: 'Note', type: 'text', required: false }] })
  })
})
