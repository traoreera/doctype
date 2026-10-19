import { describe, expect, it } from 'vitest'
import { ApiError, type ApiErrorDetail } from '@/lib/api'
import { fieldStates, isDirty, isEmpty, isValidIsoDate, mapServerErrors, newValues, recordValues, toPayload, validate } from '@/lib/form'
import type { DocRecord } from '@/lib/types'
import { CUSTOMER, STOCK_ENTRY } from './fixtures'

const rec = (data: Record<string, unknown>): DocRecord => ({
  doctype: 'Customer', name: 'c1', owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version: 3, docstatus: 0, amended_from: null, data,
})

describe('valeurs initiales', () => {
  it('un nouveau document reçoit les défauts, les booléens décochés, le reste vide', () => {
    expect(newValues(CUSTOMER)).toMatchObject({ type: null, credit_limit: 0, blocked: false, customer_name: null, company_id: null })
  })

  it('un document existant n’hérite d’aucun défaut : un champ absent reste vide', () => {
    const v = recordValues(CUSTOMER, rec({ customer_name: 'ACME' }))
    expect(v.type).toBeNull()
    expect(v.credit_limit).toBeNull()
    expect(v.blocked).toBe(false)
  })
})

describe('états des champs', () => {
  it('depends_on masque le champ et le rend facultatif', () => {
    const v = { ...newValues(CUSTOMER), type: 'Particulier' }
    const st = fieldStates(CUSTOMER, v, null)
    expect(st.company_id).toMatchObject({ visible: false, required: false })
    expect(fieldStates(CUSTOMER, { ...v, type: 'Entreprise' }, null).company_id).toMatchObject({ visible: true, required: true })
  })

  it('mandatory_depends_on rend obligatoire un champ visible', () => {
    const v = { ...newValues(CUSTOMER), credit_limit: 5000 }
    expect(fieldStates(CUSTOMER, v, null).approver).toMatchObject({ visible: true, required: false })
    expect(fieldStates(CUSTOMER, { ...v, credit_limit: 20000 }, null).approver).toMatchObject({ visible: true, required: true })
    expect(fieldStates(CUSTOMER, { ...v, credit_limit: 0 }, null).approver).toMatchObject({ visible: false, required: false })
  })

  it('un champ hidden n’est jamais affiché', () => {
    expect(fieldStates(CUSTOMER, newValues(CUSTOMER), null)['secret']?.visible).toBe(false)
  })

  it('read_only_depends_on d’un nouveau document suit les valeurs saisies', () => {
    const v = { ...newValues(CUSTOMER), blocked: true }
    expect(fieldStates(CUSTOMER, v, null)['credit_limit']?.readOnly).toBe(true)
    expect(fieldStates(CUSTOMER, { ...v, blocked: false }, null)['credit_limit']?.readOnly).toBe(false)
  })

  it('read_only_depends_on d’un document existant se lit sur l’état ENREGISTRÉ, comme le serveur', () => {
    const saved = recordValues(CUSTOMER, rec({ customer_name: 'A', credit_limit: 100, blocked: false }))
    // cocher « Bloqué » ne verrouille pas le plafond avant l'enregistrement : le serveur accepterait la modification
    const editing = { ...saved, blocked: true, credit_limit: 200 }
    expect(fieldStates(CUSTOMER, editing, saved)['credit_limit']?.readOnly).toBe(false)
    // une fois enregistré bloqué, il l'est
    const blocked = recordValues(CUSTOMER, rec({ customer_name: 'A', credit_limit: 100, blocked: true }))
    expect(fieldStates(CUSTOMER, blocked, blocked)['credit_limit']?.readOnly).toBe(true)
  })
})

