import { afterEach, describe, expect, it, vi } from 'vitest'
import CellValue from '@/components/CellValue.vue'
import FileField from '@/components/FileField.vue'
import FileThumb from '@/components/FileThumb.vue'
import { ApiClient, ApiError } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { FetchTransport } from '@/lib/transport'
import type { FieldMeta, FileLink } from '@/lib/types'
import { useFiles } from '@/stores/files'
import { flushPromises, mountWith } from './views-fixtures'

const ID = 'a'.repeat(32)
const ID2 = 'b'.repeat(32)
const link = (over: Partial<FileLink> = {}): FileLink => ({ path: `files/acme/${ID}?exp=9999999999&sig=${'0'.repeat(64)}`, filename: 'moi.png', content_type: 'image/png', size: 2048, expires: Math.floor(Date.now() / 1000) + 300, ...over })
const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })
const photo = f({ fieldname: 'photo', label: 'Photo', fieldtype: 'Image', max_mb: 1 })
const contract = f({ fieldname: 'contract', fieldtype: 'Attach' })

afterEach(() => {
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('transport et client : un téléversement part en multipart', () => {
  it('un FormData n’est ni sérialisé en JSON ni accompagné d’un Content-Type fixe (le navigateur pose la frontière)', async () => {
    const seen: { init?: RequestInit } = {}
    const transport = new FetchTransport(async (_url, init) => {
      seen.init = init
      return new Response(JSON.stringify({ id: ID, filename: 'a.png', content_type: 'image/png', size: 3 }), { status: 201 })
    })
    const api = new ApiClient(transport, () => ({ baseUrl: '/app/x', token: 't', tenant: 'acme' }))
    const meta = await api.upload('Employee', 'photo', new File(['abc'], 'a.png', { type: 'image/png' }))
    expect(meta.id).toBe(ID)
    expect(seen.init!.body).toBeInstanceOf(FormData)
    expect((seen.init!.body as FormData).get('file')).toBeInstanceOf(File)
    expect((seen.init!.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    expect((seen.init!.headers as Record<string, string>).Authorization).toBe('Bearer t')
  })

  it('l’URL cible le type et le champ, encodés', async () => {
    let url = ''
    const transport = new FetchTransport(async (u) => {
      url = String(u)
      return new Response('{}', { status: 201 })
    })
    await new ApiClient(transport, () => ({ baseUrl: '/b', token: null, tenant: null })).upload('Stock Entry', 'photo', new File(['x'], 'x.png'))
    expect(url).toBe('/b/upload/Stock%20Entry/photo')
  })

  it('les liens se demandent en JSON, comme le reste', async () => {
    let init: RequestInit | undefined
    const transport = new FetchTransport(async (_u, i) => {
      init = i
      return new Response('{}', { status: 200 })
    })
    await new ApiClient(transport, () => ({ baseUrl: '/b', token: null, tenant: null })).fileLinks([ID])
    expect(init!.body).toBe(JSON.stringify({ ids: [ID] }))
    expect((init!.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('les tailles se lisent : o, Ko, Mo', () => {
    expect([512, 2048, 1_572_864].map(formatBytes).map((s) => s.replace(/\s/g, ' '))).toEqual(['512 o', '2 Ko', '1,5 Mo'])
  })
})

describe('store des liens de fichiers', () => {
  const setup = (fileLinks: (ids: string[]) => Promise<Record<string, FileLink>>) => {
    const { w } = mountWith({ render: () => null }, {}, { fileLinks })
    void w
    return useFiles()
  }

  it('les liens se demandent par lot, une seule fois par fichier, même demandés ensemble', async () => {
    const fileLinks = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((i) => [i, link()])))
    const files = setup(fileLinks)
    await Promise.all([files.resolve([ID, ID2]), files.resolve([ID]), files.resolve([ID2, ID])])
    expect(fileLinks).toHaveBeenCalledTimes(1)
    expect(fileLinks).toHaveBeenCalledWith([ID, ID2])
    await files.resolve([ID]) // déjà valide : aucune requête
    expect(fileLinks).toHaveBeenCalledTimes(1)
    expect(files.url(ID)).toBe(`${''}/${link().path}`.replace('//', '/'))
  })

  it('plus de 100 fichiers : plusieurs lots (le plafond du serveur)', async () => {
    const fileLinks = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((i) => [i, link()])))
    const files = setup(fileLinks)
    const ids = Array.from({ length: 230 }, (_, i) => i.toString(16).padStart(32, '0'))
    await files.resolve(ids)
    expect(fileLinks.mock.calls.map((c) => c[0].length)).toEqual([100, 100, 30])
  })

  it('un lien qui expire dans moins de 30 secondes est redemandé', async () => {
    const fileLinks = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((i) => [i, link({ expires: Math.floor(Date.now() / 1000) + 20 })])))
    const files = setup(fileLinks)
    await files.resolve([ID])
    await files.resolve([ID])
    expect(fileLinks).toHaveBeenCalledTimes(2)
  })

  it('un fichier qu’on ne peut pas lire n’est pas redemandé à chaque affichage, mais l’est plus tard', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-18T10:00:00Z'))
    const fileLinks = vi.fn(async () => ({}))
    const files = setup(fileLinks)
    await files.resolve([ID])
    await files.resolve([ID])
    expect(fileLinks).toHaveBeenCalledTimes(1)
    expect(files.url(ID)).toBeNull()
    vi.setSystemTime(new Date('2026-10-18T10:01:00Z'))
    await files.resolve([ID])
    expect(fileLinks).toHaveBeenCalledTimes(2)
  })

  it('« force » redemande même un lien qu’on croit valide (une image qui a échoué)', async () => {
    const fileLinks = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((i) => [i, link()])))
    const files = setup(fileLinks)
    await files.resolve([ID])
    await files.resolve([ID], { force: true })
    expect(fileLinks).toHaveBeenCalledTimes(2)
  })

  it('une erreur réseau ne casse rien : le fichier reste sans lien', async () => {
    const files = setup(async () => {
      throw new ApiError(500, 'internal_error', 'x')
    })
    await expect(files.resolve([ID])).resolves.toBeUndefined()
    expect(files.url(ID)).toBeNull()
    expect(files.info(null)).toBeNull()
  })

  it('un fichier devenu illisible disparaît du magasin', async () => {
    let allowed = true
    const files = setup(async (ids) => (allowed ? Object.fromEntries(ids.map((i) => [i, link()])) : {}))
    await files.resolve([ID])
    expect(files.url(ID)).not.toBeNull()
    allowed = false
    await files.resolve([ID], { force: true })
    expect(files.url(ID)).toBeNull()
  })
})

