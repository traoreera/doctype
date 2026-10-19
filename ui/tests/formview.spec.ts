import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import Select from 'primevue/select'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import FormView from '@/views/FormView.vue'
import { ApiError } from '@/lib/api'
import { SERVICES } from '@/lib/services'
import type { DocRecord, DocTypeMeta } from '@/lib/types'
import { formatDate, todayIn } from '@/lib/format'
import { recentsKey } from '@/lib/recents'
import { CUSTOMER, INVOICE, MOVE, STOCK_ENTRY } from './fixtures'

// PrimeVue monte la boîte de confirmation dans un composant à part : ici on garde seulement ce qu'on lui demande, pour
// pouvoir « cliquer » sur Supprimer en appelant `accept` au moment voulu.
const asked = vi.hoisted(() => [] as { accept?: () => unknown | Promise<unknown> }[])
vi.mock('primevue/useconfirm', async (original) => ({
  ...(await original<object>()),
  useConfirm: () => ({ require: (o: { accept?: () => unknown }) => asked.push(o) }),
}))

/** Une promesse qu'on résout à la main : pour fixer l'ordre dans lequel les réponses du serveur arrivent. */
function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)))
  return { promise, resolve, reject }
}

const rec = (doctype: string, name: string, data: Record<string, unknown>, version = 1): DocRecord => ({
  doctype, name, owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version, docstatus: 0, amended_from: null, data,
})

function setup(overrides: Record<string, unknown> = {}) {
  const metas: Record<string, DocTypeMeta> = { Customer: CUSTOMER, Invoice: INVOICE, 'Stock Entry': STOCK_ENTRY, Move: MOVE }
  const api = {
    meta: vi.fn(async (d: string) => metas[d]),
    doctypes: vi.fn(async () => []),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
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
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } },
    },
  })
  return { w, api, router }
}

const nameInput = (w: ReturnType<typeof setup>['w']) => w.find('[data-field="customer_name"] input')
const title = (w: ReturnType<typeof setup>['w']) => w.find('[data-testid="form-title"]').text()