describe('validation', () => {
  const run = (values: Record<string, unknown>, saved = null) => validate(CUSTOMER, values, fieldStates(CUSTOMER, values, saved))

  it('exige les champs obligatoires visibles', () => {
    expect(run(newValues(CUSTOMER))).toEqual({ customer_name: 'Champ obligatoire' })
  })

  it('ne contrôle pas un champ masqué', () => {
    const v = { ...newValues(CUSTOMER), customer_name: 'A', company_id: 5 } // mauvais type, mais masqué (type Particulier)
    expect(run(v)).toEqual({})
  })

  it('exige le champ rendu obligatoire par sa condition', () => {
    const v = { ...newValues(CUSTOMER), customer_name: 'A', type: 'Entreprise', credit_limit: 20000 }
    expect(run(v)).toEqual({ company_id: 'Champ obligatoire', approver: 'Champ obligatoire' })
  })

  it('0 et false sont des valeurs, pas des vides', () => {
    expect(isEmpty(0)).toBe(false)
    expect(isEmpty(false)).toBe(false)
    expect(isEmpty('')).toBe(true)
    expect(isEmpty(null)).toBe(true)
    expect(isEmpty(undefined)).toBe(true)
  })

  it('refuse les types faux, comme le serveur (pas de coercition)', () => {
    const base = { ...newValues(CUSTOMER), customer_name: 'A' }
    expect(run({ ...base, credit_limit: '12' }).credit_limit).toBeDefined()
    expect(run({ ...base, credit_limit: NaN }).credit_limit).toBeDefined()
    expect(run({ ...base, credit_limit: Infinity }).credit_limit).toBeDefined()
    expect(run({ ...base, type: 'Autre' }).type).toBeDefined()
    expect(run({ ...base, since: '2026-02-30' }).since).toBeDefined()
    expect(run({ ...base, since: '20261017' }).since).toBeDefined()
    expect(run({ ...base, blocked: 'true' }).blocked).toBeDefined()
    expect(run({ ...base, since: '2026-02-28', credit_limit: 0 })).toEqual({})
  })

  it('dates : seule la forme canonique est valide', () => {
    expect(isValidIsoDate('2024-02-29')).toBe(true)
    expect(isValidIsoDate('2026-02-29')).toBe(false)
    expect(isValidIsoDate('2026-2-9')).toBe(false)
    expect(isValidIsoDate(20261017)).toBe(false)
  })
})

describe('charge utile', () => {
  it('omet vides et null, garde 0 et false', () => {
    const v = { ...newValues(CUSTOMER), customer_name: 'A', since: '', company_id: null }
    expect(toPayload(CUSTOMER, v)).toEqual({ customer_name: 'A', credit_limit: 0, blocked: false })
  })

  it('n’envoie jamais une clé hors DocType', () => {
    const v = { ...newValues(CUSTOMER), customer_name: 'A', intrus: 'x' }
    expect(toPayload(CUSTOMER, v)).not.toHaveProperty('intrus')
  })

  it('garde la valeur d’un champ masqué par sa condition (le serveur la conserve aussi)', () => {
    const v = { ...newValues(CUSTOMER), customer_name: 'A', company_id: '123', type: 'Particulier' }
    expect(toPayload(CUSTOMER, v).company_id).toBe('123')
  })
})

describe('modifications non enregistrées', () => {
  it('false/0 saisis après vide sont des modifications, vide ≡ null ≡ absent ne l’est pas', () => {
    const saved = recordValues(CUSTOMER, rec({ customer_name: 'A' }))
    expect(isDirty(CUSTOMER, { ...saved }, saved)).toBe(false)
    expect(isDirty(CUSTOMER, { ...saved, since: '' }, saved)).toBe(false)
    expect(isDirty(CUSTOMER, { ...saved, credit_limit: 0 }, saved)).toBe(true)
    expect(isDirty(CUSTOMER, { ...saved, blocked: true }, saved)).toBe(true)
  })
})

