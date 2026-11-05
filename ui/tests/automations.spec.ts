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