describe('FormView : une réponse tardive ne touche pas la page où l’on est', () => {
  it('un chargement lent, dépassé par une autre page, ne l’écrase pas', async () => {
    const slow = deferred<DocRecord>()
    const { w, api, router } = setup({
      get: vi.fn((_d: string, name: string) => (name === 'a' ? slow.promise : Promise.resolve(rec('Customer', 'b', { customer_name: 'Bravo' })))),
    })
    await router.push('/app/Customer/a')
    await flushPromises()
    await router.push('/app/Customer/b')
    await flushPromises()
    expect(title(w)).toBe('Bravo')
    slow.resolve(rec('Customer', 'a', { customer_name: 'Alpha (arrivé trop tard)' }))
    await flushPromises()
    expect(title(w)).toBe('Bravo')
    expect(api.get).toHaveBeenCalledTimes(2)
  })

  it('une erreur tardive d’un chargement dépassé ne montre pas « introuvable » sur la bonne page', async () => {
    const slow = deferred<DocRecord>()
    const { w, router } = setup({
      get: vi.fn((_d: string, name: string) => (name === 'a' ? slow.promise : Promise.resolve(rec('Customer', 'b', { customer_name: 'Bravo' })))),
    })
    await router.push('/app/Customer/a')
    await flushPromises()
    await router.push('/app/Customer/b')
    await flushPromises()
    slow.reject(new ApiError(404, 'document_not_found', 'introuvable'))
    await flushPromises()
    expect(title(w)).toBe('Bravo')
    expect(w.find('[data-testid="form-error"]').exists()).toBe(false)
  })

  it('une création encore en vol quand on change de page n’y envoie pas l’utilisateur', async () => {
    const created = deferred<DocRecord>()
    const { w, api, router } = setup({ create: vi.fn(() => created.promise) })
    await router.push('/app/Customer/new')
    await flushPromises()
    await nameInput(w).setValue('Alpha')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(api.create).toHaveBeenCalledWith('Customer', expect.objectContaining({ customer_name: 'Alpha' }))

    await router.push('/app/Invoice/new') // l'utilisateur est déjà parti
    await flushPromises()
    created.resolve(rec('Customer', 'c9', { customer_name: 'Alpha' }))
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/app/Invoice/new') // jamais /app/Invoice/c9
    expect(title(w)).toBe('Nouveau Facture')
    expect(api.get).not.toHaveBeenCalled()
    // Le bouton de la page où l'on est ne reste pas en « envoi » à cause de l'enregistrement de la page quittée.
    expect(w.find('[data-testid="save"]').classes()).not.toContain('p-button-loading')
    expect(w.find('[data-testid="save"]').attributes('disabled')).toBeUndefined()
  })

  it('une mise à jour en vol ne réécrit pas le document ouvert entre-temps', async () => {
    const saved = deferred<DocRecord>()
    const { w, router } = setup({
      get: vi.fn(async (_d: string, name: string) => rec('Customer', name, { customer_name: name === 'a' ? 'Alpha' : 'Bravo' })),
      update: vi.fn(() => saved.promise),
    })
    await router.push('/app/Customer/a')
    await flushPromises()
    await nameInput(w).setValue('Alpha 2')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    await router.push('/app/Customer/b')
    await flushPromises()
    saved.resolve(rec('Customer', 'a', { customer_name: 'Alpha 2' }, 2))
    await flushPromises()
    expect(title(w)).toBe('Bravo')
    expect((nameInput(w).element as HTMLInputElement).value).toBe('Bravo')
  })

  it('une erreur d’enregistrement d’une page quittée n’affiche pas ses erreurs sur la suivante', async () => {
    const saved = deferred<DocRecord>()
    const { w, router } = setup({
      get: vi.fn(async (_d: string, name: string) => rec('Customer', name, { customer_name: name })),
      update: vi.fn(() => saved.promise),
    })
    await router.push('/app/Customer/a')
    await flushPromises()
    await nameInput(w).setValue('A2')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    await router.push('/app/Customer/b')
    await flushPromises()
    saved.reject(new ApiError(422, 'validation_failed', 'x', [{ field: 'customer_name', msg: 'Champ obligatoire', type: 'missing' }]))
    await flushPromises()
    expect(w.find('[data-testid="form-banner"]').exists()).toBe(false)
    expect(w.find('[data-testid="error-customer_name"]').exists()).toBe(false)
  })

  it('une création normale mène au document créé, sans le relire', async () => {
    const { w, api, router } = setup({ create: vi.fn(async () => rec('Customer', 'c1', { customer_name: 'Alpha' })) })
    await router.push('/app/Customer/new')
    await flushPromises()
    await nameInput(w).setValue('Alpha')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/app/Customer/c1')
    expect(title(w)).toBe('Alpha')
    expect(api.get).not.toHaveBeenCalled() // le document vient d'être reçu : inutile de le relire
    expect(w.find('[data-testid="status"]').text()).toBe('Enregistré')
  })

  it('une suppression en vol quand on change de page ne ramène pas à la liste', async () => {
    const removed = deferred<void>()
    const { w, api, router } = setup({
      get: vi.fn(async (d: string, name: string) => rec(d, name, { customer: 'c' })),
      remove: vi.fn(() => removed.promise),
    })
    asked.length = 0
    await router.push('/app/Invoice/FA-1')
    await flushPromises()
    await w.find('[data-testid="delete"]').trigger('click')
    expect(asked).toHaveLength(1)
    const deleting = asked[0]!.accept!()
    await flushPromises()
    expect(api.remove).toHaveBeenCalledWith('Invoice', 'FA-1')

    await router.push('/app/Invoice/FA-2') // l'utilisateur est déjà sur un autre document
    await flushPromises()
    removed.resolve()
    await deleting
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/app/Invoice/FA-2') // pas renvoyé à la liste
  })

  it('une suppression qui aboutit sur la page courante ramène à la liste', async () => {
    const { w, router } = setup({
      get: vi.fn(async (d: string, name: string) => rec(d, name, { customer: 'c' })),
      remove: vi.fn(async () => undefined),
    })
    asked.length = 0
    await router.push('/app/Invoice/FA-1')
    await flushPromises()
    await w.find('[data-testid="delete"]').trigger('click')
    await asked[0]!.accept!()
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/app/Invoice')
  })
})


// ── Documents soumettables ─────────────────────────────────────────────────────────────────────────────────
const entry = (docstatus: number, over: Partial<DocRecord> = {}): DocRecord => ({
  ...rec('Stock Entry', 'MS-2026-00001', {
    purpose: 'Réception', posting_date: '2026-06-15', items: [{ item: 'vis', qty: '3.000', target_warehouse: 'paris' }],
  }, 2),
  docstatus,
  ...over,
})
const has = (w: ReturnType<typeof setup>['w'], id: string) => w.find(`[data-testid="${id}"]`).exists()
const openEntry = async (docstatus: number, over: Partial<DocRecord> = {}, api: Record<string, unknown> = {}) => {
  const ctx = setup({ get: vi.fn(async () => entry(docstatus, over)), ...api })
  asked.length = 0
  await ctx.router.push('/app/Stock Entry/MS-2026-00001')
  await flushPromises()
  return ctx
}
const status = (w: ReturnType<typeof setup>['w']) => w.find('[data-testid="status"]').text()

