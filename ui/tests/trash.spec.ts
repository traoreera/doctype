import { ConfirmationEventBus } from 'primevue'
import type { VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import type { TrashItem, TrashResult } from '@/lib/types'
import TrashView from '@/views/TrashView.vue'
import { TASK, captureToasts, flushPromises, mountWith, router, unmountAll } from './views-fixtures'

const toasts = captureToasts()
afterEach(() => {
  unmountAll()
  document.body.innerHTML = ''
})

const users = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, { id, email: `${id}@acme.test`, name: id }])))
const item = (name: string, extra: Partial<TrashItem> = {}): TrashItem => ({
  id: `id-${name}`, name, title: `Titre ${name}`, owner: 'ada', deleted_by: 'bob', deleted_at: '2026-11-02T10:00:00+00:00',
  creation: '2026-10-01T10:00:00+00:00', workflow_state: null, data: { title: `Titre ${name}` }, ...extra,
})
const result = (items: TrashItem[], total = items.length): TrashResult => ({ items, total, limit: 20, offset: 0 })

function withTrash(routes = router()) {
  routes.addRoute({ path: '/trash/:doctype', name: 'trash', component: { render: () => null } })
  return routes
}

const click = async (w: VueWrapper, id: string) => {
  await w.find(`[data-testid="${id}"]`).trigger('click')
  await flushPromises()
}
const confirmWith = async (accept: boolean, action: () => Promise<void>) => {
  const on = (o: { accept?: () => void; reject?: () => void }) => (accept ? o.accept : o.reject)?.()
  ConfirmationEventBus.on('confirm', on as never)
  await action()
  await flushPromises()
  ConfirmationEventBus.off('confirm', on as never)
}

describe('la corbeille d’un type (TrashView)', () => {
  it('liste ce qui a été supprimé : titre, nom, qui et quand', async () => {
    const { w } = mountWith(TrashView, { doctype: 'Task' }, { users, trash: vi.fn(async () => result([item('t1')])) }, withTrash())
    await flushPromises()
    const row = w.find('[data-testid="trash-t1"]')
    expect(row.text()).toContain('Titre t1')
    expect(row.text()).toContain('Supprimé par bob')
    expect(row.text()).toContain('à ada')
  })

  it('restaurer remet le document à sa place et ouvre sa fiche', async () => {
    const restoreTrashed = vi.fn(async () => ({ doctype: 'Task', name: 't1', version: 1, docstatus: 0, owner: 'ada', data: {} }))
    const { w, router: r } = mountWith(TrashView, { doctype: 'Task' }, { users, trash: vi.fn(async () => result([item('t1')])), restoreTrashed }, withTrash())
    await flushPromises()
    await click(w, 'trash-restore-t1')
    expect(restoreTrashed).toHaveBeenCalledWith('Task', 'id-t1')
    expect(r.currentRoute.value.fullPath).toBe('/app/Task/t1')
    expect(toasts.summaries()).toContain('« Titre t1 » est restauré')
  })

  it('un refus du serveur (nom repris…) est montré tel quel, le document reste dans la corbeille', async () => {
    const restoreTrashed = vi.fn(async () => {
      throw new ApiError(409, 'already_exists', 'Task t1 : ce nom est repris par un autre document')
    })
    const { w } = mountWith(TrashView, { doctype: 'Task' }, { users, trash: vi.fn(async () => result([item('t1')])), restoreTrashed }, withTrash())
    await flushPromises()
    await click(w, 'trash-restore-t1')
    expect(toasts.summaries().join(' ')).toContain('ce nom est repris')
    expect(w.find('[data-testid="trash-t1"]').exists()).toBe(true)
  })

  it('supprimer définitivement demande confirmation', async () => {
    const purgeTrashed = vi.fn(async () => undefined)
    const { w } = mountWith(TrashView, { doctype: 'Task' }, { users, trash: vi.fn(async () => result([item('t1'), item('t2')])), purgeTrashed }, withTrash())
    await flushPromises()
    await confirmWith(false, () => click(w, 'trash-purge-t1'))
    expect(purgeTrashed).not.toHaveBeenCalled()
    await confirmWith(true, () => click(w, 'trash-purge-t1'))
    expect(purgeTrashed).toHaveBeenCalledWith('Task', 'id-t1')
    expect(w.find('[data-testid="trash-t1"]').exists()).toBe(false)
    expect(w.find('[data-testid="trash-t2"]').exists()).toBe(true)
  })

  it('les boutons suivent les droits du type : un lecteur voit, sans restaurer ni purger', async () => {
    const reader = { ...TASK, rights: { ...TASK.rights, create: 'none' as const, delete: 'none' as const } }
    const { w } = mountWith(TrashView, { doctype: 'Task' }, { users, meta: async () => reader, trash: vi.fn(async () => result([item('t1')])) }, withTrash())
    await flushPromises()
    expect(w.find('[data-testid="trash-t1"]').exists()).toBe(true)
    expect(w.find('[data-testid="trash-restore-t1"]').exists()).toBe(false)
    expect(w.find('[data-testid="trash-purge-t1"]').exists()).toBe(false)
  })

  it('vide : un message ; plus de 20 : la suite à la demande', async () => {
    const empty = mountWith(TrashView, { doctype: 'Task' }, { users, trash: vi.fn(async () => result([])) }, withTrash())
    await flushPromises()
    expect(empty.w.find('[data-testid="trash-empty"]').exists()).toBe(true)

    const trash = vi.fn(async (_d: string, p: { offset?: number }) => (p.offset ? result([item('t2')], 2) : result([item('t1')], 2)))
    const { w } = mountWith(TrashView, { doctype: 'Task' }, { users, trash }, withTrash())
    await flushPromises()
    await click(w, 'trash-more')
    expect(trash).toHaveBeenLastCalledWith('Task', { limit: 20, offset: 1 })
    expect(w.find('[data-testid="trash-t2"]').exists()).toBe(true)
    expect(w.find('[data-testid="trash-more"]').exists()).toBe(false)
  })
})
