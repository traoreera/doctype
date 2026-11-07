import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError } from '@/lib/api'
import { asUtc, readFlows, readRuns, statusTone } from '@/lib/automations'
import { SERVICES } from '@/lib/services'
import StepPanel from '@/components/flow/StepPanel.vue'
import AutomationsView from '@/views/AutomationsView.vue'
import FlowBuilderView from '@/views/FlowBuilderView.vue'
import FlowNodesView from '@/views/FlowNodesView.vue'

// Ce que répond xflow (forme mesurée sur un vrai hôte, tests/integration/test_xflow_host.py).
const FLOWS = {
  status: 'ok',
  workflows: [
    { name: 'suivi', version: '1.0.0', trigger: 'manual', description: 'Ouvre le suivi d’une demande approuvée', tags: [] },
    { name: 'relance', trigger: 'cron', description: null },
    { nom: 'cassé' },
  ],
}
const RUNS = {
  status: 'ok',
  runs: [
    {
      run_id: 'r-1', workflow_name: 'suivi', status: 'success', started_at: '2026-11-05T10:57:21.937520', finished_at: '2026-11-05T10:57:21.973308',
      error: null, trigger_payload: { doctype: 'Demande', name: 'D-1', action: 'Approuver', to: 'Approuvée' },
      context: { _triggered_by: 'ipc', _actor_sub: 'bob', steps: { ouvrir: { result: { doc: { data: { secret: 'x' } } } } } },
    },
    { run_id: 'r-2', workflow_name: 'relance', status: 'failed', started_at: '2026-11-05T09:00:00+00:00', error: 'xauth ne reconnaît plus alice', context: { _triggered_by: 'scheduler' } },
  ],
}

describe('les automatisations : ce que l’écran retient de ce que dit xflow', () => {
  it('lit les flux par nom, ignore ce qui n’en est pas un', () => {
    expect(readFlows(FLOWS).map((f) => [f.name, f.trigger])).toEqual([['relance', 'cron'], ['suivi', 'manual']])
    expect(readFlows(null)).toEqual([])
    expect(readFlows({ workflows: 'x' })).toEqual([])
  })

  it('résume une exécution sans garder le résultat de ses étapes, et relie celle d’une transition à son document', () => {
    const [one, two] = readRuns(RUNS)
    expect(one).toEqual({
      id: 'r-1', flow: 'suivi', status: 'success', startedAt: '2026-11-05T10:57:21.937520Z', finishedAt: '2026-11-05T10:57:21.973308Z',
      error: null, via: 'ipc', document: { doctype: 'Demande', name: 'D-1', action: 'Approuver' },
    })
    expect(JSON.stringify(one)).not.toContain('secret')
    expect(two).toMatchObject({ via: 'scheduler', document: null, error: 'xauth ne reconnaît plus alice', finishedAt: null })
  })

  it('lit les dates d’xflow en UTC (il les écrit sans fuseau)', () => {
    expect(asUtc('2026-11-05T10:57:21')).toBe('2026-11-05T10:57:21Z')
    expect(asUtc('2026-11-05T10:57:21+02:00')).toBe('2026-11-05T10:57:21+02:00')
    expect(asUtc(null)).toBeNull()
  })

  it('teinte l’état', () => {
    expect(['success', 'failed', 'timeout', 'running', 'cancelled'].map(statusTone)).toEqual(['ok', 'cancelled', 'cancelled', 'warn', ''])
  })
})

const mounted: VueWrapper[] = []
afterEach(() => {
  mounted.splice(0).forEach((w) => w.unmount())
  document.body.innerHTML = ''
})

// Vue Flow mesure son conteneur : jsdom n'a pas ResizeObserver.
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void { }
    unobserve(): void { }
    disconnect(): void { }
  } as unknown as typeof ResizeObserver
})

/** Les vraies adresses : la liste mène à l'éditeur, qui est une PAGE (`#/automations/<nom>`), pas une boîte de dialogue. */
const ROUTES = [
  { path: '/', name: 'automations', component: AutomationsView },
  { path: '/automations/nodes', name: 'flow-nodes', component: FlowNodesView },
  { path: '/automations/new', name: 'flow-new', component: FlowBuilderView },
  { path: '/automations/:name', name: 'flow', component: FlowBuilderView, props: true },
  { path: '/app/:doctype/:name', name: 'form', component: AutomationsView },
]

