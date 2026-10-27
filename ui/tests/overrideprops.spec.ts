import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import Textarea from 'primevue/textarea'
import { afterEach, describe, expect, it } from 'vitest'
import OverrideEditor from '@/components/OverrideEditor.vue'
import { addedFields, customName, withAddedField, withoutAddedField } from '@/lib/overrideprops'
import type { ExprNode } from '@/lib/expr'
import { fieldStates, validate } from '@/lib/form'
import { propRows, withProp } from '@/lib/overrideprops'
import type { DocTypeMeta, FieldBase, FieldMeta, FormOverride } from '@/lib/types'

const field = (fieldname: string, fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({
  fieldname, label: fieldname.toUpperCase(), fieldtype, required: false, hidden: false, in_list_view: false, ...extra,
})

// Ce que `/meta` envoie : l'habillage enregistré (`SAVED`) y est déjà appliqué (`prio` obligatoire par lui, `title` par le YAML).
const FIELDS: FieldMeta[] = [
  field('title', 'Text', { required: true }),
  field('prio', 'Number', { required: true }),
  field('ref', 'Text', { read_only: true }),
  field('due', 'Date'),
  field('status', 'Select', { options: ['Ouvert', 'Fermé'], default: 'Ouvert' }),
  field('note', 'Text', { hidden: true }),
  field('secret', 'Password'),
  field('photo', 'Image'),
  field('city', 'Text', { fetch_from: 'client.city' }),
  field('lines', 'Table', { options: 'Line' }),
  field('code', 'Text', { unique: true }),
  field('sku', 'Text'),
]
const SAVED: FormOverride = { layout: [{ section: null, collapsible: false, collapsed: false, columns: [['title']] }], fields: { prio: { mandatory: true, label: 'Priorité' } } }
// Ce que `GET /override/form` dit de la définition, habillage à part.
const DEFINITION: Record<string, FieldBase> = { title: { required: true }, ref: { read_only: true }, status: { default: 'Ouvert' }, note: { hidden: true } }

const row = (rows: ReturnType<typeof propRows>, name: string) => rows.find((r) => r.field.fieldname === name)!

describe('propriétés d’écriture : la logique pure', () => {
  it('ce que la définition impose vient du serveur : une retouche redondante ne passe pas pour celle de l’habillage', () => {
    // `title` est obligatoire dans le YAML ET dans l'habillage : déduit de `/meta`, il passerait pour une retouche qu'on peut retirer.
    const redundant: FormOverride = { fields: { title: { mandatory: true }, prio: { mandatory: true } } }
    expect(row(propRows(FIELDS, redundant, { applied: redundant }), 'title').can.mandatory).toBe(true) // la déduction se trompe...
    const exact = propRows(FIELDS, redundant, { definition: DEFINITION, applied: redundant })
    expect(row(exact, 'title').base.required).toBe(true) // ... le serveur, non : la case reste grisée
    expect(row(exact, 'prio').base.required).toBe(false)
    expect(row(exact, 'status').base.default).toBe('Ouvert')
  })

  it('sans la définition, elle se déduit de /meta moins l’habillage enregistré', () => {
    const rows = propRows(FIELDS, SAVED, { applied: SAVED })
    expect(row(rows, 'title').base.required).toBe(true) // le YAML : la case reste cochée et grisée
    expect(row(rows, 'title').can.mandatory).toBe(false)
    expect(row(rows, 'prio').base.required).toBe(false) // l'habillage : on peut la décocher
    expect(row(rows, 'prio').mandatory && row(rows, 'prio').can.mandatory).toBe(true)
    // Décochée dans l'édition en cours, `prio` redevient facultatif ; la définition, elle, ne bouge pas.
    const editing = withProp(SAVED, 'prio', 'mandatory', false)
    expect(row(propRows(FIELDS, editing, { applied: SAVED }), 'prio').mandatory).toBe(false)
    expect(row(propRows(FIELDS, editing, { applied: SAVED }), 'prio').base.required).toBe(false)
  })

  it('grise ce que le serveur refuserait', () => {
    const rows = propRows(FIELDS, SAVED, { definition: DEFINITION, naming: 'sku' })
    expect(row(rows, 'note').can.mandatory).toBe(false) // caché : personne ne pourrait le remplir
    expect(row(rows, 'ref').can.mandatory).toBe(false) // en lecture seule, idem
    expect(row(rows, 'ref').can.read_only).toBe(false) // déjà en lecture seule par la définition
    // Ni table, ni secret, ni fichier, ni `fetch_from` : le serveur les écrit à sa façon, aucune retouche d'écriture.
    for (const name of ['secret', 'photo', 'city', 'lines']) {
      const r = row(rows, name)
      expect([name, r.can.mandatory, r.can.read_only, r.can.default]).toEqual([name, false, false, false])
    }
    // Une valeur unique, le champ qui nomme le document : pas de défaut (chaque création recevrait la même).
    expect(row(rows, 'code').can.default).toBe(false)
    expect(row(rows, 'sku').can.default).toBe(false)
    expect(row(rows, 'due').can.default).toBe(true)
    // Obligatoire ET lecture seule (ou masqué) : l'un grise l'autre.
    const both = withProp(SAVED, 'due', 'read_only', true)
    expect(row(propRows(FIELDS, both, { definition: DEFINITION }), 'due').can.mandatory).toBe(false)
    const required = withProp(SAVED, 'due', 'mandatory', true)
    expect(row(propRows(FIELDS, required, { definition: DEFINITION }), 'due').can.read_only).toBe(false)
    expect(row(propRows(FIELDS, required, { definition: DEFINITION }), 'due').can.hidden).toBe(false)
  })

  it('un champ obligatoire du YAML ne se verrouille qu’avec une valeur par défaut, et une retouche posée se retire toujours', () => {
    expect(row(propRows(FIELDS, SAVED, { definition: DEFINITION }), 'title').can.read_only).toBe(false)
    const withDefault = withProp(SAVED, 'title', 'default', 'Sans titre')
    expect(row(propRows(FIELDS, withDefault, { definition: DEFINITION }), 'title').can.read_only).toBe(true)
    // Verrouillé puis privé de son défaut : le serveur refusera, mais la case reste cliquable pour défaire la retouche.
    const stuck = withProp(withProp(withDefault, 'title', 'read_only', true), 'title', 'default', undefined)
    expect(row(propRows(FIELDS, stuck, { definition: DEFINITION }), 'title').can.read_only).toBe(true)
  })

  it('obligatoire, lecture seule et défaut : réservés à qui écrit TOUS les documents ; masqué reste permis', () => {
    const dressed = withProp(SAVED, 'due', 'default', 'today')
    const rows = propRows(FIELDS, dressed, { definition: DEFINITION, rules: false })
    const due = row(rows, 'due')
    expect([due.can.mandatory, due.can.read_only, due.can.default, due.can.hidden]).toEqual([false, false, false, true])
    expect(due.why).toEqual({ mandatory: 'reserved', read_only: 'reserved', default: 'reserved' })
    expect(due.default).toBe('today') // le défaut de l'organisation se montre, il ne se change pas
    // Une règle posée par l'organisation ne se retire pas non plus (le serveur refuserait : `form_rules_reserved`).
    expect([row(rows, 'prio').mandatory, row(rows, 'prio').can.mandatory]).toEqual([true, false])
    // Ce qui ne tient pas au droit garde sa raison : la définition, un champ que le serveur écrit à sa façon.
    expect(row(rows, 'title').why.mandatory).toBe('definition')
    expect(row(rows, 'photo').why.read_only).toBe('opaque')
    expect(row(rows, 'code').why.default).toBeUndefined() // une valeur unique n'a jamais de défaut, réservé ou non
    expect(row(propRows(FIELDS, dressed, { definition: DEFINITION, rules: true }), 'due').can.default).toBe(true)
  })

  it('une condition du YAML : pas de lecture seule sur ce qu’elle rend obligatoire, pas d’obligation sur ce qu’elle verrouille', () => {
    const when: ExprNode = { t: 'cmp', op: '>', l: { t: 'field', name: 'prio' }, r: { t: 'lit', v: 10 } }
    const fields = [field('approver', 'Text', { mandatory_depends_on: when }), field('closed_by', 'Text', { read_only_depends_on: when })]
    const rows = propRows(fields, {}, { definition: {} })
    expect([row(rows, 'approver').can.read_only, row(rows, 'approver').why.read_only]).toEqual([false, 'condition'])
    expect([row(rows, 'closed_by').can.mandatory, row(rows, 'closed_by').why.mandatory]).toEqual([false, 'condition'])
    expect([row(rows, 'approver').can.mandatory, row(rows, 'closed_by').can.read_only]).toEqual([true, true])
    // Enregistrée avant la condition, la retouche se retire toujours.
    const stale = propRows(fields, { fields: { approver: { read_only: true }, closed_by: { mandatory: true } } }, { definition: {} })
    expect([row(stale, 'approver').can.read_only, row(stale, 'closed_by').can.mandatory]).toEqual([true, true])
  })

  it('un champ que le YAML exige ne se masque qu’avec une valeur par défaut (le Desk la pré-remplit et l’envoie)', () => {
    const when: ExprNode = { t: 'lit', v: true }
    const fields = [...FIELDS, field('approver', 'Text', { mandatory_depends_on: when })]
    const rows = propRows(fields, SAVED, { definition: DEFINITION })
    expect([row(rows, 'title').can.hidden, row(rows, 'title').why.hidden]).toEqual([false, 'unfilled'])
    expect(row(rows, 'approver').can.hidden).toBe(false)
    expect(row(rows, 'due').can.hidden).toBe(true) // facultatif
    const withDefault = withProp(withProp(SAVED, 'title', 'default', 'Sans titre'), 'approver', 'default', 'Zoé')
    expect([row(propRows(fields, withDefault, { definition: DEFINITION }), 'title').can.hidden, row(propRows(fields, withDefault, { definition: DEFINITION }), 'approver').can.hidden]).toEqual([true, true])
    const yamlDefault = propRows([field('kind', 'Text', { default: 'A' })], {}, { definition: { kind: { required: true, default: 'A' } } })
    expect(yamlDefault[0]!.can.hidden).toBe(true)
    // Masqué avant que le YAML ne l'exige : la case se décoche toujours.
    expect(row(propRows(fields, withProp(SAVED, 'title', 'hidden', true), { definition: DEFINITION }), 'title').can.hidden).toBe(true)
  })

  it('change une propriété sans toucher au reste, et retire ce qui ne dit plus rien', () => {
    const one = withProp(SAVED, 'due', 'default', 'today')
    expect(one.layout).toEqual(SAVED.layout)
    expect(one.fields).toEqual({ prio: { mandatory: true, label: 'Priorité' }, due: { default: 'today' } })
    expect(withProp(one, 'due', 'default', null).fields).toEqual({ prio: { mandatory: true, label: 'Priorité' } })
    expect(withProp(SAVED, 'prio', 'mandatory', false).fields).toEqual({ prio: { label: 'Priorité' } }) // le libellé reste
    expect('fields' in withProp({ fields: { due: { read_only: true } } }, 'due', 'read_only', false)).toBe(false)
    expect(withProp({}, 'note', 'default', '').fields).toBeUndefined() // un champ de défaut vidé n'est pas un défaut...
    expect(withProp({}, 'flag', 'default', 0).fields).toEqual({ flag: { default: 0 } }) // ... mais 0 en est un
    expect(withProp({}, 'flag', 'default', false).fields).toEqual({ flag: { default: false } }) // ... et `false` (une case décochée par défaut)
  })
})

describe('la fiche suit ce que /meta dit de l’habillage', () => {
  const ORDER: DocTypeMeta = {
    name: 'Order', label: 'Order', submittable: true, list_columns: [],
    fields: [field('title', 'Text', { required: true }), field('follow_up', 'Text', { allow_on_submit: true, required: true })],
    layout: [{ section: null, collapsible: false, collapsed: false, columns: [['title', 'follow_up']] }],
    rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'all', cancel: 'all' },
  }

  it('soumis, un champ figé n’est plus exigé (il ne se remplirait plus) ; un champ encore modifiable, si', () => {
    const states = fieldStates(ORDER, {}, {}, 1)
    expect([states.title?.required, states.follow_up?.required]).toEqual([false, true])
    expect(validate(ORDER, {}, states)).toEqual({ follow_up: expect.any(String) })
    expect(fieldStates(ORDER, {}, null, 0).title?.required).toBe(true) // brouillon : exigé
  })
})

