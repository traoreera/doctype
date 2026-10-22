import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject } from 'vue'
import { createMemoryHistory, createRouter, RouterView, type Router } from 'vue-router'
import BlockGrid from '@/components/BlockGrid.vue'
import ExtensionSlot from '@/extensions/ExtensionSlot.vue'
import { ExtensionHost, EXTENSIONS, EXTENSION_CONTEXT } from '@/extensions/host'
import { loadExtensions } from '@/extensions/loader'
import type { DeskHandle } from '@/extensions/types'
import { buildCommands } from '@/lib/commands'
import { readExtensions } from '@/lib/config'
import { PluginClient, pluginBase, type ClientContext } from '@/lib/api'
import { SERVICES } from '@/lib/services'
import type { DocRecord } from '@/lib/types'
import type { HttpRequest, Transport } from '@/lib/transport'
import FormView from '@/views/FormView.vue'
import { defineExtension, useDesk } from '@/shared/sdk'
import { CUSTOMER } from './fixtures'
import { menuItem, openMore } from './menu'

const Dummy = defineComponent({ props: { label: { type: String, default: null } }, render() { return h('p', { 'data-testid': 'dummy' }, `bloc ${this.label ?? ''}`) } })

function makeHost(router?: Router) {
  const notify = { success: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }
  const r = router ?? createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Dummy }] })
  const plugin = vi.fn((name: string) => ({ name }) as never)
  const host = new ExtensionHost({ api: {} as never, router: r, notify, plugin })
  return { host, notify, router: r, plugin }
}

const ok = (name: string, setup: (d: DeskHandle) => void | Promise<void>) => async () => ({ default: defineExtension({ name, apiVersion: 1, setup }) })

afterEach(() => {
  vi.restoreAllMocks()
})

describe('le chargeur : chaque extension est isolée', () => {
  it('charge les extensions valides, écarte les autres et retire ce qu’elles avaient enregistré', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { host } = makeHost()
    const modules: Record<string, () => Promise<{ default?: unknown }>> = {
      '/a.js': ok('sales', (d) => d.form.action({ doctype: 'Customer', label: 'Relancer', run: () => {} })),
      '/b.js': ok('stock', (d) => {
        d.form.action({ doctype: 'Customer', label: 'Avant la panne', run: () => {} })
        throw new Error('panne')
      }),
      '/c.js': ok('other', () => {}), // se nomme `other`, livrée par `hr`
      '/d.js': async () => ({ default: { name: 'crm', apiVersion: 2, setup() {} } }),
      '/e.js': async () => ({}),
      '/f.js': async () => {
        throw new Error('404')
      },
    }
    const entries = [['sales', '/a.js'], ['stock', '/b.js'], ['hr', '/c.js'], ['crm', '/d.js'], ['x', '/e.js'], ['y', '/f.js']].map(([name, url]) => ({ name: name!, url: url! }))
    await loadExtensions(host, entries, { importer: (url) => modules[url]!() })
    expect(host.loaded).toEqual(['sales'])
    expect(host.failures.map((f) => f.name)).toEqual(['stock', 'hr', 'crm', 'x', 'y'])
    expect(host.failures.find((f) => f.name === 'hr')!.reason).toMatch(/se nomme "other"/)
    expect(host.failures.find((f) => f.name === 'crm')!.reason).toMatch(/version d'API 2/)
    expect(host.formActionItems.map((a) => a.label)).toEqual(['Relancer']) // « Avant la panne » est parti avec son extension
  })

  it('une extension trop lente est écartée, et ses appels tardifs sont ignorés', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { host } = makeHost()
    let late: DeskHandle | null = null
    await loadExtensions(host, [{ name: 'slow', url: '/s.js' }], {
      importer: ok('slow', (d) => {
        late = d
        return new Promise(() => {})
      }),
      timeoutMs: 20,
    })
    expect(host.failures[0]!.reason).toMatch(/pas prête/)
    late!.form.action({ doctype: '*', label: 'Trop tard', run: () => {} })
    expect(host.formActionItems).toEqual([])
  })

  it('une contribution mal formée fait échouer l’extension entière', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { host } = makeHost()
    const bad: [string, (d: DeskHandle) => void][] = [
      ['a', (d) => d.routes.add({ path: '../admin', component: Dummy })],
      ['b', (d) => d.sidebar.add({ label: 'X', to: 'https://evil.example' })],
      ['c', (d) => d.sidebar.add({ label: 'X', to: '//evil.example' })],
      ['d', (d) => d.form.action({ doctype: 'X', label: '', run: () => {} })],
      ['e', (d) => d.blocks.add('Bad Name', Dummy)],
      ['f', (d) => d.commands.add({ label: 'X' } as never)],
    ]
    for (const [name, setup] of bad) await loadExtensions(host, [{ name, url: name }], { importer: ok(name, setup) })
    expect(host.failures.map((f) => f.name)).toEqual(['a', 'b', 'c', 'd', 'e', 'f'])
    expect(host.loaded).toEqual([])
  })

  it('une page d’extension devient une route sous #/x/<plugin>/, retirée avec elle', async () => {
    const { host, router } = makeHost()
    const h1 = host.handle('sales')
    h1.routes.add({ path: 'pipeline', title: 'Pipeline', component: Dummy })
    expect(router.hasRoute('x:sales:pipeline')).toBe(true)
    expect(router.resolve('/x/sales/pipeline').name).toBe('x:sales:pipeline')
    host.drop('sales')
    expect(router.hasRoute('x:sales:pipeline')).toBe(false)
  })
})