describe('FormView : documents soumettables', () => {
  it('un brouillon propose Enregistrer, Supprimer et Soumettre, rien d’autre', async () => {
    const { w } = await openEntry(0)
    expect(status(w)).toBe('Brouillon')
    expect(['save', 'delete', 'submit'].map((id) => has(w, id))).toEqual([true, true, true])
    expect(['cancel-doc', 'amend', 'form-frozen'].map((id) => has(w, id))).toEqual([false, false, false])
  })

  it('soumettre exige d’avoir enregistré : le bouton se désactive dès qu’on modifie', async () => {
    const { w } = await openEntry(0)
    expect(w.find('[data-testid="submit"]').attributes('disabled')).toBeUndefined()
    await w.find('[data-field="remarks"] input').setValue('urgent')
    expect(w.find('[data-testid="submit"]').attributes('disabled')).toBeDefined()
    expect(status(w)).toBe('Non enregistré')
  })

  it('soumettre demande confirmation, envoie la version lue, puis fige le formulaire', async () => {
    const submit = vi.fn(async () => entry(1, { version: 3 }))
    const { w } = await openEntry(0, {}, { submit })
    await w.find('[data-testid="submit"]').trigger('click')
    expect(asked).toHaveLength(1)
    expect(submit).not.toHaveBeenCalled() // rien ne part avant la confirmation
    await asked[0]!.accept!()
    await flushPromises()
    expect(submit).toHaveBeenCalledWith('Stock Entry', 'MS-2026-00001', 2)
    expect(status(w)).toBe('Soumis')
    // Figé : ni saisie, ni enregistrement, ni suppression, ni nouvelle soumission ; on peut annuler.
    expect(['save', 'delete', 'submit'].map((id) => has(w, id))).toEqual([false, false, false])
    expect(has(w, 'cancel-doc')).toBe(true)
    expect(has(w, 'form-frozen')).toBe(true)
    expect(w.find('[data-field="purpose"] input').exists()).toBe(false)
    expect(w.find('[data-testid="value-purpose"]').text()).toBe('Réception')
  })

  it('Ctrl+S sur un document figé n’envoie rien', async () => {
    const update = vi.fn()
    const { w } = await openEntry(1, {}, { update })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true }))
    await flushPromises()
    expect(update).not.toHaveBeenCalled()
    expect(w.exists()).toBe(true)
  })

  it('un refus du métier (stock insuffisant) reste sur le brouillon et désigne la ligne en cause', async () => {
    const submit = vi.fn(async () => {
      throw new ApiError(422, 'hook_rejected', 'Stock insuffisant pour « Vis » dans « Paris » : 2.000 disponible, 3.000 demandé', [
        { field: 'items.0.qty', msg: 'Stock disponible : 2.000', type: 'insufficient_stock' },
      ])
    })
    const { w } = await openEntry(0, {}, { submit })
    await w.find('[data-testid="submit"]').trigger('click')
    await asked[0]!.accept!()
    await flushPromises()
    expect(w.find('[data-testid="form-banner"]').text()).toContain('Stock insuffisant')
    expect(w.find('[data-testid="error-items.0.qty"]').text()).toBe('Stock disponible : 2.000')
    expect(status(w)).toBe('Brouillon') // rien n'a été soumis
    expect(has(w, 'submit')).toBe(true) // on peut corriger puis réessayer
  })

  it('un document devenu soumis ailleurs propose de recharger plutôt que d’échouer en silence', async () => {
    const submit = vi.fn(async () => {
      throw new ApiError(409, 'invalid_docstatus', 'déjà soumis')
    })
    const { w } = await openEntry(0, {}, { submit })
    await w.find('[data-testid="submit"]').trigger('click')
    await asked[0]!.accept!()
    await flushPromises()
    expect(asked).toHaveLength(2) // la confirmation, puis la proposition de recharger
    expect(asked[1]).toMatchObject({ header: 'Le document a changé d’état' })
  })

  it('annuler un document soumis demande confirmation et le montre annulé, avec Amender', async () => {
    const cancel = vi.fn(async () => entry(2, { version: 4 }))
    const { w } = await openEntry(1, { version: 3 }, { cancel })
    await w.find('[data-testid="cancel-doc"]').trigger('click')
    expect(asked[0]).toMatchObject({ header: 'Annuler le document' })
    await asked[0]!.accept!()
    await flushPromises()
    expect(cancel).toHaveBeenCalledWith('Stock Entry', 'MS-2026-00001', 3)
    expect(status(w)).toBe('Annulé')
    expect(['cancel-doc', 'save', 'submit'].map((id) => has(w, id))).toEqual([false, false, false])
    expect(has(w, 'amend')).toBe(true)
  })

  it('amender mène à la copie en brouillon', async () => {
    const amend = vi.fn(async () => entry(0, { name: 'MS-2026-00001-1', amended_from: 'MS-2026-00001', version: 1 }))
    const get = vi.fn(async (_d: string, name: string) => (name === 'MS-2026-00001' ? entry(2) : entry(0, { name, amended_from: 'MS-2026-00001', version: 1 })))
    const { w, router } = await openEntry(2, {}, { amend, get })
    await w.find('[data-testid="amend"]').trigger('click')
    await flushPromises()
    expect(amend).toHaveBeenCalledWith('Stock Entry', 'MS-2026-00001')
    expect(router.currentRoute.value.fullPath).toBe('/app/Stock%20Entry/MS-2026-00001-1')
    expect(status(w)).toBe('Brouillon')
    expect(w.find('[data-testid="amended-from"]').text()).toBe('MS-2026-00001') // la filiation est visible, et cliquable
  })

  it('un document figé montre le titre de l’article, pas son identifiant', async () => {
    const itemMeta = { ...CUSTOMER, name: 'Item', title_field: 'customer_name' }
    const list = vi.fn(async () => ({ items: [rec('Item', 'vis', { customer_name: 'Vis à bois' })], total: 1, limit: 1, offset: 0 }))
    const { w } = await openEntry(1, {}, { list, meta: vi.fn(async (d: string) => (d === 'Item' ? itemMeta : STOCK_ENTRY)) })
    const row = w.find('[data-row="0"] [data-col="item"]')
    await vi.waitFor(() => expect(row.text()).toBe('Vis à bois')) // le titre arrive après le chargement du document
    expect(list).toHaveBeenCalledWith('Item', expect.objectContaining({ filters: { name: ['in', ['vis']] } }))
  })

  it('sans le droit de soumettre, pas de bouton', async () => {
    const restricted = { ...STOCK_ENTRY, rights: { ...STOCK_ENTRY.rights, submit: 'none' as const } }
    const ctx = setup({ get: vi.fn(async () => entry(0)), meta: vi.fn(async () => restricted) })
    await ctx.router.push('/app/Stock Entry/MS-2026-00001')
    await flushPromises()
    expect(has(ctx.w, 'submit')).toBe(false)
    expect(has(ctx.w, 'save')).toBe(true)
  })

  it('un DocType ordinaire n’affiche jamais d’état ni de transition', async () => {
    const { w, router } = setup({ get: vi.fn(async () => rec('Customer', 'c1', { customer_name: 'Alpha' })) })
    await router.push('/app/Customer/c1')
    await flushPromises()
    expect(status(w)).toBe('Enregistré')
    expect(['submit', 'cancel-doc', 'amend', 'form-frozen'].map((id) => has(w, id))).toEqual([false, false, false, false])
  })
})


