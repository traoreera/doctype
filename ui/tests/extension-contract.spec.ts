/**
 * Les extensions LIVRÉES respectent le contrat (D28) : celle du plugin de test `sales` et le modèle que pose
 * `xcore-doctype new-desk-extension`. On les charge pour de vrai dans un hôte d'extensions, comme le Desk au démarrage.
 */
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ExtensionHost } from '@/extensions/host'
import { loadExtensions, type Importer } from '@/extensions/loader'

function host() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })
  const notify = { success: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() }
  return new ExtensionHost({ api: {} as never, router, notify, plugin: () => ({}) as never })
}

// Des modules JS hors de ui/ (fichiers de plugin, sans types) : importés par un chemin LITTÉRAL, que Vite résout à la transformation
// (un chemin calculé à l'exécution ne sort pas de ui/).
const MODULES: Record<string, () => Promise<{ default?: unknown }>> = {
  // @ts-expect-error -- module JS d'un plugin, sans déclaration de types
  sales: () => import('../../tests/support/plugins/sales/desk/index.js'),
  // @ts-expect-error -- module JS du modèle, sans déclaration de types
  template: () => import('../../src/xcore_doctype/desk_template/index.js'),
}
const SALES = 'sales'
const TEMPLATE = 'template'
const load: Importer = (key) => MODULES[key]!()

describe('les extensions livrées se chargent', () => {
  it('celle du plugin `sales` : panneau et action sur Client, page, lien, commande', async () => {
    const h = host()
    await loadExtensions(h, [{ name: 'sales', url: SALES }], { importer: load })
    expect(h.failures).toEqual([])
    expect(h.loaded).toEqual(['sales'])
    expect(h.formPanelItems.map((p) => [p.doctype, p.title])).toEqual([['Customer', 'Factures']])
    expect(h.formActionItems.map((a) => a.label)).toEqual(['Nouvelle facture'])
    expect(h.routeItems.map((r) => r.fullPath)).toEqual(['/x/sales/overview'])
    expect(h.sidebarLinks('sales').map((l) => l.to)).toEqual(['/x/sales/overview'])
    expect(h.commandItems).toHaveLength(1)
  })

  it('le modèle de `new-desk-extension` respecte le contrat (son nom est posé par la commande : on le pose ici)', async () => {
    const h = host()
    const named: Importer = async (path) => {
      const mod = (await load(path)) as { default: { name: string } }
      expect(mod.default.name).toBe('__PLUGIN__') // la commande remplace ce marqueur (tests/unit/test_cli.py)
      return { default: { ...mod.default, name: 'crm' } }
    }
    await loadExtensions(h, [{ name: 'crm', url: TEMPLATE }], { importer: named })
    expect(h.failures).toEqual([])
    expect(h.routeItems.map((r) => [r.fullPath, r.title])).toEqual([['/x/crm/home', '__LABEL__']])
    expect(h.commandItems).toHaveLength(1)
  })
})
