import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { describe, expect, it } from 'vitest'
import FieldInput from '@/components/FieldInput.vue'
import SecretField from '@/components/SecretField.vue'
import TableField from '@/components/TableField.vue'
import { buildCsv, exportValue, isTextualColumn } from '@/lib/csv'
import { clockDuration, formatDuration, parseDuration } from '@/lib/duration'
import { isValidInt, isValidJsonText, isValidPercent, isValidTime, normalizeTime } from '@/lib/fieldrules'
import { buildFilters, defaultOp } from '@/lib/filters'
import { fieldStates, presetValues, recordValues, toPayload, validate } from '@/lib/form'
import { formatCell, isNumeric } from '@/lib/format'
import { isRichEmpty, plainText, sanitizeRich } from '@/lib/richtext'
import { SERVICES } from '@/lib/services'
import { FILTER_OPS, type ChildMeta, type DocTypeMeta, type FieldMeta } from '@/lib/types'
import { flushPromises, mountWith } from './views-fixtures'

const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })

describe('durées : des secondes qui se lisent « 1 h 30 min »', () => {
  it.each([
    ['1h 30m', 5400], ['1h30', 5400], ['1 h 30', 5400], ['90m', 5400], ['90 min', 5400], ['2 j 4 h', 187_200], ['1:30', 5400], ['1:30:15', 5415], ['5400', 5400], ['0', 0], ['45s', 45],
    ['', null], ['  ', null], ['abc', null], ['1.5h', null], ['-5', null], ['1h 75', null], ['99999999999 h', null], ['1:75', null],
  ])('%j -> %j', (text, seconds) => {
    expect(parseDuration(text)).toBe(seconds)
  })

  it('s’écrit comme on la lit, et en « 1:30:00 » pour un tableur', () => {
    expect([0, 59, 60, 3600, 5400, 90_061, 187_200].map(formatDuration)).toEqual(['0 s', '59 s', '1 min', '1 h', '1 h 30 min', '1 j 1 h 1 min 1 s', '2 j 4 h'])
    expect(clockDuration(5415)).toBe('1:30:15')
    expect(parseDuration(formatDuration(90_061))).toBe(90_061) // aller-retour sans perte
  })
})

describe('règles des types simples (miroir du serveur)', () => {
  it('entier, pourcentage, heure', () => {
    expect([0, -5, 2 ** 53 - 1].every(isValidInt)).toBe(true)
    expect([1.5, '3', true, 2 ** 53, null, Number.NaN].some(isValidInt)).toBe(false)
    expect([0, 12.5, 100].every(isValidPercent)).toBe(true)
    expect([-1, 100.5, '5', Number.NaN, true].some(isValidPercent)).toBe(false)
    expect(['00:00:00', '23:59:59'].every(isValidTime)).toBe(true)
    expect(['24:00:00', '9:05:00', '12:00', 5].some(isValidTime)).toBe(false)
    expect(normalizeTime('09:05')).toBe('09:05:00')
    expect(normalizeTime('09:05:30')).toBe('09:05:30')
  })

  it('un JSON est un objet ou un tableau', () => {
    expect(['{"a":[1,2]}', '[1,"x"]', '{}'].every(isValidJsonText)).toBe(true)
    expect(['"texte"', '5', 'null', '{pas du json', '', 7].some(isValidJsonText)).toBe(false)
  })
})

