import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError } from '@/lib/api'
import { asUtc, readFlows, readRuns, statusTone } from '@/lib/automations'
import { SERVICES } from '@/lib/services'
import AutomationsView from '@/views/AutomationsView.vue'

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

async function page(automations: unknown) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: AutomationsView }, { path: '/app/:doctype/:name', name: 'form', component: AutomationsView }] })
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

describe('Paramètres › Automatisations', () => {
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

import { definitionOf, draftOf, emptyDraft, eventSuggestions, readActions } from '@/lib/automations'

describe('l’éditeur : un brouillon de formulaire ↔ la définition déployée', () => {
  it('enchaîne les étapes dans l’ordre (sans on_success, xflow s’arrêterait après la première)', () => {
    const draft = emptyDraft()
    draft.name = 'suivi'
    draft.steps = [
      { id: 'a', target: 'xcore-doctype.doc.save', payload: '{"doctype": "Suivi", "data": {"demande": "{{ trigger.name }}"}}', extra: {} },
      { id: 'b', target: 'xpulse.xpulse.publish', payload: '{"user_id": "{{ trigger.user }}", "text": "ok"}', extra: { timeout_seconds: 5 } },
    ]
    const { definition } = definitionOf(draft)
    expect(definition).toEqual({
      name: 'suivi', trigger: { type: 'manual' },
      steps: [
        { id: 'a', type: 'action', plugin: 'xcore-doctype', action: 'doc.save', payload: { doctype: 'Suivi', data: { demande: '{{ trigger.name }}' } }, on_success: 'b' },
        { timeout_seconds: 5, id: 'b', type: 'action', plugin: 'xpulse', action: 'xpulse.publish', payload: { user_id: '{{ trigger.user }}', text: 'ok' }, on_success: null },
      ],
    })
    // …et la relecture rend le même brouillon (ce que le formulaire ne montre pas voyage dans `extra`), charge utile remise en forme.
    const back = draftOf(definition).draft!
    expect(back.steps.map((x) => [x.id, x.target, JSON.parse(x.payload), x.extra])).toEqual(draft.steps.map((x) => [x.id, x.target, JSON.parse(x.payload), x.extra]))
    expect(definitionOf(back).definition).toEqual(definition)
  })

  it('dit chaque faute avant le déploiement', () => {
    const draft = { ...emptyDraft(), name: 'a b', trigger: 'schedule' as const, cron: '0 9 *' }
    draft.steps = [
      { id: 'x', target: 'sans-point', payload: '[1]', extra: {} },
      { id: 'x', target: 'p.a', payload: '{', extra: {} },
    ]
    const { problems } = definitionOf(draft)
    expect(problems).toHaveLength(6)
    expect(problems.join('\n')).toMatch(/Nom/)
    expect(problems.join('\n')).toMatch(/cron/)
    expect(problems.join('\n')).toMatch(/déjà pris/)
    expect(definitionOf({ ...emptyDraft(), name: 'e', trigger: 'event' }).problems.join()).toMatch(/événement/)
  })

  it('un événement et un cron vont dans le déclencheur', () => {
    const ev = definitionOf({ ...emptyDraft(), name: 'e', trigger: 'event', event: 'doctype.Demande.after_submit' }).definition!
    expect(ev.trigger).toEqual({ type: 'event', event_name: 'doctype.Demande.after_submit' })
    const cr = definitionOf({ ...emptyDraft(), name: 'c', trigger: 'schedule', cron: '0 9 * * 1-5' }).definition!
    expect(cr.trigger).toEqual({ type: 'schedule', cron: '0 9 * * 1-5' })
  })

  it('ce que le formulaire ne sait pas montrer s’ouvre en JSON, avec la raison', () => {
    const step = { id: 'a', type: 'action', plugin: 'p', action: 'x', payload: {} }
    expect(draftOf({ name: 'f', steps: [{ id: 'c', type: 'condition' }] }).reason).toMatch(/condition/)
    expect(draftOf({ name: 'f', steps: [{ ...step, on_success: 'zz' }] }).reason).toMatch(/ordre/)
    expect(draftOf({ name: 'f', steps: [{ ...step, on_failure: 'b' }] }).reason).toMatch(/échec/)
    expect(draftOf({ name: 'f', trigger: { type: 'schedule', interval_seconds: 60 }, steps: [step] }).reason).toMatch(/périodique/)
    expect(draftOf({ name: 'f', steps: [] }).reason).toMatch(/aucune/)
  })

  it('propose les événements que le moteur émet pour chaque type, puis le catalogue d’xflow', () => {
    const list = eventSuggestions(['Demande'], ['xauth.login', 'doctype.Demande.after_save'])
    expect(list.slice(0, 2)).toEqual(['doctype.Demande.after_save', 'doctype.Demande.after_submit'])
    expect(list.filter((e) => e === 'doctype.Demande.after_save')).toHaveLength(1)
    expect(list.at(-1)).toBe('xauth.login')
    expect(readActions({ actions: [{ qualified_name: 'z.b' }, { qualified_name: 'a.x', description: 'd' }, { nom: 1 }] })).toEqual([
      { name: 'a.x', description: 'd' }, { name: 'z.b', description: '' },
    ])
  })
})

describe('Paramètres › Automatisations : créer et modifier', () => {
  function client(stored: Record<string, unknown> | null = null) {
    return {
      get: vi.fn(async (path: string) => {
        if (path === '/flows') return FLOWS
        if (path === '/executions') return RUNS
        if (path === '/registry') return { status: 'ok', actions: [{ qualified_name: 'xcore-doctype.doc.save', description: 'Créer ou modifier' }] }
        if (path === '/events') return { status: 'ok', events: [] }
        return { status: 'ok', workflow: stored }
      }),
      post: vi.fn(async (_path: string, _body?: unknown) => ({ status: 'ok', workflow_name: 'nouveau' })),
      delete: vi.fn(async () => ({ status: 'ok' })),
    }
  }
  const field = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLInputElement | HTMLTextAreaElement

  async function type(testid: string, value: string) {
    const el = field(testid)
    el.value = value
    el.dispatchEvent(new Event('input'))
    await flushPromises()
  }

  it('crée un flux depuis le formulaire et le déploie', async () => {
    const c = client()
    const w = await page(c)
    await w.find('[data-testid="automations-create"]').trigger('click')
    await flushPromises()
    await type('flow-name', 'nouveau')
    await type('flow-step-payload-0', '{"doctype": "Suivi", "data": {"demande": "{{ trigger.name }}"}}')
    ;(document.querySelector('[data-testid="flow-deploy"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(c.post).toHaveBeenCalledWith('/flows', {
      name: 'nouveau', trigger: { type: 'manual' },
      steps: [{ id: 'etape_1', type: 'action', plugin: 'xcore-doctype', action: 'doc.save', payload: { doctype: 'Suivi', data: { demande: '{{ trigger.name }}' } }, on_success: null }],
    })
    expect(c.get).toHaveBeenCalledWith('/registry')
  })

  it('un refus d’xflow reste dans l’éditeur, sous le formulaire', async () => {
    const c = client()
    c.post.mockResolvedValueOnce({ status: 'error', code: 'register_error', msg: 'Type de step inconnu' } as never)
    const w = await page(c)
    await w.find('[data-testid="automations-create"]').trigger('click')
    await flushPromises()
    await type('flow-name', 'x')
    ;(document.querySelector('[data-testid="flow-deploy"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(field('flow-server-error')?.textContent).toContain('Type de step inconnu')
  })

  it('modifie un flux : son nom ne change plus, et un flux que le formulaire ne sait pas montrer s’ouvre en JSON', async () => {
    const stored = { name: 'suivi', trigger: { type: 'manual' }, steps: [{ id: 'a', type: 'action', plugin: 'p', action: 'x', payload: {}, on_success: null }], deployed_by: 'alice' }
    const w = await page(client(stored))
    await w.find('[data-testid="flow-edit-suivi"]').trigger('click')
    await flushPromises()
    expect((field('flow-name') as HTMLInputElement).disabled).toBe(true)
    expect((field('flow-name') as HTMLInputElement).value).toBe('suivi')
    w.unmount()
    mounted.splice(0)
    document.body.innerHTML = ''

    const branchy = { name: 'suivi', steps: [{ id: 'c', type: 'condition', condition: { left: '1' } }] }
    const w2 = await page(client(branchy))
    await w2.find('[data-testid="flow-edit-suivi"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-testid="flow-json-only"]')?.textContent).toMatch(/condition/)
    expect((field('flow-json') as HTMLTextAreaElement).value).toContain('"type": "condition"')
    expect((field('flow-json') as HTMLTextAreaElement).value).not.toContain('deployed_by')
  })
})
