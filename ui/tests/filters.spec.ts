import { describe, expect, it } from 'vitest'
import { buildFilters, defaultOp, opsFor, type FilterRow } from '@/lib/filters'
import { CUSTOMER, STOCK_ENTRY } from './fixtures'

const row = (field: string, op: string, value: unknown, id = 1): FilterRow => ({ id, field, op, value })
const build = (rows: FilterRow[], quick?: { field: string; text: string }) => buildFilters(rows, CUSTOMER.fields, quick)

describe('filtres', () => {
  it('égalité : la valeur seule', () => {
    expect(build([row('type', '=', 'Entreprise')])).toEqual({ type: 'Entreprise' })
  })

  it('autres opérateurs : [opérateur, valeur]', () => {
    expect(build([row('customer_name', 'like', ' acme ')])).toEqual({ customer_name: ['like', 'acme'] })
    expect(build([row('credit_limit', '>=', '1000')])).toEqual({ credit_limit: ['>=', 1000] })
  })

  it('ignore les lignes incomplètes ou impossibles, plutôt que d’envoyer une requête qui sera refusée', () => {
    expect(build([row('customer_name', 'like', '')])).toEqual({})
    expect(build([row('credit_limit', '>', 'abc')])).toEqual({})
    expect(build([row('type', 'like', 'x')])).toEqual({}) // pas de « contient » sur un Select
    expect(build([row('customer_name', '>', 'x')])).toEqual({}) // pas d'ordre sur un texte
    expect(build([row('inconnu', '=', 'x')])).toEqual({})
    expect(build([row('credit_limit', '=', null)])).toEqual({})
  })

  it('« parmi » découpe, nettoie et convertit', () => {
    expect(build([row('type', 'in', 'Particulier, Entreprise ,')])).toEqual({ type: ['in', ['Particulier', 'Entreprise']] })
    expect(build([row('credit_limit', 'in', '1, 2,3')])).toEqual({ credit_limit: ['in', [1, 2, 3]] })
    expect(build([row('type', 'in', ' , ')])).toEqual({})
  })

  it('booléen : false est une valeur', () => {
    expect(build([row('blocked', '=', false)])).toEqual({ blocked: false })
    expect(build([row('blocked', '!=', true)])).toEqual({ blocked: ['!=', true] })
    expect(build([row('blocked', '=', null)])).toEqual({})
  })

  it('0 est une valeur numérique', () => {
    expect(build([row('credit_limit', '=', 0)])).toEqual({ credit_limit: 0 })
  })

  it('la recherche rapide est un « contient » sur le champ donné', () => {
    expect(build([], { field: 'customer_name', text: ' ac ' })).toEqual({ customer_name: ['like', 'ac'] })
    expect(build([], { field: 'customer_name', text: '   ' })).toEqual({})
  })

  it('un filtre explicite sur le même champ que la recherche rapide la remplace', () => {
    expect(build([row('customer_name', '=', 'ACME')], { field: 'customer_name', text: 'ac' })).toEqual({ customer_name: 'ACME' })
  })

  it('les opérateurs proposés sont ceux du serveur', () => {
    expect(opsFor('Text')).not.toContain('>')
    expect(opsFor('Number')).toContain('>')
    expect(opsFor('Boolean')).toEqual(['=', '!='])
    expect(defaultOp('Text')).toBe('like')
    expect(defaultOp('Select')).toBe('=')
  })
})


describe('filtres sur un Decimal', () => {
  const qty = STOCK_ENTRY.children!['Stock Entry Line']!.fields
  const decimal = (rows: FilterRow[]) => buildFilters(rows, qty)

  it('envoient du texte (jamais un number), virgule française comprise', () => {
    expect(decimal([row('qty', '>=', '12,5')])).toEqual({ qty: ['>=', '12.5'] })
    expect(decimal([row('qty', '=', '3')])).toEqual({ qty: '3' })
    // La virgule est celle des décimales : les valeurs d'une liste se séparent par des points-virgules.
    expect(decimal([row('qty', 'in', '1; 2,5 ;3.5')])).toEqual({ qty: ['in', ['1', '2.5', '3.5']] })
  })

  it('ignorent ce que le serveur refuserait : trop de décimales, pas un nombre, une liste dont un élément est faux', () => {
    expect(decimal([row('qty', '>', '1.2345')])).toEqual({})
    expect(decimal([row('qty', '>', 'abc')])).toEqual({})
    expect(decimal([row('qty', 'in', '1; x')])).toEqual({})
    expect(decimal([row('qty', 'like', '1')])).toEqual({}) // pas de « contient » sur un nombre
  })

  it('les mêmes opérateurs qu’un Number', () => {
    expect(opsFor('Decimal')).toEqual(opsFor('Number'))
  })
})
