import { mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { describe, expect, it } from 'vitest'
import FieldInput from '@/components/FieldInput.vue'
import { SERVICES } from '@/lib/services'
import { createPinia } from 'pinia'
import { CUSTOMER, STOCK_ENTRY } from './fixtures'

const number = CUSTOMER.fields.find((f) => f.fieldname === 'credit_limit')!

function mountNumber(modelValue: unknown = null) {
  return mount(FieldInput, {
    props: { field: number, modelValue, inputId: 'x' },
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {} } },
    },
  })
}

const last = (w: ReturnType<typeof mountNumber>) => (w.emitted('update:modelValue') ?? []).at(-1)?.[0]

describe('champ Nombre', () => {
  it('met la valeur à jour pendant la frappe, sans attendre le blur (Ctrl+S, conditions depends_on)', async () => {
    const w = mountNumber()
    const input = w.find('input')
    for (const key of ['2', '0', '0', '0', '0']) await input.trigger('keypress', { key })
    expect(last(w)).toBe(20000)
  })

  it('vider le champ donne null, jamais 0', async () => {
    const w = mountNumber(5)
    const input = w.find('input')
    ;(input.element as HTMLInputElement).value = ''
    await input.trigger('blur')
    expect(last(w)).toBeNull()
  })
})


const qty = STOCK_ENTRY.children!['Stock Entry Line']!.fields.find((f) => f.fieldname === 'qty')!

function mountDecimal(modelValue: unknown = null) {
  return mount(FieldInput, {
    props: { field: qty, modelValue, inputId: 'q' },
    global: {
      plugins: [PrimeVue, createPinia()],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: {} } },
    },
  })
}

describe('champ Decimal', () => {
  it('émet du TEXTE, jamais un number : la valeur reste exacte de la frappe à l’envoi', async () => {
    const w = mountDecimal()
    await w.find('input').setValue('0.1')
    const value = (w.emitted('update:modelValue') ?? []).at(-1)?.[0]
    expect(value).toBe('0.1')
    expect(typeof value).toBe('string')
  })

  it('la virgule du clavier français devient un point', async () => {
    const w = mountDecimal()
    await w.find('input').setValue('12,5')
    expect((w.emitted('update:modelValue') ?? []).at(-1)?.[0]).toBe('12.5')
  })

  it('à la sortie du champ, une valeur valide prend sa forme canonique', async () => {
    const w = mountDecimal('12.5')
    await w.find('input').trigger('blur')
    expect((w.emitted('update:modelValue') ?? []).at(-1)?.[0]).toBe('12.500')
  })

  it('une valeur invalide est laissée telle quelle : c’est la validation qui parle, pas le champ qui la réécrit', async () => {
    const w = mountDecimal('1.2345')
    await w.find('input').trigger('blur')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('vider le champ donne null, jamais « 0.000 »', async () => {
    const w = mountDecimal('5.000')
    await w.find('input').setValue('')
    expect((w.emitted('update:modelValue') ?? []).at(-1)?.[0]).toBeNull()
  })

  it('affiche la valeur stockée telle quelle', () => {
    expect(mountDecimal('1234.500').find('input').element.value).toBe('1234.500')
  })
})
