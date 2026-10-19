import { describe, expect, it, vi } from 'vitest'
import CellValue from '@/components/CellValue.vue'
import FieldInput from '@/components/FieldInput.vue'
import { dateFromUtc, formatDatetime, nowUtc, utcFromDate } from '@/lib/datetime'
import { hrefFor, isValidDatetime, isValidFormat, isValidRating } from '@/lib/fieldrules'
import { buildFilters, rowsFromFilters } from '@/lib/filters'
import { presetValues, validate, fieldStates, toPayload, startValues } from '@/lib/form'
import { currencySymbol, formatCell, isNumeric, starsText } from '@/lib/format'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'
import { buildCsv, exportValue } from '@/lib/csv'
import { flushPromises, mountWith } from './views-fixtures'
import { CUSTOMER } from './fixtures'

const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })

describe('date-heure : stockée en UTC, lue à l’heure locale (le navigateur de test est à Auckland, UTC+13)', () => {
  it('aller-retour sans perte, à la seconde', () => {
    const iso = '2026-10-18T14:30:05Z'
    expect(utcFromDate(dateFromUtc(iso)!)).toBe(iso)
  })

  it('l’affichage est à l’heure LOCALE, pas UTC', () => {
    // 14:30 UTC = 03:30 le lendemain à Auckland en octobre (UTC+13).
    expect(formatDatetime('2026-10-18T14:30:05Z')).toBe('19/10/2026 03:30')
  })

  it('une valeur illisible est rendue telle quelle, jamais « Invalid Date »', () => {
    expect(formatDatetime('hier')).toBe('hier')
    expect(dateFromUtc('2026-10-18')).toBeNull()
    expect(dateFromUtc(null)).toBeNull()
    expect(dateFromUtc('2026-02-30T10:00:00Z')?.toISOString()).not.toBe('2026-02-30T10:00:00.000Z') // le 30 février n'existe pas
  })

  it('« maintenant » est une date-heure canonique en UTC', () => {
    expect(nowUtc(new Date('2026-10-18T14:30:05.999Z'))).toBe('2026-10-18T14:30:05Z')
    expect(isValidDatetime(nowUtc())).toBe(true)
  })

  it.each(['2026-10-18', '2026-10-18T14:30:05', '2026-10-18T14:30:05+02:00', '2026-02-30T10:00:00Z', '2026-10-18T25:00:00Z', 12, null])('refuse %j', (v) => {
    expect(isValidDatetime(v)).toBe(false)
  })
})

describe('règles de format (miroir du serveur)', () => {
  it.each([
    ['email', 'alice@example.org', true], ['email', 'a@b', false], ['email', 'a b@c.fr', false], ['email', 'a@c.fr,b@c.fr', false],
    ['phone', '+33 6 12 34 56 78', true], ['phone', '12', false], ['phone', 'abc', false], ['phone', '+33 (0)1; DROP', false],
    ['url', 'https://exemple.fr/x?y=1', true], ['url', 'HTTP://EXEMPLE.FR', true], ['url', 'javascript:alert(1)', false], ['url', 'ftp://x.fr', false], ['url', 'https://a b.fr', false],
  ] as const)('%s %j -> %s', (format, value, ok) => {
    expect(isValidFormat(format, value)).toBe(ok)
  })

  it('une note est un entier de 0 à son nombre d’étoiles', () => {
    expect([0, 3, 5].map((n) => isValidRating(n, 5))).toEqual([true, true, true])
    expect([6, -1, 2.5, '3', true, null].map((n) => isValidRating(n, 5))).toEqual([false, false, false, false, false, false])
  })

  it('le lien d’un texte formaté : mailto, tel (chiffres seulement), l’adresse web ; jamais un autre schéma', () => {
    expect(hrefFor('email', 'a@b.fr')).toBe('mailto:a@b.fr')
    expect(hrefFor('phone', '+33 6 12-34')).toBe('tel:+336 1234'.replace(' ', ''))
    expect(hrefFor('url', 'https://x.fr')).toBe('https://x.fr')
    expect(hrefFor('url', 'javascript:alert(1)')).toBeNull()
    expect(hrefFor(undefined, 'https://x.fr')).toBeNull()
    expect(hrefFor('email', 'pas un courriel')).toBeNull()
  })
})

