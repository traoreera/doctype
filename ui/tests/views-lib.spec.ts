import { describe, expect, it, vi } from 'vitest'
import { layerFilters, matchesFilter, parseFiltersParam, passesLater, rowsFromFilters } from '@/lib/filters'
import { presetValues, startValues } from '@/lib/form'
import { pickView, readColumns, readView, writeColumns, writeView } from '@/lib/viewprefs'
import type { DocTypeMeta, FieldMeta, ViewsMeta } from '@/lib/types'
import { CUSTOMER } from './fixtures'

const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })

describe('les filtres portés par l’adresse', () => {
  it('un objet JSON, sinon rien', () => {
    expect(parseFiltersParam('{"docstatus":0,"purpose":"Sortie"}')).toEqual({ docstatus: 0, purpose: 'Sortie' })
    for (const bad of [undefined, null, '', 'pas du json', '[1]', '"x"', '12', ['a']]) expect(parseFiltersParam(bad)).toEqual({})
  })

  let n = 0
  const id = () => ++n
  const fields = [
    f({ fieldname: 'purpose', fieldtype: 'Select', options: ['Réception', 'Sortie'] }),
    f({ fieldname: 'qty', fieldtype: 'Decimal', decimals: 3 }),
    f({ fieldname: 'item', fieldtype: 'Text' }),
    f({ fieldname: 'lines', fieldtype: 'Table', options: 'Line' }),
    f({ fieldname: 'paid', fieldtype: 'Boolean' }),
  ]

  it('devient les lignes de la barre de filtres, que l’utilisateur voit et peut retirer', () => {
    const { rows, status } = rowsFromFilters({ docstatus: 1, purpose: 'Sortie', qty: ['>', '5.000'], item: ['like', 'vis'], paid: false }, fields, id)
    expect(status).toBe(1)
    expect(rows.map(({ field, op, value }) => ({ field, op, value }))).toEqual([
      { field: 'purpose', op: '=', value: 'Sortie' },
      { field: 'qty', op: '>', value: '5.000' },
      { field: 'item', op: 'like', value: 'vis' },
      { field: 'paid', op: '=', value: false },
    ])
    expect(new Set(rows.map((r) => r.id)).size).toBe(rows.length) // un identifiant par ligne
  })

  it('une liste « parmi » s’écrit comme la barre l’attend (virgules, points-virgules pour un Decimal)', () => {
    const { rows } = rowsFromFilters({ purpose: ['in', ['Réception', 'Sortie']], qty: ['in', ['1.000', '2.500']] }, fields, id)
    expect(rows.map((r) => r.value)).toEqual(['Réception,Sortie', '1.000;2.500'])
  })

  it('un filtre que la barre ne sait pas montrer est écarté : champ inconnu, table, opérateur impossible, état hors 0-2', () => {
    const { rows, status } = rowsFromFilters({ ghost: 1, lines: 2, purpose: ['>', 'a'], docstatus: 7 }, fields, id)
    expect(rows).toEqual([])
    expect(status).toBeNull()
  })
})

describe('évaluer un filtre côté client (mêmes opérateurs que le serveur)', () => {
  it.each([
    ['=', 'a', 'a', true], ['=', 'a', 'b', false], ['=', null, null, true], ['=', undefined, null, true], ['=', 'a', null, false],
    ['!=', 'a', 'b', true], ['!=', 'a', 'a', false], ['!=', undefined, 'a', true], // « différent » inclut l'absence, comme le serveur
  ])('égalité : %s %j %j -> %s', (op, value, operand, expected) => {
    const spec = op === '=' ? (operand === null && value !== null && value !== undefined ? null : operand) : [op, operand]
    expect(matchesFilter(value, op === '=' ? spec : [op, operand])).toBe(expected)
  })

  it('parmi, contient (sans casse)', () => {
    expect(matchesFilter('b', ['in', ['a', 'b']])).toBe(true)
    expect(matchesFilter('c', ['in', ['a', 'b']])).toBe(false)
    expect(matchesFilter('Vis M6', ['like', 'vis'])).toBe(true)
    expect(matchesFilter(5, ['like', 'x'])).toBe(false)
  })

  it('ordre : dates ISO, nombres, décimaux canoniques ; rien ne passe sans valeur', () => {
    expect(matchesFilter('2026-10-17', ['>=', '2026-10-17'])).toBe(true)
    expect(matchesFilter('2026-10-16', ['>=', '2026-10-17'])).toBe(false)
    expect(matchesFilter('2026-10-16', ['<', '2026-10-17'])).toBe(true)
    expect(matchesFilter(5, ['>', 4])).toBe(true)
    expect(matchesFilter('10.000', ['>', '9.500'])).toBe(true) // 10 > 9,5 en nombre, pas « 10.000 » < « 9.500 » en texte
    expect(matchesFilter(null, ['>', 1])).toBe(false)
    expect(matchesFilter(undefined, ['<=', 1])).toBe(false)
  })

  it('un opérateur inconnu ne laisse rien passer', () => {
    expect(matchesFilter(1, ['~', 1])).toBe(false)
  })

  it('superposer les filtres de la vue à ceux de l’utilisateur : le serveur n’en accepte qu’un par champ', () => {
    const { request, later } = layerFilters({ purpose: 'Sortie', qty: ['>', '1.000'] }, { purpose: 'Réception' })
    expect(request).toEqual({ purpose: 'Réception', qty: ['>', '1.000'] }) // celui de la vue l'emporte dans la requête…
    expect(later).toEqual({ purpose: 'Sortie' }) // …et celui de l'utilisateur s'applique ensuite aux lignes reçues
    expect(passesLater({ purpose: 'Réception' }, later)).toBe(false)
    expect(passesLater({ purpose: 'Sortie' }, later)).toBe(true)
    expect(layerFilters({ qty: 1 }, { purpose: 'x' }).later).toEqual({})
  })
})

