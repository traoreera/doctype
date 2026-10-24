/**
 * Onglets de fiche : la barre (motif « tabs » de l'APG), les sections de l'onglet ouvert seulement, les champs des autres onglets toujours
 * montés, la pastille des erreurs et le focus d'une erreur qui ouvre son onglet. Une fiche sans `tab` s'affiche comme avant.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import FormView from '@/views/FormView.vue'
import { ApiError } from '@/lib/api'
import { errorsByTab, formTabs, tabOfField } from '@/lib/form'
import { SERVICES } from '@/lib/services'
import type { DocRecord, DocTypeMeta } from '@/lib/types'
import { CUSTOMER } from './fixtures'

const TABBED: DocTypeMeta = {
  ...CUSTOMER,
  layout: [
    { tab: 'Identité', section: null, collapsible: false, collapsed: false, columns: [['customer_name', 'type'], ['country', 'since']] },
    { tab: 'Société', section: 'Société', collapsible: false, collapsed: false, columns: [['company_id']] },
    { tab: 'Crédit', section: 'Crédit', collapsible: false, collapsed: false, columns: [['credit_limit', 'approver'], ['blocked']] },
  ],
}

const rec = (name: string, data: Record<string, unknown>, version = 1): DocRecord => ({
  doctype: 'Customer', name, owner: 'u', creation: '2026-01-01T00:00:00+00:00', modified: '2026-01-01T00:00:00+00:00', version, docstatus: 0, amended_from: null, data,
})

function setup(meta: DocTypeMeta = TABBED, overrides: Record<string, unknown> = {}) {
  const api = {
    meta: vi.fn(async () => meta),
    doctypes: vi.fn(async () => []),
    get: vi.fn(async (_d: string, name: string) => rec(name, { customer_name: name.toUpperCase(), type: 'Particulier', credit_limit: 0 })),
    create: vi.fn(),
    update: vi.fn(),
    history: vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 })),
    search: vi.fn(async () => []),
    list: vi.fn(async () => ({ items: [], total: 0, limit: 1, offset: 0 })),
    ...overrides,
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/app/:doctype/new', name: 'new', component: FormView, props: (r) => ({ doctype: r.params.doctype, name: null }) },
      { path: '/app/:doctype/:name', name: 'form', component: FormView, props: true },
      { path: '/app/:doctype', name: 'list', component: { render: () => h('div') } },
    ],
  })
  const w = mount(defineComponent({ render: () => h(RouterView) }), {
    attachTo: document.body,
    global: {
      plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } },
    },
  })
  return { w, api, router }
}

let current: ReturnType<typeof setup> | null = null
afterEach(() => {
  current?.w.unmount()
  current = null
  document.body.innerHTML = ''
})

const open = async (path: string, meta?: DocTypeMeta, overrides?: Record<string, unknown>) => {
  current = setup(meta, overrides)
  await current.router.push(path)
  await flushPromises()
  return current
}
/** Les sections MONTRÉES (v-show laisse les autres dans la page, cachées). */
const shown = (w: ReturnType<typeof setup>['w']) =>
  w.findAll('section.form-section').filter((s) => (s.element as HTMLElement).style.display !== 'none').map((s) => s.attributes('data-tab'))
const tabNames = (w: ReturnType<typeof setup>['w']) => w.findAll('[role="tab"]').map((b) => b.text())

describe('les fonctions des onglets', () => {
  it('l’ordre des onglets, l’onglet d’un champ (ou d’une cellule), les erreurs par onglet', () => {
    expect(formTabs(TABBED.layout)).toEqual(['Identité', 'Société', 'Crédit'])
    expect(formTabs(CUSTOMER.layout)).toEqual([])
    expect(tabOfField(TABBED.layout, 'credit_limit')).toBe('Crédit')
    expect(tabOfField(TABBED.layout, 'lines.0.qty')).toBeNull() // une table que la disposition ne place pas
    expect(errorsByTab(TABBED.layout, ['credit_limit', 'approver', 'customer_name', 'nope'])).toEqual({ Crédit: 2, Identité: 1 })
    // Une erreur de CELLULE (`items.0.qty`) est celle de la table qui la porte, rangée dans son onglet.
    const withTable = [...TABBED.layout, { tab: 'Lignes', section: null, collapsible: false, collapsed: false, columns: [['items']] }]
    expect(tabOfField(withTable, 'items.0.qty')).toBe('Lignes')
    expect(errorsByTab(withTable, ['items.0.qty', 'items.2.item'])).toEqual({ Lignes: 2 })
  })
})

