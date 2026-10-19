import { describe, expect, it } from 'vitest'
import { isFrozen, statusLabel, statusTone, transitionsFor } from '@/lib/docstatus'
import type { DocRecord, DocTypeMeta, Right } from '@/lib/types'
import { CUSTOMER, STOCK_ENTRY } from './fixtures'

const rec = (docstatus: number): DocRecord => ({
  doctype: 'Stock Entry', name: 'MS-1', owner: 'u', creation: '', modified: '', version: 2, docstatus, amended_from: null, data: {},
})
const withRights = (over: Partial<Record<'create' | 'submit' | 'cancel', Right>>): DocTypeMeta => ({ ...STOCK_ENTRY, rights: { ...STOCK_ENTRY.rights, ...over } })

describe('état d’un document soumettable', () => {
  it('trois états, trois mots, trois tons', () => {
    expect([0, 1, 2].map(statusLabel)).toEqual(['Brouillon', 'Soumis', 'Annulé'])
    expect([0, 1, 2].map(statusTone)).toEqual(['draft', 'submitted', 'cancelled'])
  })

  it('seul un brouillon n’est pas figé', () => {
    expect([0, 1, 2].map((s) => isFrozen(rec(s)))).toEqual([false, true, true])
    expect(isFrozen(null)).toBe(false)
  })

  it('un enregistrement sans docstatus (serveur sans documents soumettables) est un brouillon, pas un document figé', () => {
    const old = { ...rec(0) } as Partial<DocRecord>
    delete old.docstatus
    expect(isFrozen(old as DocRecord)).toBe(false)
  })
})

describe('transitions offertes', () => {
  it('chaque état n’offre que sa transition', () => {
    expect(transitionsFor(STOCK_ENTRY, rec(0))).toEqual({ submit: true, cancel: false, amend: false })
    expect(transitionsFor(STOCK_ENTRY, rec(1))).toEqual({ submit: false, cancel: true, amend: false })
    expect(transitionsFor(STOCK_ENTRY, rec(2))).toEqual({ submit: false, cancel: false, amend: true })
  })

  it('un droit refusé retire le bouton', () => {
    expect(transitionsFor(withRights({ submit: 'none' }), rec(0)).submit).toBe(false)
    expect(transitionsFor(withRights({ cancel: 'none' }), rec(1)).cancel).toBe(false)
    expect(transitionsFor(withRights({ create: 'none' }), rec(2)).amend).toBe(false) // amender, c'est créer
  })

  it('un DocType qui ne se soumet pas n’offre rien, quels que soient ses droits', () => {
    expect(transitionsFor(CUSTOMER, { ...rec(0), doctype: 'Customer' })).toEqual({ submit: false, cancel: false, amend: false })
    expect(transitionsFor(null, rec(0))).toEqual({ submit: false, cancel: false, amend: false })
    expect(transitionsFor(STOCK_ENTRY, null)).toEqual({ submit: false, cancel: false, amend: false })
  })
})
