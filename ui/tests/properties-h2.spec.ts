import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { ApiError } from '@/lib/api'
import { duplicateValues, fetchDependents, fetchSource, fieldStates, mapServerErrors, toPayload } from '@/lib/form'
import { changeLines, verbOf } from '@/lib/history'
import { SERVICES } from '@/lib/services'
import type { DocRecord, DocTypeMeta, FieldMeta } from '@/lib/types'
import FormView from '@/views/FormView.vue'
import { menuItem, openMore } from './menu'

const field = (fieldname: string, fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({
  fieldname, fieldtype, required: false, hidden: false, in_list_view: false, ...extra,
})

function metaOf(name: string, fields: FieldMeta[], extra: Partial<DocTypeMeta> = {}): DocTypeMeta {
  return {
    name,
    label: name,
    fields,
    layout: [{ section: null, collapsible: false, collapsed: false, columns: [fields.map((f) => f.fieldname)] }],
    list_columns: [],
    rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'all', cancel: 'all' },
    ...extra,
  }
}

const rec = (doctype: string, name: string, data: Record<string, unknown>, extra: Partial<DocRecord> = {}): DocRecord => ({
  doctype, name, owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version: 1, docstatus: 0, amended_from: null, data, ...extra,
})

const CONTACT = metaOf('Contact', [
  field('email', 'Text', { unique: true }),
  field('badge', 'Text', { set_only_once: true }),
  field('nickname', 'Text', { no_copy: true }),
  field('company', 'Link', { options: 'Company' }),
  field('company_title', 'Text', { fetch_from: 'company.title' }),
  field('secret', 'Password'),
  field('photo', 'Image'),
  field('note', 'Text'),
])

describe('fieldStates : set_only_once, fetch_from, allow_on_submit', () => {
  it('un champ fetch_from n’est jamais saisi', () => {
    expect(fieldStates(CONTACT, {}, null).company_title?.readOnly).toBe(true)
    expect(fieldStates(CONTACT, {}, null).note?.readOnly).toBe(false)
  })

  it('set_only_once : libre à la création et tant qu’il est vide, figé une fois ENREGISTRÉ avec une valeur', () => {
    expect(fieldStates(CONTACT, {}, null).badge?.readOnly).toBe(false)
    expect(fieldStates(CONTACT, { badge: 'B1' }, { badge: null }).badge?.readOnly).toBe(false) // enregistré vide : encore libre
    expect(fieldStates(CONTACT, { badge: 'B2' }, { badge: 'B1' }).badge?.readOnly).toBe(true)
  })

  it('le champ qui nomme le document est figé comme un set_only_once', () => {
    const sku = metaOf('Sku', [field('code', 'Text', { required: true }), field('label', 'Text')], { naming: { field: 'code' } })
    expect(fieldStates(sku, { code: 'A' }, null).code?.readOnly).toBe(false)
    expect(fieldStates(sku, { code: 'A' }, { code: 'A' }).code?.readOnly).toBe(true)
    expect(fieldStates(sku, { code: 'A' }, { code: 'A' }).label?.readOnly).toBe(false)
  })

  it('soumis : tout est figé sauf allow_on_submit', () => {
    const order = metaOf('Order', [field('title', 'Text'), field('follow_up', 'Text', { allow_on_submit: true })], { submittable: true })
    const draft = fieldStates(order, {}, {}, 0)
    expect([draft.title?.readOnly, draft.follow_up?.readOnly]).toEqual([false, false])
    const submitted = fieldStates(order, {}, {}, 1)
    expect([submitted.title?.readOnly, submitted.follow_up?.readOnly]).toEqual([true, false])
  })
})

describe('duplicateValues : ce qui se recopie', () => {
  const source = rec('Contact', 'c1', {
    email: 'a@x.fr', badge: 'B1', nickname: 'Bibi', company: 'acme', company_title: 'Acme', secret: '********', photo: 'f'.repeat(32), note: 'à rappeler',
  })

  it('ne recopie ni l’unique, ni no_copy, ni le secret, ni le fichier, ni la copie fetch_from', () => {
    const copy = duplicateValues(CONTACT, source)
    expect(copy.email).toBeNull() // unique : la copie en choisirait un autre
    expect(copy.nickname).toBeNull()
    expect(copy.secret).toBeNull()
    expect(copy.photo).toBeNull()
    expect(copy.company_title).toBeNull() // le serveur la relira
    expect(copy.badge).toBe('B1')
    expect(copy.company).toBe('acme')
    expect(copy.note).toBe('à rappeler')
  })

  it('le champ de nommage ne se recopie pas, les lignes gardent leurs valeurs sauf fichiers et champs fetch_from', () => {
    const line = metaOf('Invoice Line', [field('product', 'Link', { options: 'Product' }), field('product_title', 'Text', { fetch_from: 'product.title' }), field('qty', 'Number')])
    const sku = metaOf('Sku', [field('code', 'Text', { required: true }), field('items', 'Table', { options: 'Invoice Line' })], { naming: { field: 'code' }, children: { 'Invoice Line': { name: 'Invoice Line', fields: line.fields } } })
    const copy = duplicateValues(sku, rec('Sku', 'A', { code: 'A', items: [{ product: 'p', product_title: 'Vis', qty: 2 }] }))
    expect(copy.code).toBeNull()
    expect(copy.items).toEqual([{ product: 'p', qty: 2 }].map((r) => expect.objectContaining(r)))
    expect((copy.items as Record<string, unknown>[])[0]).not.toHaveProperty('product_title')
  })
})