describe('erreurs du serveur', () => {
  const err = (code: string, msg: string, details: ApiErrorDetail[] = [], status = 422) => new ApiError(status, code, msg, details)

  it('rattache les erreurs aux champs connus', () => {
    const m = mapServerErrors(err('validation_failed', 'x', [{ field: 'company_id', msg: 'Champ obligatoire', type: 'missing' }]), CUSTOMER)
    expect(m.fields).toEqual({ company_id: 'Champ obligatoire' })
    expect(m.general).toBe('Corrigez les champs en erreur')
  })

  it('traduit les types Pydantic au lieu d’afficher l’anglais', () => {
    const m = mapServerErrors(err('validation_failed', 'x', [{ field: 'credit_limit', msg: 'nombre attendu', type: 'value_error' }, { field: 'since', msg: 'Input should be a valid date', type: 'string_type' }]), CUSTOMER)
    expect(m.fields.credit_limit).toBe('Valeur invalide')
    expect(m.fields.since).toBe('Valeur invalide')
  })

  it('lecture seule', () => {
    const m = mapServerErrors(err('validation_failed', 'x', [{ field: 'credit_limit', msg: 'Champ en lecture seule', type: 'read_only' }]), CUSTOMER)
    expect(m.fields.credit_limit).toBe('Lecture seule')
  })

  it('un champ inconnu du formulaire passe en message général, sans être perdu', () => {
    const m = mapServerErrors(err('validation_failed', 'invalide', [{ field: 'inconnu', msg: 'Quelque chose', type: 'x' }]), CUSTOMER)
    expect(m.fields).toEqual({})
    expect(m.general).toBe('Quelque chose')
  })

  it('le message d’un hook qui refuse est montré tel quel', () => {
    const m = mapServerErrors(err('hook_rejected', 'Le plafond dépasse 1 M€'), CUSTOMER)
    expect(m.general).toBe('Le plafond dépasse 1 M€')
    expect(m.fields).toEqual({})
  })

  it('un hook indisponible, lent ou trop gros reçoit un texte utilisable', () => {
    expect(mapServerErrors(err('hook_unavailable', 'x', [], 503), CUSTOMER).general).toMatch(/indisponible/)
    expect(mapServerErrors(err('hook_timeout', 'x', [], 504), CUSTOMER).general).toMatch(/trop de temps/)
    expect(mapServerErrors(err('hook_payload_too_large', 'x', [], 413), CUSTOMER).general).toMatch(/volumineux/)
  })
})


describe('champ Decimal', () => {
  const lines = (...qty: unknown[]) => qty.map((q) => ({ item: 'vis', qty: q, target_warehouse: 'w' }))
  const values = (rows: unknown[]) => ({ ...newValues(STOCK_ENTRY), purpose: 'Réception', posting_date: '2026-06-15', items: rows })
  const check = (rows: unknown[]) => validate(STOCK_ENTRY, values(rows), fieldStates(STOCK_ENTRY, values(rows), null))

  it('accepte du texte décimal, refuse un number (le serveur refuse un flottant)', () => {
    expect(check(lines('12.5', '3', '0.001'))).toEqual({})
    expect(check(lines(12.5))['items.0.qty']).toBeDefined()
  })

  it('dit pourquoi : trop de décimales, ou pas un nombre', () => {
    expect(check(lines('1.2345'))['items.0.qty']).toBe('Au plus 3 décimales')
    expect(check(lines('abc'))['items.0.qty']).toBe('Valeur invalide')
    expect(check(lines('1,5'))['items.0.qty']).toBe('Valeur invalide') // la virgule a été normalisée à la frappe : ce qui reste est faux
  })

  it('une quantité vide est « obligatoire », pas « invalide »', () => {
    expect(check(lines(null))['items.0.qty']).toBe('Champ obligatoire')
  })

  it('le payload porte la forme canonique, et ressaisir la même valeur n’est pas une modification', () => {
    const v = values(lines('12.5'))
    expect(toPayload(STOCK_ENTRY, v).items).toEqual([{ item: 'vis', qty: '12.500', target_warehouse: 'w' }])
    const saved = values(lines('12.500'))
    expect(isDirty(STOCK_ENTRY, v, saved)).toBe(false)
    expect(isDirty(STOCK_ENTRY, values(lines('12.501')), saved)).toBe(true)
  })

  it('le payload ne transforme jamais une valeur invalide en autre chose (le serveur la refusera avec son message)', () => {
    expect(toPayload(STOCK_ENTRY, values(lines('1.2345'))).items).toEqual([{ item: 'vis', qty: '1.2345', target_warehouse: 'w' }])
  })
})

describe('refus d’un hook sur une ligne', () => {
  it('« stock insuffisant » est signalé sur la cellule en cause et expliqué en haut', () => {
    const err = new ApiError(422, 'hook_rejected', 'Stock insuffisant pour « Vis » dans « Paris » : 10.000 disponible, 11.000 demandé', [
      { field: 'items.1.qty', msg: 'Stock disponible : 10.000', type: 'insufficient_stock' },
    ])
    const mapped = mapServerErrors(err, STOCK_ENTRY)
    expect(mapped.fields).toEqual({ 'items.1.qty': 'Stock disponible : 10.000' })
    expect(mapped.general).toContain('Stock insuffisant')
  })
})
