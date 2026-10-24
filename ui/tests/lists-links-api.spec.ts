import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import AutoComplete from 'primevue/autocomplete'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import BulkActions from '@/components/BulkActions.vue'
import ConnectionsPanel from '@/components/ConnectionsPanel.vue'
import FieldInput from '@/components/FieldInput.vue'
import LinkField from '@/components/LinkField.vue'
import LinkPreview from '@/components/LinkPreview.vue'
import ListSidebar from '@/components/ListSidebar.vue'
import { endpoints, fieldDocs, sampleData, sampleValue } from '@/lib/apidocs'
import { quickEntryFields } from '@/lib/form'
import { SERVICES } from '@/lib/services'
import { captureToasts } from './views-fixtures'
import type { DocRecord, DocTypeMeta, FieldMeta } from '@/lib/types'
import ApiDocsView from '@/views/ApiDocsView.vue'

const asked = vi.hoisted(() => [] as { accept?: () => unknown | Promise<unknown> }[])
vi.mock('primevue/useconfirm', async (original) => ({
  ...(await original<object>()),
  useConfirm: () => ({ require: (o: { accept?: () => unknown }) => asked.push(o) }),
}))

const field = (fieldname: string, fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({
  fieldname, fieldtype, required: false, hidden: false, in_list_view: false, ...extra,
})
const RIGHTS = { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' } as DocTypeMeta['rights']

function metaOf(name: string, fields: FieldMeta[], extra: Partial<DocTypeMeta> = {}): DocTypeMeta {
  return {
    name, label: name, fields,
    layout: [{ section: null, collapsible: false, collapsed: false, columns: [fields.map((f) => f.fieldname)] }],
    list_columns: fields.filter((f) => f.in_list_view).map((f) => f.fieldname),
    rights: RIGHTS,
    ...extra,
  }
}
const rec = (doctype: string, name: string, data: Record<string, unknown>): DocRecord => ({
  doctype, name, owner: 'u', creation: '', modified: '', version: 1, docstatus: 0, amended_from: null, data,
})

const TASK = metaOf('Task', [
  field('title', 'Text', { required: true, in_list_view: true }),
  field('status', 'Select', { options: ['Ouverte', 'Fermée'], in_list_view: true }),
  field('project', 'Link', { options: 'Project', in_list_view: true }),
  field('notes', 'LongText'),
  field('secret', 'Password'),
  field('uid', 'Text', { unique: true }),
  field('ro', 'Text', { read_only: true }),
], { title_field: 'title' })
const PROJECT = metaOf('Project', [field('title', 'Text', { required: true })], { title_field: 'title' })

const stub = { render: () => null }
const appRouter = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/app/:doctype', name: 'list', component: stub },
      { path: '/app/:doctype/new', name: 'new', component: stub },
      { path: '/app/:doctype/:name', name: 'form', component: stub },
      { path: '/:rest(.*)*', component: stub },
    ],
  })

function mountWith<T>(component: T, props: Record<string, unknown>, api: Record<string, unknown> = {}, router = appRouter()) {
  const w = mount(component as never, {
    props: props as never,
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/xcore-doctype', loginUrl: null }, api: { doctypes: async () => [], search: async () => [], ...api } } },
    },
  })
  return { w, router }
}

afterEach(() => {
  document.body.innerHTML = ''
  asked.length = 0
})