describe('affichage', () => {
  it('une devise : le montant exact et son symbole', () => {
    expect(formatCell(f({ fieldname: 'p', fieldtype: 'Currency', decimals: 2, options: 'EUR' }), '1234.5').replace(/\s/g, ' ')).toMatch(/^1 234,50 €$/)
    expect(formatCell(f({ fieldname: 'p', fieldtype: 'Currency', decimals: 2 }), '5.00')).toBe('5,00') // sans code : pas de symbole
    expect(currencySymbol('USD')).toMatch(/\$/)
    expect(currencySymbol('???')).toBe('???') // un code inconnu n'est pas une erreur
  })

  it('une note en étoiles, bornée', () => {
    expect(starsText(3, 5)).toBe('★★★☆☆')
    expect(starsText(9, 5)).toBe('★★★★★')
    expect(formatCell(f({ fieldname: 'r', fieldtype: 'Rating', stars: 3 }), 2)).toBe('★★☆')
  })

  it('une date-heure locale, un texte long réduit à sa première ligne', () => {
    expect(formatCell(f({ fieldname: 'd', fieldtype: 'Datetime' }), '2026-10-18T14:30:00Z')).toBe('19/10/2026 03:30')
    expect(formatCell(f({ fieldname: 'b', fieldtype: 'LongText' }), 'ligne 1\nligne 2')).toBe('ligne 1…')
    expect(formatCell(f({ fieldname: 'b', fieldtype: 'LongText' }), 'court')).toBe('court')
    expect(formatCell(f({ fieldname: 'b', fieldtype: 'LongText' }), 'x'.repeat(200))).toBe(`${'x'.repeat(80)}…`)
  })

  it('devises et notes s’alignent à droite comme les nombres', () => {
    expect(['Currency', 'Rating', 'Decimal', 'Number'].every((t) => isNumeric(f({ fieldname: 'x', fieldtype: t as never })))).toBe(true)
    expect(isNumeric(f({ fieldname: 'x', fieldtype: 'Datetime' }))).toBe(false)
  })
})

const META = {
  ...CUSTOMER,
  fields: [
    f({ fieldname: 'email', fieldtype: 'Text', format: 'email' }),
    f({ fieldname: 'bio', fieldtype: 'LongText' }),
    f({ fieldname: 'salary', fieldtype: 'Currency', decimals: 2, options: 'EUR' }),
    f({ fieldname: 'level', fieldtype: 'Rating', stars: 5 }),
    f({ fieldname: 'tint', fieldtype: 'Color' }),
    f({ fieldname: 'met', fieldtype: 'Datetime' }),
    f({ fieldname: 'since', fieldtype: 'Datetime', default: 'now' }),
  ],
  layout: [],
} as DocTypeMeta