async function page(automations: unknown) {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push('/')
  const w = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {}, automations } },
    },
  })
  mounted.push(w)
  await flushPromises()
  return w
}

describe('Automatisations : la liste', () => {
  it('montre les flux et les exécutions, et lance un flux à la demande', async () => {
    const client = {
      get: vi.fn(async (path: string) => (path === '/flows' ? FLOWS : RUNS)),
      post: vi.fn(async () => ({ status: 'pending', run_id: 'r-3' })),
    }
    const w = await page(client)
    expect(client.get).toHaveBeenCalledWith('/executions', { limit: 20 })
    expect(w.find('[data-testid="flow-suivi"]').text()).toContain('À la demande')
    expect(w.find('[data-testid="flow-run-relance"]').exists()).toBe(false) // un flux planifié ne se lance pas d'ici
    expect(w.find('[data-testid="run-r-1"]').text()).toContain('Approuver · D-1')
    expect(w.find('[data-testid="run-r-2"] .status-pill').classes()).toContain('cancelled')
    await w.find('[data-testid="flow-run-suivi"]').trigger('click')
    await flushPromises()
    expect(client.post).toHaveBeenCalledWith('/run/suivi', {})
    expect(client.get).toHaveBeenCalledTimes(4) // relu après le lancement
  })

  it('un 403 se lit « réservé », jamais une panne', async () => {
    const w = await page({ get: vi.fn(async () => { throw new ApiError(403, 'forbidden', 'Interdit') }) })
    expect(w.find('[data-testid="automations-reserved"]').exists()).toBe(true)
    expect(w.find('[data-testid="automations-failure"]').exists()).toBe(false)
  })

  it('un refus d’xflow dans le corps (status: error) est montré tel quel', async () => {
    const w = await page({ get: vi.fn(async () => ({ status: 'error', code: 'tenant_mismatch', msg: 'Organisation différente' })) })
    expect(w.find('[data-testid="automations-failure"]').text()).toContain('Organisation différente')
  })
})