describe('actions en lot (BulkActions)', () => {
  const toasts = captureToasts()
  const selection = [rec('Task', 'a', { title: 'A' }), rec('Task', 'b', { title: 'B' })]

  it('propose modifier et assigner à qui peut écrire, supprimer à qui peut supprimer, soumettre seulement d’un type soumettable', async () => {
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection })
    expect(w.find('[data-testid="bulk-actions"]').text()).toContain('2')
    expect(w.find('[data-testid="bulk-delete"]').exists()).toBe(true)
    const reader = mountWith(BulkActions, { doctype: 'Task', meta: { ...TASK, rights: { ...RIGHTS, write: 'none', delete: 'none' } }, selection })
    expect(reader.w.find('[data-testid="bulk-actions"]').exists()).toBe(false)
    expect(reader.w.find('[data-testid="bulk-delete"]').exists()).toBe(false)
  })

  it('supprimer demande confirmation puis envoie UNE demande au serveur', async () => {
    const bulk = vi.fn(async () => ({ results: [{ name: 'a', ok: true }, { name: 'b', ok: true }], done: 2, failed: 0 }))
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection }, { bulk })
    await w.find('[data-testid="bulk-delete"]').trigger('click')
    expect(asked).toHaveLength(1)
    await asked[0]!.accept!()
    await flushPromises()
    expect(bulk).toHaveBeenCalledWith('Task', 'delete', ['a', 'b'], undefined)
    expect(w.emitted('done')).toHaveLength(1)
  })

  it('un refus sur un document s’affiche dans un compte rendu avec la raison du serveur, les autres sont passés', async () => {
    const bulk = vi.fn(async () => ({
      results: [{ name: 'a', ok: true }, { name: 'b', ok: false, code: 'document_in_use', message: 'Suppression impossible : utilisé par Tâche' }],
      done: 1, failed: 1,
    }))
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection }, { bulk })
    await w.find('[data-testid="bulk-delete"]').trigger('click')
    await asked[0]!.accept!()
    await flushPromises()
    const report = document.body.querySelector('[data-testid="bulk-report"]')
    expect(report?.textContent).toContain('Suppression impossible')
    expect(report?.textContent).toContain('1 supprimé')
    expect(w.emitted('done')).toHaveLength(1) // un document est parti : la liste se relit
  })

  it('un seul document refusé : son message en notification, pas de compte rendu', async () => {
    const bulk = vi.fn(async () => ({ results: [{ name: 'a', ok: false, code: 'document_in_use', message: 'Suppression impossible : utilisé par Facture' }], done: 0, failed: 1 }))
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection: [selection[0]!] }, { bulk })
    await w.find('[data-testid="bulk-delete"]').trigger('click')
    await asked[0]!.accept!()
    await flushPromises()
    expect(document.body.querySelector('[data-testid="bulk-report"]')).toBeNull()
    expect(toasts.summaries()).toEqual(['Suppression impossible : utilisé par Facture']) // le message du serveur, pas « une erreur est survenue »
    expect(w.emitted('done')).toBeUndefined() // rien n'a changé : la liste ne se relit pas
  })

  it('modifier un champ : le choix des champs exclut ce qui ne se pose pas en lot, la valeur part comme dans un formulaire', async () => {
    const bulk = vi.fn(async () => ({ results: [{ name: 'a', ok: true }, { name: 'b', ok: true }], done: 2, failed: 0 }))
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection }, { bulk })
    const vm = w.vm as unknown as { editable: FieldMeta[]; openEdit: () => void }
    expect(vm.editable.map((f) => f.fieldname)).toEqual(['title', 'status', 'project', 'notes']) // ni mot de passe, ni unique, ni lecture seule
    vm.openEdit()
    await flushPromises()
    expect(document.body.querySelector('[data-testid="bulk-edit-dialog"]')).not.toBeNull()
    ;(w.vm as unknown as { chosen: string }).chosen = 'status'
    ;(w.vm as unknown as { value: unknown }).value = 'Fermée'
    await flushPromises()
    ;(document.body.querySelector('[data-testid="bulk-edit-apply"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(bulk).toHaveBeenCalledWith('Task', 'update', ['a', 'b'], { status: 'Fermée' })
  })

  it('une valeur laissée vide vide le champ', async () => {
    const bulk = vi.fn(async () => ({ results: [], done: 0, failed: 0 }))
    const { w } = mountWith(BulkActions, { doctype: 'Task', meta: TASK, selection }, { bulk })
    const vm = w.vm as unknown as { openEdit: () => void; chosen: string }
    vm.openEdit()
    vm.chosen = 'notes'
    await flushPromises()
    ;(document.body.querySelector('[data-testid="bulk-edit-apply"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(bulk).toHaveBeenCalledWith('Task', 'update', ['a', 'b'], { notes: null })
  })
})

