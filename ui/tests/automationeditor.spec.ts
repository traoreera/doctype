import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError } from '@/lib/api'
import type { FlowDefinition } from '@/lib/automation'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta } from '@/lib/types'
import type { FlowRun } from '@/lib/xflow'
import AutomationEditorView from '@/views/AutomationEditorView.vue'
import AutomationsView from '@/views/AutomationsView.vue'

// Vue Flow mesure son conteneur : jsdom n'a pas ResizeObserver.
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver
})

const FLOW: FlowDefinition = {
  name: 'longues_demandes',
  version: '1.0.0',
  trigger: { type: 'event', event_name: 'doctype.Demande.after_transition' },
  steps: [
    { id: 'lire', type: 'action', plugin: 'xcore-doctype', action: 'doc.get', payload: { doctype: 'Demande', name: '{{ trigger.name }}' }, on_success: 'longue' },
    { id: 'longue', type: 'condition', condition: { left: '{{ steps.lire.result.doc.data.jours }}', operator: '>', right: '5' }, if_true: 'commenter' },
    { id: 'commenter', type: 'action', plugin: 'xcore-doctype', action: 'doc.comment.add', payload: { doctype: 'Demande', name: '{{ steps.lire.result.doc.name }}', content: 'x' } },
  ],
  deployed_by: 'alice',
}
const DEMANDE = {
  name: 'Demande', label: 'Demande', layout: [], list_columns: [],
  fields: [{ fieldname: 'objet', fieldtype: 'Text', required: true }, { fieldname: 'jours', fieldtype: 'Int', required: false }],
  rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
  workflow: { states: [{ name: 'Brouillon' }, { name: 'À approuver' }], transitions: [{ action: 'Soumettre', from: 'Brouillon', to: 'À approuver' }] },
} as unknown as DocTypeMeta
const RUN: FlowRun = {
  run_id: 'r1', workflow_name: 'longues_demandes', workflow_version: '1.0.0', status: 'success', trigger_payload: { name: 'D-1' },
  steps: {
    lire: { step_id: 'lire', status: 'success', attempt: 1, started_at: null, finished_at: null, result: { doc: { name: 'D-1', data: { jours: 8 } } }, error: null },
    commenter: { step_id: 'commenter', status: 'failed', attempt: 2, started_at: null, finished_at: null, result: null, error: 'refusé' },
  },
  current_step_id: null, started_at: '2026-10-27T16:45:37.227984', finished_at: null, error: null,
}

let wrapper: VueWrapper | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

function flowsClient(over: Record<string, unknown> = {}) {
  let stored: FlowDefinition | null = structuredClone(FLOW)
  return {
    availability: vi.fn(async () => 'yes'),
    flows: vi.fn(async () => (stored ? [{ name: stored.name, version: '1.0.0', trigger: 'event', description: null, tags: [] }] : [])),
    flow: vi.fn(async (name: string) => {
      if (!stored || stored.name !== name) throw new ApiError(404, 'not_found', 'Workflow introuvable.')
      return structuredClone(stored)
    }),
    deploy: vi.fn(async (d: FlowDefinition) => void (stored = { ...structuredClone(d), deployed_by: 'alice' })),
    remove: vi.fn(async () => void (stored = null)),
    run: vi.fn(async () => 'r1'),
    runs: vi.fn(async () => [RUN]),
    runDetail: vi.fn(async () => RUN),
    cancelRun: vi.fn(async () => {}),
    catalog: vi.fn(async () => [{ qualified_name: 'mailer.send', plugin: 'mailer', action: 'send', description: '', input_schema: {} }]),
    events: vi.fn(async () => []),
    ...over,
  }
}

async function open(path: string, flows = flowsClient()) {
  const api = {
    doctypes: vi.fn(async () => [{ name: 'Demande', label: 'Demande', module: 'flux' }]),
    meta: vi.fn(async () => DEMANDE),
    users: vi.fn(async () => ({})),
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/automations', name: 'automations', component: AutomationsView },
      { path: '/automations/:flow', name: 'automation', component: AutomationEditorView, props: true },
    ],
  })
  await router.push(path)
  await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/app/xcore-doctype', loginUrl: null }, api, flows } } },
  })
  await flushPromises()
  return { router, flows, api }
}
const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const click = async (id: string) => {
  q(id)!.click()
  await flushPromises()
}
const type = async (id: string, value: string, commit = false) => {
  const input = q(id) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  if (commit) input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
  await flushPromises()
}
const deployed = (flows: ReturnType<typeof flowsClient>) => flows.deploy.mock.calls.at(-1)![0] as FlowDefinition

describe('la liste des automatisations', () => {
  it('xflow absent : la page le dit ; réservé : elle dit quel droit manque', async () => {
    await open('/automations', flowsClient({ availability: vi.fn(async () => 'absent') }))
    expect(q('flows-absent')).not.toBeNull()
    wrapper!.unmount()
    wrapper = null
    await open('/automations', flowsClient({ availability: vi.fn(async () => 'reserved') }))
    expect(q('flows-reserved')?.textContent).toContain('xflow:workflows:read')
  })

  it('chaque automatisation avec son déclencheur et sa dernière exécution ; un nom technique invalide n’ouvre rien', async () => {
    const { router } = await open('/automations')
    expect(q('flow-longues_demandes')?.textContent).toContain('Événement')
    expect(q('flow-longues_demandes')?.textContent).toContain('Réussie')
    await click('flow-create')
    await type('flow-new-name', 'Mon flux')
    expect((q('flow-new-ok') as HTMLButtonElement).disabled).toBe(true)
    await type('flow-new-name', 'longues_demandes')
    expect(document.body.textContent).toContain('porte déjà ce nom')
    await type('flow-new-name', 'relances')
    await click('flow-new-ok')
    expect(router.currentRoute.value.fullPath).toBe('/automations/relances?new=1')
  })
})

