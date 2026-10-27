import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Checkbox from 'primevue/checkbox'
import PrimeVue from 'primevue/config'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import FieldInput from '@/components/FieldInput.vue'
import { ApiError } from '@/lib/api'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, FieldBase, FieldMeta, FormOverride } from '@/lib/types'
import FormBuilderView from '@/views/FormBuilderView.vue'

const field = (fieldname: string, fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({
  fieldname, label: fieldname.toUpperCase(), fieldtype, required: false, hidden: false, in_list_view: false, ...extra,
})
// Ce que `/meta` envoie : l'habillage enregistré y est déjà appliqué (`prio` obligatoire par lui, `title` par le YAML).
const FIELDS: FieldMeta[] = [
  field('title', 'Text', { required: true }),
  field('prio', 'Number', { required: true, label: 'Priorité' }),
  field('ref', 'Text', { read_only: true }),
  field('due', 'Date'),
  field('status', 'Select', { options: ['Ouvert', 'Fermé'], default: 'Ouvert' }),
  field('note', 'Text', { hidden: true }),
  field('secret', 'Password'),
  field('photo', 'Image'),
  field('city', 'Text', { fetch_from: 'client.city' }),
  field('lines', 'Table', { options: 'Line' }),
  field('code', 'Text', { unique: true }),
  field('sku', 'Text'),
]
const DEFINITION: Record<string, FieldBase> = { title: { required: true }, ref: { read_only: true }, status: { default: 'Ouvert' }, note: { hidden: true } }
const LABELS = Object.fromEntries(FIELDS.map((f) => [f.fieldname, f.fieldname.toUpperCase()]))
const LAYOUT = [
  { section: 'Général', collapsible: false, collapsed: false, columns: [['title', 'prio'], ['due', 'status']] },
  { section: null, collapsible: false, collapsed: false, columns: [['ref', 'secret', 'photo', 'city', 'lines', 'code', 'sku']] },
]
const SAVED: FormOverride = { fields: { prio: { mandatory: true, label: 'Priorité' } } }
const ALL = { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' } as DocTypeMeta['rights']
const META = (rights = ALL): DocTypeMeta => ({ name: 'Ticket', label: 'Ticket', fields: FIELDS, layout: LAYOUT, list_columns: [], rights, naming: { field: 'sku' } })

let wrapper: VueWrapper | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

async function open(opts: { rights?: DocTypeMeta['rights']; config?: FormOverride; save?: (...a: unknown[]) => Promise<unknown> } = {}) {
  const api = {
    meta: vi.fn(async () => META(opts.rights)),
    doctypes: vi.fn(async () => []),
    formOverride: vi.fn(async () => ({ doctype: 'Ticket', config: opts.config ?? SAVED, definition: DEFINITION, labels: LABELS })),
    saveFormOverride: vi.fn(opts.save ?? (async (_d: string, c: FormOverride) => ({ doctype: 'Ticket', config: c }))),
    removeFormOverride: vi.fn(async () => {}),
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/customize/:doctype', name: 'customize', component: FormBuilderView, props: true },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/workflow/:doctype', name: 'workflow', component: { render: () => null } },
    ],
  })
  await router.push('/customize/Ticket')
  await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, ToastService, ConfirmationService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api } } },
  })
  await flushPromises()
  return { w: wrapper, api }
}
const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const click = async (id: string) => {
  q(id)!.click()
  await flushPromises()
}
const box = (w: VueWrapper, id: string) => w.findAllComponents(Checkbox).find((c) => c.attributes('data-testid') === id)!
const sent = (api: { saveFormOverride: ReturnType<typeof vi.fn> }) => api.saveFormOverride.mock.calls.at(-1)![1] as FormOverride

/** Un glisser-déposer simulé : jsdom n'a pas de DataTransfer, on en passe un qui suffit au composant. */
function dnd(source: string, target: Element): void {
  const store: Record<string, string> = {}
  const dataTransfer = { setData: (k: string, v: string) => (store[k] = v), getData: (k: string) => store[k] ?? '', get types() { return Object.keys(store) }, effectAllowed: '' }
  const fire = (el: Element, type: string) => {
    const e = new Event(type, { bubbles: true, cancelable: true })
    Object.defineProperty(e, 'dataTransfer', { value: dataTransfer })
    el.dispatchEvent(e)
  }
  fire(q(`fb-field-${source}`)!, 'dragstart')
  fire(target, 'dragover')
  fire(target, 'drop')
}