describe('texte enrichi : un sous-ensemble fermé, filtré avant l’envoi et avant l’affichage', () => {
  it('garde ce que l’éditeur produit', () => {
    const html = '<h2>Titre</h2><p>Un <strong>gras</strong>, un <em>italique</em>, <u>souligné</u>, <s>barré</s>.<br>Ligne</p><ul><li><p>a</p></li></ul><blockquote><p>q</p></blockquote><pre><code>x</code></pre><hr><p><a href="https://x.fr/a?b=1&amp;c=2">lien</a></p>'
    expect(sanitizeRich(html)).toBe(html)
  })

  it('retire les attributs, les balises hors liste et tout ce qui s’exécute', () => {
    expect(sanitizeRich('<p onclick="x()" style="color:red">a</p>')).toBe('<p>a</p>')
    expect(sanitizeRich('<p>a</p><script>alert(1)</script><p>b</p>')).toBe('<p>a</p><p>b</p>')
    expect(sanitizeRich('<img src=x onerror=alert(1)>texte')).toBe('texte')
    expect(sanitizeRich('<a href="javascript:alert(1)">piège</a>')).toBe('piège') // le lien disparaît, pas son texte
    expect(sanitizeRich('<a href="https://x.fr" target="_blank" rel="x" onclick="y">ok</a>')).toBe('<a href="https://x.fr">ok</a>')
    expect(sanitizeRich('<iframe src="https://x"></iframe><style>p{}</style><p>seul</p>')).toBe('<p>seul</p>')
    expect(sanitizeRich('<div>bloc</div>')).toBe('<p>bloc</p>')
    expect(sanitizeRich('1 < 2 & 3')).toBe('1 &lt; 2 &amp; 3')
  })

  it('le texte brut et le vide', () => {
    expect(plainText('<p>Un <strong>gras</strong></p><ul><li><p>a</p></li><li><p>b</p></li></ul>')).toBe('Un gras a b')
    expect([null, '', '<p></p>', '<p> </p>'].every(isRichEmpty)).toBe(true)
    expect(isRichEmpty('<p>x</p>')).toBe(false)
    expect(isRichEmpty('<hr>')).toBe(false)
  })
})

const META = {
  name: 'Specimen', label: 'Échantillon', title_field: 'title', timezone: 'UTC', layout: [], list_columns: [], rights: {},
  fields: [
    f({ fieldname: 'title', fieldtype: 'Text', required: true, in_list_view: true }),
    f({ fieldname: 'n', fieldtype: 'Int' }),
    f({ fieldname: 'pct', fieldtype: 'Percent' }),
    f({ fieldname: 'at', fieldtype: 'Time' }),
    f({ fieldname: 'dur', fieldtype: 'Duration' }),
    f({ fieldname: 'src', fieldtype: 'Code', options: 'python' }),
    f({ fieldname: 'extra', fieldtype: 'JSON' }),
    f({ fieldname: 'body', fieldtype: 'TextEditor' }),
    f({ fieldname: 'secret', fieldtype: 'Password' }),
  ],
} as unknown as DocTypeMeta
const check = (values: Record<string, unknown>) => validate(META, values, fieldStates(META, values, null))

describe('formulaire : contrôle, envoi et relecture', () => {
  it('chaque type est contrôlé avant l’envoi', () => {
    expect(check({ title: 't', n: 5, pct: 50, at: '08:00:00', dur: 5400, src: 'x', extra: '{"a":1}', body: '<p>x</p>', secret: 'abc' })).toEqual({})
    const bad = check({ title: 't', n: 1.5, pct: 101, at: '25:00:00', dur: 'abc', extra: '{pas du json', secret: '' })
    expect(Object.keys(bad).sort()).toEqual(['at', 'dur', 'extra', 'n', 'pct'].sort()) // un secret vide est « vide » (donc absent), pas une erreur
    expect(bad.dur).toContain('Durée attendue')
    expect(bad.extra).toContain('JSON invalide')
  })

  it('un JSON s’édite comme du texte et part comme un objet ; la relecture le remet en forme', () => {
    const rec = { doctype: 'Specimen', name: 'a', owner: 'u', creation: '', modified: '', version: 1, docstatus: 0, amended_from: null, data: { title: 't', extra: { b: [1, 2], a: 1 } } }
    const values = recordValues(META, rec)
    expect(values.extra).toBe('{\n  "b": [\n    1,\n    2\n  ],\n  "a": 1\n}')
    expect(toPayload(META, values).extra).toEqual({ b: [1, 2], a: 1 }) // l'objet, jamais le texte
    expect(toPayload(META, { ...values, extra: '{pas du json' }).extra).toBe('{pas du json') // arrêté par `validate` avant l'envoi
  })

  it('l’adresse pré-remplit les types simples, jamais un secret, du HTML ni un objet', () => {
    const preset = presetValues(META, { n: '12', pct: '33.5', at: '08:30', dur: '1h30', src: 'print(1)', extra: '{"a":1}', body: '<p>x</p>', secret: 'pirate' })
    expect(preset).toEqual({ n: 12, pct: 33.5, at: '08:30:00', dur: 5400, src: 'print(1)' })
    expect(presetValues(META, { n: '1.5', pct: '200', at: '99:00', dur: 'abc' })).toEqual({})
  })
})