const withFiles = (fileLinks: (ids: string[]) => Promise<Record<string, FileLink>>) => ({ fileLinks, meta: async () => ({}) })

describe('vignette', () => {
  it('une image, avec le lien signé ; pendant le chargement une icône', async () => {
    const { w } = mountWith(FileThumb, { id: ID, size: 40 }, withFiles(async () => ({ [ID]: link() })))
    expect(w.find('[data-testid="thumb-empty"]').exists()).toBe(true)
    await flushPromises()
    const img = w.find('img')
    expect(img.attributes('src')).toContain(`files/acme/${ID}`)
    expect(img.attributes('width')).toBe('40')
    expect(img.attributes('referrerpolicy')).toBe('no-referrer')
    expect(w.find('[data-testid="thumb-empty"]').exists()).toBe(false)
  })

  it('sans identifiant, sans droit, ou valeur qui n’est pas un texte : l’icône, jamais une image cassée', async () => {
    expect(mountWith(FileThumb, { id: null }, withFiles(async () => ({}))).w.find('img').exists()).toBe(false)
    expect(mountWith(FileThumb, { id: 12 }, withFiles(async () => ({}))).w.find('img').exists()).toBe(false)
    const { w } = mountWith(FileThumb, { id: ID }, withFiles(async () => ({})))
    await flushPromises()
    expect(w.find('img').exists()).toBe(false)
  })

  it('un lien expiré : le lien est redemandé UNE fois, puis l’icône', async () => {
    const fileLinks = vi.fn(async () => ({ [ID]: link() }))
    const { w } = mountWith(FileThumb, { id: ID }, withFiles(fileLinks))
    await flushPromises()
    await w.find('img').trigger('error')
    await flushPromises()
    expect(fileLinks).toHaveBeenCalledTimes(2)
    await w.find('img').trigger('error')
    await flushPromises()
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('[data-testid="thumb-empty"]').exists()).toBe(true)
    expect(fileLinks).toHaveBeenCalledTimes(2) // pas de boucle
  })
})