describe('formulaire', () => {
  const check = (values: Record<string, unknown>) => validate(META, values, fieldStates(META, values, null))

  it('le défaut « now » est l’instant de l’ouverture, en UTC', () => {
    expect(isValidDatetime(startValues(META).since)).toBe(true)
    expect(startValues(META).met).toBeNull()
  })

  it('un formulaire bien rempli ne produit aucune erreur et part sous sa forme canonique', () => {
    const v = { email: 'a@b.fr', bio: 'x', salary: '12.5', level: 3, tint: '#336699', met: '2026-10-18T14:30:00Z', since: nowUtc() }
    expect(check(v)).toEqual({})
    expect(toPayload(META, v).salary).toBe('12.50')
  })

  it('chaque valeur invalide est refusée avec un message, avant l’envoi', () => {
    const errors = check({ email: 'oups', salary: '1.234', level: 9, tint: 'rouge', met: '2026-10-18', bio: 'x'.repeat(100_001) })
    expect(Object.keys(errors).sort()).toEqual(['bio', 'email', 'level', 'met', 'salary', 'tint'])
    expect(errors.email).toBe('Adresse e-mail invalide')
    expect(errors.salary).toBe('Au plus 2 décimales')
  })

  it('l’adresse pré-remplit les nouveaux types, en les contrôlant', () => {
    expect(presetValues(META, { salary: '9', level: '4', tint: '#aabbcc', met: '2026-10-18T14:30:00Z', email: 'a@b.fr' })).toEqual({
      salary: '9.00', level: 4, tint: '#aabbcc', met: '2026-10-18T14:30:00Z', email: 'a@b.fr',
    })
    expect(presetValues(META, { salary: '1.234', level: '9', tint: '#AABBCC', met: 'hier' })).toEqual({})
  })
})

describe('filtres', () => {
  it('devise, note, date-heure : mêmes opérateurs que le serveur, valeurs contrôlées', () => {
    const rows = [
      { id: 1, field: 'salary', op: '>=', value: '10,5' },
      { id: 2, field: 'level', op: '=', value: 3 },
      { id: 3, field: 'met', op: '>=', value: '2026-10-18T09:00:00Z' },
      { id: 4, field: 'salary', op: 'in', value: '1;2,5' },
    ]
    expect(buildFilters(rows.slice(0, 3), META.fields)).toEqual({ salary: ['>=', '10.5'], level: 3, met: ['>=', '2026-10-18T09:00:00Z'] })
    expect(buildFilters([rows[3]!], META.fields)).toEqual({ salary: ['in', ['1', '2.5']] }) // le « ; » sépare, la virgule est décimale
    expect(buildFilters([{ id: 5, field: 'salary', op: '>', value: 'abc' }], META.fields)).toEqual({}) // ligne incomplète : ignorée
  })

  it('un texte long ne se filtre que par « contient »', () => {
    expect(buildFilters([{ id: 1, field: 'bio', op: 'like', value: 'rando' }], META.fields)).toEqual({ bio: ['like', 'rando'] })
    expect(buildFilters([{ id: 1, field: 'bio', op: '=', value: 'rando' }], META.fields)).toEqual({}) // l'égalité n'existe pas pour lui
  })

  it('les filtres de l’adresse reviennent dans la barre', () => {
    const { rows } = rowsFromFilters({ salary: ['in', ['1', '2.5']], level: 4 }, META.fields, () => 1)
    expect(rows.map((r) => r.value)).toEqual(['1;2.5', 4])
  })
})

describe('export CSV', () => {
  it('devise et note sont des nombres (virgule décimale), date-heure et couleur du texte', () => {
    const rec = { doctype: 'X', name: 'N', owner: 'a', creation: '', modified: '', version: 1, docstatus: 0, amended_from: null, data: { salary: '1234.50', level: 4, met: '2026-10-18T14:30:00Z', tint: '#336699' } }
    expect(exportValue(META.fields[2], 'salary', rec)).toEqual({ text: '1234,50', textual: false })
    expect(exportValue(META.fields[3], 'level', rec)).toEqual({ text: '4', textual: false })
    expect(exportValue(META.fields[5], 'met', rec)).toEqual({ text: '2026-10-18T14:30:00Z', textual: true })
    expect(buildCsv(['Salaire'], [['-12,5']], [false])).toContain('-12,5') // un solde négatif n'est pas neutralisé
  })
})

const mountInput = (field: FieldMeta, value: unknown) => {
  const { w } = mountWith(FieldInput, { field, modelValue: value, inputId: 'x' })
  return w
}
const last = (w: ReturnType<typeof mountInput>) => w.emitted('update:modelValue')!.at(-1)![0]