describe('FormView : une fiche à onglets', () => {
  it('montre la barre et le premier onglet ; les champs des autres onglets restent montés', async () => {
    const { w } = await open('/app/Customer/c1')
    expect(tabNames(w)).toEqual(['Identité', 'Crédit']) // « Société » : son seul champ est masqué par sa condition (type ≠ Entreprise)
    expect(w.find('[role="tab"][aria-selected="true"]').text()).toBe('Identité')
    expect(shown(w)).toEqual(['Identité'])
    expect(w.find('[data-field="credit_limit"]').exists()).toBe(true) // monté, seulement caché
    expect(w.find('[role="tabpanel"]').attributes('aria-labelledby')).toBe(w.find('[role="tab"][aria-selected="true"]').attributes('id'))
    for (const tab of w.findAll('[role="tab"]')) expect(document.getElementById(tab.attributes('aria-controls')!)?.getAttribute('role')).toBe('tabpanel')
  })

  it('un clic ouvre un onglet ; les flèches, Début et Fin se déplacent d’onglet en onglet', async () => {
    const { w } = await open('/app/Customer/c1')
    await w.find('[data-testid="form-tab-Crédit"]').trigger('click')
    expect(shown(w)).toEqual(['Crédit'])
    await w.find('[data-testid="form-tab-Crédit"]').trigger('keydown', { key: 'ArrowRight' }) // dernier onglet : on revient au premier
    expect(shown(w)).toEqual(['Identité'])
    expect(document.activeElement).toBe(w.find('[data-testid="form-tab-Identité"]').element)
    await w.find('[data-testid="form-tab-Identité"]').trigger('keydown', { key: 'End' })
    expect(shown(w)).toEqual(['Crédit'])
    expect(w.findAll('[role="tab"]').map((b) => b.attributes('tabindex'))).toEqual(['-1', '0'])
  })

  it('un onglet apparaît quand une condition montre son champ', async () => {
    const { w } = await open('/app/Customer/c1')
    await w.find('[data-field="type"]').findComponent({ name: 'Select' }).vm.$emit('update:modelValue', 'Entreprise')
    await flushPromises()
    expect(tabNames(w)).toEqual(['Identité', 'Société', 'Crédit'])
  })

  it('une erreur du serveur dans un autre onglet : une pastille sur l’onglet, et le focus l’ouvre', async () => {
    const { w } = await open('/app/Customer/c1', TABBED, {
      update: vi.fn(async () => {
        throw new ApiError(422, 'validation_failed', 'x', [{ field: 'credit_limit', msg: 'Plafond dépassé', type: 'hook_rejected' }])
      }),
    })
    await w.find('[data-field="customer_name"] input').setValue('Changé')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="form-tab-errors-Crédit"]').text()).toBe('1')
    expect(w.find('[data-testid="form-tab-errors-Crédit"]').attributes('aria-label')).toBe('1 erreur dans cet onglet')
    expect(shown(w)).toEqual(['Crédit']) // le premier champ à corriger est là-bas : son onglet s'est ouvert
    expect((document.activeElement as HTMLElement).closest('[data-field]')?.getAttribute('data-field')).toBe('credit_limit')
  })

  it('enregistrer garde l’onglet ouvert ; un autre document s’ouvre sur le premier', async () => {
    const { w, router } = await open('/app/Customer/c1', TABBED, {
      update: vi.fn(async (_d: string, name: string, data: Record<string, unknown>) => rec(name, data, 2)),
    })
    await w.find('[data-testid="form-tab-Crédit"]').trigger('click')
    await w.find('[data-field="blocked"] input').setValue(true)
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(shown(w)).toEqual(['Crédit'])
    await router.push('/app/Customer/c2')
    await flushPromises()
    expect(shown(w)).toEqual(['Identité'])
  })

  it('quitter un formulaire NEUF pour un document existant ouvre celui-ci sur son premier onglet', async () => {
    const { w, router } = await open('/app/Customer/new')
    await w.find('[data-testid="form-tab-Crédit"]').trigger('click')
    expect(shown(w)).toEqual(['Crédit'])
    await router.push('/app/Customer/c2') // même composant, autre document : rien ne doit rester de l'onglet choisi
    await flushPromises()
    expect(shown(w)).toEqual(['Identité'])
  })

  it('créer un document garde l’onglet où l’on était', async () => {
    const { w, router } = await open('/app/Customer/new', TABBED, {
      create: vi.fn(async (_d: string, data: Record<string, unknown>) => rec('c9', data)),
    })
    await w.find('[data-field="customer_name"] input').setValue('Neuf')
    await w.find('[data-testid="form-tab-Crédit"]').trigger('click')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/app/Customer/c9')
    expect(shown(w)).toEqual(['Crédit'])
  })

  it('une fiche sans onglets s’affiche comme avant', async () => {
    const { w } = await open('/app/Customer/c1', CUSTOMER)
    expect(w.find('[role="tablist"]').exists()).toBe(false)
    expect(w.find('[role="tabpanel"]').exists()).toBe(false)
    expect(shown(w)).toEqual([undefined, undefined])
  })
})
