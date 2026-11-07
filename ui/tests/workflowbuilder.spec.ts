import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, WorkflowDef, WorkflowOverride } from '@/lib/types'
import WorkflowBuilderView from '@/views/WorkflowBuilderView.vue'

// Vue Flow mesure son conteneur : jsdom n'a pas ResizeObserver.
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void { }
    unobserve(): void { }
    disconnect(): void { }
  } as unknown as typeof ResizeObserver
})

const LEAVE: WorkflowDef = {
  states: [{ name: 'Brouillon' }, { name: 'À approuver' }, { name: 'Approuvée', docstatus: 'submitted' }, { name: 'Refusée', docstatus: 'cancelled' }],
  transitions: [
    { action: 'Soumettre', from: 'Brouillon', to: 'À approuver' },
    { action: 'Approuver', from: 'À approuver', to: 'Approuvée', not_owner: true },
    { action: 'Refuser', from: 'À approuver', to: 'Refusée', not_owner: true },
    { action: 'Reprendre', from: 'À approuver', to: 'Brouillon', condition: 'comment' },
  ],
}
const GRANTS = { Soumettre: 'hr:leave_request:workflow_soumettre', Approuver: 'hr:leave_request:workflow_approuver', Refuser: 'hr:leave_request:workflow_refuser', Reprendre: 'hr:leave_request:workflow_reprendre' }
const ALL = { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' } as DocTypeMeta['rights']
const meta = (rights = ALL, workflow: WorkflowDef | null = LEAVE): DocTypeMeta => ({
  name: 'Leave Request', label: 'Demande de congé', fields: [], layout: [], list_columns: [], rights, ...(workflow ? { workflow } : {}),
})

let wrapper: VueWrapper | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

async function open(m: DocTypeMeta, config: WorkflowOverride = {}) {
  const api = {
    meta: vi.fn(async () => m),
    workflowOverride: vi.fn(async () => ({ doctype: 'Leave Request', config, definition: LEAVE, grants: GRANTS, grant_choices: { 'hr:leave_request:write': 'Créer/modifier Demande de congé', ...Object.fromEntries(Object.values(GRANTS).map((g) => [g, g])) }, submittable: true })),
    saveWorkflowOverride: vi.fn(async (_d: string, c: WorkflowOverride) => ({ doctype: 'Leave Request', config: c })),
    removeWorkflowOverride: vi.fn(async () => { }),
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workflow/:doctype', name: 'workflow', component: WorkflowBuilderView, props: true },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
    ],
  })
  await router.push('/workflow/Leave%20Request')
  await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } } },
  })
  await flushPromises()
  return { w: wrapper, api }
}
const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)

