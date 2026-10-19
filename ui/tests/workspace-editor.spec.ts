import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import Select from 'primevue/select'
import ToastService from 'primevue/toastservice'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import BlockDialog from '@/components/BlockDialog.vue'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, Workspace } from '@/lib/types'
import WorkspaceView from '@/views/WorkspaceView.vue'

const SPACE: Workspace = {
  name: 'stock',
  label: 'Stock',
  icon: 'warehouse',
  description: 'Réceptions',
  blocks: [
    { type: 'header', text: 'Vue' },
    { type: 'number_card', label: 'Brouillons', doctype: 'Stock Entry', function: 'count', cols: 3 },
    { type: 'chart', label: 'Par nature', doctype: 'Stock Entry', kind: 'donut', group_by: 'purpose', function: 'count', limit: 10, cols: 6 },
    { type: 'shortcut', doctype: 'Item', label: 'Article', icon: 'box', create: true },
  ],
}

const META = (name: string): DocTypeMeta =>
  ({
    name,
    label: name,
    fields: [
      { fieldname: 'purpose', label: 'Nature', fieldtype: 'Select', options: ['A', 'B'] },
      { fieldname: 'qty', label: 'Quantité', fieldtype: 'Number' },
      { fieldname: 'note', label: 'Note', fieldtype: 'Text' },
      { fieldname: 'day', label: 'Jour', fieldtype: 'Date' },
    ],
    layout: [],
    list_columns: [],
    rights: {},
    submittable: true,
  }) as unknown as DocTypeMeta