describe('le Form Builder (« Personnaliser »)', () => {
  it('montre le formulaire tel qu’il est : sections, colonnes, cartes ; un champ que l’application masque n’apparaît nulle part', async () => {
    await open()
    expect(q('fb-section-title-0')?.textContent).toContain('Général')
    expect(q('fb-column-0-0')?.textContent).toContain('Priorité') // le libellé de l'organisation
    expect(q('fb-field-note')).toBeNull()
    expect((q('fb-save') as HTMLButtonElement).disabled).toBe(true) // rien n'a changé
  })

  it('glisser une carte dans une autre colonne, puis enregistrer : la disposition part au serveur', async () => {
    const { api } = await open()
    dnd('due', q('fb-field-prio')!) // déposée SUR « Priorité » : avant elle
    await flushPromises()
    await click('fb-save')
    expect(sent(api).layout![0]!.columns).toEqual([['title', 'due', 'prio'], ['status']])
    expect(sent(api).fields).toEqual(SAVED.fields) // les retouches de champ voyagent avec
  })

  it('au clavier : choisir un champ, puis le déplacer depuis le panneau', async () => {
    const { api } = await open()
    await click('fb-field-title')
    await click('fb-right')
    await click('fb-next-section')
    await click('fb-save')
    expect(sent(api).layout).toEqual([
      { section: 'Général', collapsible: false, collapsed: false, columns: [['prio'], ['due', 'status']] },
      { section: null, collapsible: false, collapsed: false, columns: [['title', 'ref', 'secret', 'photo', 'city', 'lines', 'code', 'sku']] },
    ])
  })

  it('retirer un champ du formulaire le range dans la palette ; l’y déposer aussi', async () => {
    await open()
    await click('fb-field-sku')
    await click('fb-unplace')
    expect(q('fb-palette')?.textContent).toContain('SKU')
    dnd('code', q('fb-palette')!)
    await flushPromises()
    expect(q('fb-palette')?.textContent).toContain('CODE')
    await click('fb-field-code')
    await click('fb-place') // le clavier remet un champ non placé dans la section choisie (la dernière, par défaut)
    expect(q('fb-palette')?.textContent).not.toContain('CODE')
  })

  it('une section : titre, colonnes (rien ne se perd en en retirant), repliable ; une section ajoutée vide n’est pas envoyée', async () => {
    const { api } = await open()
    await click('fb-add-section')
    await click('fb-section-title-0')
    await click('fb-columns-1')
    await click('fb-save')
    expect(sent(api).layout![0]!.columns).toEqual([['title', 'prio', 'due', 'status']])
    expect(sent(api).layout).toHaveLength(2) // la section ajoutée, vide, n'est pas partie
  })

  it('des onglets : le premier ajouté fait passer le formulaire en onglets ; un onglet sans nom bloque l’enregistrement', async () => {
    const { api } = await open()
    await click('fb-add-tab')
    expect(q('fb-tab-0')?.textContent).toContain('Détails')
    await click('fb-tab-1')
    const name = q('fb-tab-name') as HTMLInputElement
    name.value = ' '
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(q('fb-board-problem')?.textContent).toContain('nom')
    expect((q('fb-save') as HTMLButtonElement).disabled).toBe(true)
    name.value = 'Suivi'
    name.dispatchEvent(new Event('input'))
    await flushPromises()
    await click('fb-tab-0')
    dnd('sku', q('fb-tab-1')!) // déposé sur le TITRE d'un onglet : on ne voit pas ses colonnes d'ici
    await flushPromises()
    expect(q('fb-tab-1')?.getAttribute('aria-selected')).toBe('true') // on suit le champ
    await click('fb-save')
    expect(sent(api).layout!.map((s) => s.tab)).toEqual(['Détails', 'Détails', 'Suivi'])
  })

  it('les règles d’un champ : ce que l’application impose est coché et grisé ; une case cochée part à l’enregistrement', async () => {
    const { w, api } = await open()
    await click('fb-field-title')
    expect(box(w, 'fb-mandatory').props('modelValue')).toBe(true)
    expect(box(w, 'fb-mandatory').props('disabled')).toBe(true)
    await click('fb-field-due')
    box(w, 'fb-readonly').vm.$emit('update:modelValue', true)
    await flushPromises()
    await click('fb-save')
    expect(sent(api).fields).toEqual({ ...SAVED.fields, due: { read_only: true } })
  })

  it('le défaut se saisit avec le champ du formulaire ; pas de défaut pour le champ qui nomme ni pour un champ opaque', async () => {
    const { w, api } = await open()
    await click('fb-field-prio')
    w.findAllComponents(FieldInput).find((c) => (c.props('field') as FieldMeta).fieldname === 'prio')!.vm.$emit('update:modelValue', 3)
    await flushPromises()
    await click('fb-save')
    expect(sent(api).fields!.prio).toEqual({ mandatory: true, label: 'Priorité', default: 3 })
    for (const name of ['sku', 'lines', 'secret', 'city']) {
      await click(`fb-field-${name}`)
      expect(w.findAllComponents(FieldInput).some((c) => (c.props('field') as FieldMeta).fieldname === name)).toBe(false)
    }
  })

  it('libellé : une retouche d’affichage ; vidée, celui de l’application revient', async () => {
    const { api } = await open()
    await click('fb-field-due')
    const label = q('fb-label') as HTMLInputElement
    label.value = 'Échéance'
    label.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(q('fb-field-due')?.textContent).toContain('Échéance')
    await click('fb-save')
    expect(sent(api).fields!.due).toEqual({ label: 'Échéance' })
  })

  it('ajouter un champ : il est placé dans le formulaire et part dans `added` ; un Select sans valeurs ne s’ajoute pas', async () => {
    const { api } = await open()
    const label = q('fb-new-label') as HTMLInputElement
    label.value = 'Segment client'
    label.dispatchEvent(new Event('input'))
    await flushPromises()
    expect(document.body.textContent).toContain('custom_segment_client')
    await click('fb-add')
    expect(q('fb-field-custom_segment_client')).not.toBeNull()
    await click('fb-save')
    expect((sent(api) as { added?: unknown }).added).toEqual([{ fieldname: 'custom_segment_client', fieldtype: 'Text', label: 'Segment client' }])
    expect(sent(api).layout!.flatMap((s) => s.columns.flat())).toContain('custom_segment_client') // placé, pas reversé en bas
  })

  it('supprimer un champ ajouté le retire du formulaire ET de l’habillage', async () => {
    const config: FormOverride & { added: unknown[] } = { ...SAVED, layout: [{ section: null, collapsible: false, collapsed: false, columns: [['title', 'custom_x']] }], added: [{ fieldname: 'custom_x', fieldtype: 'Text', label: 'X' }] }
    const { api } = await open({ config })
    await click('fb-field-custom_x')
    await click('fb-remove-added')
    await click('fb-save')
    expect((sent(api) as { added?: unknown }).added).toBeUndefined()
    expect(sent(api).layout!.flatMap((s) => s.columns.flat())).not.toContain('custom_x')
  })

  it('sans le droit d’écrire TOUS les documents : règles et ajout réservés, libellés et disposition restent', async () => {
    const { w } = await open({ rights: { ...ALL, write: 'restricted' } })
    await click('fb-field-due')
    expect(q('fb-rules-reserved')).not.toBeNull()
    expect(box(w, 'fb-mandatory').props('disabled')).toBe(true)
    expect(box(w, 'fb-hidden').props('disabled')).toBe(false)
    expect(q('fb-new-label')).toBeNull()
    expect((q('fb-label') as HTMLInputElement).disabled).toBe(false)
  })

  it('le refus du serveur reste sous les yeux ; « Revenir à l’application » retire l’habillage', async () => {
    const refusal = new ApiError(422, 'form_rules_reserved', 'Obligatoire, lecture seule et valeur par défaut s’imposent à tous (champs : prio)')
    const { api } = await open({ save: async () => { throw refusal } })
    await click('fb-field-due')
    await click('fb-unplace')
    await click('fb-save')
    expect(q('fb-problem')?.textContent).toContain('champs : prio')
    await click('fb-reset')
    expect(api.removeFormOverride).toHaveBeenCalledWith('Ticket')
  })

  it('un lecteur voit la disposition sans pouvoir la changer', async () => {
    await open({ rights: { ...ALL, write: 'none' } })
    expect(q('fb-readonly')).not.toBeNull()
    expect(q('fb-save')).toBeNull()
    expect(q('fb-field-title')?.getAttribute('draggable')).toBe('false')
  })
})