describe('listes, filtres et export', () => {
  it('l’affichage d’une cellule', () => {
    const cell = (type: FieldMeta['fieldtype'], v: unknown) => formatCell(f({ fieldname: 'x', fieldtype: type }), v)
    expect(cell('Int', 12345)).toBe('12345') // pas de séparateur de milliers : un identifiant, un nombre de pièces
    expect(cell('Percent', 12.5)).toBe('12,5 %')
    expect(cell('Duration', 5400)).toBe('1 h 30 min')
    expect(cell('Time', '08:00:00')).toBe('08:00')
    expect(cell('Time', '08:00:30')).toBe('08:00:30')
    expect(cell('Password', '********')).toBe('••••••••')
    expect(cell('TextEditor', '<p>Un <strong>gras</strong></p>')).toBe('Un gras')
    expect(cell('JSON', { a: 1 })).toBe('{"a":1}')
    expect(cell('Code', 'ligne 1\nligne 2')).toBe('ligne 1…')
    expect(['Int', 'Percent', 'Duration'].every((t) => isNumeric(f({ fieldname: 'x', fieldtype: t as FieldMeta['fieldtype'] })))).toBe(true)
    expect(isNumeric(f({ fieldname: 'x', fieldtype: 'Time' }))).toBe(false)
  })

  it('les opérateurs, comme le serveur', () => {
    expect(FILTER_OPS.Int).toContain('>=')
    expect(FILTER_OPS.Time).toContain('<')
    expect(FILTER_OPS.Code).toEqual(['like'])
    for (const t of ['JSON', 'Password', 'TextEditor'] as const) expect(FILTER_OPS[t]).toEqual([])
    expect(defaultOp('Code')).toBe('like')
    expect(defaultOp('Int')).toBe('=')
  })

  it('un filtre de durée se saisit « 1h30 » et part en secondes ; une heure se complète', () => {
    const rows = (field: string, op: string, value: unknown) => [{ id: 1, field, op, value }]
    expect(buildFilters(rows('dur', '>=', '1h30'), META.fields)).toEqual({ dur: ['>=', 5400] })
    expect(buildFilters(rows('dur', 'in', '1h, 90m, 30s'), META.fields)).toEqual({ dur: ['in', [3600, 5400, 30]] })
    expect(buildFilters(rows('dur', '=', 'bientôt'), META.fields)).toEqual({}) // illisible : ignoré, jamais envoyé
    expect(buildFilters(rows('at', '>=', '08:30'), META.fields)).toEqual({ at: ['>=', '08:30:00'] })
    expect(buildFilters(rows('at', '>=', '8h'), META.fields)).toEqual({})
    expect(buildFilters(rows('n', '>', 5), META.fields)).toEqual({ n: ['>', 5] })
    expect(buildFilters(rows('pct', '<', '50'), META.fields)).toEqual({ pct: ['<', 50] })
    expect(buildFilters(rows('secret', '=', 'x'), META.fields)).toEqual({}) // un secret ne se filtre pas
    expect(buildFilters(rows('extra', 'like', 'x'), META.fields)).toEqual({})
  })

  it('l’export : nombres en virgule décimale, durée en « 1:30:00 », secret jamais exporté', () => {
    const rec = { doctype: 'X', name: 'N', owner: 'a', creation: '', modified: '', version: 1, docstatus: 0, amended_from: null, data: { n: -3, pct: 12.5, dur: 5415, secret: '********', body: '<p>Un <em>texte</em></p>', extra: { a: 1 } } }
    const of = (name: string) => exportValue(META.fields.find((x) => x.fieldname === name), name, rec)
    expect(of('n')).toEqual({ text: '-3', textual: false })
    expect(of('pct')).toEqual({ text: '12,5', textual: false })
    expect(of('dur')).toEqual({ text: '1:30:15', textual: true })
    expect(of('secret')).toEqual({ text: '', textual: true })
    expect(of('body')).toEqual({ text: 'Un texte', textual: true })
    expect(of('extra')).toEqual({ text: '{"a":1}', textual: true })
    expect(isTextualColumn(META.fields[1])).toBe(false)
    expect(buildCsv(['n'], [['-3']], [false])).toContain('-3')
  })
})

