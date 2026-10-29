/**
 * Les BLOCS incorporés d'un document d'impression (`lib/printedit.ts`, `process/impression.md` §4 et §12) : le
 * tableau des lignes, les totaux, une grille de champs, réglés dans le panneau de droite de l'éditeur.
 *
 * Aucun composant, aucune requête : un bloc qu'on manipule, et ce que le serveur recevra. Les règles de
 * `printing.py` §8 sont vérifiées ICI, avant tout enregistrement.
 */
import { describe, expect, it } from 'vitest'
import {
  DEFAULT_PAGE,
  MAX_COLUMNS,
  addColumn,
  columnFields,
  fromTemplate,
  groupableColumns,
  insertField,
  moveColumn,
  moveFieldInBlock,
  printableFields,
  rawBlock,
  removeColumn,
  setColumnWidth,
  setGroupBy,
  tableColumns,
  toggleField,
  toggleSubtotal,
  totalFields,
  updateBlock,
  updateColumn,
  type Draft,
} from '@/lib/printedit'
import { INVOICE, TEMPLATE } from './print-fixtures'

const draft = (): Draft => fromTemplate(TEMPLATE)

describe('aller et retour', () => {
  it('lit le gabarit du serveur sans rien y changer', () => {
    const d = draft()
    expect(d.name).toBe('standard')
    expect(d.label).toBe('Facture')
    expect(d.blocks.map((b) => b.type)).toEqual(['title', 'fields', 'table', 'totals'])
    expect(tableColumns(d.blocks[2]!).map((c) => c.field)).toEqual(['article', 'qty'])
  })

  it('rend un bloc au serveur exactement comme il l’attend : les clés d’édition retirées', () => {
    const out = rawBlock(draft().blocks[2]!)
    expect(JSON.stringify(out)).not.toContain('"key"')
    expect(out.columns).toEqual([{ field: 'article' }, { field: 'qty', align: 'right' }])
  })

  it('un gabarit vide se lit quand même', () => {
    const d = fromTemplate(null)
    expect(d.blocks).toEqual([])
    expect(d.page).toEqual(DEFAULT_PAGE)
    expect(d.name).toBe('standard')
  })

  it('n’envoie pas les propriétés d’un autre genre de bloc', () => {
    let d = fromTemplate({ blocks: [{ type: 'totals', fields: ['total'] }] })
    d = updateBlock(d, d.blocks[0]!.key, { field: 'items', text: 'perdu', style: 'plain' })
    expect(rawBlock(d.blocks[0]!)).toEqual({ type: 'totals', fields: ['total'] })
  })

  it('une grille de champs garde son NOMBRE de colonnes, à l’ouverture comme à l’envoi', () => {
    // Deux défauts trouvés le 29 oct. : `fromTemplate` ne gardait que les LISTES, et l’envoi retirait le nombre.
    const d = fromTemplate({ blocks: [{ type: 'fields', columns: 3, fields: ['client'] }] })
    expect(d.blocks[0]!.columns).toBe(3)
    expect(rawBlock(d.blocks[0]!).columns).toBe(3)
  })
})

describe('choisir les champs à afficher', () => {
  it('ajoute et retire, en gardant l’ordre de choix', () => {
    const d = draft()
    const block = d.blocks[1]!.key
    let out = toggleField(d, block, 'total')
    expect(out.blocks[1]!.fields).toEqual(['client', 'date', 'total'])
    out = toggleField(out, block, 'date')
    expect(out.blocks[1]!.fields).toEqual(['client', 'total'])
  })

  it('réordonne un champ dans le bloc', () => {
    const d = draft()
    const block = d.blocks[1]!.key
    expect(moveFieldInBlock(d, block, 'date', 'up').blocks[1]!.fields).toEqual(['date', 'client'])
    expect(moveFieldInBlock(d, block, 'client', 'up').blocks[1]!.fields).toEqual(['client', 'date'])
  })

  it('n’offre que ce qui s’imprime : ni secret, ni fichier, ni table', () => {
    const names = printableFields(INVOICE).map((x) => x.fieldname)
    expect(names).toEqual(['client', 'date', 'total'])
    expect(totalFields(INVOICE).map((x) => x.fieldname)).toEqual(['total'])
  })
})