async function open(api: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })
  const saveWorkspaceOverride = vi.fn(async () => ({ name: 'stock', config: {} }))
  const w = mount(WorkspaceView, {
    props: { name: 'stock' },
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: {
        [SERVICES as symbol]: {
          transport: {},
          config: { baseUrl: '', loginUrl: null },
          api: {
            workspaces: async () => [SPACE],
            list: async () => ({ items: [], total: 3, limit: 1, offset: 0 }),
            aggregate: async () => ({ group: null, metrics: [{ function: 'count', field: null, label: null, decimals: null }], rows: [{ key: null, values: [7] }], truncated: false }),
            doctypes: async () => [
              { name: 'Stock Entry', label: 'Mouvement' },
              { name: 'Item', label: 'Article' },
            ],
            meta: async (name: string) => META(name),
            saveWorkspaceOverride,
            removeWorkspaceOverride: vi.fn(async () => undefined),
            ...api,
          },
        },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return { w, saveWorkspaceOverride }
}

const edit = async (w: Awaited<ReturnType<typeof open>>['w']) => {
  await w.find('[data-testid="workspace-edit"]').trigger('click')
  await flushPromises()
}
const kinds = (w: Awaited<ReturnType<typeof open>>['w']) => w.findAll('.tile-kind').map((k) => k.text())

describe('personnaliser un espace', () => {
  it('« Personnaliser » montre chaque bloc dans sa tuile, les cartes restant vivantes, sans rien enregistrer', async () => {
    const { w, saveWorkspaceOverride } = await open()
    await edit(w)
    expect(kinds(w)).toEqual(['Titre', 'Chiffre', 'Graphique', 'Raccourci'])
    expect(w.find('[data-testid="tile-1"]').text()).toContain('7') // la carte calcule vraiment
    expect(w.find('[data-testid="editor-save"]').attributes('disabled')).toBeDefined() // rien n'a changé : rien à enregistrer
    expect(saveWorkspaceOverride).not.toHaveBeenCalled()
  })

  it('les flèches déplacent un bloc ; la première ne recule pas, la dernière n’avance pas', async () => {
    const { w } = await open()
    await edit(w)
    expect(w.find('[data-testid="tile-earlier-0"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="tile-later-3"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="tile-later-1"]').trigger('click')
    expect(kinds(w)).toEqual(['Titre', 'Graphique', 'Chiffre', 'Raccourci'])
    expect(w.find('[data-testid="editor-dirty"]').exists()).toBe(true)
  })

  it('déplacer ou redimensionner une tuile ne recalcule aucune carte, et chacune garde SES chiffres', async () => {
    const aggregate = vi.fn(async (_doctype: string, query: { group_by?: string }) => ({
      group: null,
      metrics: [{ function: 'count', field: null, label: null, decimals: null }],
      rows: [{ key: null, values: [query.group_by ? 11 : 7] }],
      truncated: false,
    }))
    const { w } = await open({ aggregate })
    await edit(w)
    const calls = aggregate.mock.calls.length
    expect(calls).toBeGreaterThan(0)
    await w.find('[data-testid="tile-later-1"]').trigger('click')
    const size = w.findAllComponents(Select).find((s) => s.attributes('data-testid') === 'tile-size-1')!
    size.vm.$emit('update:modelValue', 12)
    await flushPromises()
    expect(aggregate.mock.calls.length).toBe(calls)
    expect(w.find('[data-testid="tile-1"]').text()).toContain('11') // le graphique, passé en 2e position, garde ses 11
    expect(w.find('[data-testid="tile-2"]').text()).toContain('7') // le chiffre, passé en 3e, garde ses 7
  })

  it('glisser une tuile sur une autre lui prend sa place', async () => {
    const { w } = await open()
    await edit(w)
    await w.find('[data-testid="tile-3"]').trigger('dragstart')
    await w.find('[data-testid="tile-1"]').trigger('dragover')
    await w.find('[data-testid="tile-1"]').trigger('drop')
    expect(kinds(w)).toEqual(['Titre', 'Raccourci', 'Chiffre', 'Graphique'])
  })

  it('une largeur choisie s’applique à la tuile, et un titre n’en a pas à choisir', async () => {
    const { w } = await open()
    await edit(w)
    expect(w.find('[data-testid="tile-size-0"]').exists()).toBe(false)
    expect(w.find('[data-testid="tile-size-3"]').exists()).toBe(false)
    const size = w.findAllComponents(Select).find((s) => s.attributes('data-testid') === 'tile-size-2')!
    size.vm.$emit('update:modelValue', 12)
    await flushPromises()
    expect(w.find('[data-testid="tile-2"]').attributes('style')).toContain('--span: 12')
  })

  it('retirer un bloc le retire de la liste', async () => {
    const { w } = await open()
    await edit(w)
    await w.find('[data-testid="tile-remove-1"]').trigger('click')
    expect(kinds(w)).toEqual(['Titre', 'Graphique', 'Raccourci'])
  })

  it('enregistre la DESCRIPTION des blocs, sans les champs de lecture que le serveur refuserait', async () => {
    const { w, saveWorkspaceOverride } = await open()
    await edit(w)
    await w.find('[data-testid="tile-later-1"]').trigger('click')
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(saveWorkspaceOverride).toHaveBeenCalledWith('stock', {
      label: 'Stock',
      icon: 'warehouse',
      description: 'Réceptions',
      blocks: [
        { type: 'header', text: 'Vue' },
        { type: 'chart', label: 'Par nature', doctype: 'Stock Entry', kind: 'donut', group_by: 'purpose', function: 'count', limit: 10, cols: 6 },
        { type: 'number_card', label: 'Brouillons', doctype: 'Stock Entry', function: 'count', cols: 3 },
        { type: 'shortcut', doctype: 'Item', label: 'Article', new: true },
      ],
    }, 'user')
    expect(w.find('[data-testid="block-grid-edit"]').exists()).toBe(false) // enregistré : on sort de l'édition
  })

  it('un refus du serveur garde l’édition ouverte, le travail intact', async () => {
    const { ApiError } = await import('@/lib/api')
    const { w } = await open({ saveWorkspaceOverride: vi.fn(async () => { throw new ApiError(422, 'validation_failed', 'blocs.1 : champ inconnu') }) })
    await edit(w)
    await w.find('[data-testid="tile-remove-1"]').trigger('click')
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="block-grid-edit"]').exists()).toBe(true)
    expect(kinds(w)).toEqual(['Titre', 'Graphique', 'Raccourci'])
  })

  it('« Annuler » abandonne le brouillon : l’espace d’origine revient', async () => {
    const { w } = await open()
    await edit(w)
    await w.find('[data-testid="tile-remove-1"]').trigger('click')
    await w.find('[data-testid="editor-cancel"]').trigger('click')
    expect(w.find('[data-testid="block-grid-edit"]').exists()).toBe(false)
    expect(w.findAll('[data-testid^="quick-list"], .number-card').length).toBe(1)
  })

  it('un espace vide d’origine invite à ajouter une première carte', async () => {
    const { w } = await open()
    await edit(w)
    for (let i = 0; i < 4; i++) await w.find('[data-testid="tile-remove-0"]').trigger('click')
    expect(w.find('[data-testid="block-empty"]').exists()).toBe(true)
  })
})

describe('la fenêtre d’un bloc', () => {
  const mountDialog = async (block: unknown, adding = true) => {
    const w = mount(BlockDialog, {
      props: { visible: true, block: block as never, adding },
      global: {
        plugins: [PrimeVue, createPinia()],
        provide: { [SERVICES as symbol]: { transport: {}, config: {}, api: { doctypes: async () => [{ name: 'Stock Entry', label: 'Mouvement' }], meta: async (n: string) => META(n) } } },
      },
      attachTo: document.body,
    })
    await flushPromises()
    return w
  }
  // Les fenêtres des tests précédents restent dans le DOM : on vise toujours la DERNIÈRE.
  const validate = async () => {
    const all = document.querySelectorAll<HTMLElement>('[data-testid="bf-validate"]')
    all[all.length - 1]!.click()
    await flushPromises()
  }
  const select = (w: Awaited<ReturnType<typeof mountDialog>>, id: string) => w.findAllComponents(Select).find((s) => s.attributes('data-testid') === id)!

  it('refuse un bloc incomplet avec la raison, sans rien émettre', async () => {
    const w = await mountDialog({ type: 'chart', label: '', doctype: '', kind: 'bar', function: 'count', group_by: '', limit: 10, cols: 6 })
    await validate()
    expect(w.emitted('save')).toBeUndefined()
    expect(document.body.textContent).toContain('Donnez un titre')
  })

  it('un graphique se compose avec les champs DU type choisi, et une date propose sa période', async () => {
    const w = await mountDialog({ type: 'chart', label: 'Mouvements par jour', doctype: '', kind: 'bar', function: 'count', group_by: '', limit: 10, cols: 6 })
    select(w, 'bf-doctype').vm.$emit('update:modelValue', 'Stock Entry')
    await flushPromises()
    const groups = (select(w, 'bf-group').props('options') as { value: string }[]).map((o) => o.value)
    expect(groups).toEqual(['purpose', 'day', 'docstatus', 'owner', 'creation', 'modified']) // pas la Note (Text) ni la Quantité
    select(w, 'bf-group').vm.$emit('update:modelValue', 'day')
    await flushPromises()
    expect(select(w, 'bf-bucket').exists()).toBe(true)
    await validate()
    expect(w.emitted('save')![0]![0]).toMatchObject({ type: 'chart', doctype: 'Stock Entry', group_by: 'day', bucket: 'month', periods: 12 })
  })

  it('un camembert ne propose pas de moyenne, et changer de type de document efface les champs de l’ancien', async () => {
    const w = await mountDialog({ type: 'chart', label: 'X', doctype: 'Stock Entry', kind: 'bar', function: 'avg', field: 'qty', group_by: 'purpose', limit: 10, cols: 6 })
    expect((select(w, 'bf-function').props('options') as { value: string }[]).map((o) => o.value)).toEqual(['count', 'sum', 'avg', 'min', 'max'])
    select(w, 'bf-kind').vm.$emit('update:modelValue', 'donut')
    await flushPromises()
    expect((select(w, 'bf-function').props('options') as { value: string }[]).map((o) => o.value)).toEqual(['count', 'sum'])
    expect(select(w, 'bf-function').props('modelValue')).toBe('count') // ce que le serveur refuserait est remplacé, pas envoyé
    select(w, 'bf-doctype').vm.$emit('update:modelValue', 'Item')
    await flushPromises()
    expect(select(w, 'bf-group').props('modelValue')).toBe('')
  })
})

describe('le tableau de bord d’un type', () => {
  const BOARD_BLOCKS = [
    { type: 'number_card', label: 'Total', doctype: 'Invoice', function: 'count', cols: 3 },
    { type: 'chart', label: 'Par mois', doctype: 'Invoice', kind: 'bar', group_by: 'creation', bucket: 'month', periods: 12, function: 'count', limit: 10, cols: 8 },
  ] as never

  async function board(save = vi.fn(async () => undefined)) {
    const { default: BlockEditor } = await import('@/components/BlockEditor.vue')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { render: () => null } },
        { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      ],
    })
    const w = mount(BlockEditor, {
      props: { blocks: BOARD_BLOCKS, doctype: 'Invoice', save, reset: vi.fn(async () => undefined) },
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: {}, api: { doctypes: async () => [{ name: 'Invoice', label: 'Facture' }], meta: async (n: string) => META(n), aggregate: async () => ({ group: null, metrics: [{ function: 'count', field: null, label: null, decimals: null }], rows: [{ key: null, values: [1] }], truncated: false }) } } },
      },
      attachTo: document.body,
    })
    await flushPromises()
    ;(w.vm as unknown as { start(): void }).start()
    await flushPromises()
    return { w, save }
  }

  it('envoie les blocs SANS leur `doctype` (le type est implicite : le serveur le refuserait)', async () => {
    const { w, save } = await board()
    await w.find('[data-testid="tile-later-0"]').trigger('click')
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    const sent = (save.mock.calls[0] as unknown as [Record<string, unknown>[]])[0]
    expect(sent.map((b) => b.label)).toEqual(['Par mois', 'Total'])
    expect(sent.every((b) => !('doctype' in b))).toBe(true)
  })

  it('n’offre que des titres, des chiffres et des graphiques, et pas de JSON', async () => {
    const { w } = await board()
    expect(w.find('[data-testid="workspace-json"]').exists()).toBe(false)
    await w.find('[data-testid="editor-add"]').trigger('click')
    await flushPromises()
    const items = [...document.querySelectorAll('.p-menu-item')].slice(-4).map((i) => i.textContent?.trim())
    expect(items).toEqual(['Chiffre', 'Graphique', 'Titre', 'Espace'])
  })

  it('le formulaire d’un bloc ne demande pas le type de document', async () => {
    const { w } = await board()
    await w.find('[data-testid="tile-edit-0"]').trigger('click')
    await flushPromises()
    const dialogs = document.querySelectorAll('[data-testid="block-dialog"]')
    const last = dialogs[dialogs.length - 1]!
    expect(last.querySelector('[data-testid="bf-doctype"]')).toBeNull()
    expect(last.querySelector('[data-testid="bf-function"]')).not.toBeNull()
  })
})

