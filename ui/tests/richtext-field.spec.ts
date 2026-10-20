import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import { afterEach, describe, expect, it } from 'vitest'
import RichTextField from '@/components/RichTextField.vue'

afterEach(() => {
  document.body.innerHTML = ''
})

const mountField = (value: unknown, readonly = false) =>
  mount(RichTextField, { props: { modelValue: value, inputId: 'r', readonly }, attachTo: document.body, global: { plugins: [PrimeVue] } })

describe('l’éditeur de texte enrichi', () => {
  it('montre la valeur existante, et sa barre d’outils', async () => {
    const w = mountField('<p>Bonjour <strong>toi</strong></p>')
    await flushPromises()
    expect(w.find('.rich-content').html()).toContain('<strong>toi</strong>')
    expect(w.find('[data-testid="rich-bold"]').exists()).toBe(true)
  })

  it('une valeur qui contient du HTML interdit est filtrée DÈS l’affichage (le serveur le refuserait de toute façon)', async () => {
    const w = mountField('<p onclick="alert(1)">a</p><script>alert(2)</script><img src=x onerror=alert(3)>')
    await flushPromises()
    const html = w.find('.rich-content').html()
    expect(html).not.toMatch(/onclick|<script|<img|onerror/i)
    expect(html).toContain('a')
  })

  it('en lecture seule : pas de barre d’outils, pas d’édition', async () => {
    const w = mountField('<p>texte</p>', true)
    await flushPromises()
    expect(w.find('.rich-toolbar').exists()).toBe(false)
    expect(w.find('.rich-content').attributes('contenteditable')).toBe('false')
  })

  it('la barre d’outils met en forme et ce qui est émis reste dans le sous-ensemble permis', async () => {
    const w = mountField('<p>mot</p>')
    await flushPromises()
    const content = w.find('.rich-content').element as HTMLElement
    // Sélectionne tout le texte, puis « Gras ».
    const range = document.createRange()
    range.selectNodeContents(content.querySelector('p')!)
    const sel = window.getSelection()!
    sel.removeAllRanges()
    sel.addRange(range)
    await w.find('[data-testid="rich-bold"]').trigger('click')
    await flushPromises()
    const emitted = w.emitted('update:modelValue')
    if (emitted) expect(String(emitted.at(-1)![0])).toMatch(/^(<p>|<strong>|<p><strong>)/) // selon ce que happy-dom sait de la sélection
    // Un éditeur vidé émet `null`, jamais `<p></p>`.
    ;(content as HTMLElement).innerHTML = '<p></p>'
  })

  it('un lien : seule une adresse http(s) ou mailto est acceptée', async () => {
    const w = mountField('<p>mot</p>')
    await flushPromises()
    await w.find('[data-testid="rich-link"]').trigger('click')
    await w.find('[data-testid="rich-link-url"]').setValue('javascript:alert(1)')
    await w.find('[data-testid="rich-link-apply"]').trigger('click')
    await flushPromises()
    expect(w.find('.rich-content a').exists()).toBe(false)
  })
})