describe('connexions (ConnectionsPanel)', () => {
  it('liste ce qui cite le document, et ouvre la liste filtrée sur le lien', async () => {
    const connections = vi.fn(async () => [
      { doctype: 'Task', label: 'Tâche', field: 'project', field_label: 'Projet', table: null, count: 3 },
      { doctype: 'Note', label: 'Note', field: 'ref_name', field_label: 'Cible', table: null, count: 1, type_field: 'ref_type' },
      { doctype: 'Visit', label: 'Visite', field: 'place', field_label: 'Lieu', table: 'stops', count: 2 },
    ])
    const { w } = mountWith(ConnectionsPanel, { doctype: 'Project', name: 'p1', version: 1 }, { connections })
    await flushPromises()
    expect(connections).toHaveBeenCalledWith('Project', 'p1')
    const task = w.find('[data-testid="connection-Task"] a')
    expect(task.text()).toContain('Tâche')
    expect(task.text()).toContain('3')
    expect(w.findComponent({ name: 'RouterLink' }).props('to')).toMatchObject({ name: 'list', params: { doctype: 'Task' }, query: { filters: '{"project":"p1"}' } })
    // Un DynamicLink pose aussi son type : le même nom visant un autre type ne compte pas.
    const note = w.findAllComponents({ name: 'RouterLink' }).find((l) => (l.props('to') as { params: { doctype: string } }).params.doctype === 'Note')!
    expect((note.props('to') as { query: { filters: string } }).query.filters).toBe('{"ref_name":"p1","ref_type":"Project"}')
    // Un lien porté par les lignes d'un tableau se compte mais ne s'ouvre pas : une liste ne se filtre pas sur un tableau.
    const visit = w.find('[data-testid="connection-Visit"]')
    expect(visit.find('a').exists()).toBe(false)
    expect(visit.text()).toContain('dans les lignes')
  })

  it('n’apparaît pas quand rien ne cite le document, ni quand la lecture échoue', async () => {
    const empty = mountWith(ConnectionsPanel, { doctype: 'Project', name: 'p1', version: 1 }, { connections: async () => [] })
    await flushPromises()
    expect(empty.w.find('[data-testid="connections"]').exists()).toBe(false)
    const broken = mountWith(ConnectionsPanel, { doctype: 'Project', name: 'p1', version: 1 }, { connections: async () => { throw new Error('hors ligne') } })
    await flushPromises()
    expect(broken.w.find('[data-testid="connections"]').exists()).toBe(false)
  })
})

describe('barre latérale de la liste (ListSidebar)', () => {
  it('montre les valeurs d’un champ à choix avec leur nombre, dans les filtres en vigueur sauf les siens, et un clic remonte la valeur', async () => {
    const aggregate = vi.fn(async (_d: string, q: { group_by?: string }) => ({
      group: { field: q.group_by!, bucket: null, fieldtype: q.group_by === 'status' ? 'Select' : 'Link', options: q.group_by === 'project' ? 'Project' : ['Ouverte', 'Fermée'] },
      metrics: [{ function: 'count', field: null, label: null, decimals: null }],
      rows: q.group_by === 'status' ? [{ key: 'Ouverte', values: [5] }, { key: 'Fermée', values: [2] }] : [{ key: 'p1', values: [7] }],
      truncated: false,
    }))
    const list = vi.fn(async () => ({ items: [rec('Project', 'p1', { title: 'Refonte' })], total: 1, limit: 100, offset: 0 }))
    const { w } = mountWith(ListSidebar, { meta: TASK, filters: { status: 'Ouverte', title: ['like', 'x'] } }, { aggregate, list, meta: async (d: string) => (d === 'Project' ? PROJECT : TASK) })
    await flushPromises()
    const status = aggregate.mock.calls.find((c) => (c[1] as { group_by: string }).group_by === 'status')!
    expect((status[1] as { filters: object }).filters).toEqual({ title: ['like', 'x'] }) // le filtre de ce champ est retiré : les autres valeurs restent visibles
    const project = aggregate.mock.calls.find((c) => (c[1] as { group_by: string }).group_by === 'project')!
    expect((project[1] as { filters: object }).filters).toEqual({ status: 'Ouverte', title: ['like', 'x'] })
    expect(w.find('[data-testid="sidebar-status-Ouverte"]').text()).toContain('5')
    expect(w.find('[data-testid="sidebar-status-Ouverte"]').classes()).toContain('on') // la valeur déjà filtrée est en évidence
    expect(w.find('[data-testid="sidebar-status-Fermée"]').classes()).not.toContain('on')
    expect(w.find('[data-testid="sidebar-project-p1"]').text()).toContain('Refonte') // le titre du projet, pas son identifiant
    await w.find('[data-testid="sidebar-status-Fermée"]').trigger('click')
    expect(w.emitted('pick')).toEqual([['status', 'Fermée']])
  })

  it('au plus trois champs, ceux de la liste d’abord ; rien quand le type n’a aucun champ à choix', async () => {
    const many = metaOf('Many', ['a', 'b', 'c', 'd', 'e'].map((n, i) => field(n, 'Select', { options: ['x'], in_list_view: i >= 3 })))
    const aggregate = vi.fn(async (_doctype: string, _query: object) => ({ group: null, metrics: [], rows: [], truncated: false }))
    const a = mountWith(ListSidebar, { meta: many, filters: {} }, { aggregate })
    await flushPromises()
    expect(aggregate.mock.calls.map((c) => (c[1] as { group_by: string }).group_by)).toEqual(['d', 'e', 'a'])
    expect(a.w.exists()).toBe(true)
    const none = mountWith(ListSidebar, { meta: metaOf('Plain', [field('t', 'Text')]), filters: {} }, { aggregate })
    expect(none.w.find('[data-testid="list-sidebar"]').exists()).toBe(false)
  })
})