const mountInput = (field: FieldMeta, value: unknown) => mountWith(FieldInput, { field, modelValue: value, inputId: 'x' }).w
const last = (w: ReturnType<typeof mountInput>) => w.emitted('update:modelValue')!.at(-1)![0]

describe('champs de saisie', () => {
  it('un entier n’a pas de décimale', async () => {
    const w = mountInput(f({ fieldname: 'n', fieldtype: 'Int' }), 5)
    expect((w.find('input').element as HTMLInputElement).value).toBe('5')
    await w.find('input').setValue('12')
    await w.find('input').trigger('blur')
    expect(last(w)).toBe(12)
  })

  it('une heure ouvre le sélecteur du navigateur et se complète en hh:mm:ss', async () => {
    const w = mountInput(f({ fieldname: 'at', fieldtype: 'Time' }), '08:00:00')
    expect(w.find('input').attributes('type')).toBe('time')
    expect(w.find('input').attributes('step')).toBe('1')
    await w.find('input').setValue('09:15')
    expect(last(w)).toBe('09:15:00')
    await w.find('input').setValue('')
    expect(last(w)).toBeNull()
  })

  it('une durée se saisit en clair, s’envoie en secondes, et garde un texte illisible plutôt que de l’effacer', async () => {
    const w = mountInput(f({ fieldname: 'd', fieldtype: 'Duration' }), 5400)
    expect((w.find('input').element as HTMLInputElement).value).toBe('1 h 30 min')
    await w.find('input').setValue('2h')
    expect(last(w)).toBe(7200)
    await w.find('input').setValue('bientôt')
    expect(last(w)).toBe('bientôt') // `validate` le signalera ; rien n'est perdu en silence
    await w.find('input').setValue('')
    expect(last(w)).toBeNull()
    await w.find('input').setValue('45min')
    await w.find('input').trigger('blur')
    expect((w.find('input').element as HTMLInputElement).value).toBe('45 min')
  })

  it('du code et du JSON sont des zones monospace ; les vider efface la valeur', async () => {
    const w = mountInput(f({ fieldname: 's', fieldtype: 'Code', options: 'python' }), 'print(1)')
    expect(w.find('textarea').classes()).toContain('code-input')
    expect(w.find('textarea').attributes('data-language')).toBe('python')
    await w.find('textarea').setValue('')
    expect(last(w)).toBeNull()
    const j = mountInput(f({ fieldname: 'j', fieldtype: 'JSON' }), '{"a":1}')
    expect(j.find('textarea').attributes('data-language')).toBe('json')
  })
})

describe('un secret : un masque tant qu’on n’y touche pas', () => {
  const mountSecret = (value: unknown) => mount(SecretField, { props: { modelValue: value, inputId: 's' }, global: { plugins: [PrimeVue] } })

  it('un secret enregistré s’affiche masqué, sans champ de saisie ; « Changer » ouvre une saisie vide', async () => {
    const w = mountSecret('********')
    expect(w.find('[data-testid="secret-set"]').exists()).toBe(true)
    expect(w.find('input').exists()).toBe(false)
    expect(w.text()).not.toContain('********')
    await w.find('[data-testid="secret-change"]').trigger('click')
    expect(w.emitted('update:modelValue')!.at(-1)![0]).toBeNull()
    await w.setProps({ modelValue: null })
    expect(w.find('input').exists()).toBe(true)
  })

  it('un nouveau secret se saisit ; vide, il s’efface', async () => {
    const w = mountSecret(null)
    await w.find('input').setValue('nouveau-secret')
    expect(w.emitted('update:modelValue')!.at(-1)![0]).toBe('nouveau-secret')
    await w.find('input').setValue('')
    expect(w.emitted('update:modelValue')!.at(-1)![0]).toBeNull()
  })

  it('le masque repart tel quel à l’enregistrement (« inchangé »)', () => {
    expect(toPayload(META, { title: 't', secret: '********' }).secret).toBe('********')
  })
})