describe('l’éditeur d’automatisation : la page, le graphe et le panneau', () => {
  function client(stored: Record<string, unknown> | null = null) {
    return {
      get: vi.fn(async (path: string) => {
        if (path === '/flows') return FLOWS
        if (path === '/executions') return RUNS
        return { status: 'ok', workflow: stored }
      }),
      post: vi.fn(async (_path: string, _body?: unknown) => ({ status: 'ok', workflow_name: 'nouveau' })),
      delete: vi.fn(async () => ({ status: 'ok' })),
    }
  }

  /** Les types de l'organisation : l'éditeur propose leurs champs au lieu de faire taper du JSON. */
  const CUSTOMER = {
    name: 'Customer', label: 'Client', list_columns: [], layout: [],
    rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
    fields: [
      { fieldname: 'customer_name', label: 'Nom', fieldtype: 'Text', required: true, hidden: false, in_list_view: true },
      { fieldname: 'type', label: 'Catégorie', fieldtype: 'Select', options: ['Particulier', 'Société'], required: false, hidden: false, in_list_view: false },
      { fieldname: 'total', label: 'Total', fieldtype: 'Currency', required: false, hidden: false, in_list_view: false, read_only: true },
    ],
  }
  const api = {
    doctypes: vi.fn(async () => [{ name: 'Customer', label: 'Client', module: 'sales', create: true }]),
    meta: vi.fn(async () => CUSTOMER),
  }

  async function builder(automations: unknown, at = '/automations/new') {
    const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
    await router.push(at)
    await router.isReady()
    const w = mount({ template: '<RouterView />' }, {
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api, automations } },
      },
    })
    mounted.push(w)
    await flushPromises()
    return { w, router }
  }

  const q = (testid: string) => document.body.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
  async function type(testid: string, value: string) {
    const el = q(testid) as HTMLInputElement | HTMLTextAreaElement
    el.value = value
    el.dispatchEvent(new Event('input'))
    await flushPromises()
  }

  it('la liste mène à l’éditeur, et « Nouvelle automatisation » ouvre la page vide', async () => {
    const { w } = await builder(client(), '/')
    expect(w.find('[data-testid="automations-create"]').attributes('href')).toContain('/automations/new')
    expect(w.find('[data-testid="flow-edit-suivi"]').attributes('href')).toContain('/automations/suivi')
  })

  it('un flux neuf n’a que son déclencheur ; poser une étape la relie, et elle vise le document déclencheur', async () => {
    const { w } = await builder(client())
    expect(q('fl-node-trigger')).not.toBeNull()
    expect(q('fl-empty')).not.toBeNull()
    // Aucun JSON, aucune accolade : la palette ne propose que ce qui se règle dans des listes.
    expect(q('fl-add-transform')).toBeNull()
    expect(q('fl-add-template')).toBeNull()

    await w.find('[data-testid="fl-add-action"]').trigger('click')
    expect(q('fl-node-action_1')).not.toBeNull()
    expect(q('fl-step-panel')).not.toBeNull()
    expect(q('fl-gesture')).not.toBeNull()
    await w.find('[data-testid="fl-add-condition"]').trigger('click')
    expect(q('fl-node-condition_1')).not.toBeNull()
  })

  it('déploie l’appel exact du geste choisi : ni JSON tapé, ni accolade écrite à la main', async () => {
    const c = client()
    const { w, router } = await builder(c)
    await type('fl-name', 'nouveau')
    await w.find('[data-testid="fl-add-action"]').trigger('click')
    // Choisir « Soumettre le document » dans la liste des gestes : c'est le seul geste de l'utilisateur, et il vise d'office le
    // document déclencheur. (La liste PrimeVue ne s'ouvre pas en jsdom : on émet ce que le panneau émet.)
    w.findComponent(StepPanel).vm.$emit('patch', {
      plugin: 'xcore-doctype',
      action: 'doc.submit',
      payload: { doctype: '{{ trigger.doctype }}', name: '{{ trigger.name }}', version: '{{ trigger.version }}' },
    })
    await flushPromises()
    ;(q('fl-deploy') as HTMLButtonElement).click()
    await flushPromises()
    expect(c.post).toHaveBeenCalledWith('/flows', {
      name: 'nouveau',
      trigger: { type: 'manual' },
      steps: [
        {
          id: 'action_1',
          type: 'action',
          plugin: 'xcore-doctype',
          action: 'doc.submit',
          payload: { doctype: '{{ trigger.doctype }}', name: '{{ trigger.name }}', version: '{{ trigger.version }}' },
          // Posée d'un clic À DROITE du déclencheur (une carte + 60 px), sa position voyage dans la définition comme celle d'une étape
          // déposée à la souris : sans elle, elle atterrissait hors du cadre (le défaut du 7 nov.).
          _x: 270,
          _y: 0,
        },
      ],
    })
    expect(router.currentRoute.value.params.name).toBe('nouveau') // la création est devenue une modification
  })

  it('un nœud déclaré par un PLUGIN se pose, se règle par son formulaire et part comme une action ordinaire', async () => {
    const catalog = {
      nodes: [{
        plugin: 'mailer', action: 'mail.send', label: 'Envoyer un e-mail', icon: 'envelope',
        fields: [{ name: 'to', label: 'Destinataire', type: 'email', required: true }, { name: 'body', label: 'Message', type: 'longtext' }],
        outputs: [{ path: 'message_id', label: 'Identifiant du message' }],
      }],
      events: [{ plugin: 'mailer', name: 'mailer.received', label: 'E-mail reçu', fields: [{ name: 'sender', label: 'Expéditeur' }] }],
    }
    const flowCatalog = vi.fn(async () => catalog)
    Object.assign(api, { flowCatalog })
    try {
      const c = client()
      const { w } = await builder(c)
      expect(flowCatalog).toHaveBeenCalled()
      await type('fl-name', 'courrier')
      await w.find('[data-testid="fl-add-node-mailer:mail.send"]').trigger('click')
      // La carte dit le nœud du plugin, pas l'appel `mailer.mail.send` ; le panneau montre SON formulaire, pas « action étrangère ».
      expect(q('fl-node-action_1')?.textContent).toContain('Envoyer un e-mail')
      expect(q('fl-node-title')?.textContent).toBe('Envoyer un e-mail')
      expect(q('fl-foreign-action')).toBeNull()
      expect(q('fl-node-outputs')?.textContent).toContain('Identifiant du message')
      expect(q('fl-incomplete-action_1')).not.toBeNull() // le destinataire est obligatoire

      // Sans destinataire, le flux ne part pas : la faute nomme l'étape et le champ.
      ;(q('fl-deploy') as HTMLButtonElement).click()
      await flushPromises()
      expect(document.body.textContent).toContain('Destinataire')
      expect(c.post).not.toHaveBeenCalled()

      await type('fl-node-field-to', 'compta@example.com')
      expect(q('fl-incomplete-action_1')).toBeNull()
      ;(q('fl-deploy') as HTMLButtonElement).click()
      await flushPromises()
      expect(c.post).toHaveBeenCalledWith('/flows', expect.objectContaining({
        steps: [expect.objectContaining({ id: 'action_1', type: 'action', plugin: 'mailer', action: 'mail.send', payload: { to: 'compta@example.com' } })],
      }))
    } finally {
      delete (api as Record<string, unknown>).flowCatalog
    }
  })

  it('un événement de plugin déclenche le flux, et ses clés comme le résultat d’un nœud précédent se reprennent', async () => {
    const catalog = {
      nodes: [{ plugin: 'mailer', action: 'mail.send', label: 'Envoyer un e-mail', icon: 'envelope', fields: [{ name: 'to', label: 'Destinataire', type: 'email' }], outputs: [{ path: 'message_id', label: 'Identifiant du message' }] }],
      events: [{ plugin: 'mailer', name: 'mailer.received', label: 'E-mail reçu', fields: [{ name: 'sender', label: 'Expéditeur' }] }],
    }
    Object.assign(api, { flowCatalog: vi.fn(async () => catalog) })
    try {
      await builder(client({
        name: 'repondre',
        trigger: { type: 'event', event_name: 'mailer.received' },
        steps: [
          { id: 'envoyer', type: 'action', plugin: 'mailer', action: 'mail.send', payload: { to: '{{ trigger.sender }}' }, on_success: 'suivre' },
          { id: 'suivre', type: 'action', plugin: 'mailer', action: 'mail.send', payload: { to: '{{ steps.envoyer.result.message_id }}' } },
        ],
      }), '/automations/repondre')
      // Le déclencheur se reconnaît comme l'événement du plugin, et dit ce que ses étapes pourront reprendre.
      expect(q('fl-event-origin')).not.toBeNull()
      expect(q('fl-event-fields')?.textContent).toContain('Expéditeur')
      expect(q('fl-event-other')).toBeNull()

      ;(q('fl-node-envoyer') as HTMLButtonElement).click()
      await flushPromises()
      expect(q('fl-node-field-to-source')?.textContent).toContain('Du déclencheur')
      expect(q('fl-node-field-to')?.textContent).toContain('Expéditeur')

      ;(q('fl-node-suivre') as HTMLButtonElement).click()
      await flushPromises()
      expect(q('fl-node-field-to-source')?.textContent).toContain('Résultat d’une étape')
      expect(q('fl-node-field-to')?.textContent).toContain('envoyer › Identifiant du message')
    } finally {
      delete (api as Record<string, unknown>).flowCatalog
    }
  })

  it('dit les fautes sans appeler xflow', async () => {
    const c = client()
    const { w } = await builder(c)
    ;(q('fl-deploy') as HTMLButtonElement).click()
    await flushPromises()
    expect(document.body.textContent).toContain('Nom')
    expect(c.post).not.toHaveBeenCalled()

    await type('fl-name', 'bon_nom')
    await w.find('[data-testid="fl-add-action"]').trigger('click')
    ;(q('fl-deploy') as HTMLButtonElement).click()
    await flushPromises()
    // L'étape est posée sans type de document : le flux ne part pas, et la faute nomme l'étape.
    expect(q('fl-problem')?.textContent).toContain('action_1')
    expect(c.post).not.toHaveBeenCalled()
  })

  it('un refus d’xflow reste dans la page, sans rien perdre du tracé', async () => {
    const c = client({
      name: 'refus',
      trigger: { type: 'manual' },
      steps: [{ id: 'action_1', type: 'action', plugin: 'xcore-doctype', action: 'doc.save', payload: { doctype: 'Customer', data: { customer_name: 'X' } } }],
    })
    c.post.mockResolvedValueOnce({ status: 'error', code: 'register_error', msg: 'Type de step inconnu' } as never)
    await builder(c, '/automations/refus')
    ;(q('fl-deploy') as HTMLButtonElement).click()
    await flushPromises()
    expect(q('fl-server-error')?.textContent).toContain('Type de step inconnu')
    expect(q('fl-node-action_1')).not.toBeNull()
  })

  it('modifie un flux : son nom ne change plus, et tout ce que le panneau ne montre pas revient tel quel', async () => {
    const stored = {
      name: 'suivi',
      trigger: { type: 'manual' },
      steps: [
        { id: 'verifie', type: 'condition', condition: { left: '{{ trigger.to }}', operator: '==', right: 'Approuvée' }, if_true: 'ouvrir' },
        { id: 'ouvrir', type: 'action', plugin: 'p', action: 'x', payload: {}, retry: { max_attempts: 4 } },
      ],
      deployed_by: 'alice',
    }
    const c = client(stored)
    const { w } = await builder(c, '/automations/suivi')
    expect((q('fl-name') as HTMLInputElement).disabled).toBe(true)
    expect((q('fl-name') as HTMLInputElement).value).toBe('suivi')
    // On rouvre sur le DÉCLENCHEUR : l'identité du flux est là sans rien chercher.
    expect(q('fl-trigger-panel')).not.toBeNull()
    // Une condition se DESSINE : deux nœuds, et sa comparaison se lit dans le panneau — « Nouvel état », pas `trigger.to`.
    expect(q('fl-node-verifie')).not.toBeNull()
    expect(q('fl-node-ouvrir')).not.toBeNull()
    await w.find('[data-testid="fl-node-verifie"]').trigger('click')
    expect(q('fl-step-panel')?.textContent).toContain('Nouvel état')
    expect(document.body.textContent).not.toContain('{{ trigger.to }}')
    expect(w.findAll('.fl-outlet-yes').length).toBeGreaterThan(0)
    ;(q('fl-deploy') as HTMLButtonElement).click()
    await flushPromises()
    const sent = c.post.mock.calls[0]![1] as Record<string, unknown>
    expect((sent.steps as Record<string, unknown>[])[1]).toMatchObject({ retry: { max_attempts: 4 } })
    expect(sent).not.toHaveProperty('deployed_by') // le serveur le réécrit de toute façon
  })

  it('ce que l’écran ne sait pas régler est montré, gardé intact, et redéployé tel quel', async () => {
    const stored = {
      name: 'suivi',
      trigger: { type: 'event', event_name: 'xauth.login' },
      steps: [
        { id: 'bizarre', type: 'quantique', truc: 1 },
        { id: 'maison', type: 'action', plugin: 'compta', action: 'cloturer', payload: { exercice: 2026 } },
      ],
    }
    const c = client(stored)
    const { w } = await builder(c, '/automations/suivi')
    expect(q('fl-node-bizarre')).not.toBeNull()
    expect(q('fl-event-other')).not.toBeNull() // l'événement d'un autre plugin, montré sur le déclencheur
    await w.find('[data-testid="fl-node-bizarre"]').trigger('click')
    expect(q('fl-unknown-kind')).not.toBeNull() // l'étape d'un type que l'écran ne règle pas
    await w.find('[data-testid="fl-node-maison"]').trigger('click')
    expect(q('fl-foreign-action')?.textContent).toContain('compta.cloturer')
    ;(q('fl-deploy') as HTMLButtonElement).click()
    await flushPromises()
    const sent = c.post.mock.calls[0]?.[1] as Record<string, unknown>
    expect(sent.trigger).toMatchObject({ type: 'event', event_name: 'xauth.login' })
    expect(sent.steps).toMatchObject([
      { id: 'bizarre', type: 'quantique', truc: 1 },
      { id: 'maison', plugin: 'compta', action: 'cloturer', payload: { exercice: 2026 } },
    ])
  })

  it('un 403 d’xflow se lit « réservé », jamais une panne', async () => {
    const c = client()
    c.get.mockImplementation((async (path: string) => {
      if (path === '/flows/suivi') throw new ApiError(403, 'forbidden', 'Interdit')
      return { status: 'ok' }
    }) as never)
    await builder(c, '/automations/suivi')
    expect(q('fl-reserved')).not.toBeNull()
    expect(q('fl-failure')).toBeNull()
  })
})

