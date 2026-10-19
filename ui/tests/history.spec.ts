import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { describe, expect, it, vi } from 'vitest'
import HistoryPanel from '@/components/HistoryPanel.vue'
import { ApiClient } from '@/lib/api'
import { changeLines, linkValues, showValue, summarizeRows, verbOf } from '@/lib/history'
import { SERVICES } from '@/lib/services'
import type { HttpRequest, Transport } from '@/lib/transport'
import type { HistoryResult, VersionEntry } from '@/lib/types'
import { CUSTOMER, INVOICE } from './fixtures'

const field = (n: string) => CUSTOMER.fields.find((f) => f.fieldname === n)

const entry = (id: number, over: Partial<VersionEntry> = {}): VersionEntry => ({
  id, version: id, action: 'update', user: 'alice', owner: 'alice', creation: new Date(Date.now() - 5 * 60_000).toISOString(),
  changes: { credit_limit: { old: 100, new: 200 } }, ...over,
})

describe('texte des valeurs', () => {
  it('rien, un tableau, un booléen, une date, un nombre', () => {
    expect(showValue(field('credit_limit'), null)).toBe('vide')
    expect(showValue(field('credit_limit'), '')).toBe('vide')
    expect(showValue(field('credit_limit'), 0)).toBe('0') // 0 est une valeur
    expect(showValue(field('blocked'), false)).toBe('Non')
    expect(showValue(field('since'), '2026-10-17')).toBe('17/10/2026')
    expect(showValue(undefined, [1, 2, 3])).toBe('3 lignes')
    expect(showValue(undefined, [1])).toBe('1 ligne')
  })

  it('un lien se lit par le titre du document cible, à défaut par son nom', () => {
    expect(showValue(field('country'), 'FR', (_t, n) => (n === 'FR' ? 'France' : n))).toBe('France')
    expect(showValue(field('country'), 'DE')).toBe('DE')
  })

  it('un champ qui n’existe plus dans la définition garde sa valeur brute', () => {
    expect(showValue(undefined, 'ancienne valeur')).toBe('ancienne valeur')
  })
})

describe('résumé des lignes', () => {
  const r = (...q: number[]) => q.map((x) => ({ q: x }))

  it.each([
    [r(1, 2), r(1, 2, 3), '2 lignes', '3 lignes · 1 ajoutée'],
    [r(1, 2, 3), r(1), '3 lignes', '1 ligne · 2 supprimées'],
    [r(1, 2), r(1, 9), '2 lignes', '2 lignes · 1 modifiée'],
    [r(1, 2), r(9, 9, 3), '2 lignes', '3 lignes · 1 ajoutée, 2 modifiées'],
    [[], r(1), 'vide', '1 ligne · 1 ajoutée'],
    [r(1), [], '1 ligne', 'vide · 1 supprimée'],
    [r(1), r(1), '1 ligne', '1 ligne'],
    [null, r(1, 2), 'vide', '2 lignes · 2 ajoutées'],
  ])('%j -> %j', (before, after, oldText, newText) => {
    expect(summarizeRows(before, after)).toEqual({ old: oldText, new: newText })
  })
})

describe('lignes de modification', () => {
  it('dans l’ordre de la définition, les champs disparus à la fin, avec les libellés', () => {
    const e = entry(1, { changes: { ancien: { old: 1, new: 2 }, blocked: { old: false, new: true }, customer_name: { old: 'A', new: 'B' } } })
    expect(changeLines(CUSTOMER, e).map((c) => [c.field, c.label])).toEqual([
      ['customer_name', 'Nom'],
      ['blocked', 'Bloqué'],
      ['ancien', 'ancien'],
    ])
    expect(changeLines(CUSTOMER, e)[1]).toMatchObject({ old: 'Non', new: 'Oui' })
  })

  it('un champ Table se résume par ses lignes', () => {
    const e = entry(1, { changes: { items: { old: [{ q: 1 }], new: [{ q: 1 }, { q: 2 }] } } })
    expect(changeLines(INVOICE, e)[0]).toMatchObject({ label: 'Lignes', old: '1 ligne', new: '2 lignes · 1 ajoutée' })
  })

  it('les liens cités, avant et après, sont rassemblés par DocType cible', () => {
    const e = [entry(1, { changes: { country: { old: 'FR', new: 'DE' }, customer_name: { old: 'a', new: 'b' } } }), entry(2, { changes: { country: { old: null, new: 'FR' } } })]
    expect([...linkValues(CUSTOMER, e)]).toEqual([['Country', new Set(['FR', 'DE'])]])
  })

  it('le verbe suit l’action', () => {
    expect(verbOf('create')).toMatch(/créé/)
    expect(verbOf('update')).toMatch(/modifié/)
    expect(verbOf('delete')).toMatch(/supprimé/)
  })
})