describe('FormView : saisie', () => {
  beforeEach(() => localStorage.clear())

  it('un nouveau mouvement porte la date du jour de l’organisation', async () => {
    const { w, router } = setup()
    await router.push('/app/Move/new')
    await flushPromises()
    expect((w.find('[data-field="posting_date"] input').element as HTMLInputElement).value).toBe(formatDate(todayIn('Europe/Paris')))
  })

  it('les colonnes de la grille suivent la nature choisie', async () => {
    const { w, router } = setup()
    await router.push('/app/Move/new')
    await flushPromises()
    const heads = () => w.findAll('.form-table th').map((h) => h.text())
    expect(heads()).toEqual(['N°', 'Article*', 'Quantité*', 'Entrepôt source', 'Entrepôt cible', '']) // nature pas encore choisie : tout est là, rien d'obligatoire
    w.findComponent(Select).vm.$emit('update:modelValue', 'Sortie') // le choix dans la liste déroulante (la première liste du formulaire : la nature)
    await flushPromises()
    expect(heads()).toEqual(['N°', 'Article*', 'Quantité*', 'Entrepôt source*', ''])
  })

  it('un document ouvert est mémorisé pour l’accueil, avec son titre', async () => {
    const { router } = setup({ get: vi.fn(async () => rec('Customer', 'c1', { customer_name: 'ACME SAS' })) })
    await router.push('/app/Customer/c1')
    await flushPromises()
    const stored = JSON.parse(localStorage.getItem(recentsKey(null, '')) ?? '[]')
    expect(stored).toMatchObject([{ doctype: 'Customer', name: 'c1', title: 'ACME SAS' }])
  })

  it('un nouveau document n’est pas mémorisé : il n’existe pas encore', async () => {
    const { router } = setup()
    await router.push('/app/Customer/new')
    await flushPromises()
    expect(localStorage.getItem(recentsKey(null, ''))).toBeNull()
  })
})