describe('pour qui on enregistre', () => {
  const SelectButtonC = async () => (await import('primevue/selectbutton')).default

  async function mine(workspace: Workspace, api: Record<string, unknown> = {}) {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { render: () => null } },
        { path: '/app/:doctype', name: 'list', component: { render: () => null } },
        { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
        { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
      ],
    })
    const save = vi.fn(async () => ({ name: 'stock', config: {} }))
    const remove = vi.fn(async () => undefined)
    const w = mount(WorkspaceView, {
      props: { name: 'stock' },
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: {
          [SERVICES as symbol]: {
            transport: {},
            config: { baseUrl: '', loginUrl: null },
            api: {
              workspaces: async () => [workspace],
              list: async () => ({ items: [], total: 1, limit: 1, offset: 0 }),
              aggregate: async () => ({ group: null, metrics: [{ function: 'count', field: null, label: null, decimals: null }], rows: [{ key: null, values: [1] }], truncated: false }),
              doctypes: async () => [{ name: 'Stock Entry', label: 'Mouvement' }, { name: 'Item', label: 'Article' }],
              meta: async (n: string) => META(n),
              saveWorkspaceOverride: save,
              removeWorkspaceOverride: remove,
              ...api,
            },
          },
        },
      },
      attachTo: document.body,
    })
    await flushPromises()
    await w.find('[data-testid="workspace-edit"]').trigger('click')
    await flushPromises()
    return { w, save, remove }
  }

  it('par défaut on enregistre POUR SOI : la disposition des autres n’est pas touchée', async () => {
    const { w, save } = await mine({ ...SPACE, shareable: true })
    await w.find('[data-testid="tile-remove-1"]').trigger('click')
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith('stock', expect.anything(), 'user')
  })

  it('qui peut écrire ce que la page montre choisit « toute l’organisation » ; les autres ne le peuvent pas', async () => {
    const admin = await mine({ ...SPACE, shareable: true })
    const SB = await SelectButtonC()
    admin.w.findComponent(SB).vm.$emit('update:modelValue', 'org')
    await flushPromises()
    await admin.w.find('[data-testid="tile-remove-1"]').trigger('click')
    await admin.w.find('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(admin.save).toHaveBeenCalledWith('stock', expect.anything(), 'org')

    const reader = await mine({ ...SPACE, shareable: false })
    const options = reader.w.findComponent(SB).props('options') as { value: string; disabled: boolean }[]
    expect(options.find((o) => o.value === 'org')!.disabled).toBe(true)
    expect(options.find((o) => o.value === 'user')!.disabled).toBe(false)
  })

  it('dit d’où vient la disposition affichée : la mienne, celle de l’organisation, ou rien (celle du plugin)', async () => {
    expect((await mine({ ...SPACE, layer: 'user' })).w.find('[data-testid="editor-layer"]').text()).toBe('Ma disposition')
    expect((await mine({ ...SPACE, layer: 'org' })).w.find('[data-testid="editor-layer"]').text()).toBe('Disposition de l’organisation')
    expect((await mine({ ...SPACE, layer: null })).w.find('[data-testid="editor-layer"]').exists()).toBe(false)
  })

  it('« Retirer ma disposition » ne retire que la mienne (confirmation, puis scope user)', async () => {
    const { ConfirmationEventBus } = await import('primevue')
    const { w, remove } = await mine({ ...SPACE, layer: 'user', shareable: true })
    expect(w.find('[data-testid="editor-reset"]').text()).toBe('Retirer ma disposition')
    const on = (o: { accept?: () => void }) => o.accept?.()
    ConfirmationEventBus.on('confirm', on as never)
    await w.find('[data-testid="editor-reset"]').trigger('click')
    await flushPromises()
    ConfirmationEventBus.off('confirm', on as never)
    expect(remove).toHaveBeenCalledWith('stock', 'user')
  })

  it('un refus du serveur (pas le droit d’écrire pour l’organisation) garde l’édition ouverte', async () => {
    const { ApiError } = await import('@/lib/api')
    const { w } = await mine({ ...SPACE, shareable: true }, { saveWorkspaceOverride: vi.fn(async () => { throw new ApiError(403, 'forbidden', 'refusé') }) })
    await w.find('[data-testid="tile-remove-1"]').trigger('click')
    await w.find('[data-testid="editor-save"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="block-grid-edit"]').exists()).toBe(true)
  })
})