const mounted: VueWrapper[] = []
afterEach(() => {
  for (const w of mounted.splice(0)) w.unmount()
  document.body.innerHTML = ''
})

const cell = (testid: string) => document.body.querySelector(`[data-testid="${testid}"]`)

// Les écrans qui RÉGLAIENT ces propriétés (la boîte « Habiller » d'un formulaire) sont devenus le Form Builder : leurs tests vivent dans
// `formbuilderview.spec.ts`. Reste l'éditeur de JSON, qui sert à l'habillage d'un espace de travail.
describe('l’éditeur d’habillage en JSON (espaces de travail)', () => {
  it('montre l’habillage en JSON et renvoie ce qu’on a tapé ; un JSON illisible est dit, rien n’est envoyé', async () => {
    const w = mount(OverrideEditor, {
      props: { visible: true, title: 'Espace', initial: { label: 'Stock' } },
      attachTo: document.body,
      global: { plugins: [PrimeVue] },
    })
    mounted.push(w)
    await flushPromises()
    expect(JSON.parse(String(w.findComponent(Textarea).props('modelValue')))).toEqual({ label: 'Stock' })
    w.findComponent(Textarea).vm.$emit('update:modelValue', '{ "label": ')
    await flushPromises()
    ;(cell('override-save') as HTMLButtonElement).click()
    await flushPromises()
    expect(cell('override-parse-error')).not.toBeNull()
    expect(w.emitted('save')).toBeUndefined()
    w.findComponent(Textarea).vm.$emit('update:modelValue', '{ "label": "Magasin" }')
    await flushPromises()
    ;(cell('override-save') as HTMLButtonElement).click()
    await flushPromises()
    expect(w.emitted('save')?.[0]?.[0]).toEqual({ label: 'Magasin' })
  })
})