describe('fetch_from côté Desk', () => {
  it('retrouve les champs qui lisent un lien et le champ de la cible', () => {
    const deps = fetchDependents(CONTACT.fields, 'company')
    expect(deps.map((f) => f.fieldname)).toEqual(['company_title'])
    expect(fetchSource(deps[0]!)).toBe('title')
    expect(fetchDependents(CONTACT.fields, 'note')).toEqual([])
  })

  it('la valeur recopiée part avec le document (le serveur l’écrase, un document soumis la garde)', () => {
    expect(toPayload(CONTACT, { company: 'acme', company_title: 'Acme', note: null })).toEqual({ company: 'acme', company_title: 'Acme' })
  })
})

describe('erreurs du serveur et historique', () => {
  it('l’erreur d’un nom refusé se range sous le champ « Nom » d’un DocType à nom saisi', () => {
    const tag = metaOf('Tag', [field('color', 'Text')], { naming: 'prompt' })
    const err = new ApiError(422, 'validation_failed', 'invalide', [{ field: 'name', msg: 'Le nom ne peut contenir ni /', type: 'invalid_name' }])
    expect(mapServerErrors(err, tag).fields).toEqual({ name: 'Le nom ne peut contenir ni /' })
    expect(mapServerErrors(err, CONTACT).fields).toEqual({}) // pas de champ « name » ailleurs : message général
  })

  it('une doublon sur un champ unique est montré sous le champ, avec le message du serveur', () => {
    const err = new ApiError(422, 'validation_failed', 'invalide', [{ field: 'email', msg: 'Cette valeur est déjà utilisée : "a@x.fr"', type: 'unique' }])
    expect(mapServerErrors(err, CONTACT).fields.email).toBe('Cette valeur est déjà utilisée : "a@x.fr"')
  })

  it('un renommage s’écrit « a renommé » et son champ « name » s’appelle Nom', () => {
    expect(verbOf('rename')).toBe('a renommé ce document')
    const lines = changeLines(CONTACT, { id: 1, version: 2, action: 'rename', user: 'u', creation: '', changes: { name: { old: 'A', new: 'B' } } } as never)
    expect(lines).toEqual([{ field: 'name', label: 'Nom', old: 'A', new: 'B' }])
  })
})

// ── Le formulaire ─────────────────────────────────────────────────────────────────────────────

function setup(metas: Record<string, DocTypeMeta>, overrides: Record<string, unknown> = {}) {
  const api = {
    meta: vi.fn(async (d: string) => metas[d]),
    doctypes: vi.fn(async () => []),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    rename: vi.fn(),
    submit: vi.fn(),
    cancel: vi.fn(),
    amend: vi.fn(),
    history: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })),
    search: vi.fn(async () => []),
    list: vi.fn(async () => ({ items: [], total: 0, limit: 1, offset: 0 })),
    ...overrides,
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/app/:doctype/new', name: 'new', component: FormView, props: (r) => ({ doctype: r.params.doctype, name: null }) },
      { path: '/app/:doctype/:name', name: 'form', component: FormView, props: true },
      { path: '/app/:doctype', name: 'list', component: { render: () => h('div', 'liste') }, props: true },
    ],
  })
  const Root = defineComponent({ render: () => h(RouterView) })
  const w = mount(Root, {
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } },
    },
  })
  return { w, api, router }
}

const TAG = metaOf('Tag', [field('color', 'Text')], { naming: 'prompt' })
const PLACE = metaOf('Place', [field('label', 'Text')], { allow_rename: true })
const ORDER = metaOf('Order', [field('title', 'Text'), field('follow_up', 'Text', { allow_on_submit: true })], { submittable: true, track_changes: true })
const NOTE = metaOf('Note', [field('ref_type', 'Select', { options: ['Place', 'Gadget'] }), field('ref_name', 'DynamicLink', { options: 'ref_type' })])
const INVOICE = metaOf('Invoice', [field('company', 'Link', { options: 'Company' }), field('company_title', 'Text', { fetch_from: 'company.title' })])
const COMPANY = metaOf('Company', [field('title', 'Text')], { title_field: 'title' })

