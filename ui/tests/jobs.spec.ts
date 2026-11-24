import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { jobOver, useJob } from '@/composables/useJob'
import { ApiClient, ApiError, type ClientContext } from '@/lib/api'
import type { HttpRequest, HttpResponse, Transport } from '@/lib/transport'
import type { Job } from '@/lib/types'
import JobsView from '@/views/JobsView.vue'
import { captureToasts, flushPromises, mountWith, unmountAll } from './views-fixtures'

/** Les tâches de fond du serveur (traoreera/doctype#71) : le Desk lit leur état, il ne les dépose pas. */
const job = (over: Partial<Job> = {}): Job => ({
  id: 'a'.repeat(32), name: 'accounts.install_chart', key: 'chart:Kounandy', status: 'running', attempts: 1, max_attempts: 5,
  message: '42/100 comptes', owner: 'alice', creation: '2026-11-24T09:00:00+00:00', started_at: '2026-11-24T09:00:01+00:00', finished_at: null,
  ...over,
})

describe('le client', () => {
  function setup(responses: HttpResponse[]) {
    const requests: HttpRequest[] = []
    const transport: Transport = { async request(req) { requests.push(req); return responses.shift() ?? { status: 200, json: {} } } }
    const context: ClientContext = { baseUrl: '/app/xcore-doctype', token: 'tok', tenant: 'acme' }
    return { api: new ApiClient(transport, () => context, vi.fn()), requests }
  }

  it('liste, lit et relance par les routes du serveur', async () => {
    const { api, requests } = setup([{ status: 200, json: { items: [job()] } }, { status: 200, json: job() }, { status: 200, json: job({ status: 'pending' }) }])
    expect((await api.jobs({ name: 'accounts.install_chart', status: 'running', limit: 10 }))[0]!.message).toBe('42/100 comptes')
    await api.job('a/b')
    await api.retryJob('abc')
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      'GET /app/xcore-doctype/jobs?name=accounts.install_chart&status=running&limit=10',
      'GET /app/xcore-doctype/jobs/a%2Fb',
      'POST /app/xcore-doctype/jobs/abc/retry',
    ])
  })
})

describe('suivre une tâche jusqu’à sa fin', () => {
  afterEach(() => vi.useRealTimers())

  it('relit tant qu’elle tourne, s’arrête quand elle est finie', async () => {
    vi.useFakeTimers()
    const states = [job({ message: '1/3' }), job({ message: '2/3' }), job({ status: 'done', message: null })]
    const api = { job: vi.fn(async () => states.shift() ?? job({ status: 'done' })) }
    const scope = effectScope()
    const followed = scope.run(() => useJob(api, () => 'x', 1000))!
    await vi.advanceTimersByTimeAsync(0)
    expect(followed.job.value?.message).toBe('1/3')
    await vi.advanceTimersByTimeAsync(1000)
    expect(followed.job.value?.message).toBe('2/3')
    await vi.advanceTimersByTimeAsync(1000)
    expect(jobOver(followed.job.value)).toBe(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.job).toHaveBeenCalledTimes(3) // finie : plus aucune relecture
    scope.stop()
  })

  it('une relecture qui échoue n’arrête pas le suivi, et la raison se lit', async () => {
    vi.useFakeTimers()
    const api = { job: vi.fn().mockRejectedValueOnce(new ApiError(404, 'job_not_found', 'Tâche introuvable')).mockResolvedValue(job({ status: 'done' })) }
    const scope = effectScope()
    const followed = scope.run(() => useJob(api, () => 'x', 1000))!
    await vi.advanceTimersByTimeAsync(0)
    expect(followed.failure.value).toBe('Tâche introuvable')
    await vi.advanceTimersByTimeAsync(1000)
    expect(followed.failure.value).toBeNull()
    expect(followed.job.value?.status).toBe('done')
    scope.stop()
  })

  it('quand le composant disparaît, plus rien n’est relu ; sans identifiant, rien n’est demandé', async () => {
    vi.useFakeTimers()
    const api = { job: vi.fn(async () => job()) }
    const scope = effectScope()
    scope.run(() => useJob(api, () => 'x', 1000))
    await vi.advanceTimersByTimeAsync(0)
    scope.stop()
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.job).toHaveBeenCalledTimes(1)
    const none = { job: vi.fn(async () => job()) }
    const idle = effectScope()
    idle.run(() => useJob(none, () => null, 1000))
    await vi.advanceTimersByTimeAsync(0)
    expect(none.job).not.toHaveBeenCalled()
    idle.stop()
  })
})

describe('la page « Tâches de fond »', () => {
  const toasts = captureToasts()
  afterEach(() => {
    unmountAll()
    document.body.innerHTML = ''
  })
  const find = (id: string) => document.querySelector(`[data-testid="${id}"]`)

  it('montre l’état, l’avancement et les essais de chaque tâche', async () => {
    mountWith(JobsView, {}, { jobs: vi.fn(async () => [job(), job({ id: 'b'.repeat(32), status: 'done', message: null })]) })
    await flushPromises()
    expect(find('jobs-list')?.textContent).toContain('accounts.install_chart')
    expect(find(`job-message-${'a'.repeat(32)}`)?.textContent).toBe('42/100 comptes')
    expect(find(`job-${'a'.repeat(32)}`)?.querySelector('.status-pill')?.getAttribute('data-state')).toBe('running')
    expect(find(`job-retry-${'a'.repeat(32)}`)).toBeNull() // seule une tâche échouée se relance
  })

  it('dit quand il n’y en a aucune, et la raison quand le serveur refuse', async () => {
    mountWith(JobsView, {}, { jobs: vi.fn(async () => []) })
    await flushPromises()
    expect(find('jobs-empty')).not.toBeNull()
    unmountAll()
    document.body.innerHTML = ''
    mountWith(JobsView, {}, { jobs: vi.fn(async () => { throw new ApiError(503, 'x', 'Le serveur redémarre') }) })
    await flushPromises()
    expect(find('jobs-failed')?.textContent).toBe('Le serveur redémarre')
  })

  it('relance une tâche échouée et relit la liste ; un refus du serveur dit sa raison', async () => {
    const failed = job({ status: 'failed', message: 'serveur comptable injoignable', attempts: 5 })
    const jobs = vi.fn(async () => [failed])
    const retryJob = vi.fn().mockResolvedValueOnce(job({ status: 'pending' })).mockRejectedValueOnce(new ApiError(409, 'job_already_active', 'Une tâche de cette clé est déjà en cours'))
    mountWith(JobsView, {}, { jobs, retryJob })
    await flushPromises()
    ;(find(`job-retry-${failed.id}`) as HTMLButtonElement).click()
    await flushPromises()
    expect(retryJob).toHaveBeenCalledWith(failed.id)
    expect(jobs).toHaveBeenCalledTimes(2)
    ;(find(`job-retry-${failed.id}`) as HTMLButtonElement).click()
    await flushPromises()
    expect(toasts.summaries().at(-1)).toBe('Une tâche de cette clé est déjà en cours')
  })
})