describe('ce que le Desk lit de l’hôte', () => {
  it('filtre par type et par `when`, et une condition qui lève vaut « caché »', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { host } = makeHost()
    const d = host.handle('sales')
    d.form.action({ doctype: 'Customer', label: 'A', run: () => {} })
    d.form.action({ doctype: '*', label: 'B', run: () => {} })
    d.form.action({ doctype: 'Invoice', label: 'C', run: () => {} })
    d.form.action({ doctype: 'Customer', label: 'D', when: ({ record }) => record?.docstatus === 1, run: () => {} })
    d.form.action({ doctype: 'Customer', label: 'E', when: () => { throw new Error('x') }, run: () => {} })
    const ctx = { doctype: 'Customer', name: 'c1', record: null, meta: CUSTOMER, values: {} }
    expect(host.formActions(ctx).map((a) => a.label)).toEqual(['A', 'B'])
  })

  it('une action de liste qui se cache pour ce type n’offre pas, à elle seule, les cases à cocher', () => {
    const { host } = makeHost()
    const d = host.handle('sales')
    d.list.action({ doctype: '*', label: 'Partout', when: ({ doctype }) => doctype === 'Invoice', run: () => {} })
    expect(host.hasListActions({ doctype: 'Invoice', meta: CUSTOMER })).toBe(true)
    expect(host.hasListActions({ doctype: 'Customer', meta: CUSTOMER })).toBe(false)
  })

  it('la feuille de style d’une extension : même origine seulement, retirée avec elle', () => {
    const { host } = makeHost()
    const d = host.handle('sales')
    d.styles.add('/app/xcore-doctype/desk/ext/sales/style.css')
    const link = document.head.querySelector<HTMLLinkElement>('link[data-extension="sales"]')
    expect(link?.rel).toBe('stylesheet')
    expect(() => d.styles.add('https://cdn.example.net/x.css')).toThrow(/même origine/)
    expect(() => d.styles.add('javascript:alert(1)')).toThrow(/même origine/)
    host.drop('sales')
    expect(document.head.querySelector('link[data-extension="sales"]')).toBeNull()
  })

  it('range les liens sous leur espace, les autres à part', () => {
    const { host } = makeHost()
    const d = host.handle('sales')
    d.sidebar.add({ workspace: 'sales', label: 'Pipeline', to: '/x/sales/pipeline' })
    d.sidebar.add({ workspace: 'ghost', label: 'Perdu', to: '/x/sales/lost' })
    d.sidebar.add({ label: 'Libre', to: '/x/sales/free' })
    expect(host.sidebarLinks('sales').map((l) => l.label)).toEqual(['Pipeline'])
    expect(host.sidebarLinks(null, ['sales', 'stock']).map((l) => l.label)).toEqual(['Perdu', 'Libre'])
  })

  it('une action qui échoue devient une notification, jamais une erreur dans la page', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { host, notify } = makeHost()
    await host.run('sales', () => Promise.reject(new Error('refusé')))
    expect(notify.error).toHaveBeenCalledTimes(1)
  })

  it('les commandes des extensions entrent dans la palette', () => {
    const run = vi.fn()
    const out = buildCommands({ workspaces: [], summaries: [], recents: [], extensions: [{ extension: 'sales', label: 'Pipeline des ventes', run }] })
    const c = out.find((x) => x.group === 'extension')!
    expect(c.label).toBe('Pipeline des ventes')
    c.extension!.run()
    expect(run).toHaveBeenCalled()
  })
})