describe('choisir le tableau et ses colonnes', () => {
  it('n’offre en colonnes que les champs des LIGNES, et pas une image ni un texte riche', () => {
    expect(columnFields(INVOICE, 'items').map((x) => x.fieldname)).toEqual(['article', 'entrepot', 'qty', 'amount'])
    expect(columnFields(INVOICE, 'inconnu')).toEqual([])
  })

  it('ajoute une colonne, une seule fois, et pas plus que le serveur n’en accepte', () => {
    const d = draft()
    const block = d.blocks[2]!.key
    let out = addColumn(d, block, 'amount')
    expect(tableColumns(out.blocks[2]!).map((c) => c.field)).toEqual(['article', 'qty', 'amount'])
    out = addColumn(out, block, 'amount') // deux fois : refusé
    expect(tableColumns(out.blocks[2]!)).toHaveLength(3)
    for (let i = 0; i < MAX_COLUMNS + 5; i++) out = addColumn(out, block, `c${i}`)
    expect(tableColumns(out.blocks[2]!).length).toBeLessThanOrEqual(MAX_COLUMNS)
  })

  it('déplace et renomme une colonne', () => {
    const d = draft()
    const block = d.blocks[2]!.key
    const columns = tableColumns(d.blocks[2]!)
    expect(tableColumns(moveColumn(d, block, columns[1]!.key, 'left').blocks[2]!).map((c) => c.field)).toEqual(['qty', 'article'])
    const named = updateColumn(d, block, columns[1]!.key, { label: 'Qté', align: 'right', width: 20 })
    expect(tableColumns(named.blocks[2]!)[1]).toMatchObject({ field: 'qty', label: 'Qté', align: 'right', width: 20 })
  })

  it('retirer une colonne emporte son sous-total : le serveur le refuserait sinon', () => {
    let d = draft()
    const block = d.blocks[2]!.key
    d = setGroupBy(d, block, 'entrepot')
    d = toggleSubtotal(d, block, 'qty')
    expect(d.blocks[2]!.subtotal).toEqual(['qty'])
    const columns = tableColumns(d.blocks[2]!)
    d = removeColumn(d, block, columns.find((c) => c.field === 'qty')!.key)
    expect(d.blocks[2]!.subtotal).toEqual([])
  })

  it('ne plus regrouper emporte les sous-totaux', () => {
    let d = setGroupBy(draft(), draft().blocks[2]!.key, 'entrepot')
    const block = d.blocks[2]!.key
    d = setGroupBy(d, block, 'entrepot')
    d = toggleSubtotal(d, block, 'qty')
    d = setGroupBy(d, block, null)
    expect(d.blocks[2]!.group_by).toBeNull()
    expect(d.blocks[2]!.subtotal).toEqual([])
  })

  it('un sous-total sans regroupement ne se pose pas', () => {
    const d = draft()
    const out = toggleSubtotal(d, d.blocks[2]!.key, 'qty')
    expect(out.blocks[2]!.subtotal ?? []).toEqual([])
  })

  it('ne propose au regroupement que ce que le serveur regroupe', () => {
    const names = groupableColumns(INVOICE, 'items').map((x) => x.fieldname)
    expect(names).toContain('entrepot')
    expect(names).toContain('article')
    expect(names).not.toContain('qty') // un Decimal ne regroupe pas
    expect(names).not.toContain('amount')
  })
})

describe('régler le bloc au panneau', () => {
  it('un champ ajouté se pose une seule fois, à sa place', () => {
    const d = draft()
    const block = d.blocks[1]!.key
    expect(insertField(d, block, 'total', 1).blocks[1]!.fields).toEqual(['client', 'total', 'date'])
    expect(insertField(d, block, 'client', 0)).toBe(d)
  })

  it('une largeur ne fait jamais dépasser 100 % au total', () => {
    const d = fromTemplate({ blocks: [{ type: 'table', field: 'items', columns: [{ field: 'article', width: 50 }, { field: 'qty', width: 20 }, { field: 'amount' }] }] })
    const table = d.blocks[0]!
    const [article, qty, amount] = tableColumns(table)
    const widths = (x: Draft) => tableColumns(x.blocks[0]!).map((c) => c.width ?? null)
    expect(widths(setColumnWidth(d, table.key, article!.key, 61.4))).toEqual([61, 20, null])
    expect(widths(setColumnWidth(d, table.key, article!.key, 95))).toEqual([80, 20, null]) // 100 - 20
    expect(widths(setColumnWidth(d, table.key, qty!.key, -4))).toEqual([50, 1, null])
    expect(widths(setColumnWidth(d, table.key, amount!.key, 60))).toEqual([50, 20, 30])
  })
})
