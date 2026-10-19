import { describe, expect, it } from 'vitest'
import { dateFromIso, formatCell, formatDate, formatNumber, isoFromDate, relativeTime } from '@/lib/format'
import { CUSTOMER } from './fixtures'

const field = (n: string) => CUSTOMER.fields.find((f) => f.fieldname === n)!

describe('dates', () => {
  it('formate sans fuseau', () => {
    expect(formatDate('2026-10-17')).toBe('17/10/2026')
    expect(formatDate('pas une date')).toBe('pas une date')
  })

  it('aller-retour Date <-> ISO sans décalage d’un jour, quel que soit le fuseau', () => {
    for (const iso of ['2026-01-01', '2026-12-31', '2024-02-29', '2026-03-29', '2026-10-25']) {
      expect(isoFromDate(dateFromIso(iso)!)).toBe(iso)
    }
    expect(dateFromIso(null)).toBeNull()
    expect(dateFromIso('x')).toBeNull()
  })
})

describe('temps relatif', () => {
  const now = Date.parse('2026-10-17T12:00:00Z')
  it('en français', () => {
    expect(relativeTime('2026-10-17T11:55:00Z', now)).toBe('il y a 5 minutes')
    expect(relativeTime('2026-10-16T12:00:00Z', now)).toBe('hier')
    expect(relativeTime('2026-10-17T11:59:59Z', now)).toBe('à l’instant')
    expect(relativeTime('n’importe quoi', now)).toBe('n’importe quoi')
  })
})

describe('cellules', () => {
  it('formate par type', () => {
    expect(formatCell(field('credit_limit'), 1234.5)).toBe(formatNumber(1234.5))
    expect(formatCell(field('credit_limit'), 0)).toBe('0')
    expect(formatCell(field('since'), '2026-10-17')).toBe('17/10/2026')
    expect(formatCell(field('blocked'), true)).toBe('Oui')
    expect(formatCell(field('blocked'), false)).toBe('Non')
    expect(formatCell(field('customer_name'), null)).toBe('')
    expect(formatCell(field('customer_name'), '')).toBe('')
  })

  it('un lien est remplacé par le titre du document cible quand on le connaît', () => {
    expect(formatCell(field('country'), 'FR', { FR: 'France' })).toBe('France')
    expect(formatCell(field('country'), 'DE', { FR: 'France' })).toBe('DE')
  })
})