describe('champ Link : créer sans quitter le formulaire, aperçu', () => {
  const api = (create = vi.fn(async (_d: string, data: Record<string, unknown>) => rec('Project', 'new-1', data))) => ({
    meta: vi.fn(async () => PROJECT),
    search: vi.fn(async () => []),
    list: vi.fn(async () => ({ items: [], total: 0, limit: 1, offset: 0 })),
    create,
  })

  it('quickEntryFields : les obligatoires et le titre ; null quand le type exige ce que la saisie rapide ne sait pas remplir', () => {
    expect(quickEntryFields(PROJECT)?.map((f) => f.fieldname)).toEqual(['title'])
    expect(quickEntryFields(TASK)?.map((f) => f.fieldname)).toEqual(['title'])
    expect(quickEntryFields(metaOf('X', [field('t', 'Text', { required: true }), field('rows', 'Table', { required: true, options: 'Row' })]))).toBeNull()
    expect(quickEntryFields(metaOf('Y', [field('f', 'Attach', { required: true })]))).toBeNull()
    expect(quickEntryFields(metaOf('Z', [field('a', 'Text'), field('b', 'Text', { mandatory_depends_on: { op: 'eq' } as never })]))).toBeNull()
    expect(quickEntryFields(metaOf('W', [field('a', 'Text', { required: true, depends_on: { op: 'eq' } as never })]))).toBeNull()
  })

  it('les filtres du champ (link_filters) accompagnent l’autocomplétion, et la relancent quand ils changent', async () => {
    // D29 : le Desk ne propose que ce que l'écriture accepterait ; le serveur revérifie de toute façon.
    const search = vi.fn(async () => [{ name: 'p1', title: 'Abidjan' }])
    const { w } = mountWith(LinkField, { modelValue: null, target: 'Project', filters: { pays: 'CI', actif: true } }, { ...api(), search })
    await flushPromises()
    // L'autocomplétion de PrimeVue émet `complete` quand l'utilisateur tape : on exerce le gestionnaire, pas son minuteur.
    w.findComponent(AutoComplete).vm.$emit('complete', { query: 'Abi' })
    await flushPromises()
    expect(search).toHaveBeenCalledWith('Project', 'Abi', 10, null, { pays: 'CI', actif: true }) // états admis (aucun), puis les filtres
    expect(w.findComponent(AutoComplete).props('suggestions')).toEqual([{ name: 'p1', label: 'Abidjan' }])

    // Le pays du document change : ce qui était proposé ne vaut plus.
    await w.setProps({ filters: { pays: 'FR', actif: true } } as never)
    await flushPromises()
    expect(w.findComponent(AutoComplete).props('suggestions')).toEqual([])
  })

  it('« + » à côté du champ ouvre la saisie rapide et choisit le document créé', async () => {
    const create = vi.fn(async (_d: string, data: Record<string, unknown>) => rec('Project', 'new-1', data))
    const { w } = mountWith(LinkField, { modelValue: null, target: 'Project' }, api(create))
    await flushPromises()
    await w.find('input').setValue('Refo')
    await w.find('[data-testid="link-create"]').trigger('click')
    await flushPromises()
    const dialog = document.body.querySelector('[data-testid="quick-entry"]')
    expect(dialog).not.toBeNull()
    expect((dialog!.querySelector('[data-field="title"] input') as HTMLInputElement).value).toBe('Refo') // ce qui était tapé devient le titre
    ;(document.body.querySelector('[data-testid="quick-save"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(create).toHaveBeenCalledWith('Project', { title: 'Refo' })
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual(['new-1'])
  })

  it('un titre vide est refusé avant tout appel', async () => {
    const create = vi.fn()
    const { w } = mountWith(LinkField, { modelValue: null, target: 'Project' }, api(create as never))
    await flushPromises()
    await w.find('[data-testid="link-create"]').trigger('click')
    await flushPromises()
    ;(document.body.querySelector('[data-testid="quick-save"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(create).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-testid="quick-entry"] .error')?.textContent).toBe('Champ obligatoire')
  })

  it('une erreur du serveur est dite dans la boîte, qui reste ouverte', async () => {
    const { ApiError } = await import('@/lib/api')
    const create = vi.fn(async () => {
      throw new ApiError(422, 'validation_failed', 'invalide', [{ field: 'title', msg: 'Trop court', type: 'too_short_title' }])
    })
    const { w } = mountWith(LinkField, { modelValue: null, target: 'Project' }, api(create as never))
    await flushPromises()
    await w.find('input').setValue('x')
    await w.find('[data-testid="link-create"]').trigger('click')
    await flushPromises()
    ;(document.body.querySelector('[data-testid="quick-save"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(create).toHaveBeenCalled()
    expect(document.body.querySelector('[data-testid="quick-entry"]')).not.toBeNull()
    expect(document.body.querySelector('[data-testid="quick-entry"] .error')?.textContent).toBe('Trop court')
  })

  it('pas de création pour qui ne peut pas créer ; la fiche complète pour un type que la saisie rapide ne sait pas remplir', async () => {
    const reader = { ...api(), meta: vi.fn(async () => ({ ...PROJECT, rights: { ...RIGHTS, create: 'none' as const } })) }
    mountWith(LinkField, { modelValue: null, target: 'Project' }, reader)
    await flushPromises()
    expect(document.body.querySelector('[data-testid="link-create"]')).toBeNull()
    expect(document.body.querySelector('[data-testid="link-create-full"]')).toBeNull()
    document.body.innerHTML = ''
    const heavy = { ...api(), meta: vi.fn(async () => metaOf('Project', [field('files', 'Table', { required: true, options: 'Row' })])) }
    mountWith(LinkField, { modelValue: null, target: 'Project' }, heavy)
    await flushPromises()
    expect(document.body.querySelector('[data-testid="link-create"]')).toBeNull()
    expect(document.body.querySelector('[data-testid="link-create-full"]')).not.toBeNull()
  })

  it('un champ en lecture seule ne propose pas de créer', async () => {
    const { w } = mountWith(LinkField, { modelValue: 'p1', target: 'Project', readonly: true }, api())
    await flushPromises()
    expect(w.find('[data-testid="link-create"]').exists()).toBe(false)
  })

  it('l’aperçu d’un lien ne lit rien avant le survol, puis montre le titre et quelques champs', async () => {
    vi.useFakeTimers()
    try {
      const list = vi.fn(async () => ({ items: [rec('Task', 't1', { title: 'Écrire', status: 'Ouverte', notes: 'long' })], total: 1, limit: 1, offset: 0 }))
      const { w } = mountWith(LinkPreview, { target: 'Task', name: 't1' }, { meta: async () => TASK, list })
      expect(list).not.toHaveBeenCalled()
      await w.find('.link-preview').trigger('mouseenter')
      await vi.advanceTimersByTimeAsync(400)
      await flushPromises()
      const card = w.find('[data-testid="link-preview"]')
      expect(card.text()).toContain('Écrire')
      expect(card.text()).toContain('Ouverte')
      expect(card.text()).not.toContain('long') // un texte long n'est pas un aperçu
      await w.find('.link-preview').trigger('mouseleave')
      expect(w.find('[data-testid="link-preview"]').exists()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('l’autocomplétion d’un lien contraint (`target_status`) ne demande au serveur que les états admis', async () => {
    const search = vi.fn(async () => [{ name: 'p1', title: 'Paris' }])
    const typed = async (extra: Partial<FieldMeta>) => {
      search.mockClear()
      const { w } = mountWith(FieldInput, { field: field('project', 'Link', { options: 'Project', ...extra }), modelValue: null, inputId: 'x' }, { ...api(), search })
      await flushPromises()
      w.findComponent({ name: 'AutoComplete' }).vm.$emit('complete', { query: ' par ' })
      await flushPromises()
      return search.mock.calls.at(-1)
    }
    expect((await typed({ target_status: 'not_cancelled' }))?.slice(0, 4)).toEqual(['Project', 'par', 10, [0, 1]]) // le 5e : les filtres du champ
    expect((await typed({ target_status: ['submitted', 'cancelled'] }))?.slice(0, 4)).toEqual(['Project', 'par', 10, [1, 2]])
    expect((await typed({}))?.slice(0, 4)).toEqual(['Project', 'par', 10, null]) // sans contrainte : tous les états
  })

  it('l’aperçu d’un document soumettable dit son état ; celui d’un type qui ne se soumet pas n’en dit rien', async () => {
    vi.useFakeTimers()
    try {
      const INVOICE = metaOf('Invoice', [field('title', 'Text', { in_list_view: true })], { title_field: 'title', submittable: true })
      const cancelled = { ...rec('Invoice', 'f1', { title: 'Facture 1' }), docstatus: 2 }
      const hover = async (meta: DocTypeMeta, record: DocRecord) => {
        const { w } = mountWith(LinkPreview, { target: meta.name, name: record.name }, { meta: async () => meta, list: vi.fn(async () => ({ items: [record], total: 1, limit: 1, offset: 0 })) })
        await w.find('.link-preview').trigger('mouseenter')
        await vi.advanceTimersByTimeAsync(400)
        await flushPromises()
        return w
      }
      const badge = (await hover(INVOICE, cancelled)).find('[data-testid="link-preview-status"]')
      expect(badge.text()).toBe('Annulé')
      expect(badge.classes()).toContain('cancelled')
      expect((await hover(INVOICE, { ...cancelled, name: 'f2', docstatus: 1 })).find('[data-testid="link-preview-status"]').text()).toBe('Soumis')
      const plain = await hover(TASK, { ...rec('Task', 't1', { title: 'Écrire' }), docstatus: 2 }) // un docstatus sans objet n'y change rien
      expect(plain.find('[data-testid="link-preview"]').exists()).toBe(true)
      expect(plain.find('[data-testid="link-preview-status"]').exists()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('un aperçu illisible n’affiche rien', async () => {
    vi.useFakeTimers()
    try {
      const { w } = mountWith(LinkPreview, { target: 'Task', name: 't1' }, { meta: async () => { throw new Error('refusé') } })
      await w.find('.link-preview').trigger('mouseenter')
      await vi.advanceTimersByTimeAsync(400)
      await flushPromises()
      expect(w.find('[data-testid="link-preview"]').exists()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('documentation de l’API (lib/apidocs)', () => {
  const ORDER = metaOf('Order', [
    field('title', 'Text', { required: true, in_list_view: true }),
    field('total', 'Decimal', { decimals: 2, in_list_view: true }),
    field('status', 'Select', { options: ['Ouverte', 'Fermée'], in_list_view: true }),
    field('ref', 'Text', { unique: true, set_only_once: true }),
    field('company_title', 'Text', { fetch_from: 'company.title' }),
    field('secret', 'Password'),
  ], { submittable: true, allow_rename: true, rights: { ...RIGHTS, submit: 'all', cancel: 'all' } })

  it('les routes dépendent du type et des droits : soumettre/renommer seulement si possibles, rien que l’utilisateur ne puisse employer', () => {
    const ids = (m: DocTypeMeta) => endpoints(m, 'https://h/app/xcore-doctype').map((e) => e.id)
    expect(ids(ORDER)).toEqual(expect.arrayContaining(['list', 'get', 'create', 'update', 'delete', 'submit', 'cancel', 'amend', 'rename', 'bulk', 'connections', 'history']))
    expect(ids(TASK)).not.toEqual(expect.arrayContaining(['submit']))
    expect(ids(TASK)).not.toContain('rename')
    const reader = { ...TASK, rights: { read: 'all', write: 'none', create: 'none', delete: 'none', submit: 'none', cancel: 'none' } as DocTypeMeta['rights'] }
    expect(ids(reader)).not.toEqual(expect.arrayContaining(['create']))
    expect(ids(reader)).toEqual(expect.arrayContaining(['list', 'get', 'search', 'aggregate']))
    expect(ids(reader)).not.toContain('update')
    const single = metaOf('Settings', [field('a', 'Text')], { single: true })
    expect(ids(single)).not.toEqual(expect.arrayContaining(['create', 'delete', 'bulk']))
  })

  it('chaque route a un curl prêt à copier, avec le jeton, l’organisation et un corps d’exemple valide', () => {
    const create = endpoints(ORDER, 'https://h/app/xcore-doctype').find((e) => e.id === 'create')!
    expect(create.curl).toContain("curl -X POST 'https://h/app/xcore-doctype/doc/Order'")
    expect(create.curl).toContain("Authorization: Bearer $TOKEN")
    expect(create.curl).toContain("X-Tenant-ID: $TENANT")
    expect(create.body).toEqual({ data: { title: 'texte', total: '12.50', status: 'Ouverte' } }) // ni champ copié, ni secret, ni champ hors liste
    const prompt = endpoints({ ...ORDER, naming: 'prompt' }, 'b').find((e) => e.id === 'create')!
    expect(prompt.body).toMatchObject({ name: 'mon-nom' })
  })

  it('un nom ou un filtre avec une apostrophe ne casse pas la commande', () => {
    const odd = metaOf("L'Ordre", [field('a', 'Text')])
    const list = endpoints(odd, "http://h/app/x'y").find((e) => e.id === 'list')!
    expect(list.curl).toContain("'\\''")
  })

  it('les valeurs d’exemple ont la forme que le serveur accepte', () => {
    expect(sampleValue(field('d', 'Decimal', { decimals: 3 }))).toBe('12.500')
    expect(sampleValue(field('d', 'Decimal', { decimals: 0 }))).toBe('12')
    expect(sampleValue(field('s', 'Select', { options: ['x', 'y'] }))).toBe('x')
    expect(sampleValue(field('e', 'Text', { format: 'email' }))).toBe('nom@exemple.fr')
    expect(sampleValue(field('t', 'Time'))).toBe('09:30:00')
    expect(sampleValue(field('r', 'Rating', { stars: 5 }))).toBe(3)
    expect(sampleData(ORDER)).not.toHaveProperty('secret')
  })

  it('les champs : types, bornes, opérateurs de filtre et règles', () => {
    const docs = Object.fromEntries(fieldDocs(ORDER).map((f) => [f.fieldname, f]))
    expect(docs.title).toMatchObject({ type: 'Text', required: true })
    expect(docs.total!.detail).toBe('2 décimales')
    expect(docs.status!.detail).toBe('Ouverte | Fermée')
    expect(docs.status!.filter).toEqual(['=', '!=', 'in'])
    expect(docs.ref!.rules).toEqual(['unique', 'valeur posée une seule fois'])
    expect(docs.company_title!.rules).toEqual(['copié de company.title'])
    expect(docs.secret!.filter).toEqual([]) // un secret ne se filtre pas
  })

  it('la page se génère du type choisi', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/api/:doctype?', name: 'api-docs', component: ApiDocsView, props: true }, { path: '/:rest(.*)*', component: { render: () => null } }] })
    const { w } = mountWith(
      ApiDocsView,
      { doctype: 'Order' },
      { doctypes: async () => [{ name: 'Order', label: 'Commande', module: 'shop' }], meta: async () => ORDER },
      router,
    )
    await flushPromises()
    expect(w.find('[data-testid="route-create"]').text()).toContain('/doc/Order')
    expect(w.find('[data-testid="field-ref"]').text()).toContain('unique')
    expect(w.find('[data-testid="api-base"]').text()).toContain('/app/xcore-doctype')
    expect(w.find('[data-testid="api-example"]').text()).toContain('"title": "texte"')
  })
})