describe('couleurs libres', () => {
  it('une couleur #rrggbb se choisit pour une carte de chiffres ; un nom de la palette reste possible', async () => {
    const SelectC = (await import('primevue/select')).default
    const w = mount(BlockDialog, {
      props: { visible: true, block: { type: 'number_card', label: 'N', doctype: 'Stock Entry', function: 'count', cols: 3 } as never, adding: false },
      global: {
        plugins: [PrimeVue, createPinia()],
        provide: { [SERVICES as symbol]: { transport: {}, config: {}, api: { doctypes: async () => [{ name: 'Stock Entry', label: 'Mouvement' }], meta: async (n: string) => META(n) } } },
      },
      attachTo: document.body,
    })
    await flushPromises()
    const select = () => w.findAllComponents(SelectC).find((s) => s.attributes('data-testid') === 'bf-color')!
    expect(select().props('modelValue')).toBe('blue') // le défaut d'une carte
    expect((select().props('options') as { value: string }[]).map((o) => o.value)).toEqual(['blue', 'green', 'orange', 'red', 'purple', 'teal', 'gray', 'custom'])
    select().vm.$emit('update:modelValue', 'custom')
    await flushPromises()
    const input = document.querySelector<HTMLInputElement>('[data-testid="bf-color-custom"]')!
    input.value = '#D6452A'
    input.dispatchEvent(new Event('input'))
    await flushPromises()
    const all = document.querySelectorAll<HTMLElement>('[data-testid="bf-validate"]')
    all[all.length - 1]!.click()
    await flushPromises()
    expect(w.emitted('save')![0]![0]).toMatchObject({ color: '#d6452a' }) // en minuscules, comme le serveur le range
  })

  it('un graphique en camembert n’a pas de couleur à choisir (palette), une courbe ou des barres oui', async () => {
    const SelectC = (await import('primevue/select')).default
    const w = mount(BlockDialog, {
      props: { visible: true, block: { type: 'chart', label: 'C', doctype: 'Stock Entry', kind: 'bar', function: 'count', group_by: 'purpose', limit: 10, cols: 6, color: '#2a78d6' } as never, adding: false },
      global: {
        plugins: [PrimeVue, createPinia()],
        provide: { [SERVICES as symbol]: { transport: {}, config: {}, api: { doctypes: async () => [{ name: 'Stock Entry', label: 'Mouvement' }], meta: async (n: string) => META(n) } } },
      },
      attachTo: document.body,
    })
    await flushPromises()
    const find = (id: string) => w.findAllComponents(SelectC).find((s) => s.attributes('data-testid') === id)
    expect(find('bf-color')).toBeTruthy()
    find('bf-kind')!.vm.$emit('update:modelValue', 'donut')
    await flushPromises()
    expect(find('bf-color')).toBeUndefined()
    const all = document.querySelectorAll<HTMLElement>('[data-testid="bf-validate"]')
    all[all.length - 1]!.click()
    await flushPromises()
    expect(w.emitted('save')![0]![0]).not.toHaveProperty('color') // le serveur refuserait une couleur sur un camembert
  })
})
