import { describe, expect, it } from 'vitest'
import { bucketLabel, foldParts, formatMetric, groupLabel, linkTargets, metricSpec, plotValue, queryOf } from '@/lib/aggregate'
import type { AggregateGroup, AggregateResult, ChartBlock, NumberCardBlock } from '@/lib/types'

const card = (over: Partial<NumberCardBlock> = {}): NumberCardBlock => ({ type: 'number_card', label: 'x', doctype: 'Bin', function: 'count', ...over })
const chart = (over: Partial<ChartBlock> = {}): ChartBlock => ({ type: 'chart', label: 'x', doctype: 'Bin', kind: 'bar', group_by: 'item', function: 'count', limit: 10, ...over })

describe('la requête d’un bloc', () => {
  it('une carte : une mesure, des filtres, aucun regroupement', () => {
    expect(queryOf(card())).toEqual({ metrics: ['count'] })
    expect(queryOf(card({ function: 'sum', field: 'qty', filters: { docstatus: 1 } }))).toEqual({ metrics: ['sum:qty'], filters: { docstatus: 1 } })
  })

  it('un graphique : regroupement, découpage, fenêtre, limite', () => {
    expect(queryOf(chart({ group_by: 'posting_date', bucket: 'month', periods: 6, function: 'avg', field: 'qty' }))).toEqual({
      metrics: ['avg:qty'], group_by: 'posting_date', bucket: 'month', periods: 6, limit: 10,
    })
  })

  it('des filtres vides ne sont pas envoyés', () => {
    expect(queryOf(card({ filters: {} }))).toEqual({ metrics: ['count'] })
  })

  it('metricSpec', () => {
    expect(metricSpec('count')).toBe('count')
    expect(metricSpec('sum', 'qty')).toBe('sum:qty')
    expect(metricSpec('sum', null)).toBe('count')
  })
})

describe('formater une mesure', () => {
  it('un entier, un nombre, une quantité exacte, rien', () => {
    expect(formatMetric(1234, { function: 'count', decimals: null }).replace(/\s/g, ' ')).toBe('1 234')
    expect(formatMetric('1234.500', { function: 'sum', decimals: 3 }).replace(/\s/g, ' ')).toBe('1 234,500')
    expect(formatMetric(null, { function: 'avg', decimals: null })).toBe('—')
    expect(formatMetric(undefined)).toBe('—')
  })

  it('une moyenne n’a pas dix décimales', () => {
    expect(formatMetric(5.333333333, { function: 'avg', decimals: null })).toBe('5,33')
    expect(formatMetric(5.333333333, { function: 'sum', decimals: null })).toBe('5,333333333')
  })

  it('un Decimal reste exact au-delà de 15 chiffres', () => {
    expect(formatMetric('4503599627370497.02', { function: 'sum', decimals: 2 })).toBe('4503599627370497,02')
  })

  it('plotValue : un nombre pour dessiner, zéro plutôt que NaN', () => {
    expect(plotValue('12.50')).toBe(12.5)
    expect(plotValue(null)).toBe(0)
    expect(plotValue('abc')).toBe(0)
  })
})

describe('libellés d’un groupe', () => {
  const g = (over: Partial<AggregateGroup>): AggregateGroup => ({ field: 'f', bucket: null, fieldtype: 'Select', ...over })

  it('périodes', () => {
    expect(bucketLabel('2026-10', 'month')).toMatch(/oct.*26/)
    expect(bucketLabel('2026-10-17', 'day')).toMatch(/17.*oct/)
    expect(bucketLabel('2026', 'year')).toBe('2026')
    expect(bucketLabel('n’importe quoi', 'month')).toBe('n’importe quoi')
  })

  it('un groupe vide, un état, un booléen, un lien résolu', () => {
    expect(groupLabel(g({}), null)).toBe('(vide)')
    expect(groupLabel(g({}), '')).toBe('(vide)')
    expect(groupLabel(g({ field: 'docstatus', fieldtype: null }), 1)).toBe('Soumis')
    expect(groupLabel(g({ fieldtype: 'Boolean' }), true)).toBe('Oui')
    expect(groupLabel(g({ fieldtype: 'Boolean' }), false)).toBe('Non')
    expect(groupLabel(g({ fieldtype: 'Link', options: 'Item' }), 'a1', (_t, n) => `Titre ${n}`)).toBe('Titre a1')
    expect(groupLabel(g({ fieldtype: 'Link', options: 'Item' }), 'a1')).toBe('a1') // pas de résolveur : le nom
    expect(groupLabel(g({}), 'Réception')).toBe('Réception')
    expect(groupLabel(null, 'x')).toBe('x')
  })

  it('les liens à résoudre avant de dessiner', () => {
    const res = (group: AggregateGroup | null, keys: (string | null)[]): AggregateResult => ({
      group, metrics: [], truncated: false, rows: keys.map((key) => ({ key, values: [1] })),
    })
    expect(linkTargets(res(g({ fieldtype: 'Link', options: 'Item' }), ['a', null, 'b']))).toEqual({ target: 'Item', names: ['a', 'b'] })
    expect(linkTargets(res(g({}), ['a']))).toBeNull()
    expect(linkTargets(res(null, [null]))).toBeNull()
  })
})

describe('les parts d’un camembert', () => {
  const p = (label: string, raw: number | string | null) => ({ label, raw })

  it('trié du plus gros au plus petit ; zéro, négatif et vide ne sont pas des parts', () => {
    const { parts, folded } = foldParts([p('b', 2), p('a', 5), p('z', 0), p('n', -3), p('v', null)], 6, null, 'Autres')
    expect(parts.map((x) => x.label)).toEqual(['a', 'b'])
    expect(folded).toBe(false)
  })

  it('au-delà du maximum, la queue se replie en UNE part « Autres » (le maximum comprend ce repli)', () => {
    const many = Array.from({ length: 9 }, (_, i) => p(`g${i}`, 100 - i)) // 100, 99, … 92
    const { parts, folded } = foldParts(many, 6, null, 'Autres')
    expect(parts.map((x) => x.label)).toEqual(['g0', 'g1', 'g2', 'g3', 'g4', 'Autres'])
    expect(parts.at(-1)!.raw).toBe(95 + 94 + 93 + 92) // g5…g8
    expect(folded).toBe(true)
  })

  it('exactement le maximum : rien à replier', () => {
    expect(foldParts(Array.from({ length: 6 }, (_, i) => p(`g${i}`, i + 1)), 6, null, 'Autres').folded).toBe(false)
  })

  it('le repli d’un Decimal reste exact', () => {
    const decs = [p('a', '10.10'), p('b', '0.10'), p('c', '0.20'), p('d', '0.30')]
    const { parts } = foldParts(decs, 2, 2, 'Autres')
    expect(parts.map((x) => [x.label, x.raw])).toEqual([['a', '10.10'], ['Autres', '0.60']]) // 0,1 + 0,2 + 0,3, pas 0,6000000000000001
  })
})
