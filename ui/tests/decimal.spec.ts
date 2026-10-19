import { describe, expect, it } from 'vitest'
import cases from '../../tests/vectors/decimal_cases.json'
import { canonicalDecimal, formatDecimal, normalizeDecimal, sumDecimals } from '../src/lib/decimal'

// Les mêmes cas que `tests/unit/test_decimals.py` (écrits à la main côté serveur) : si le navigateur et le serveur divergent,
// l'un des deux échoue.
describe('Decimal : mêmes vecteurs que le serveur', () => {
  it.each(cases.cases)('$raw avec $decimals décimales', (c) => {
    expect(canonicalDecimal(c.raw, c.decimals)).toBe(c.expect)
  })

  it('refuse tout ce qui n’est pas une chaîne (un number est déjà inexact)', () => {
    for (const bad of [12.5, 0.1, 1, true, null, undefined, [], {}]) expect(canonicalDecimal(bad, 3)).toBeNull()
  })
})

describe('saisie francophone', () => {
  it('la virgule devient un point, les espaces autour disparaissent', () => {
    expect(normalizeDecimal(' 12,5 ')).toBe('12.5')
    expect(canonicalDecimal(normalizeDecimal('12,5'), 3)).toBe('12.500')
  })

  it('ne modifie pas ce qui n’est pas un nombre (c’est la validation qui refusera)', () => {
    expect(normalizeDecimal('1,2,3')).toBe('1.2,3')
    expect(canonicalDecimal(normalizeDecimal('1,2,3'), 3)).toBeNull()
  })
})

describe('affichage', () => {
  it('groupe les milliers et garde les décimales du champ', () => {
    expect(formatDecimal('1234.5', 3).replace(/\s/g, ' ')).toBe('1 234,500')
    expect(formatDecimal('0.000', 3)).toBe('0,000')
    expect(formatDecimal('-5.50', 2)).toBe('-5,50')
  })

  it('au-delà de 15 chiffres, le texte exact et pas un nombre arrondi', () => {
    expect(formatDecimal('123456789012345.678', 3)).toBe('123456789012345,678')
  })

  it('une valeur illisible est montrée telle quelle', () => {
    expect(formatDecimal('n’importe quoi', 3)).toBe('n’importe quoi')
  })
})

describe('sumDecimals : une somme exacte', () => {
  it('0,1 + 0,2 + 0,3 fait 0,6 et les grands montants ne perdent pas un centime', () => {
    expect(sumDecimals(['0.10', '0.20', '0.30'], 2)).toBe('0.60')
    expect(sumDecimals(['4503599627370497.01', '0.01'], 2)).toBe('4503599627370497.02')
  })

  it('négatifs, zéro, entiers, valeurs illisibles ignorées', () => {
    expect(sumDecimals(['-1.500', '0.500'], 3)).toBe('-1.000')
    expect(sumDecimals(['-0.001', '0.001'], 3)).toBe('0.000')
    expect(sumDecimals([], 2)).toBe('0.00')
    expect(sumDecimals(['12', '30'], 0)).toBe('42')
    expect(sumDecimals(['1.00', 'abc', '2.5000', '3.00'], 2)).toBe('6.50') // des zéros en trop ne changent pas la valeur
    expect(sumDecimals(['1.00', '2.505', '3.00'], 2)).toBe('4.00') // une 3e décimale significative : refusée, comme à l'écriture
    expect(sumDecimals(['-5'], 0)).toBe('-5')
  })
})
