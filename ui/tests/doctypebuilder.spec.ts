import { describe, expect, it } from 'vitest'
import {
  addField, assemble, boardOf, typedDefault, emptyDefinition, fieldnameOf, optionsKind, patchField, removeField, renameField, retypeField, uniqueName, yamlFileName,
  type DevDefinition,
} from '@/lib/doctypebuilder'
import { moveField, placed, unplace } from '@/lib/formbuilder'

const DEF: DevDefinition = {
  schema_version: 1, name: 'Stock Entry', label: 'Mouvement', module: 'stock', title_field: 'ref', naming: { field: 'ref' },
  hooks: { validate: ['stock.check'] }, // ce que le constructeur n'édite pas voyage tel quel
  fields: [
    { fieldname: 'ref', fieldtype: 'Text', label: 'Référence', required: true },
    { fieldname: 'qty', fieldtype: 'Int' },
    { fieldname: 'kind', fieldtype: 'Text' },
    { fieldname: 'target', fieldtype: 'DynamicLink', options: 'kind' },
    { fieldname: 'copy', fieldtype: 'Text', fetch_from: 'ref.label' },
    { fieldname: 'secret', fieldtype: 'Text', hidden: true },
  ],
}

describe('le constructeur de DocTypes (mode développeur)', () => {
  it('un nom de champ se tire d’un libellé, sans collision', () => {
    expect(fieldnameOf('Date d’échéance')).toBe('date_d_echeance')
    expect(fieldnameOf('2e adresse')).toBe('f_2e_adresse')
    expect(fieldnameOf('  ')).toBe('')
    expect(uniqueName('qty', ['qty', 'qty_2'])).toBe('qty_3')
    expect(yamlFileName(DEF)).toBe('stock_entry.yaml')
  })

  it('un type neuf a un champ obligatoire (un type sans champ est refusé) ; sans disposition, tous les champs visibles sont placés', () => {
    expect(emptyDefinition().fields).toHaveLength(1)
    expect(placed(boardOf(DEF))).toEqual(['ref', 'qty', 'kind', 'target', 'copy']) // pas le champ masqué
  })

  it('ajouter un champ : nommé d’après son libellé, prêt pour son type', () => {
    const { def, name } = addField(DEF, 'Select', 'Qté')
    expect(name).toBe('qte')
    expect(def.fields.at(-1)).toEqual({ fieldname: 'qte', fieldtype: 'Select', label: 'Qté', options: ['Option 1', 'Option 2'] })
    expect(addField(DEF, 'Decimal').def.fields.at(-1)).toMatchObject({ fieldtype: 'Decimal', decimals: 2 })
    expect(addField(DEF, 'Int', 'Qty').name).toBe('qty_2')
  })

  it('une propriété vide est retirée : le YAML ne garde que ce qui compte', () => {
    let def = patchField(DEF, 'qty', { required: true, label: 'Quantité', description: '' })
    expect(def.fields[1]).toEqual({ fieldname: 'qty', fieldtype: 'Int', required: true, label: 'Quantité' })
    def = patchField(def, 'qty', { required: false, label: '' })
    expect(def.fields[1]).toEqual({ fieldname: 'qty', fieldtype: 'Int' })
    expect(patchField(DEF, 'qty', { fieldname: 'x' }).fields[1]!.fieldname).toBe('qty') // un renommage passe par renameField
  })

  it('changer de type emporte ce qui était propre à l’ancien', () => {
    const select = retypeField(DEF, 'qty', 'Select')
    expect(select.fields[1]).toEqual({ fieldname: 'qty', fieldtype: 'Select', options: ['Option 1', 'Option 2'] })
    expect(retypeField(select, 'qty', 'Date').fields[1]).toEqual({ fieldname: 'qty', fieldtype: 'Date' })
  })

  it('renommer un champ le renomme PARTOUT : disposition, titre, nommage, fetch_from, DynamicLink', () => {
    const got = renameField(DEF, boardOf(DEF), 'ref', 'code')!
    expect(got.def.title_field).toBe('code')
    expect(got.def.naming).toEqual({ field: 'code' })
    expect(got.def.fields.find((f) => f.fieldname === 'copy')!.fetch_from).toBe('code.label')
    expect(placed(got.board)).toContain('code')
    expect(renameField(DEF, boardOf(DEF), 'kind', 'type')!.def.fields.find((f) => f.fieldname === 'target')!.options).toBe('type')
    // Refusés : un nom pris, invalide, ou inchangé.
    expect(renameField(DEF, boardOf(DEF), 'ref', 'qty')).toBeNull()
    expect(renameField(DEF, boardOf(DEF), 'ref', 'Code!')).toBeNull()
    expect(renameField(DEF, boardOf(DEF), 'ref', 'ref')).toBeNull()
  })

  it('supprimer un champ retire ce qui le citait', () => {
    const got = removeField(DEF, boardOf(DEF), 'ref')
    expect(got.def.fields.map((f) => f.fieldname)).not.toContain('ref')
    expect(got.def.title_field).toBeUndefined()
    expect(got.def.naming).toBeUndefined()
    expect(placed(got.board)).not.toContain('ref')
  })

  it('assembler : la disposition du tableau, sans champ masqué ni disparu ; le reste de la définition intact', () => {
    let board = moveField(boardOf(DEF), 'qty', { tab: 0, section: 0, column: 0, index: 0 })
    board = moveField(unplace(board, 'copy'), 'copy', { tab: 0, section: 0, column: 0, index: 99 })
    const def = patchField(DEF, 'kind', { hidden: true })
    const out = assemble(def, board)
    expect(out.layout![0]!.columns[0]).toEqual(['qty', 'ref', 'target', 'copy'])
    expect(out.hooks).toEqual({ validate: ['stock.check'] })
    expect(assemble(DEF, unplace(unplace(unplace(unplace(unplace(boardOf(DEF), 'ref'), 'qty'), 'kind'), 'target'), 'copy')).layout).toBeUndefined()
  })

  it('le sens de `options` selon le type', () => {
    expect(['Select', 'Link', 'Table', 'DynamicLink', 'Text'].map((t) => optionsKind(t as never))).toEqual(['values', 'doctype', 'child', 'typeField', null])
  })

  it('la valeur par défaut tapée prend le type du champ', () => {
    expect(typedDefault('Int', ' 12 ')).toBe(12)
    expect(typedDefault('Number', '2.5')).toBe(2.5)
    expect(typedDefault('Int', 'abc')).toBe('abc') // le serveur dira pourquoi
    expect(typedDefault('Decimal', '12.50')).toBe('12.50') // un décimal reste une chaîne exacte
    expect(typedDefault('Date', 'today')).toBe('today')
    expect(typedDefault('Text', '  ')).toBeUndefined()
  })
})