describe('FormView : nom saisi (naming: prompt)', () => {
  it('demande le nom, le refuse vide, puis l’envoie avec le document', async () => {
    const { w, api, router } = setup({ Tag: TAG }, { create: vi.fn(async (_d: string, _data: unknown, name: string) => rec('Tag', name, { color: 'red' })) })
    await router.push('/app/Tag/new')
    await flushPromises()
    expect(w.find('[data-testid="new-name"]').exists()).toBe(true)
    await w.find('[data-field="color"] input').setValue('red')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(api.create).not.toHaveBeenCalled()
    expect(w.find('[data-testid="error-name"]').text()).toBe('Champ obligatoire')
    await w.find('[data-testid="new-name"]').setValue('  urgent ')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(api.create).toHaveBeenCalledWith('Tag', { color: 'red' }, 'urgent')
  })

  it('un autre nommage n’affiche pas le champ', async () => {
    const { w, router } = setup({ Place: PLACE })
    await router.push('/app/Place/new')
    await flushPromises()
    expect(w.find('[data-testid="new-name"]').exists()).toBe(false)
  })
})

describe('FormView : renommer et dupliquer', () => {
  it('Renommer ouvre une boîte, appelle le serveur puis suit le nouveau nom', async () => {
    const { w, api, router } = setup(
      { Place: PLACE },
      {
        get: vi.fn(async (_d: string, name: string) => rec('Place', name, { label: 'Paris' })),
        rename: vi.fn(async (_d: string, _n: string, to: string) => rec('Place', to, { label: 'Paris' }, { version: 2 })),
      },
    )
    await router.push('/app/Place/p1')
    await flushPromises()
    await openMore(w)
    menuItem('rename')!.click()
    await flushPromises()
    const input = document.body.querySelector('[data-testid="rename-input"]') as HTMLInputElement
    expect(input.value).toBe('p1')
    input.value = 'Paris 8e'
    input.dispatchEvent(new Event('input'))
    ;(document.body.querySelector('[data-testid="rename-confirm"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(api.rename).toHaveBeenCalledWith('Place', 'p1', 'Paris 8e')
    expect(router.currentRoute.value.params.name).toBe('Paris 8e')
  })

  it('un nom déjà pris est dit dans la boîte, qui reste ouverte', async () => {
    const { w, router } = setup(
      { Place: PLACE },
      {
        get: vi.fn(async (_d: string, name: string) => rec('Place', name, { label: 'Paris' })),
        rename: vi.fn(async () => {
          throw new ApiError(409, 'already_exists', 'Place existe déjà', [])
        }),
      },
    )
    await router.push('/app/Place/p1')
    await flushPromises()
    await openMore(w)
    menuItem('rename')!.click()
    await flushPromises()
    const input = document.body.querySelector('[data-testid="rename-input"]') as HTMLInputElement
    input.value = 'autre'
    input.dispatchEvent(new Event('input'))
    ;(document.body.querySelector('[data-testid="rename-confirm"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(document.body.querySelector('[data-testid="rename-error"]')?.textContent).toContain('existe déjà')
  })

  it('Renommer n’est offert qu’aux types qui l’autorisent', async () => {
    const { w, router } = setup({ Tag: { ...TAG, naming: 'hash' } }, { get: vi.fn(async (_d: string, name: string) => rec('Tag', name, { color: 'red' })) })
    await router.push('/app/Tag/t1')
    await flushPromises()
    await openMore(w)
    expect(menuItem('rename')).toBeNull()
    expect(menuItem('duplicate')).not.toBeNull()
  })

  it('Dupliquer ouvre un formulaire neuf pré-rempli par la copie, sans l’unique ni no_copy', async () => {
    const { w, router } = setup({ Contact: CONTACT, Company: COMPANY }, { get: vi.fn(async (_d: string, name: string) => rec('Contact', name, { email: 'a@x.fr', note: 'à rappeler', nickname: 'Bibi' })) })
    await router.push('/app/Contact/c1')
    await flushPromises()
    await openMore(w)
    menuItem('duplicate')!.click()
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('new')
    expect(router.currentRoute.value.query.copy).toBe('c1')
    expect((w.find('[data-field="note"] input').element as HTMLInputElement).value).toBe('à rappeler')
    expect((w.find('[data-field="email"] input').element as HTMLInputElement).value).toBe('')
    expect((w.find('[data-field="nickname"] input').element as HTMLInputElement).value).toBe('')
    expect(w.find('[data-testid="form-copy"]').text()).toContain('c1')
  })
})

describe('FormView : ce qui entoure un document enregistré', () => {
  it('les connexions ne font pas taire l’activité : les deux panneaux sont là', async () => {
    const connections = vi.fn(async () => [{ doctype: 'Visit', label: 'Visite', field: 'place', field_label: 'Lieu', table: null, count: 2 }])
    const collab = vi.fn(async () => ({ comments: [], assignments: [], attachments: [], can_assign: true, can_attach: true, me: 'u' }))
    const { w, router } = setup({ Place: PLACE }, { get: vi.fn(async (_d: string, name: string) => rec('Place', name, { label: 'Paris' })), connections, collab })
    await router.push('/app/Place/p1')
    await flushPromises()
    expect(w.find('[data-testid="connections"]').exists()).toBe(true)
    expect(w.find('[data-testid="activity"]').exists()).toBe(true) // le fil de commentaires et de modifications
    expect(w.find('[data-testid="collab"]').exists()).toBe(true)
  })

  it('un document neuf n’a ni connexions ni activité', async () => {
    const { w, router } = setup({ Place: PLACE })
    await router.push('/app/Place/new')
    await flushPromises()
    expect(w.find('[data-testid="connections"]').exists()).toBe(false)
    expect(w.find('[data-testid="collab"]').exists()).toBe(false)
  })
})

describe('FormView : document soumis et champs allow_on_submit', () => {
  const submitted = (data: Record<string, unknown> = { title: 'O' }) => rec('Order', 'o1', data, { docstatus: 1, version: 3 })

  it('seul le champ autorisé reste saisissable, et l’enregistrement part avec tout le document', async () => {
    const update = vi.fn(async (_d: string, _n: string, version: number, data: Record<string, unknown>) => rec('Order', 'o1', data, { docstatus: 1, version: version + 1 }))
    const { w, router } = setup({ Order: ORDER }, { get: vi.fn(async () => submitted()), update })
    await router.push('/app/Order/o1')
    await flushPromises()
    expect(w.find('[data-testid="form-frozen"]').text()).toContain('seuls les champs prévus')
    expect(w.find('[data-field="title"] input').exists()).toBe(false) // figé : une valeur, pas un champ
    expect(w.find('[data-field="follow_up"] input').exists()).toBe(true)
    await w.find('[data-field="follow_up"] input').setValue('rappeler lundi')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('Order', 'o1', 3, { title: 'O', follow_up: 'rappeler lundi' })
  })

  it('un document soumis sans champ autorisé reste entièrement figé', async () => {
    const plain = metaOf('Order', [field('title', 'Text')], { submittable: true })
    const { w, router } = setup({ Order: plain }, { get: vi.fn(async () => submitted()) })
    await router.push('/app/Order/o1')
    await flushPromises()
    expect(w.find('[data-testid="save"]').exists()).toBe(false)
    expect(w.find('[data-testid="form-frozen"]').text()).toContain('figé')
  })
})

describe('FormView : DynamicLink', () => {
  it('attend le choix du type, cherche dans CE type, et efface la valeur quand le type change', async () => {
    const search = vi.fn(async () => [{ name: 'p1', title: 'Paris' }])
    const { w, router, api } = setup({ Note: NOTE, Place: PLACE }, { search, get: vi.fn(async () => rec('Note', 'n1', { ref_type: 'Place', ref_name: 'p1' })) })
    await router.push('/app/Note/new')
    await flushPromises()
    expect(w.find('[data-testid="dynamic-first"]').exists()).toBe(true) // pas de type : champ désactivé
    expect(api.search).not.toHaveBeenCalled()
    await router.push('/app/Note/n1')
    await flushPromises()
    expect(w.find('[data-testid="dynamic-first"]').exists()).toBe(false)
    expect(w.find('[data-field="ref_name"] input').exists()).toBe(true)
  })
})

describe('FormView : aperçu de fetch_from', () => {
  it('choisir le lien montre la valeur copiée avant l’enregistrement, la vider efface la copie', async () => {
    const list = vi.fn(async (doctype: string) => ({
      items: doctype === 'Company' ? [rec('Company', 'acme', { title: 'Acme SA' })] : [],
      total: 1, limit: 100, offset: 0,
    }))
    const { w, router } = setup({ Invoice: INVOICE, Company: COMPANY }, { list })
    await router.push('/app/Invoice/new')
    await flushPromises()
    const form = w.findComponent(FormView)
    // Le lien est choisi comme le ferait l'autocomplétion : par la valeur du champ.
    const link = form.findComponent({ name: 'LinkField' })
    link.vm.$emit('update:modelValue', 'acme')
    await flushPromises()
    expect(w.find('[data-testid="value-company_title"]').text()).toBe('Acme SA')
    link.vm.$emit('update:modelValue', null)
    await flushPromises()
    expect(w.find('[data-testid="value-company_title"]').text()).toBe('—')
  })
})