describe('Nœuds personnalisés : l’organisation décrit une action qu’aucun plugin ne décrit', () => {
  async function nodesPage(api: Record<string, unknown>) {
    const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
    await router.push('/automations/nodes')
    await router.isReady()
    const w = mount({ template: '<RouterView />' }, {
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api, automations: {} } },
      },
    })
    mounted.push(w)
    await flushPromises()
    return w
  }
  const q = (testid: string) => document.body.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
  async function type(testid: string, value: string) {
    const el = q(testid) as HTMLInputElement
    el.value = value
    el.dispatchEvent(new Event('input'))
    await flushPromises()
  }

  it('crée un nœud : le libellé d’un champ donne sa clé, et l’appel exact part au serveur', async () => {
    const saveFlowNode = vi.fn(async (node: Record<string, unknown>) => node)
    const flowNodes = vi.fn(async () => ({ nodes: [], can_edit: true }))
    await nodesPage({ flowNodes, flowCatalog: vi.fn(async () => ({ nodes: [{ plugin: 'mailer', action: 'mail.send', label: 'Envoyer', fields: [], outputs: [] }] })), saveFlowNode })
    expect(q('fn-empty')).not.toBeNull()
    ;(q('fn-create') as HTMLButtonElement).click()
    await flushPromises()
    expect((q('fn-save') as HTMLButtonElement).disabled).toBe(true) // rien de saisi : trois fautes dites
    expect(document.body.querySelectorAll('[data-testid="fn-problem"]').length).toBe(3)
    await type('fn-label', 'Noter dans le journal')
    await type('fn-plugin', 'mailer')
    await type('fn-action', 'mail.log')
    ;(q('fn-add-field') as HTMLButtonElement).click()
    await flushPromises()
    await type('fn-field-label-0', 'Note du jour')
    expect((q('fn-field-name-0') as HTMLInputElement).value).toBe('note_du_jour')
    ;(q('fn-add-output') as HTMLButtonElement).click()
    await flushPromises()
    await type('fn-output-label-0', 'Numéro de la note')
    await type('fn-output-path-0', 'entry')
    expect(q('fn-problem')).toBeNull()
    ;(q('fn-save') as HTMLButtonElement).click()
    await flushPromises()
    expect(saveFlowNode).toHaveBeenCalledWith({
      plugin: 'mailer', action: 'mail.log', label: 'Noter dans le journal', icon: 'bolt',
      fields: [{ name: 'note_du_jour', label: 'Note du jour', type: 'text', required: false }],
      outputs: [{ path: 'entry', label: 'Numéro de la note' }],
    }, undefined)
    expect(flowNodes).toHaveBeenCalledTimes(2) // relue après l'enregistrement
  })

  it('sans le droit de déployer, on consulte sans rien pouvoir changer', async () => {
    const node = { plugin: 'mailer', action: 'mail.log', label: 'Noter', icon: 'book', fields: [{ name: 'note', label: 'Note', type: 'text' }], outputs: [], shadowed: true }
    await nodesPage({ flowNodes: vi.fn(async () => ({ nodes: [node], can_edit: false })), flowCatalog: vi.fn(async () => ({ nodes: [] })) })
    expect(q('fn-reserved')).not.toBeNull()
    expect(q('fn-create')).toBeNull()
    expect(q('fn-node-mailer:mail.log')?.textContent).toContain('Le plugin décrit aussi cette action')
    ;(q('fn-node-mailer:mail.log') as HTMLButtonElement).click()
    await flushPromises()
    expect((q('fn-label') as HTMLInputElement).disabled).toBe(true)
    expect(q('fn-save')).toBeNull()
  })
})
