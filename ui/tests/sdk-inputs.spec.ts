/**
 * Ce que le SDK ajoute pour les extensions (traoreera/doctype#66, #67) : un champ de date utilisable seul, et l'état d'une page gardé
 * dans la query de l'adresse sans que la page soit remontée.
 */
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import DatePicker from 'primevue/datepicker'
import { describe, expect, it } from 'vitest'
import { defineComponent, h, onMounted } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import ExtensionPage from '@/extensions/ExtensionPage.vue'
import { DateInput, DatetimeInput, useQuery } from '@/shared/sdk'

const last = (w: { emitted(name: string): unknown[][] | undefined }) => (w.emitted('update:modelValue') ?? []).at(-1)?.[0]

describe('DateInput', () => {
  const at = (props: Record<string, unknown>) => mount(DateInput, { props: { modelValue: null, ...props }, global: { plugins: [PrimeVue] } })

  it('lit une valeur ISO comme le jour LOCAL et rend le jour local choisi, jamais décalé par l’UTC', async () => {
    const w = at({ modelValue: '2026-11-10' })
    const picker = w.findComponent(DatePicker)
    const shown = picker.props('modelValue') as Date
    expect([shown.getFullYear(), shown.getMonth(), shown.getDate()]).toEqual([2026, 10, 10])
    // Minuit local à Auckland est la veille en UTC : `toISOString()` rendrait le 14.
    picker.vm.$emit('update:modelValue', new Date(2026, 10, 15))
    expect(last(w)).toBe('2026-11-15')
    picker.vm.$emit('update:modelValue', null)
    expect(last(w)).toBeNull()
    w.unmount()
  })

  it('suit le format du Desk, borne par `min` / `max`, et une valeur illisible vaut « vide »', () => {
    const w = at({ modelValue: '10/11/2026', min: '2026-01-01', max: '2026-12-31', label: 'Au', inputId: 'au' })
    const picker = w.findComponent(DatePicker)
    expect(picker.props('modelValue')).toBeNull()
    expect(picker.props('dateFormat')).toBe('dd/mm/yy')
    expect((picker.props('minDate') as Date).getMonth()).toBe(0)
    expect((picker.props('maxDate') as Date).getDate()).toBe(31)
    expect(w.find('label').attributes('for')).toBe('au')
    expect(w.find('label').text()).toBe('Au')
    w.unmount()
  })

  it('`clearable: false` retire la barre « Aujourd’hui / Effacer »', () => {
    const w = at({ clearable: false })
    expect(w.findComponent(DatePicker).props('showButtonBar')).toBe(false)
    w.unmount()
  })
})

describe('DatetimeInput', () => {
  it('la valeur est l’instant UTC à la minute, l’affichage l’heure locale', () => {
    const w = mount(DatetimeInput, { props: { modelValue: '2026-10-18T14:30:00Z' }, global: { plugins: [PrimeVue] } })
    const picker = w.findComponent(DatePicker)
    expect((picker.props('modelValue') as Date).toISOString()).toBe('2026-10-18T14:30:00.000Z')
    expect(picker.props('showTime')).toBe(true)
    picker.vm.$emit('update:modelValue', new Date('2026-10-19T08:15:42.500Z'))
    expect(last(w)).toBe('2026-10-19T08:15:00Z')
    w.unmount()
  })
})

describe('une page d’extension et sa query', () => {
  async function page() {
    let mounts = 0
    let handle!: ReturnType<typeof useQuery>
    const Screen = defineComponent({
      props: { id: { type: String, default: '' } },
      setup(props) {
        handle = useQuery()
        onMounted(() => mounts++)
        return () => h('p', { 'data-testid': 'screen' }, `${props.id}|${handle.query.value.etat ?? ''}|${handle.query.value.date ?? ''}`)
      },
    })
    const Page = defineComponent({ setup: () => () => h(ExtensionPage, { extension: 'accounts', title: 'États', component: Screen }) })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { render: () => null } },
        { path: '/x/accounts/etats/:id', component: Page },
      ],
    })
    await router.push('/')
    await router.push('/x/accounts/etats/a?etat=bilan')
    const w = mount({ render: () => h(Page) }, { global: { plugins: [router] } })
    await flushPromises()
    return { w, router, text: () => w.get('[data-testid="screen"]').text(), mounts: () => mounts, handle: () => handle }
  }

  it('changer la query ne remonte PAS la page, qui la lit de façon réactive', async () => {
    const p = await page()
    expect(p.text()).toBe('a|bilan|')
    await p.handle().setQuery({ etat: 'balance', date: '2026-11-10', vide: '', rien: null })
    await flushPromises()
    expect(p.text()).toBe('a|balance|2026-11-10')
    expect(p.router.currentRoute.value.fullPath).toBe('/x/accounts/etats/a?etat=balance&date=2026-11-10')
    expect(p.mounts()).toBe(1)
    p.w.unmount()
  })

  it('`setQuery` remplace l’entrée d’historique par défaut, et en ajoute une sur demande', async () => {
    const p = await page()
    await p.handle().setQuery({ etat: 'balance' })
    p.router.back()
    await flushPromises()
    expect(p.router.currentRoute.value.fullPath).toBe('/') // l'entrée `?etat=bilan` a été remplacée, pas empilée
    await p.router.push('/x/accounts/etats/a?etat=bilan')
    await p.handle().setQuery({ etat: 'grand-livre' }, { replace: false })
    p.router.back()
    await flushPromises()
    expect(p.router.currentRoute.value.fullPath).toBe('/x/accounts/etats/a?etat=bilan')
    p.w.unmount()
  })

  it('un autre PARAMÈTRE de chemin repart de zéro (un autre document)', async () => {
    const p = await page()
    await p.router.push('/x/accounts/etats/b?etat=bilan')
    await flushPromises()
    expect(p.text()).toBe('b|bilan|')
    expect(p.mounts()).toBe(2)
    p.w.unmount()
  })
})
