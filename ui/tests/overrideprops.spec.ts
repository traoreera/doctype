import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Checkbox from 'primevue/checkbox'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import Textarea from 'primevue/textarea'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import FieldInput from '@/components/FieldInput.vue'
import OverrideEditor from '@/components/OverrideEditor.vue'
import { fieldStates, validate } from '@/lib/form'
import { propRows, withProp } from '@/lib/overrideprops'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, FieldBase, FieldMeta, FormOverride } from '@/lib/types'
import FormView from '@/views/FormView.vue'
import { menuItem, openMore } from './menu'

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

async function open(initial: FormOverride = SAVED, definition: Record<string, FieldBase> | null = DEFINITION) {
  const w = mount(OverrideEditor, {
    props: { visible: true, title: 'Disposition', initial, fields: FIELDS, definition, naming: 'sku', doctype: 'Ticket' },
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {} } },
    },
  })
  mounted.push(w)
  await flushPromises()
  return w
}
const json = (w: VueWrapper) => JSON.parse(String(w.findComponent(Textarea).props('modelValue')))
const box = (w: VueWrapper, testid: string) => w.findAllComponents(Checkbox).find((c) => c.attributes('data-testid') === testid)!
const cell = (testid: string) => document.body.querySelector(`[data-testid="${testid}"]`)

describe('l’éditeur d’habillage d’un formulaire', () => {
  it('une case cochée écrit la propriété dans le JSON, qui part tel quel à l’enregistrement', async () => {
    const w = await open()
    box(w, 'prop-read_only-due').vm.$emit('update:modelValue', true)
    await flushPromises()
    expect(json(w).fields.due).toEqual({ read_only: true })
    ;(cell('override-save') as HTMLButtonElement).click()
    await flushPromises()
    expect(w.emitted('save')?.[0]?.[0]).toEqual({ ...SAVED, fields: { ...SAVED.fields, due: { read_only: true } } })
  })

  it('ce que la définition impose est coché et grisé ; ce que l’habillage a posé se décoche', async () => {
    const w = await open()
    expect(box(w, 'prop-mandatory-title').props('modelValue')).toBe(true)
    expect(box(w, 'prop-mandatory-title').props('disabled')).toBe(true)
    expect(box(w, 'prop-mandatory-prio').props('disabled')).toBe(false)
    box(w, 'prop-mandatory-prio').vm.$emit('update:modelValue', false)
    await flushPromises()
    expect(json(w).fields.prio).toEqual({ label: 'Priorité' })
    expect(box(w, 'prop-mandatory-note').props('disabled')).toBe(true) // masqué par la définition
  })

  it('un champ que le serveur écrit à sa façon dit pourquoi ses cases sont grisées', async () => {
    const w = await open()
    expect(box(w, 'prop-mandatory-lines').props('disabled')).toBe(true)
    expect(box(w, 'prop-read_only-secret').props('disabled')).toBe(true)
    expect(cell('prop-mandatory-photo')?.closest('td')?.getAttribute('title')).toContain('à sa façon')
    expect(cell('prop-mandatory-title')?.closest('td')?.getAttribute('title')).toContain('définition')
  })

  it('la valeur par défaut se saisit avec le champ du formulaire, « Aujourd’hui » pose le défaut vivant, la croix le retire', async () => {
    const w = await open()
    const prio = w.findAllComponents(FieldInput).find((c) => (c.props('field') as FieldMeta).fieldname === 'prio')!
    prio.vm.$emit('update:modelValue', 3)
    await flushPromises()
    expect(json(w).fields.prio).toEqual({ mandatory: true, label: 'Priorité', default: 3 })
    ;(cell('prop-live-due') as HTMLButtonElement).click()
    await flushPromises()
    expect(json(w).fields.due).toEqual({ default: 'today' })
    expect(cell('prop-default-due')?.textContent).toContain('Aujourd’hui')
    ;(cell('prop-clear-due') as HTMLButtonElement).click()
    await flushPromises()
    expect(json(w).fields.due).toBeUndefined()
    // Ni table, ni secret, ni fichier, ni fetch_from, ni valeur unique, ni le champ qui nomme : pas de saisie de défaut du tout.
    for (const name of ['lines', 'secret', 'photo', 'city', 'code', 'sku']) {
      expect(document.body.querySelector(`[data-testid="prop-default-${name}"] input`)).toBeNull()
    }
  })

  it('le défaut de la définition se lit tant que l’habillage n’en pose pas', async () => {
    const w = await open()
    expect(cell('prop-default-status')?.querySelector('[data-testid="prop-base-default"]')?.textContent).toContain('Ouvert')
    const status = w.findAllComponents(FieldInput).find((c) => (c.props('field') as FieldMeta).fieldname === 'status')!
    status.vm.$emit('update:modelValue', 'Fermé')
    await flushPromises()
    expect(json(w).fields.status).toEqual({ default: 'Fermé' })
    expect(cell('prop-default-status')?.querySelector('[data-testid="prop-base-default"]')).toBeNull()
  })

  it('un JSON illisible grise le tableau sans rien perdre de ce qu’on a tapé', async () => {
    const w = await open()
    w.findComponent(Textarea).vm.$emit('update:modelValue', '{ "fields": ')
    await flushPromises()
    expect(cell('override-props-blocked')).not.toBeNull()
    expect(box(w, 'prop-read_only-due').props('disabled')).toBe(true)
    expect(w.findComponent(Textarea).props('modelValue')).toBe('{ "fields": ')
  })

  it('sans champs (un espace de travail), il reste un simple éditeur de JSON', async () => {
    const w = mount(OverrideEditor, {
      props: { visible: true, title: 'Espace', initial: { label: 'Stock' } },
      attachTo: document.body,
      global: { plugins: [PrimeVue] },
    })
    mounted.push(w)
    await flushPromises()
    expect(cell('override-props')).toBeNull()
    expect(json(w)).toEqual({ label: 'Stock' })
  })
})