describe('un nouveau document pré-rempli par l’adresse', () => {
  const meta = {
    ...CUSTOMER,
    fields: [
      f({ fieldname: 'title', fieldtype: 'Text' }),
      f({ fieldname: 'kind', fieldtype: 'Select', options: ['A', 'B'] }),
      f({ fieldname: 'due', fieldtype: 'Date' }),
      f({ fieldname: 'flag', fieldtype: 'Boolean' }),
      f({ fieldname: 'hours', fieldtype: 'Number' }),
      f({ fieldname: 'cost', fieldtype: 'Decimal', decimals: 2 }),
      f({ fieldname: 'secret', fieldtype: 'Text', hidden: true }),
      f({ fieldname: 'lines', fieldtype: 'Table', options: 'L' }),
    ],
  } as DocTypeMeta

  it('chaque valeur est contrôlée par le type de son champ', () => {
    expect(presetValues(meta, { title: 'Hello', kind: 'B', due: '2026-10-20', flag: 'true', hours: '2.5', cost: '12.5' })).toEqual({
      title: 'Hello', kind: 'B', due: '2026-10-20', flag: true, hours: 2.5, cost: '12.50',
    })
  })

  it('une valeur invalide, un champ inconnu, caché ou une table sont ignorés, jamais passés tels quels', () => {
    const out = presetValues(meta, { kind: 'Z', due: '20261020', flag: 'oui', hours: 'x', cost: '1.234', secret: 'x', lines: 'x', ghost: 'x', title: ['a', 'b'] })
    expect(out).toEqual({})
    expect(presetValues(meta, { due: '2026-02-30' })).toEqual({}) // une date qui n'existe pas
    expect(presetValues(meta, { hours: '' })).toEqual({})
    expect(presetValues(meta, { title: 'x'.repeat(501) })).toEqual({})
  })

  it('les défauts d’abord, l’adresse par-dessus', () => {
    const v = startValues(CUSTOMER, { type: 'Entreprise', credit_limit: '9' })
    expect(v.type).toBe('Entreprise')
    expect(v.credit_limit).toBe(9)
    expect(v.blocked).toBe(false)
    expect(startValues(CUSTOMER)).toEqual(startValues(CUSTOMER, {}))
  })
})

describe('la vue que l’appareil a choisie', () => {
  const views = (available: ViewsMeta['available'], def: ViewsMeta['default'] = 'list'): ViewsMeta => ({ available, default: def, dashboard: [] })

  it('l’adresse d’abord, puis le dernier choix, puis la vue par défaut du type', () => {
    const v = views(['list', 'report', 'kanban'], 'report')
    expect(pickView(v, 'kanban', 'report')).toBe('kanban')
    expect(pickView(v, undefined, 'kanban')).toBe('kanban')
    expect(pickView(v, undefined, null)).toBe('report')
  })

  it('une vue que le type ne propose pas (plus) retombe sur la suivante, puis sur la liste', () => {
    const v = views(['list', 'report'])
    expect(pickView(v, 'gantt', 'calendar')).toBe('list')
    expect(pickView(v, 'gantt', 'report')).toBe('report')
    expect(pickView(undefined, 'kanban', 'kanban')).toBe('list') // serveur plus ancien : la liste seule
    expect(pickView(v, 42, 7 as never)).toBe('list')
  })

  it('se souvient par type, sans jamais lever (navigation privée)', () => {
    localStorage.clear()
    expect(readView('Invoice')).toBeNull()
    writeView('Invoice', 'kanban')
    writeView('Item', 'report')
    expect([readView('Invoice'), readView('Item')]).toEqual(['kanban', 'report'])
    writeColumns('Invoice', ['customer', 'total'])
    expect(readColumns('Invoice')).toEqual(['customer', 'total'])
    localStorage.setItem('xdt.desk.columns.Bin', '{"pas":"un tableau"}')
    expect(readColumns('Bin')).toBeNull()
    localStorage.setItem('xdt.desk.columns.Bin', '[1,2]')
    expect(readColumns('Bin')).toBeNull()
    // Un stockage qui refuse tout (navigation privée) : lecture et écriture ne lèvent jamais, la valeur est simplement absente.
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('bloqué') }, setItem: () => { throw new Error('bloqué') } })
    try {
      expect(readView('Invoice')).toBeNull()
      expect(readColumns('Invoice')).toBeNull()
      expect(() => writeView('Invoice', 'list')).not.toThrow()
      expect(() => writeColumns('Invoice', ['a'])).not.toThrow()
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
