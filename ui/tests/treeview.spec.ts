import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TreeView from '@/components/views/TreeView.vue'
import type { TreeResult } from '@/lib/types'
import { TASK, mountWith } from './views-fixtures'

const node = (name: string, children: TreeResult['items'] = []) => ({ name, title: name, children })
const result = (items: TreeResult['items'], extra: Partial<TreeResult> = {}): TreeResult => ({ parent: 'parent_node', items, total: items.length, truncated: false, ...extra })

async function open(tree: ReturnType<typeof vi.fn>) {
  const { w, router } = mountWith(TreeView, { doctype: 'Task', meta: TASK }, { tree })
  await router.isReady()
  await flushPromises()
  return w
}

describe('l’arborescence d’un type', () => {
  it('imbrique chaque document sous son parent et compte les nœuds', async () => {
    const tree = vi.fn(async () => result([node('a', [node('b', [node('c')])]), node('d')], { total: 4 }))
    const w = await open(tree)
    expect(tree).toHaveBeenCalledWith('Task')
    expect(w.find('[data-testid="tree-node-a"]').exists()).toBe(true)
    expect(w.find('[data-testid="tree-node-c"]').exists()).toBe(true)
    expect(w.find('[data-testid="tree-node-d"]').exists()).toBe(true)
    expect(w.find('[data-testid="tree-node-c"]').attributes('aria-level')).toBe('3')
    expect(w.find('[data-testid="tree-count"]').text()).toContain('4')
  })

  it('plie une branche : ses descendants disparaissent, le nœud reste', async () => {
    const w = await open(vi.fn(async () => result([node('a', [node('b')])])))
    expect(w.find('[data-testid="tree-node-b"]').exists()).toBe(true)
    await w.find('[data-testid="tree-toggle-a"]').trigger('click')
    expect(w.find('[data-testid="tree-node-a"]').exists()).toBe(true)
    expect(w.find('[data-testid="tree-node-b"]').exists()).toBe(false)
    await w.find('[data-testid="tree-toggle-a"]').trigger('click')
    expect(w.find('[data-testid="tree-node-b"]').exists()).toBe(true)
  })

  it('affiche un message quand il n’y a rien', async () => {
    const w = await open(vi.fn(async () => result([])))
    expect(w.find('[data-testid="tree-empty"]').exists()).toBe(true)
  })

  it('signale la troncature quand la forêt dépasse la page', async () => {
    const w = await open(vi.fn(async () => result([node('a')], { total: 500, truncated: true })))
    expect(w.find('[data-testid="tree-truncated"]').text()).toContain('500')
  })

  it('affiche l’erreur du serveur au lieu d’une forêt vide', async () => {
    const w = await open(vi.fn(async () => {
      throw new Error('boom')
    }))
    expect(w.find('[data-testid="tree-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="tree-empty"]').exists()).toBe(false)
  })
})
