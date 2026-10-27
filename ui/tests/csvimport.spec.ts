import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ImportDialog from '@/components/ImportDialog.vue'
import { decodeCsv, importable, templateCsv, templateHeaders } from '@/lib/csvimport'
import { SERVICES } from '@/lib/services'
import type { DocTypeMeta, FieldMeta } from '@/lib/types'
import { captureToasts } from './views-fixtures'

const field = (fieldname: string, fieldtype: FieldMeta['fieldtype'], extra: Partial<FieldMeta> = {}): FieldMeta => ({
  fieldname, fieldtype, required: false, hidden: false, in_list_view: false, ...extra,
})
const RIGHTS = { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' } as DocTypeMeta['rights']
const CLIENT: DocTypeMeta = {
  name: 'Client', label: 'Client', layout: [], list_columns: [], rights: RIGHTS, timezone: 'UTC',
  fields: [
    field('nom', 'Text', { label: 'Nom' }),
    field('age', 'Int', { label: 'Âge' }),
    field('a', 'Text', { label: 'Code' }),
    field('b', 'Text', { label: 'code' }),
    field('lignes', 'Table', { label: 'Lignes', options: 'Ligne' }),
    field('secret', 'Password', { label: 'Secret' }),
    field('photo', 'Image', { label: 'Photo' }),
    field('pays_nom', 'Text', { label: 'Nom du pays', fetch_from: 'pays.nom' }),
    field('calc', 'Text', { label: 'Calculé', read_only: true }),
    field('cache', 'Text', { label: 'Caché', hidden: true }),
  ],
}

describe('import CSV : préparer le fichier', () => {
  it('le modèle ne propose que les champs importables, par libellé, et par nom quand le libellé est ambigu ou se confond avec le nom', () => {
    expect(CLIENT.fields.filter(importable).map((f) => f.fieldname)).toEqual(['nom', 'age', 'a', 'b'])
    expect(templateHeaders(CLIENT, 'insert')).toEqual(['nom', 'Âge', 'a', 'b'])
    expect(templateHeaders(CLIENT, 'update')).toEqual(['name', 'nom', 'Âge', 'a', 'b'])
    expect(templateHeaders({ ...CLIENT, naming: 'prompt' }, 'insert')[0]).toBe('name')
    expect(templateCsv(CLIENT, 'insert')).toBe('﻿nom;Âge;a;b\r\n')
  })

  it('un libellé que le serveur confondrait (casse à l’allemande, nom d’un autre champ) devient le nom du champ', () => {
    const meta: DocTypeMeta = {
      ...CLIENT,
      fields: [field('rue', 'Text', { label: 'Straße' }), field('rue2', 'Text', { label: 'STRASSE' }), field('code', 'Text', { label: 'Code' }), field('ref', 'Text', { label: 'code' }), field('x', 'Text', { label: 'ref' })],
    }
    expect(templateHeaders(meta, 'insert')).toEqual(['rue', 'rue2', 'code', 'ref', 'x'])
  })

  it('lit l’UTF-8 (avec BOM) et, à défaut, le Windows-1252 d’Excel', () => {
    expect(decodeCsv(new TextEncoder().encode('Nom;Âge\nÉva;3\n'))).toBe('Nom;Âge\nÉva;3\n')
    expect(decodeCsv(new Uint8Array([0x4e, 0x6f, 0x6d, 0x0a, 0xc9, 0x76, 0x61]))).toBe('Nom\nÉva') // 0xC9 seul n'est pas de l'UTF-8 : É en 1252
    expect(decodeCsv(new Uint8Array([0x80, 0x92, 0x9c, 0xe9]))).toBe('€’œé')
  })
})

describe('import CSV : la boîte de dialogue', () => {
  const toasts = captureToasts()
  let w: VueWrapper | null = null
  afterEach(() => {
    w?.unmount()
    w = null
    document.body.innerHTML = ''
  })

  function open(meta: DocTypeMeta, importCsv: (...a: unknown[]) => Promise<unknown>) {
    w = mount(ImportDialog as never, {
      props: { doctype: 'Client', meta, visible: false } as never,
      attachTo: document.body,
      global: {
        plugins: [PrimeVue, ToastService, createPinia()],
        provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '/x', loginUrl: null }, api: { importCsv } } },
      },
    })
    return w
  }
  const q = (id: string) => document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`)
  async function choose(text: string | Uint8Array) {
    const input = q('import-file') as HTMLInputElement
    const file = new File([text as BlobPart], 'clients.csv', { type: 'text/csv' })
    Object.defineProperty(input, 'files', { value: [file], configurable: true })
    input.dispatchEvent(new Event('change'))
    await flushPromises()
  }

  it('envoie le texte du fichier et le mode choisi ; tout passé : une notification, la liste se relit, la boîte se ferme', async () => {
    const importCsv = vi.fn(async () => ({ results: [{ row: 2, ok: true, name: 'x' }], done: 1, failed: 0 }))
    const d = open(CLIENT, importCsv)
    await d.setProps({ visible: true } as never)
    expect((q('import-run') as HTMLButtonElement).disabled).toBe(true) // pas de fichier, rien à envoyer
    await choose('Nom;Âge\nÉva;3\n')
    expect(q('import-mode')).not.toBeNull()
    q('import-run')!.click()
    await flushPromises()
    expect(importCsv).toHaveBeenCalledWith('Client', 'Nom;Âge\nÉva;3\n', 'insert')
    expect(d.emitted('imported')).toHaveLength(1)
    expect(d.emitted('update:visible')?.at(-1)).toEqual([false])
    expect(toasts.summaries()).toContain('1 document importé')
  })

  it('les lignes refusées restent sous les yeux, numérotées comme dans le fichier, avec la raison du serveur', async () => {
    const importCsv = vi.fn(async () => ({
      results: [
        { row: 2, ok: true, name: 'x' },
        { row: 3, ok: false, code: 'invalid_value', message: 'Valeur illisible', details: [{ field: 'age', msg: 'nombre entier attendu' }] },
        { row: 4, ok: false, code: 'hook_rejected', message: 'Ce nom est refusé' },
      ],
      done: 1, failed: 2,
    }))
    const d = open(CLIENT, importCsv)
    await d.setProps({ visible: true } as never)
    await choose('x')
    q('import-run')!.click()
    await flushPromises()
    expect(q('import-report')?.textContent).toContain('1 importé, 2 lignes refusées')
    expect(q('import-fail-3')?.textContent).toContain('Ligne 3')
    expect(q('import-fail-3')?.textContent).toContain('age : nombre entier attendu')
    expect(q('import-fail-4')?.textContent).toContain('Ce nom est refusé')
    expect(q('import-fail-2')).toBeNull()
    expect(d.emitted('imported')).toHaveLength(1) // une ligne est passée : la liste se relit
  })

  it('un en-tête refusé : le message du serveur, rien d’importé', async () => {
    const { ApiError } = await import('@/lib/api')
    const importCsv = vi.fn(async () => { throw new ApiError(422, 'csv_columns', 'L’en-tête du fichier ne correspond pas au type', []) })
    const d = open(CLIENT, importCsv)
    await d.setProps({ visible: true } as never)
    await choose('x')
    q('import-run')!.click()
    await flushPromises()
    expect(q('import-error')?.textContent).toContain('ne correspond pas')
    expect(d.emitted('imported')).toBeUndefined()
  })

  it('le mode suit les droits : qui ne peut que modifier n’a que la mise à jour', async () => {
    const importCsv = vi.fn(async () => ({ results: [], done: 0, failed: 0 }))
    const d = open({ ...CLIENT, rights: { ...RIGHTS, create: 'none' } }, importCsv)
    await d.setProps({ visible: true } as never)
    expect(q('import-mode')).toBeNull()
    await choose('name;Nom\nx;y\n')
    q('import-run')!.click()
    await flushPromises()
    expect(importCsv).toHaveBeenCalledWith('Client', 'name;Nom\nx;y\n', 'update')
  })
})