describe('l’éditeur d’une automatisation (le canevas)', () => {
  it('dessine le déclencheur et une carte par étape, chacune avec ses sorties nommées', async () => {
    await open('/automations/longues_demandes')
    expect(q('af-node-__trigger')?.textContent).toContain('Quand un document Demande change d’état')
    expect(q('af-node-lire')?.textContent).toContain('Lire un document · Demande')
    expect(q('af-node-longue')?.textContent).toContain('vrai')
    expect(q('af-node-longue')?.textContent).toContain('faux')
    expect(document.body.textContent).toContain('Enregistrée par')
  })

  it('renommer une étape réécrit ses références ; enregistrer renvoie la définition SANS qui l’a déployée', async () => {
    const { flows } = await open('/automations/longues_demandes')
    await click('af-choose-lire')
    expect(q('af-panel-title')?.textContent).toContain('lire')
    await type('af-id', 'demande', true)
    await click('af-save')
    const sent = deployed(flows)
    expect('deployed_by' in sent).toBe(false) // xflow le pose lui-même : qui déploie signe
    expect(sent.steps[0]!.id).toBe('demande')
    expect((sent.steps[1]!.condition as { left: string }).left).toBe('{{ steps.demande.result.doc.data.jours }}')
    expect(sent.steps[2]!.payload).toMatchObject({ name: '{{ steps.demande.result.doc.name }}' })
  })

  it('un champ de Document se remplit avec la valeur typée du champ (un entier reste un entier)', async () => {
    const { flows } = await open('/automations/longues_demandes')
    await click('af-choose-commenter')
    await click('af-add-doc.patch') // accrochée à « ensuite » de commenter
    expect(q('af-node-modifier')).not.toBeNull()
    expect(q('af-docname')).not.toBeNull()
    // Ajouter le champ « jours » puis taper 10 : le moteur refuserait « "10" » pour un Int.
    const add = wrapper!.findAllComponents({ name: 'Select' }).find((c) => c.attributes('data-testid') === 'af-add-field')!
    add.vm.$emit('update:modelValue', 'jours')
    await flushPromises()
    await type('af-data-value-jours', '10')
    await click('af-save')
    const step = deployed(flows).steps.find((s) => s.id === 'modifier')!
    expect(step).toMatchObject({ plugin: 'xcore-doctype', action: 'doc.patch', payload: { doctype: 'Demande', data: { jours: 10 } } })
    expect(deployed(flows).steps.find((s) => s.id === 'commenter')!.on_success).toBe('modifier')
  })

  it('ce qui empêche d’enregistrer est dit, et le bouton reste fermé', async () => {
    await open('/automations/longues_demandes')
    await click('af-add-condition') // posée seule : reliée à rien, sans valeur à comparer
    await click('af-choose-condition')
    expect(q('af-node-condition')).not.toBeNull()
    const pane = document.body.querySelector<HTMLElement>('.vue-flow__pane')!
    pane.click()
    await flushPromises()
    expect((q('af-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('une automatisation neuve (404 + ?new=1) part d’un déclencheur manuel ; un nom inconnu sans ?new est « introuvable »', async () => {
    const flows = flowsClient()
    await open('/automations/relances?new=1', flows)
    expect(q('af-node-__trigger')?.textContent).toContain('Manuel')
    await click('af-choose-__trigger')
    await click('af-add-doc.get') // le déclencheur choisi : la nouvelle étape devient la première
    expect((q('af-save') as HTMLButtonElement).disabled).toBe(true) // déclencheur manuel : aucun type de document à reprendre
    wrapper!.findAllComponents({ name: 'Select' }).find((c) => c.attributes('data-testid') === 'af-doctype')!.vm.$emit('update:modelValue', 'Demande')
    await flushPromises()
    await click('af-save')
    expect(deployed(flows)).toMatchObject({ name: 'relances', entry_step: 'lire', trigger: { type: 'manual' } })
    wrapper!.unmount()
    wrapper = null
    await open('/automations/inconnue')
    expect(q('af-not-found')).not.toBeNull()
  })

  it('une exécution posée sur le canevas : chaque étape porte son statut, choisie elle montre son résultat ou son erreur', async () => {
    await open('/automations/longues_demandes')
    await click('af-tab-runs')
    await click('af-run-r1')
    expect(q('af-node-lire')?.getAttribute('data-tone')).toBe('ok')
    expect(q('af-node-commenter')?.getAttribute('data-tone')).toBe('bad')
    expect(q('af-node-longue')?.getAttribute('data-tone')).toBeNull() // absente de l'exécution : aucun statut inventé
    await click('af-choose-commenter')
    expect(q('af-step-error')?.textContent).toContain('refusé')
    expect((q('af-save') as HTMLButtonElement).disabled).toBe(true) // on regarde, on n'édite pas
    await click('af-run-hide')
    expect(q('af-node-lire')?.getAttribute('data-tone')).toBeNull()
  })

  it('sans le droit d’écrire, tout se lit et rien ne se modifie', async () => {
    await open('/automations/longues_demandes')
    const { useSession } = await import('@/stores/session')
    useSession().permissions = ['xflow:workflows:read', 'xflow:runs:read']
    await flushPromises()
    expect(q('af-readonly')?.textContent).toContain('xflow:workflows:write')
    expect(q('af-save')).toBeNull()
    expect((q('af-add-doc.get') as HTMLButtonElement).disabled).toBe(true)
    expect(q('af-run')).toBeNull()
  })
})