describe('config.json et le client du plugin', () => {
  it('ne garde que des extensions bien formées, servies par la même origine', () => {
    const href = 'https://erp.example.com/app/xcore-doctype/desk/'
    const out = readExtensions(
      [
        { name: 'sales', url: 'ext/sales/index.js?v=1' },
        { name: 'evil', url: 'https://cdn.example.net/x.js' },
        { name: 'proto', url: '//cdn.example.net/x.js' },
        { name: 'Bad', url: 'ext/Bad/index.js' },
        { name: 'sales', url: 'ext/sales/again.js' },
        'nope',
      ],
      href,
    )
    expect(out).toEqual([{ name: 'sales', url: 'https://erp.example.com/app/xcore-doctype/desk/ext/sales/index.js?v=1' }])
    expect(readExtensions(undefined, href)).toEqual([])
  })

  it('appelle les routes du plugin avec la session, jamais en dehors', async () => {
    expect(pluginBase('/app/xcore-doctype', 'sales')).toBe('/app/sales')
    const calls: HttpRequest[] = []
    const transport: Transport = { request: async (r) => (calls.push(r), { status: 200, json: { ok: true }, headers: {} }) } as Transport
    const ctx = (): ClientContext => ({ baseUrl: '/app/sales', token: 'tok', tenant: 'acme' })
    const client = new PluginClient(transport, ctx)
    await client.get('/pipeline', { stage: 'won' })
    await client.post('deals', { a: 1 })
    expect(calls.map((c) => [c.method, c.url])).toEqual([['GET', '/app/sales/pipeline?stage=won'], ['POST', '/app/sales/deals']])
    expect(calls[0]!.headers).toMatchObject({ Authorization: 'Bearer tok', 'X-Tenant-ID': 'acme' })
    // Ce que le parseur d'URL du navigateur lit comme une remontée : `%2e%2e`, `.%2E`, un `..` coupé par une tabulation, un `..` suivi de `?`.
    for (const bad of ['../stock/x', '/a/../../stock', '//evil.example/x', 'https://evil.example/x', '/a\\b', '/%2e%2e/xcore-doctype/doc/Invoice', '/.%2E/auth/x', '/.\t./auth/x', '/a/..?x', '/a/%2e%2e/%2e%2e/auth', '/x\n', '/a#b', '/%ZZ']) {
      expect(() => client.get(bad)).toThrow(/chemin de plugin invalide/)
    }
  })
})

