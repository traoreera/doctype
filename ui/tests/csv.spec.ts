import { describe, expect, it } from 'vitest'
import { buildCsv, csvCell, csvFilename, exportValue, isSystemColumn } from '@/lib/csv'
import type { DocRecord, FieldMeta } from '@/lib/types'

const field = (fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({ fieldname: 'f', fieldtype, required: false, hidden: false, in_list_view: false, ...extra })
const rec = (data: Record<string, unknown>, extra: Partial<DocRecord> = {}): DocRecord =>
  ({ doctype: 'X', name: 'N1', owner: 'alice', creation: '2026-10-01T08:00:00+00:00', modified: '2026-10-02T09:30:00+00:00', version: 1, docstatus: 0, amended_from: null, data, ...extra })

describe('une cellule CSV', () => {
  it('guillemets autour de ce qui contient le séparateur, un guillemet ou un saut de ligne', () => {
    expect(csvCell('simple', true)).toBe('simple')
    expect(csvCell('a;b', true)).toBe('"a;b"')
    expect(csvCell('il a dit "oui"', true)).toBe('"il a dit ""oui"""')
    expect(csvCell('ligne 1\nligne 2', true)).toBe('"ligne 1\nligne 2"')
    expect(csvCell('virgule, ok', true)).toBe('virgule, ok') // la virgule n'est pas le séparateur
  })

  it.each(['=1+1', '+33 6 12', '-2+3', '@SUM(A1)', '\tTAB', '\rCR', '=HYPERLINK("http://x","y")', '=cmd|\' /C calc\'!A0'])(
    'le texte %j ne devient jamais une formule',
    (text) => {
      const cell = csvCell(text, true)
      expect(cell.replace(/^"/, '').startsWith("'")).toBe(true) // l'apostrophe est devant : le tableur lit du texte
    },
  )

  it('un nombre négatif reste un nombre, un texte ordinaire n’est pas touché', () => {
    expect(csvCell('-12,5', false)).toBe('-12,5')
    expect(csvCell('Bonjour = monde', true)).toBe('Bonjour = monde') // seul le PREMIER caractère compte
    expect(csvCell('a-b', true)).toBe('a-b')
  })
})

describe('le fichier', () => {
  it('BOM UTF-8, séparateur « ; », CRLF, fin de ligne finale', () => {
    const csv = buildCsv(['Nom', 'Qté'], [['Vis', '12,5'], ['Écrou', '3']], [true, false])
    expect(csv.startsWith('﻿')).toBe(true)
    expect(csv.slice(1)).toBe('Nom;Qté\r\nVis;12,5\r\nÉcrou;3\r\n')
  })

  it('les en-têtes sont du texte (un libellé « =Total » est neutralisé), les nombres non', () => {
    const csv = buildCsv(['=Total', 'Montant'], [['-5,00', '-5,00']], [false, false])
    const [head, row] = csv.slice(1).trim().split('\r\n')
    expect(head).toBe("'=Total;Montant")
    expect(row).toBe('-5,00;-5,00')
  })

  it('une colonne texte d’une ligne est neutralisée, une colonne nombre non', () => {
    const csv = buildCsv(['A', 'B'], [['=1+1', '-3']], [true, false])
    expect(csv.slice(1).trim().split('\r\n')[1]).toBe("'=1+1;-3")
  })

  it('un tableau vide donne les seuls en-têtes', () => {
    expect(buildCsv(['A'], [], [true])).toBe('﻿A\r\n')
  })
})

describe('les valeurs exportées', () => {
  it('nombre et décimal : virgule décimale, jamais une chaîne que le tableur lirait comme du texte', () => {
    expect(exportValue(field('Number'), 'f', rec({ f: 12.5 }))).toEqual({ text: '12,5', textual: false })
    expect(exportValue(field('Decimal', { decimals: 3 }), 'f', rec({ f: '1234.500' }))).toEqual({ text: '1234,500', textual: false })
    expect(exportValue(field('Decimal', { decimals: 3 }), 'f', rec({ f: '-0.250' }))).toEqual({ text: '-0,250', textual: false })
  })

  it('booléen, date, lien, texte, valeur absente', () => {
    expect(exportValue(field('Boolean'), 'f', rec({ f: true })).text).toBe('Oui')
    expect(exportValue(field('Boolean'), 'f', rec({ f: false })).text).toBe('Non')
    expect(exportValue(field('Date'), 'f', rec({ f: '2026-10-17' })).text).toBe('2026-10-17')
    expect(exportValue(field('Link', { options: 'Item' }), 'f', rec({ f: 'a1b2' })).text).toBe('a1b2')
    expect(exportValue(field('Text'), 'f', rec({ f: '=cmd' }))).toEqual({ text: '=cmd', textual: true })
    expect(exportValue(field('Number'), 'f', rec({}))).toEqual({ text: '', textual: true })
  })

  it('colonnes système', () => {
    const r = rec({}, { docstatus: 1 })
    expect(exportValue(undefined, 'name', r).text).toBe('N1')
    expect(exportValue(undefined, 'owner', r).text).toBe('alice')
    expect(exportValue(undefined, 'docstatus', r).text).toBe('Soumis')
    expect(exportValue(undefined, 'modified', r).text).toBe('2026-10-02T09:30:00+00:00')
    expect(['name', 'owner', 'creation', 'modified', 'docstatus', 'qty'].map(isSystemColumn)).toEqual([true, true, true, true, true, false])
  })

  it('un nom de fichier sûr', () => {
    expect(csvFilename('Stock Entry', '2026-10-17')).toBe('stock-entry-2026-10-17.csv')
    expect(csvFilename('../../etc/passwd', '2026-10-17')).toBe('etc-passwd-2026-10-17.csv')
    expect(csvFilename('???', '2026-10-17')).toBe('export-2026-10-17.csv')
  })
})