describe('champs de saisie', () => {
  it('un texte formaté ouvre le bon clavier', () => {
    expect(mountInput(f({ fieldname: 'e', fieldtype: 'Text', format: 'email' }), '').find('input').attributes('type')).toBe('email')
    expect(mountInput(f({ fieldname: 'e', fieldtype: 'Text', format: 'phone' }), '').find('input').attributes('type')).toBe('tel')
    expect(mountInput(f({ fieldname: 'e', fieldtype: 'Text', format: 'url' }), '').find('input').attributes('type')).toBe('url')
    expect(mountInput(f({ fieldname: 'e', fieldtype: 'Text' }), '').find('input').attributes('type')).toBe('text')
  })

  it('un texte long est une zone de texte ; la vider efface la valeur', async () => {
    const w = mountInput(f({ fieldname: 'b', fieldtype: 'LongText' }), 'abc')
    await w.find('textarea').setValue('')
    expect(last(w)).toBeNull()
  })

  it('une devise garde le texte exact, normalise la virgule et montre son symbole', async () => {
    const w = mountInput(f({ fieldname: 's', fieldtype: 'Currency', decimals: 2, options: 'EUR' }), '')
    expect(w.find('.amount-unit').text()).toBe('€')
    await w.find('input').setValue('12,5')
    expect(last(w)).toBe('12.5')
    await w.find('input').trigger('blur')
    expect(w.emitted('update:modelValue')!.at(-1)![0]).toBe('12.5') // pas encore canonique : le parent relit la valeur courante
  })

  it('une note : cliquer une étoile la choisit, recliquer la même l’efface, les flèches ajustent', async () => {
    const w = mountInput(f({ fieldname: 'r', fieldtype: 'Rating', stars: 5 }), 2)
    expect(w.findAll('[role="radio"]')).toHaveLength(5)
    expect(w.findAll('[role="radio"]').map((b) => b.attributes('aria-checked'))).toEqual(['false', 'true', 'false', 'false', 'false'])
    await w.find('[data-testid="rating-4"]').trigger('click')
    expect(last(w)).toBe(4)
    await w.find('[data-testid="rating-2"]').trigger('click')
    expect(last(w)).toBeNull() // la note choisie, recliquée, s'efface
    await w.find('[role="radiogroup"]').trigger('keydown', { key: 'ArrowRight' })
    expect(last(w)).toBe(3)
    await w.find('[role="radiogroup"]').trigger('keydown', { key: 'ArrowLeft' })
    expect(last(w)).toBe(1)
  })

  it('une note ne dépasse jamais son nombre d’étoiles', async () => {
    const w = mountInput(f({ fieldname: 'r', fieldtype: 'Rating', stars: 3 }), 3)
    expect(w.findAll('[role="radio"]')).toHaveLength(3)
    await w.find('[role="radiogroup"]').trigger('keydown', { key: 'ArrowRight' })
    expect(last(w)).toBe(3)
  })

  it('une couleur : le sélecteur natif, la valeur lisible, un bouton pour l’effacer', async () => {
    const w = mountInput(f({ fieldname: 'c', fieldtype: 'Color' }), '#336699')
    expect((w.find('input[type="color"]').element as HTMLInputElement).value).toBe('#336699')
    expect(w.find('code').text()).toBe('#336699')
    await w.find('input[type="color"]').setValue('#aabbcc')
    expect(last(w)).toBe('#aabbcc')
    await w.find('[data-testid="color-clear"]').trigger('click')
    expect(last(w)).toBeNull()
    expect(mountInput(f({ fieldname: 'c', fieldtype: 'Color' }), null).find('[data-testid="color-clear"]').exists()).toBe(false)
  })

  it('une valeur de couleur invalide ne casse pas le sélecteur', () => {
    expect((mountInput(f({ fieldname: 'c', fieldtype: 'Color' }), 'rouge').find('input[type="color"]').element as HTMLInputElement).value).toBe('#000000')
  })
})