describe('appel de l’API', () => {
  it('construit l’URL et la pagination', async () => {
    const sent: HttpRequest[] = []
    const transport: Transport = { async request(r) { sent.push(r); return { status: 200, json: { items: [], total: 0, limit: 5, offset: 10 } } } }
    const api = new ApiClient(transport, () => ({ baseUrl: '/api', token: 't', tenant: 'acme' }))
    await api.history('Sales Invoice', 'FA/1 2', { limit: 5, offset: 10 })
    expect(sent[0]!.url).toBe('/api/history/Sales%20Invoice/FA%2F1%202?limit=5&offset=10')
    await api.history('Customer', 'c')
    expect(sent[1]!.url).toBe('/api/history/Customer/c')
  })
})

function mountPanel(pages: HistoryResult[], props: Record<string, unknown> = {}) {
  const history = vi.fn(async (_d: string, _n: string, p: { limit?: number; offset?: number } = {}) => {
    const next = pages.shift()
    if (!next) throw new Error('boom')
    return { ...next, offset: p.offset ?? 0 }
  })
  const w = mount(HistoryPanel, {
    props: { meta: CUSTOMER, doctype: 'Customer', name: 'c1', version: 1, ...props },
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { history, list: vi.fn(), meta: vi.fn() } } },
    },
  })
  return { w, history }
}
const page = (items: VersionEntry[], total = items.length): HistoryResult => ({ items, total, limit: 20, offset: 0 })

describe('panneau d’historique', () => {
  it('ne demande rien tant qu’il est fermé, puis charge à l’ouverture', async () => {
    const { w, history } = mountPanel([page([entry(2), entry(1, { action: 'create', changes: {} })])])
    await flushPromises()
    expect(history).not.toHaveBeenCalled()
    await w.find('header').trigger('click')
    await flushPromises()
    expect(history).toHaveBeenCalledOnce()
    const lines = w.findAll('[data-testid="history-entry"]')
    expect(lines).toHaveLength(2)
    expect(lines[0]!.text()).toContain('alice a modifié')
    expect(lines[0]!.text()).toContain('Plafond de crédit : 100 → 200')
    expect(lines[1]!.text()).toContain('a créé ce document')
    expect(lines[1]!.find('.changes').exists()).toBe(false) // une création ne détaille pas chaque champ
  })

  it('« Voir plus » ajoute la page suivante à la suite', async () => {
    const { w, history } = mountPanel([page([entry(3), entry(2)], 3), page([entry(1)], 3)])
    await w.find('header').trigger('click')
    await flushPromises()
    expect(w.findAll('[data-testid="history-entry"]')).toHaveLength(2)
    await w.find('[data-testid="history-more"]').trigger('click')
    await flushPromises()
    expect(history).toHaveBeenLastCalledWith('Customer', 'c1', { limit: 20, offset: 2 })
    expect(w.findAll('[data-testid="history-entry"]')).toHaveLength(3)
    expect(w.find('[data-testid="history-more"]').exists()).toBe(false) // tout est chargé
  })

  it('une sauvegarde (nouvelle version) recharge le panneau ouvert', async () => {
    const { w, history } = mountPanel([page([entry(1)]), page([entry(2), entry(1)])])
    await w.find('header').trigger('click')
    await flushPromises()
    await w.setProps({ version: 2 })
    await flushPromises()
    expect(history).toHaveBeenCalledTimes(2)
    expect(w.findAll('[data-testid="history-entry"]')).toHaveLength(2)
  })

  it('une sauvegarde ne charge rien tant que le panneau est fermé', async () => {
    const { w, history } = mountPanel([page([entry(1)])])
    await w.setProps({ version: 2 })
    await flushPromises()
    expect(history).not.toHaveBeenCalled()
  })

  it('un échec se dit sans casser le formulaire', async () => {
    const { w } = mountPanel([])
    await w.find('header').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="history-failed"]').exists()).toBe(true)
  })

  it('sans entrée, il le dit', async () => {
    const { w } = mountPanel([page([])])
    await w.find('header').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Aucun historique')
  })
})


describe('historique d’un document soumettable', () => {
  it('soumettre et annuler ont leur verbe', () => {
    expect(verbOf('submit')).toBe('a soumis ce document')
    expect(verbOf('cancel')).toBe('a annulé ce document')
    expect(verbOf('create')).toBe('a créé ce document')
  })
})