describe('les composants des extensions dans le Desk', () => {
  function provide(host: ExtensionHost) {
    return { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia()], provide: { [EXTENSIONS as symbol]: host } }
  }

  it('un bloc d’espace d’extension est dessiné par son composant, avec ses props ; absent, un encart', async () => {
    const { host } = makeHost()
    const Block = defineComponent({
      props: { config: { type: Object, required: true }, label: { type: String, default: null } },
      setup(props) {
        const desk = useDesk() // le contexte de SON extension
        return () => h('p', { 'data-testid': 'pipeline' }, `${desk.name}:${(props.config as { stage: string }).stage}:${props.label}`)
      },
    })
    host.handle('sales').blocks.add('pipeline', Block)
    const w = mount(BlockGrid, {
      props: { blocks: [{ type: 'extension', component: 'sales.pipeline', label: 'Pipeline', props: { stage: 'won' } }, { type: 'extension', component: 'crm.ghost' }] },
      global: provide(host),
    })
    expect(w.find('[data-testid="pipeline"]').text()).toBe('sales:won:Pipeline')
    expect(w.find('[data-testid="ext-missing"]').text()).toContain('crm.ghost')
    w.unmount()
  })

  it('les réglages d’un bloc ne deviennent jamais des attributs ni des propriétés DOM (innerHTML, style)', async () => {
    const { host } = makeHost()
    // Un composant ordinaire, à une racine, qui ne déclare rien : avant, chaque clé du bloc tombait sur cette racine.
    host.handle('sales').blocks.add('plain', defineComponent({ setup: () => () => h('div', { 'data-testid': 'plain' }, 'contenu') }))
    const forged = { innerHTML: '<a id="pwn" href="https://evil.example/">x</a>', style: 'position:fixed;inset:0' }
    const w = mount(BlockGrid, { props: { blocks: [{ type: 'extension', component: 'sales.plain', props: forged }] }, global: provide(host) })
    const root = w.find('[data-testid="plain"]')
    expect(root.text()).toBe('contenu')
    expect(w.find('#pwn').exists()).toBe(false)
    expect(root.attributes('style')).toBeUndefined()
    expect(root.attributes('config')).toBe('[object Object]') // non déclarée : UN attribut inerte, jamais les clés une à une
    w.unmount()
  })

  it('un composant en panne réessaie quand on lui donne autre chose à afficher', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { host } = makeHost()
    const Flaky = defineComponent({ props: { id: { type: String, required: true } }, setup(p) {
      if (p.id === 'bad') throw new Error('panne')
      return () => h('p', { 'data-testid': 'flaky' }, p.id)
    } })
    const w = mount(ExtensionSlot, { props: { extension: 'sales', component: Flaky, props: { id: 'bad' } }, global: provide(host) })
    await flushPromises()
    expect(w.find('[data-testid="ext-broken"]').exists()).toBe(true)
    await w.setProps({ props: { id: 'good' } })
    await flushPromises()
    expect(w.find('[data-testid="flaky"]').text()).toBe('good')
    w.unmount()
  })

  it('un composant qui plante est remplacé par un encart, le reste de la page vit', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { host } = makeHost()
    host.handle('sales').blocks.add('boom', defineComponent({ setup() { throw new Error('boom') } }))
    const w = mount(BlockGrid, { props: { blocks: [{ type: 'header', text: 'Titre' }, { type: 'extension', component: 'sales.boom' }] }, global: provide(host) })
    await flushPromises()
    expect(w.find('[data-testid="ext-broken"]').exists()).toBe(true)
    expect(w.text()).toContain('Titre')
    w.unmount()
  })

  it('la fiche montre les actions et les panneaux des extensions et leur passe le document', async () => {
    const record: DocRecord = { doctype: 'Customer', name: 'c1', owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version: 3, docstatus: 0, amended_from: null, data: { customer_name: 'Acme' } }
    const api = { meta: vi.fn(async () => CUSTOMER), get: vi.fn(async (_dt: string, _name: string): Promise<DocRecord> => record), history: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })), connections: vi.fn(async () => []), collab: vi.fn(async () => ({ comments: [], assignments: [], attachments: [] })), list: vi.fn(async () => ({ items: [], total: 0, limit: 1, offset: 0 })), search: vi.fn(async () => []) }
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/app/:doctype/new', name: 'new', component: Dummy }, { path: '/app/:doctype/:name', name: 'form', component: FormView, props: true }, { path: '/app/:doctype', name: 'list', component: Dummy }] })
    const { host } = makeHost(router)
    const seen: unknown[] = []
    const d = host.handle('sales')
    d.form.action({ doctype: 'Customer', label: 'Relancer', primary: true, run: (ctx) => void seen.push(['primary', ctx.record?.name, ctx.values.customer_name]) })
    d.form.action({ doctype: 'Customer', label: 'Archiver', run: (ctx) => void seen.push(['menu', ctx.name]) })
    d.form.panel({
      doctype: 'Customer',
      title: 'Encours',
      // Comme un vrai panneau (celui de `sales`) : il lit son document UNE fois, au montage.
      component: defineComponent({ props: { name: { type: String, required: true } }, setup: (p) => {
        const first = p.name
        const ext = inject(EXTENSION_CONTEXT)!.name
        return () => h('p', { 'data-testid': 'balance' }, `encours de ${first} : ${ext}`)
      } }),
    })
    await router.push('/app/Customer/c1')
    const w = mount(defineComponent({ render: () => h(RouterView) }), {
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api }, [EXTENSIONS as symbol]: host },
      },
    })
    await flushPromises()
    expect(w.find('[data-testid="balance"]').text()).toBe('encours de c1 : sales')
    expect(w.find('[data-testid="ext-panel"] header').text()).toBe('Encours')
    await w.find('[data-testid="ext-action-sales-Relancer"]').trigger('click')
    await openMore(w)
    menuItem('ext-action-sales-Archiver')!.click()
    await flushPromises()
    expect(seen).toEqual([['primary', 'c1', 'Acme'], ['menu', 'c1']])

    // Un autre document : le panneau se remonte pour LUI (avant : monté une fois, il gardait le précédent).
    api.get.mockImplementation(async (_dt: string, name: string) => ({ ...record, name, data: { customer_name: name === 'c2' ? 'Globex' : 'Acme' } }))
    await router.push('/app/Customer/c2')
    await flushPromises()
    expect(w.find('[data-testid="balance"]').text()).toBe('encours de c2 : sales')
    w.unmount()
  })

  it('le reload() d’une action ne réécrit pas la page où l’on est allé entre-temps', async () => {
    const rec = (name: string, label: string): DocRecord => ({ doctype: 'Customer', name, owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version: 1, docstatus: 0, amended_from: null, data: { customer_name: label } })
    let release!: () => void
    const late = new Promise<void>((r) => (release = r))
    let reloading = false
    const api = {
      meta: vi.fn(async () => CUSTOMER),
      get: vi.fn(async (_dt: string, name: string) => {
        if (name === 'c1' && reloading) {
          await late // la relecture de c1 arrive APRÈS l'ouverture de c2
          return rec('c1', 'Acme relu')
        }
        return rec(name, name === 'c1' ? 'Acme' : 'Globex')
      }),
      history: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })), connections: vi.fn(async () => []),
      collab: vi.fn(async () => ({ comments: [], assignments: [], attachments: [] })), list: vi.fn(async () => ({ items: [], total: 0, limit: 1, offset: 0 })), search: vi.fn(async () => []),
    }
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/app/:doctype/new', name: 'new', component: Dummy }, { path: '/app/:doctype/:name', name: 'form', component: FormView, props: true }, { path: '/app/:doctype', name: 'list', component: Dummy }] })
    const { host } = makeHost(router)
    let pending: Promise<void> | null = null
    host.handle('sales').form.action({ doctype: 'Customer', label: 'Relire', primary: true, run: ({ reload }) => {
      reloading = true
      pending = reload()
    } })
    await router.push('/app/Customer/c1')
    const w = mount(defineComponent({ render: () => h(RouterView) }), {
      attachTo: document.body,
      global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api }, [EXTENSIONS as symbol]: host } },
    })
    await flushPromises()
    await w.find('[data-testid="ext-action-sales-Relire"]').trigger('click')
    await router.push('/app/Customer/c2')
    await flushPromises()
    expect(w.find('[data-testid="form-title"]').text()).toBe('Globex')
    release()
    await pending
    await flushPromises()
    expect(w.find('[data-testid="form-title"]').text()).toBe('Globex') // la relecture tardive de c1 n'a rien écrit ici
    w.unmount()
  })
})
