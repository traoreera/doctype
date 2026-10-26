/**
 * Partage dans le Desk : la carte « Partagé avec » de la fiche (qui peut écrire partage et retire ; un autre ne voit que son partage et peut y
 * renoncer) et la page « Partagés avec moi ».
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import SharesCard from '@/components/collab/SharesCard.vue'
import type { SharedDoc, SharesResult } from '@/lib/types'
import SharedView from '@/views/SharedView.vue'
import { captureToasts, flushPromises, mountWith, unmountAll } from './views-fixtures'

const toasts = captureToasts()
afterEach(() => {
  unmountAll()
  document.body.innerHTML = ''
})

const users = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, { id, email: `${id}@acme.test`, name: `${id}@acme.test` }])))

describe('la carte « Partagé avec » (SharesCard)', () => {
  const setup = (first: SharesResult) => {
    let state = first
    const api = {
      users,
      shares: vi.fn(async () => state),
      share: vi.fn(async (_d: string, _n: string, member: string, write: boolean) => {
        state = { ...state, items: [...state.items.filter((i) => i.member !== member), { member, write, owner: 'ada', creation: '2026-10-26T10:00:00+00:00' }] }
        return { member, write }
      }),
      unshare: vi.fn(async (_d: string, _n: string, member: string) => {
        state = { ...state, items: state.items.filter((i) => i.member !== member) }
      }),
      people: vi.fn(async () => []),
    }
    return { ...mountWith(SharesCard, { doctype: 'Task', name: 't1' }, api), api }
  }

  it('qui écrit le document partage, en lecture ou en écriture, et retire', async () => {
    const { w, api } = setup({ items: [], can_share: true, me: 'ada' })
    await flushPromises()
    expect(w.find('[data-testid="shares-empty"]').exists()).toBe(true)
    await w.find('[data-testid="share-member"] input').setValue('sam@acme.test')
    await w.find('[data-testid="share-write"] input').setValue(true)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(api.share).toHaveBeenCalledWith('Task', 't1', 'sam@acme.test', true)
    expect(w.find('[data-testid="share-sam@acme.test"]').text()).toContain('écriture')
    await w.find('[data-testid="share-remove-sam@acme.test"]').trigger('click')
    await flushPromises()
    expect(api.unshare).toHaveBeenCalledWith('Task', 't1', 'sam@acme.test')
    expect(w.find('[data-testid="share-sam@acme.test"]').exists()).toBe(false)
  })

  it('un membre qui ne peut pas écrire ne voit que son partage, et peut y renoncer ; pas de formulaire', async () => {
    const { w, api } = setup({ items: [{ member: 'sam', write: false, owner: 'ada', creation: '' }], can_share: false, me: 'sam' })
    await flushPromises()
    expect(w.find('form').exists()).toBe(false)
    const leave = w.find('[data-testid="share-remove-sam"]')
    expect(leave.attributes('aria-label')).toBe('Ne plus suivre ce partage')
    await leave.trigger('click')
    await flushPromises()
    expect(api.unshare).toHaveBeenCalledWith('Task', 't1', 'sam')
  })

  it('rien à montrer ni à faire : pas de carte ; un refus du serveur est notifié', async () => {
    const empty = setup({ items: [], can_share: false, me: 'sam' })
    await flushPromises()
    expect(empty.w.find('[data-testid="shares"]').exists()).toBe(false)
    unmountAll()
    const { w, api } = setup({ items: [], can_share: true, me: 'ada' })
    await flushPromises()
    api.share.mockRejectedValueOnce(new Error('inconnu'))
    await w.find('[data-testid="share-member"] input').setValue('x@ailleurs.test')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(toasts.summaries().length).toBe(1)
  })
})

describe('« Partagés avec moi » (SharedView)', () => {
  it('liste les documents partagés avec leur titre, leur niveau et qui les a partagés ; chacun ouvre sa fiche', async () => {
    const items: SharedDoc[] = [{ doctype: 'Task', label: 'Tâche', name: 't1', title: 'Préparer le salon', write: true, owner: 'ada', creation: '2026-10-26T10:00:00+00:00' }]
    const { w } = mountWith(SharedView, {}, { users, sharedWithMe: vi.fn(async () => items) })
    await flushPromises()
    const row = w.find('[data-testid="shared-Task-t1"]')
    expect(row.text()).toContain('Préparer le salon')
    expect(row.text()).toContain('écriture')
    expect(row.find('a').attributes('href')).toContain('/app/Task/t1')
  })

  it('rien de partagé : un message, pas une liste vide', async () => {
    const { w } = mountWith(SharedView, {}, { users, sharedWithMe: vi.fn(async () => []) })
    await flushPromises()
    expect(w.find('[data-testid="shared-empty"]').exists()).toBe(true)
  })
})