describe('valeurs cliquables (listes, rapports, cartes)', () => {
  const show = async (field: FieldMeta, value: unknown, text?: string) => {
    const api = { meta: async () => ({ title_field: 'name_t' }), list: vi.fn(async () => ({ items: [{ name: 'a1', data: { name_t: 'Atelier Dupont' } }], total: 1, limit: 1, offset: 0 })) }
    const { w } = mountWith(CellValue, { field, value, text }, api)
    await flushPromises()
    return w
  }

  it('un lien ouvre le document cité, sous son TITRE', async () => {
    const w = await show(f({ fieldname: 'c', fieldtype: 'Link', options: 'Customer' }), 'a1')
    const a = w.find('a')
    expect(a.attributes('href')).toBe('/app/Customer/a1')
    expect(a.text()).toBe('Atelier Dupont') // pas « a1 »
  })

  it('un courriel ouvre le courrier, un téléphone compose', async () => {
    const mail = await show(f({ fieldname: 'e', fieldtype: 'Text', format: 'email' }), 'a@b.fr')
    expect(mail.find('a').attributes('href')).toBe('mailto:a@b.fr')
    expect(mail.find('a').attributes('target')).toBeUndefined()
    const tel = await show(f({ fieldname: 'e', fieldtype: 'Text', format: 'phone' }), '+33 6 12 34 56 78')
    expect(tel.find('a').attributes('href')).toBe('tel:+33612345678')
  })

  it('une adresse web s’ouvre dans un nouvel onglet, sans donner accès à la page d’origine', async () => {
    const w = await show(f({ fieldname: 'u', fieldtype: 'Text', format: 'url' }), 'https://exemple.fr/p')
    expect(w.find('a').attributes('target')).toBe('_blank')
    expect(w.find('a').attributes('rel')).toBe('noopener noreferrer')
  })

  it('une valeur dont le format est invalide (donnée ancienne) n’est JAMAIS un lien', async () => {
    const w = await show(f({ fieldname: 'u', fieldtype: 'Text', format: 'url' }), 'javascript:alert(1)')
    expect(w.find('a').exists()).toBe(false)
    expect(w.text()).toBe('javascript:alert(1)') // affiché comme du texte, inerte
  })

  it('une couleur montre son échantillon, une note ses étoiles', async () => {
    const color = await show(f({ fieldname: 'c', fieldtype: 'Color' }), '#336699')
    expect(color.find('.cell-swatch').attributes('style')).toContain('background')
    expect(color.text()).toBe('#336699')
    const stars = await show(f({ fieldname: 'r', fieldtype: 'Rating', stars: 5 }), 3)
    expect(stars.text()).toBe('★★★☆☆')
    expect(stars.find('.cell-stars').attributes('aria-label')).toBe('3 / 5')
  })

  it('le texte calculé par l’appelant (une quantité et son unité) remplace le texte par défaut ; une valeur vide n’affiche rien', async () => {
    expect((await show(f({ fieldname: 'q', fieldtype: 'Number' }), 3, '3 kg')).text()).toBe('3 kg')
    expect((await show(f({ fieldname: 'q', fieldtype: 'Text' }), '')).text()).toBe('')
    expect((await show(f({ fieldname: 'q', fieldtype: 'Link', options: 'X' }), null)).find('a').exists()).toBe(false)
  })

  it('cliquer un lien dans une ligne ne déclenche pas l’ouverture de la ligne', async () => {
    const w = await show(f({ fieldname: 'c', fieldtype: 'Link', options: 'Customer' }), 'a1')
    const stop = vi.fn()
    w.find('a').element.addEventListener('click', (e) => (e.defaultPrevented ? null : stop()), { once: true })
    const parentClick = vi.fn()
    w.element.parentElement?.addEventListener('click', parentClick)
    await w.find('a').trigger('click')
    expect(parentClick).not.toHaveBeenCalled()
  })
})