const pick = async (w: ReturnType<typeof mountWith>['w'], file: File) => {
  const input = w.find('[data-testid="file-input"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

describe('champ Image / Attach', () => {
  const mk = (field: FieldMeta, value: unknown, extra: Record<string, unknown> = {}) =>
    mountWith(FileField, { field, modelValue: value, doctype: 'Employee', inputId: 'x' }, { meta: async () => ({}), fileLinks: async (ids: string[]) => Object.fromEntries(ids.map((i) => [i, link()])), ...extra })

  it('un fichier choisi part au serveur POUR ce champ de ce type, puis son identifiant est émis', async () => {
    const upload = vi.fn(async () => ({ id: ID, filename: 'moi.png', content_type: 'image/png', size: 3 }))
    const { w } = mk(photo, null, { upload })
    const file = new File(['abc'], 'moi.png', { type: 'image/png' })
    await pick(w, file)
    expect(upload).toHaveBeenCalledWith('Employee', 'photo', file)
    expect(w.emitted('update:modelValue')).toEqual([[ID]])
  })

  it('trop gros ou pas une image : refusé avant l’envoi, avec le motif', async () => {
    const upload = vi.fn()
    const { w } = mk(photo, null, { upload })
    await pick(w, new File([new Uint8Array(1024 * 1024 + 1)], 'gros.png', { type: 'image/png' }))
    expect(w.find('[data-testid="file-error"]').text()).toContain('maximum 1 Mo')
    await pick(w, new File(['x'], 'a.pdf', { type: 'application/pdf' }))
    expect(w.find('[data-testid="file-error"]').text()).toBe('Ce fichier n’est pas une image')
    expect(upload).not.toHaveBeenCalled()
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('une pièce jointe n’exige pas une image', async () => {
    const upload = vi.fn(async () => ({ id: ID2, filename: 'c.pdf', content_type: 'application/pdf', size: 1 }))
    const { w } = mk(contract, null, { upload })
    await pick(w, new File(['x'], 'c.pdf', { type: 'application/pdf' }))
    expect(w.emitted('update:modelValue')).toEqual([[ID2]])
    expect(w.find('input[type="file"]').attributes('accept')).toBeUndefined()
  })

  it('le serveur refuse (contenu, droits) : le motif est dit, rien n’est émis', async () => {
    const upload = vi.fn(async () => {
      throw new ApiError(415, 'file_type_not_allowed', 'Image attendue : PNG, JPEG, GIF ou WebP')
    })
    const { w } = mk(photo, null, { upload })
    await pick(w, new File(['x'], 'faux.png', { type: 'image/png' }))
    expect(w.find('[data-testid="file-error"]').text()).toContain('Image attendue')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('une image montre sa vignette, « Remplacer » et « Retirer » ; retirer émet null', async () => {
    const { w } = mk(photo, ID)
    await flushPromises()
    expect(w.find('img').exists()).toBe(true)
    expect(w.find('[data-testid="file-pick"]').text()).toBe('Remplacer')
    await w.find('[data-testid="file-remove"]').trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([[null]])
  })

  it('sans fichier : une invitation, « Choisir un fichier », pas de « Retirer »', () => {
    const { w } = mk(photo, null)
    expect(w.text()).toContain('Aucun fichier')
    expect(w.find('[data-testid="file-pick"]').text()).toBe('Choisir un fichier')
    expect(w.find('[data-testid="file-remove"]').exists()).toBe(false)
  })

  it('une pièce jointe montre son nom et sa taille, avec un lien de téléchargement', async () => {
    const { w } = mk(contract, ID, { fileLinks: async () => ({ [ID]: link({ filename: 'contrat.pdf', size: 1_572_864 }) }) })
    await flushPromises()
    const a = w.find('[data-testid="file-name"]')
    expect(a.text()).toBe('contrat.pdf')
    expect(a.attributes('href')).toContain(`files/acme/${ID}`)
    expect(a.attributes('download')).toBe('contrat.pdf')
    expect(w.text().replace(/\s/g, ' ')).toContain('1,5 Mo')
  })

  it('déposer un fichier sur la zone le téléverse', async () => {
    const upload = vi.fn(async () => ({ id: ID, filename: 'd.png', content_type: 'image/png', size: 1 }))
    const { w } = mk(photo, null, { upload })
    const file = new File(['x'], 'd.png', { type: 'image/png' })
    await w.find('[data-testid="file-field"]').trigger('dragover')
    expect(w.find('.file-field').classes()).toContain('over')
    await w.find('[data-testid="file-field"]').trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    expect(upload).toHaveBeenCalledWith('Employee', 'photo', file)
    expect(w.find('.file-field').classes()).not.toContain('over')
  })

  it('une image n’accepte que les formats d’image dans le sélecteur', () => {
    expect(mk(photo, null).w.find('input[type="file"]').attributes('accept')).toBe('image/png,image/jpeg,image/gif,image/webp')
  })
})

describe('cellules fichier', () => {
  const show = async (field: FieldMeta, value: unknown) => {
    const { w } = mountWith(CellValue, { field, value }, withFiles(async () => ({ [ID]: link({ filename: 'contrat.pdf' }) })))
    await flushPromises()
    return w
  }

  it('une image est une vignette, une pièce jointe un lien qui porte son nom', async () => {
    expect((await show(photo, ID)).find('[data-testid="cell-image"]').exists()).toBe(true)
    const file = await show(contract, ID)
    expect(file.find('[data-testid="cell-file"]').text()).toBe('contrat.pdf')
    expect(file.find('a').attributes('download')).toBe('contrat.pdf')
  })

  it('sans valeur : rien', async () => {
    expect((await show(photo, null)).html()).not.toContain('thumb')
    expect((await show(contract, '')).find('a').exists()).toBe(false)
  })
})