describe('la fiche ouvre l’éditeur avec ce que le serveur dit de la définition', () => {
  const SKU: DocTypeMeta = {
    name: 'Sku', label: 'Sku', naming: { field: 'code' }, list_columns: [],
    fields: [field('code', 'Text', { required: true }), field('label', 'Text', { required: true })],
    layout: [{ section: null, collapsible: false, collapsed: false, columns: [['code', 'label']] }],
    rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
  }

  it('« Disposition » passe la définition et le champ qui nomme le document à l’éditeur', async () => {
    const config: FormOverride = { fields: { label: { mandatory: true } } }
    const api = {
      meta: vi.fn(async () => SKU),
      doctypes: vi.fn(async () => []),
      get: vi.fn(async () => ({ doctype: 'Sku', name: 'A', owner: 'u', creation: '', modified: '', version: 1, docstatus: 0, amended_from: null, data: { code: 'A', label: 'a' } })),
      history: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })),
      list: vi.fn(async () => ({ items: [], total: 0, limit: 1, offset: 0 })),
      formOverride: vi.fn(async () => ({ doctype: 'Sku', config, definition: { code: { required: true }, label: { required: true } } })),
    }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/app/:doctype/:name', name: 'form', component: FormView, props: true }],
    })
    const w = mount(defineComponent({ render: () => h(RouterView) }), {
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } },
      },
    })
    mounted.push(w)
    await router.push('/app/Sku/A')
    await flushPromises()
    await openMore(w)
    menuItem('form-layout-edit')!.click()
    await flushPromises()
    const editor = w.findComponent(OverrideEditor)
    expect(editor.props('definition')).toEqual({ code: { required: true }, label: { required: true } })
    expect(editor.props('naming')).toBe('code')
    // `label` : obligatoire par le YAML ET par l'habillage. La définition du serveur garde sa case grisée (la déduction la croirait retirable).
    expect(box(w, 'prop-mandatory-label').props('disabled')).toBe(true)
    expect(document.body.querySelector('[data-testid="prop-default-code"] input')).toBeNull() // le champ qui nomme : pas de défaut
  })
})