describe('l’éditeur du circuit d’approbation (le graphe)', () => {
  it('dessine un nœud par état et une étiquette par transition ; chacun est UN bouton au clavier', async () => {
    await open(meta())
    expect(q('wf-title')?.textContent).toContain('Demande de congé')
    for (const s of ['Brouillon', 'À approuver', 'Approuvée', 'Refusée']) expect(q(`wf-state-${s}`)).not.toBeNull()
    for (const a of ['Soumettre', 'Approuver', 'Refuser', 'Reprendre']) expect(q(`wf-edge-${a}`)).not.toBeNull()
    expect(q('wf-state-Approuvée')?.textContent).toContain('document soumis')
    expect(document.body.querySelector('.vue-flow__node[tabindex], .vue-flow__edge[tabindex]')).toBeNull() // pas de second arrêt de tabulation
  })

  it('choisir une transition montre ce que l’application exige : le droit, la condition, l’auteur', async () => {
    await open(meta())
    q('wf-edge-Reprendre')!.click()
    await flushPromises()
    expect(q('wf-panel-title')?.textContent).toBe('Reprendre')
    expect(q('wf-grant')?.textContent).toBe('hr:leave_request:workflow_reprendre')
    expect(q('wf-panel')?.textContent).toContain('comment')
    q('wf-edge-Approuver')!.click()
    await flushPromises()
    expect((q('wf-owner')!.querySelector('input') as HTMLInputElement).disabled).toBe(true) // exigé par l'application : grisé
  })

  it('fermer une transition puis enregistrer envoie le resserrement au serveur', async () => {
    const { api } = await open(meta())
    expect((q('wf-save') as HTMLButtonElement).disabled).toBe(true) // rien à enregistrer
    q('wf-edge-Refuser')!.click()
    await flushPromises()
      ; (q('wf-closed')!.querySelector('input') as HTMLInputElement).click()
    await flushPromises()
    expect(q('wf-edge-Refuser')?.className).toContain('wf-edge-label-closed')
    q('wf-save')!.click()
    await flushPromises()
    expect(api.saveWorkflowOverride).toHaveBeenCalledWith('Leave Request', { transitions: { Refuser: { closed: true } } })
  })

  it('un cul-de-sac se dit avant d’enregistrer, et l’enregistrement est impossible', async () => {
    await open(meta(), { transitions: { Approuver: { closed: true }, Refuser: { closed: true } } })
    q('wf-edge-Reprendre')!.click()
    await flushPromises()
      ; (q('wf-closed')!.querySelector('input') as HTMLInputElement).click()
    await flushPromises()
    expect(q('wf-deadend')?.textContent).toContain('À approuver')
    expect(q('wf-state-À approuver')?.className).toContain('wf-state-stuck')
    expect((q('wf-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('rouvrir tout ce qui était fermé retire l’habillage au lieu d’enregistrer un habillage vide', async () => {
    const { api } = await open(meta(), { transitions: { Refuser: { closed: true } } })
    q('wf-edge-Refuser')!.click()
    await flushPromises()
      ; (q('wf-closed')!.querySelector('input') as HTMLInputElement).click()
    await flushPromises()
    q('wf-save')!.click()
    await flushPromises()
    expect(api.removeWorkflowOverride).toHaveBeenCalledWith('Leave Request')
    expect(api.saveWorkflowOverride).not.toHaveBeenCalled()
  })

  it('qui ne modifie pas TOUS les documents voit le circuit, sans rien pouvoir y changer', async () => {
    await open(meta({ ...ALL, write: 'restricted' }))
    expect(q('wf-reserved')).not.toBeNull()
    expect(q('wf-save')).toBeNull()
    q('wf-edge-Refuser')!.click()
    await flushPromises()
    expect((q('wf-closed')!.querySelector('input') as HTMLInputElement).disabled).toBe(true)
  })

  it('ajoute un état puis la transition qui y mène, par le panneau, et les enregistre pour l’organisation', async () => {
    const { api } = await open(meta())
    q('wf-add-state')!.click()
    await flushPromises()
    const name = q('wf-new-state-name') as HTMLInputElement
    name.value = 'Visée'
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    q('wf-new-state-ok')!.click()
    await flushPromises()
    // L'état est là, mais rien n'y mène encore : enregistrer est impossible et la page le dit.
    expect(q('wf-state-Visée')).not.toBeNull()
    expect(q('wf-pending')?.textContent).toContain('Visée')
    expect((q('wf-save') as HTMLButtonElement).disabled).toBe(true)
    // Le panneau enchaîne sur la transition : son nom est proposé (« Vers Visée »), sa cible aussi ; il manque qui peut la faire.
    expect((q('wf-new-action') as HTMLInputElement).value).toBe('Vers Visée')
    expect((q('wf-new-transition-ok') as HTMLButtonElement).disabled).toBe(true)
    const grant = wrapper!.findAllComponents({ name: 'Select' }).find((c) => c.attributes('data-testid') === 'wf-new-grant')!
    grant.vm.$emit('update:modelValue', 'hr:leave_request:write')
    await flushPromises()
    q('wf-new-transition-ok')!.click()
    await flushPromises()
    expect(q('wf-pending')).toBeNull()
    expect(q('wf-edge-Vers Visée')).not.toBeNull()
    q('wf-save')!.click()
    await flushPromises()
    expect(api.saveWorkflowOverride).toHaveBeenCalledWith('Leave Request', {
      states: [{ name: 'Visée', docstatus: 'draft' }],
      added: [{ action: 'Vers Visée', from: 'Brouillon', to: 'Visée', grant: 'hr:leave_request:write' }],
    })
  })

  it('une transition ajoutée se règle et se supprime ; celles de l’application, non', async () => {
    await open(meta(), {
      states: [{ name: 'Visée' }],
      added: [{ action: 'Viser', from: 'À approuver', to: 'Visée', grant: 'hr:leave_request:workflow_approuver' }],
    })
    q('wf-edge-Viser')!.click()
    await flushPromises()
    expect(q('wf-panel')?.textContent).toContain('Transition ajoutée par votre organisation')
    expect(q('wf-remove-transition')).not.toBeNull()
    q('wf-remove-transition')!.click()
    await flushPromises()
    // Sans transition pour y mener, l'état ajouté ne s'enregistrerait plus : la page le dit.
    expect(q('wf-pending')?.textContent).toContain('Visée')
    q('wf-edge-Refuser')!.click()
    await flushPromises()
    expect(q('wf-remove-transition')).toBeNull()
  })

  it('un type sans circuit le dit, sans dessiner un faux circuit qu’on ne pourrait pas enregistrer', async () => {
    const { api } = await open(meta(ALL, null))
    expect(q('wf-none')).not.toBeNull()
    expect(q('wf-add-state')).toBeNull()
    expect(api.workflowOverride).not.toHaveBeenCalled()
  })
})
