import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Checkbox from 'primevue/checkbox'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ApiError } from '@/lib/api'
import type { DevDefinition } from '@/lib/doctypebuilder'
import { SERVICES } from '@/lib/services'
import DoctypeBuilderView from '@/views/DoctypeBuilderView.vue'

const downloads = vi.hoisted(() => [] as { name: string; text: string }[])
vi.mock('@/lib/download', () => ({ downloadText: (name: string, text: string) => downloads.push({ name, text }) }))

const ORDER: DevDefinition = {
  schema_version: 1, name: 'Order', label: 'Commande', module: 'shop', title_field: 'ref',
  hooks: { validate: ['shop.check'] },
  fields: [{ fieldname: 'ref', fieldtype: 'Text', label: 'Référence', required: true }, { fieldname: 'qty', fieldtype: 'Int', label: 'Quantité' }],
}

let wrapper: VueWrapper | null = null
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  downloads.length = 0
})

async function open(path: string, api: Record<string, unknown> = {}) {
  const services = {
    doctypes: vi.fn(async () => [{ name: 'Country', label: 'Pays' }, { name: 'Order', label: 'Commande' }]),
    devDefinition: vi.fn(async () => ({ doctype: 'Order', owner: 'shop', definition: ORDER, yaml: '' })),
    devCheck: vi.fn(async (d: DevDefinition) => ({ doctype: d.name, yaml: `name: ${d.name}\n`, grants: ['shop:order:read'] })),
    ...api,
  }
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/dev/doctype/:doctype?', name: 'dev-doctype', component: DoctypeBuilderView, props: true }] })
  await router.push(path)
  await router.isReady()
  wrapper = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: { plugins: [PrimeVue, ToastService, createPinia(), router], provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null, developer: true }, api: services } } },
  })
  await flushPromises()
  return { w: wrapper, api: services }
}
const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const click = async (id: string) => {
  q(id)!.click()
  await flushPromises()
}
const type = async (id: string, value: string, commit = false) => {
  const input = q(id) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  if (commit) input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
  await flushPromises()
}
const sent = (api: { devCheck: ReturnType<typeof vi.fn> }) => api.devCheck.mock.calls.at(-1)![0] as DevDefinition

describe('le constructeur de DocTypes (mode développeur)', () => {
  it('un type neuf : ajouter un champ depuis la palette, nommer le type, vérifier, télécharger le YAML', async () => {
    const { api } = await open('/dev/doctype')
    expect(q('dev-title')?.textContent).toContain('Nouveau type')
    await type('dev-type-name', 'Ticket')
    await type('dev-type-module', 'support')
    await click('dev-add-Int')
    expect(q('fb-field-nouveau_int')).not.toBeNull() // placé, et ses propriétés ouvertes
    expect(q('fb-panel-title')?.textContent).toContain('Nouveau Int')
    await click('dev-download')
    const def = sent(api)
    expect(def).toMatchObject({ name: 'Ticket', module: 'support' })
    expect(def.fields.map((f) => f.fieldname)).toEqual(['title', 'nouveau_int'])
    expect(def.layout![0]!.columns[0]).toEqual(['title', 'nouveau_int'])
    expect(downloads).toEqual([{ name: 'ticket.yaml', text: 'name: Ticket\n' }])
    expect(q('dev-yaml')?.textContent).toContain('name: Ticket')
    expect(q('dev-verdict')?.textContent).toContain('shop:order:read')
  })

  it('modifier un type existant : on part de son YAML d’origine, ce que le constructeur n’édite pas voyage tel quel', async () => {
    const { api } = await open('/dev/doctype/Order')
    expect(api.devDefinition).toHaveBeenCalledWith('Order')
    expect(document.body.textContent).toContain('plugin « shop »')
    await click('fb-field-ref')
    await type('dev-name', 'code', true) // renommer : le champ titre suit
    await click('dev-check')
    expect(sent(api).hooks).toEqual({ validate: ['shop.check'] })
    expect(sent(api).title_field).toBe('code')
    expect(sent(api).fields[0]!.fieldname).toBe('code')
    expect(sent(api).layout![0]!.columns[0]).toEqual(['code', 'qty']) // il garde SA place dans le formulaire
  })

  it('un nom de champ invalide ou pris est dit, et rien n’est renommé', async () => {
    await open('/dev/doctype/Order')
    await click('fb-field-ref')
    await type('dev-name', 'qty', true)
    expect(document.body.textContent).toContain('déjà ce nom')
    await type('dev-name', 'Ref!', true)
    expect(document.body.textContent).toContain('commence par une lettre')
    expect(q('fb-field-ref')).not.toBeNull()
  })

  it('masquer un champ le retire du formulaire (le schéma refuse un champ masqué placé)', async () => {
    const { w, api } = await open('/dev/doctype/Order')
    await click('fb-field-qty')
    w.findAllComponents(Checkbox).find((c) => c.attributes('data-testid') === 'dev-hidden')!.vm.$emit('update:modelValue', true)
    await flushPromises()
    expect(q('fb-field-qty')).toBeNull()
    await click('dev-check')
    expect(sent(api).fields[1]).toMatchObject({ fieldname: 'qty', hidden: true })
    expect(sent(api).layout!.flatMap((s) => s.columns.flat())).not.toContain('qty')
  })

  it('un refus du serveur s’affiche ligne par ligne, et rien n’est téléchargé', async () => {
    const devCheck = vi.fn(async () => {
      throw new ApiError(422, 'invalid_definition', 'définition invalide', [{ field: 'fields.1.options', msg: 'DocType cible inconnu' }])
    })
    await open('/dev/doctype/Order', { devCheck })
    await click('dev-download')
    expect(q('dev-refused')).not.toBeNull()
    expect(q('dev-verdict')?.textContent).toContain('fields.1.options : DocType cible inconnu')
    expect(downloads).toEqual([])
  })

  it('mode développeur fermé sur le serveur : la page le dit, et comment l’ouvrir', async () => {
    const devDefinition = vi.fn(async () => {
      throw new ApiError(404, 'developer_mode_off', 'mode développeur désactivé')
    })
    await open('/dev/doctype/Order', { devDefinition })
    expect(q('dev-error')?.textContent).toContain('developer_mode: true')
    expect(q('dev-check')).toBeNull()
  })
})