describe('sélection multiple (Table dont les lignes n’ont qu’un Link)', () => {
  const TAGS_FIELD = f({ fieldname: 'tags', fieldtype: 'Table', options: 'Specimen Tag', multiselect: true })
  const CHILD: ChildMeta = { name: 'Specimen Tag', label: 'Étiquette', fields: [f({ fieldname: 'tag', fieldtype: 'Link', options: 'Tag', required: true })] }

  function mountTags(rows: Record<string, unknown>[], readOnly = false) {
    const searchCalls: string[] = []
    const w = mount(TableField, {
      props: { field: TAGS_FIELD, child: CHILD, modelValue: rows, required: false, readOnly, errors: {}, formId: 'f', parentValues: {} },
      global: {
        plugins: [PrimeVue, createPinia()],
        stubs: { RouterLink: { props: ['to'], template: '<a class="tag-name"><slot /></a>' } },
        provide: {
          [SERVICES as symbol]: {
            transport: {}, config: { baseUrl: '', loginUrl: null },
            api: {
              list: async (_d: string, o: { filters?: { name?: [string, string[]] } }) => ({ items: (o.filters?.name?.[1] ?? []).map((n) => ({ name: n, data: { label: `Étiquette ${n}` }, doctype: 'Tag' })), total: 1, limit: 20, offset: 0 }),
              meta: async () => ({ name: 'Tag', title_field: 'label', fields: [], layout: [], list_columns: [], rights: {} }),
              search: async (_d: string, q: string) => {
                searchCalls.push(q)
                return [{ name: 't3', title: 'Trois' }]
              },
            },
          },
        },
      },
    })
    return { w, searchCalls }
  }

  it('les valeurs s’affichent comme des étiquettes (par leur titre), pas comme une grille', async () => {
    const { w } = mountTags([{ tag: 't1' }, { tag: 't2' }])
    await flushPromises()
    expect(w.find('table').exists()).toBe(false)
    expect(w.findAll('.tag').map((t) => t.attributes('data-testid'))).toEqual(['tag-t1', 'tag-t2'])
  })

  it('retirer une étiquette retire sa ligne ; en lecture seule, rien ne se retire ni ne s’ajoute', async () => {
    const { w } = mountTags([{ tag: 't1' }, { tag: 't2' }])
    await flushPromises()
    await w.find('[data-testid="tag-remove-t1"]').trigger('click')
    expect(w.emitted('update:modelValue')!.at(-1)![0]).toEqual([{ tag: 't2' }])
    const ro = mountTags([{ tag: 't1' }], true)
    await flushPromises()
    expect(ro.w.find('[data-testid="tag-remove-t1"]').exists()).toBe(false)
    expect(ro.w.find('[data-testid="tag-add"]').exists()).toBe(false)
  })

  it('une étiquette vide ou déjà présente n’est pas ajoutée deux fois', async () => {
    const { w } = mountTags([{ tag: 't1' }])
    await flushPromises()
    const add = w.findComponent({ name: 'LinkField' })
    add.vm.$emit('update:modelValue', 't1')
    add.vm.$emit('update:modelValue', null)
    expect(w.emitted('update:modelValue')).toBeUndefined()
    add.vm.$emit('update:modelValue', 't3')
    expect(w.emitted('update:modelValue')!.at(-1)![0]).toEqual([{ tag: 't1' }, expect.objectContaining({ tag: 't3' })])
  })
})
