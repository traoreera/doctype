/**
 * Droits par champ côté Desk (traoreera/doctype#74). Le serveur est l'autorité : un champ que l'utilisateur ne lit pas n'est même pas
 * dans `/meta`. Ce que le Desk fait de ce qui RESTE : un champ réservé qu'il voit sans pouvoir l'écrire (`access: 'read'`) est
 * verrouillé, jamais recopié par « Dupliquer », jamais envoyé à la création, et le refus du serveur se dit dans la langue de la page.
 */
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/lib/api'
import { creationPayload, duplicateValues, fieldStates, mapServerErrors, newValues, toPayload } from '@/lib/form'
import type { DocRecord, DocTypeMeta } from '@/lib/types'
import { CUSTOMER } from './fixtures'

/** Le méta tel que le serveur le sert à qui lit `credit_limit` sans pouvoir l'écrire. */
const RESERVED: DocTypeMeta = {
  ...CUSTOMER,
  fields: CUSTOMER.fields.map((f) => (f.fieldname === 'credit_limit' ? { ...f, access: 'read' as const, read_only: true } : f)),
}
const rec = (data: Record<string, unknown>): DocRecord => ({
  doctype: 'Customer', name: 'c1', owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version: 3, docstatus: 0, amended_from: null, data,
})

describe('un champ réservé que l’utilisateur voit sans pouvoir l’écrire', () => {
  it('est verrouillé sur la fiche, à la création comme ensuite', () => {
    expect(fieldStates(RESERVED, newValues(RESERVED), null).credit_limit).toMatchObject({ visible: true, readOnly: true })
    expect(fieldStates(CUSTOMER, newValues(CUSTOMER), null).credit_limit!.readOnly).toBe(false)
  })

  it('n’est pas recopié par « Dupliquer » : la création serait refusée', () => {
    const source = rec({ customer_name: 'ACME', credit_limit: 9000 })
    expect(duplicateValues(CUSTOMER, source).credit_limit).toBe(9000)
    expect(duplicateValues(RESERVED, source)).toMatchObject({ customer_name: 'ACME', credit_limit: 0 }) // son défaut, comme un nouveau document
  })

  it('ne part pas à la création (le serveur pose son défaut), mais repart tel quel à la mise à jour (elle remplace le document)', () => {
    const values = { ...newValues(RESERVED), customer_name: 'ACME' }
    expect(toPayload(RESERVED, values)).toMatchObject({ customer_name: 'ACME', credit_limit: 0 })
    expect(creationPayload(RESERVED, values)).not.toHaveProperty('credit_limit')
    expect(creationPayload(RESERVED, values)).toMatchObject({ customer_name: 'ACME' })
    expect(creationPayload(CUSTOMER, { ...newValues(CUSTOMER), customer_name: 'ACME' })).toHaveProperty('credit_limit', 0) // un champ ordinaire part
  })

  it('le refus du serveur est rangé sous le champ, dans la langue de la page', () => {
    const refused = new ApiError(403, 'field_not_writable', 'Vous ne pouvez pas modifier : credit_limit', [
      { field: 'credit_limit', msg: 'Champ réservé : vous ne pouvez pas le modifier', type: 'field_not_writable' },
    ])
    const mapped = mapServerErrors(refused, RESERVED)
    expect(mapped.fields.credit_limit).toBe('Champ réservé : vous ne pouvez pas le modifier')
    expect(mapped.general).toBe('Vous ne pouvez pas modifier un champ réservé de ce document')
    // Un champ que l'utilisateur ne LIT pas n'est pas dans son méta : le refus ne se range sous aucun champ, le message général le dit.
    const hidden = new ApiError(403, 'field_not_writable', 'Vous ne pouvez pas modifier : salary', [{ field: 'salary', msg: 'x', type: 'field_not_writable' }])
    expect(mapServerErrors(hidden, RESERVED).fields).toEqual({})
    const unread = new ApiError(403, 'field_not_readable', 'Filtre impossible…', [{ field: 'salary', msg: 'x', type: 'field_not_readable' }])
    expect(mapServerErrors(unread, RESERVED).general).toBe('Cette demande porte sur un champ que vous ne pouvez pas lire')
  })
})