describe('customName : le nom qu’exige le serveur', () => {
  it('préfixe, met en minuscules et remplace ce qui n’est pas permis', () => {
    expect(customName('Segment client')).toBe('custom_segment_client')
    expect(customName('Référence N°2')).toBe('custom_reference_n_2')
    expect(customName('custom_deja')).toBe('custom_deja') // on ne préfixe pas deux fois
    expect(customName('   ')).toBe('')
  })
})

describe('withAddedField / withoutAddedField', () => {
  it('écrit un champ propre, sans les clés vides', () => {
    const out = withAddedField({}, { fieldname: 'custom_a', fieldtype: 'Text', label: '  ', required: false })
    expect(addedFields(out)).toEqual([{ fieldname: 'custom_a', fieldtype: 'Text' }])
  })

  it('remplace un champ du même nom au lieu de le dupliquer, et sait le retirer', () => {
    let cfg = withAddedField({}, { fieldname: 'custom_a', fieldtype: 'Text', label: 'A' })
    cfg = withAddedField(cfg, { fieldname: 'custom_a', fieldtype: 'Int', label: 'A' })
    expect(addedFields(cfg)).toHaveLength(1)
    expect(addedFields(cfg)[0]!.fieldtype).toBe('Int')
    expect('added' in withoutAddedField(cfg, 'custom_a')).toBe(false)
  })
})
