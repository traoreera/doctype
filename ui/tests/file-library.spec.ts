import { ConfirmationEventBus } from 'primevue'
import Paginator from 'primevue/paginator'
import Select from 'primevue/select'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { FILE_SORTS, citationKey, detachTarget, detailLines, isImage, sortQuery } from '@/lib/filelibrary'
import type { DocTypeMeta, FileLibraryItem, FileLibraryResult } from '@/lib/types'
import FilesView from '@/views/FilesView.vue'
import { TASK, captureToasts, doc, flushPromises, mountWith, unmountAll } from './views-fixtures'

afterEach(() => {
  unmountAll()
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

const PHOTO = 'a'.repeat(32)
const PDF = 'b'.repeat(32)
const item = (over: Partial<FileLibraryItem> = {}): FileLibraryItem => ({
  file_id: PHOTO,
  filename: 'photo.png',
  content_type: 'image/png',
  size: 2048,
  owner: 'ada',
  creation: '2026-10-20T08:00:00Z',
  doctype: 'Employee',
  doctype_label: 'Salarié',
  docname: 'E-1',
  field: 'photo',
  field_label: 'Photo',
  attachment: null,
  version: 3,
  docstatus: 0,
  track_changes: true,
  can_detach: true,
  ...over,
})
const attached = item({ file_id: PDF, filename: 'avenant.pdf', content_type: 'application/pdf', field: '_attachment', field_label: null, attachment: 'c'.repeat(32), version: 3 })
const result = (items: FileLibraryItem[], over: Partial<FileLibraryResult> = {}): FileLibraryResult => ({
  items,
  total: items.length,
  limit: 20,
  offset: 0,
  doctypes: [{ name: 'Employee', label: 'Salarié', count: items.length }],
  unavailable: [],
  ...over,
})
const EMPLOYEE: DocTypeMeta = { ...TASK, name: 'Employee', label: 'Salarié', title_field: 'full_name' }
const later = (s: number) => Math.floor(Date.now() / 1000) + s
const link = (id: string, expires = later(600)) => ({ path: `files/acme/${id}?exp=${expires}&sig=x`, filename: id, content_type: 'image/png', size: 1, expires })

function setup(api: Record<string, unknown> = {}) {
  const fileLibrary = vi.fn(async (): Promise<FileLibraryResult> => result([item(), attached]))
  const detachFile = vi.fn(async () => ({ detached: 'field', version: 4 }))
  const fileLinks = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, link(id)])))
  const all = {
    meta: async () => EMPLOYEE,
    list: async () => ({ items: [doc('E-1', { full_name: 'Hana Diallo' }, { doctype: 'Employee' })], total: 1, limit: 1, offset: 0 }),
    users: async () => ({ ada: { id: 'ada', email: 'ada@acme.test', name: 'Ada Lovelace' } }),
    fileLibrary,
    detachFile,
    fileLinks,
    ...api,
  }
  const { w, router } = mountWith(FilesView, {}, all)
  return { w, router, fileLibrary: all.fileLibrary as typeof fileLibrary, detachFile: all.detachFile as typeof detachFile, fileLinks: all.fileLinks as typeof fileLinks }
}
const rows = (w: ReturnType<typeof setup>['w']) => w.findAll('[data-testid="file-row"]')
/** Répond à la prochaine boîte de confirmation : accepter, ou refuser. */
function answer(yes: boolean): () => void {
  const on = (o: { accept?: () => void; reject?: () => void }) => (yes ? o.accept?.() : o.reject?.())
  ConfirmationEventBus.on('confirm', on as never)
  return () => ConfirmationEventBus.off('confirm', on as never)
}

describe('écran « Fichiers » (lib)', () => {
  it('la clé d’une ligne est le fichier ET ce qui le cite (un fichier que deux documents citent fait deux lignes)', () => {
    expect(citationKey(item())).not.toBe(citationKey(item({ docname: 'E-2' })))
    expect(citationKey(item())).toBe(citationKey(item({ size: 1 })))
  })

  it('détacher : la pièce jointe seule, ou le champ du document avec le fichier vu et la version lue', () => {
    expect(detachTarget(attached)).toEqual({ attachment: 'c'.repeat(32) })
    expect(detachTarget(item())).toEqual({ doctype: 'Employee', name: 'E-1', field: 'photo', file_id: PHOTO, version: 3 })
  })

  it('le détail d’un refus : un champ nommé par son libellé, sans doublon ni ligne vide', () => {
    const lines = detailLines(
      [{ field: 'photo', msg: 'Champ obligatoire' }, { field: 'photo', msg: 'Champ obligatoire' }, { msg: '  ' }, { msg: 'Refusé par la règle' }],
      (f) => (f === 'photo' ? 'Photo' : f),
    )
    expect(lines).toEqual(['Photo : Champ obligatoire', 'Refusé par la règle'])
  })

  it('les tris proposés : date, nom, taille, dans les deux sens, comme le serveur les attend', () => {
    expect(FILE_SORTS.map(sortQuery)).toEqual([
      { order_by: 'creation', order: 'desc' },
      { order_by: 'creation', order: 'asc' },
      { order_by: 'filename', order: 'asc' },
      { order_by: 'filename', order: 'desc' },
      { order_by: 'size', order: 'desc' },
      { order_by: 'size', order: 'asc' },
    ])
  })

  it('seules les vraies images ont une vignette (le serveur sert le reste en téléchargement)', () => {
    expect(isImage({ content_type: 'image/png' })).toBe(true)
    expect(isImage({ content_type: 'image/svg+xml' })).toBe(false)
    expect(isImage({ content_type: 'application/pdf' })).toBe(false)
  })
})

describe('écran « Fichiers »', () => {
  const toasts = captureToasts()

  it('un titre de page, et une ligne par citation : fichier, document (son titre, lien vers sa fiche), champ, taille, auteur', async () => {
    const { w, fileLibrary } = setup()
    await flushPromises()
    expect(w.find('h1').text()).toBe('Fichiers')
    expect(fileLibrary).toHaveBeenCalledWith({ doctype: null, q: undefined, order_by: 'creation', order: 'desc', limit: 20, offset: 0 })
    expect(rows(w)).toHaveLength(2)
    const [photo, pdf] = rows(w)
    expect(photo!.text()).toContain('photo.png')
    expect(photo!.find('[data-testid="file-doc"]').text()).toBe('Hana Diallo')
    expect(photo!.find('[data-testid="file-doc"]').attributes('href')).toBe('/app/Employee/E-1')
    expect(photo!.find('[data-testid="file-field"]').text()).toBe('Photo')
    expect(photo!.text()).toContain('2 Ko')
    expect(photo!.text()).toContain('Ada Lovelace')
    expect(pdf!.find('[data-testid="file-field"]').text()).toBe('Pièce jointe')
    expect(w.find('[data-testid="files-count"]').text()).toBe('2 fichiers')
    expect(w.find('[data-testid="files-paginator"]').exists()).toBe(false) // une seule page
  })

  it('télécharger : un lien signé ; s’il a expiré, il est redemandé avant d’être suivi', async () => {
    const exp = later(600) // une seule fois : recalculée, elle changerait de seconde entre le lien et l'attente
    const fileLinks = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, link(id, exp)])))
    const { w } = setup({ fileLinks })
    await flushPromises()
    const a = rows(w)[1]!.find('[data-testid="file-download"]')
    expect(a.attributes('href')).toBe(`/files/acme/${PDF}?exp=${exp}&sig=x`)
    expect(a.attributes('download')).toBe('avenant.pdf')

    // Le lien expire dans 10 s : trop court pour servir, le clic en redemande un avant de télécharger.
    const stale = vi.fn(async (ids: string[]) => Object.fromEntries(ids.map((id) => [id, link(id, later(10))])))
    const second = setup({ fileLinks: stale, fileLibrary: async () => result([attached]) })
    await flushPromises()
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const calls = stale.mock.calls.length
    await second.w.findAll('[data-testid="file-download"]').at(-1)!.trigger('click')
    await flushPromises()
    expect(stale.mock.calls.length).toBe(calls + 1)
    expect(stale).toHaveBeenLastCalledWith([PDF])
    expect(clicked).toHaveBeenCalledTimes(1)
  })

  it('chercher un nom : la demande part à la fin de la frappe, depuis la première page', async () => {
    const { w, fileLibrary } = setup()
    await flushPromises()
    await w.find('[data-testid="files-search"]').setValue('  avenant ')
    await new Promise((r) => setTimeout(r, 350))
    await flushPromises()
    expect(fileLibrary).toHaveBeenLastCalledWith({ doctype: null, q: 'avenant', order_by: 'creation', order: 'desc', limit: 20, offset: 0 })
  })

  it('filtrer par type : les types proposés sont ceux du serveur, avec leur nombre', async () => {
    const { w, fileLibrary } = setup()
    await flushPromises()
    const select = w.findComponent(Select)
    expect(select.props('options')).toEqual([{ value: 'Employee', label: 'Salarié (2)' }])
    select.vm.$emit('update:modelValue', 'Employee')
    await flushPromises()
    expect(fileLibrary).toHaveBeenLastCalledWith({ doctype: 'Employee', q: undefined, order_by: 'creation', order: 'desc', limit: 20, offset: 0 })
  })

  it('trier : par date, nom ou taille, dans les deux sens ; un nouveau tri repart de la première page', async () => {
    const many = Array.from({ length: 20 }, (_, i) => item({ file_id: String(i).padStart(32, '0'), filename: `f${i}.png` }))
    const fileLibrary = vi.fn(async () => result(many, { total: 45 }))
    const { w } = setup({ fileLibrary })
    await flushPromises()
    const sort = w.findAllComponents(Select).find((c) => c.attributes('data-testid') === 'files-sort')!
    expect((sort.props('options') as { label: string }[]).map((o) => o.label)).toEqual([
      'Les plus récents',
      'Les plus anciens',
      'Nom (A → Z)',
      'Nom (Z → A)',
      'Les plus gros',
      'Les plus petits',
    ])
    w.findComponent(Paginator).vm.$emit('page', { first: 20, rows: 20, page: 1, pageCount: 3 })
    await flushPromises()
    sort.vm.$emit('update:modelValue', 'size:asc')
    await flushPromises()
    expect(fileLibrary).toHaveBeenLastCalledWith({ doctype: null, q: undefined, order_by: 'size', order: 'asc', limit: 20, offset: 0 })
    sort.vm.$emit('update:modelValue', 'filename:desc')
    await flushPromises()
    expect(fileLibrary).toHaveBeenLastCalledWith({ doctype: null, q: undefined, order_by: 'filename', order: 'desc', limit: 20, offset: 0 })
  })

  it('l’état du document citant : soumis, annulé, rien pour un brouillon', async () => {
    const { w } = setup({ fileLibrary: async () => result([item(), item({ docname: 'E-2', docstatus: 1 }), item({ docname: 'E-3', docstatus: 2 })]) })
    await flushPromises()
    expect(rows(w).map((r) => r.find('[data-testid="file-doctype"]').text())).toEqual(['Salarié', 'Salarié · soumis', 'Salarié · annulé'])
  })

  it('la confirmation ne promet « historisé » que si le type garde un historique', async () => {
    const said: string[] = []
    const capture = (o: { message?: string; reject?: () => void }) => {
      said.push(o.message ?? '')
      o.reject?.()
    }
    ConfirmationEventBus.on('confirm', capture as never)
    try {
      const { w } = setup({ fileLibrary: async () => result([item(), item({ docname: 'E-2', track_changes: false })]) })
      await flushPromises()
      for (const r of rows(w)) await r.find('[data-testid="file-detach"]').trigger('click')
      await flushPromises()
    } finally {
      ConfirmationEventBus.off('confirm', capture as never)
    }
    expect(said).toHaveLength(2)
    expect(said[0]).toContain('(et historisé)')
    expect(said[1]).not.toContain('historisé')
    expect(said[1]).toContain('Le document est modifié ;')
  })

  it('une page à la fois : la page suivante demande la suite', async () => {
    const many = Array.from({ length: 20 }, (_, i) => item({ file_id: String(i).padStart(32, '0'), filename: `f${i}.png` }))
    const fileLibrary = vi.fn(async () => result(many, { total: 45 }))
    const { w } = setup({ fileLibrary })
    await flushPromises()
    expect(w.find('[data-testid="files-paginator"]').exists()).toBe(true)
    w.findComponent(Paginator).vm.$emit('page', { first: 20, rows: 20, page: 1, pageCount: 3 })
    await flushPromises()
    expect(fileLibrary).toHaveBeenLastCalledWith({ doctype: null, q: undefined, order_by: 'creation', order: 'desc', limit: 20, offset: 20 })
  })

  it('« Détacher » seulement là où le serveur le permet ; refuser la confirmation ne retire rien', async () => {
    const { w, detachFile } = setup({ fileLibrary: async () => result([item(), item({ docname: 'E-2', can_detach: false })]) })
    await flushPromises()
    expect(rows(w).map((r) => r.find('[data-testid="file-detach"]').exists())).toEqual([true, false])
    const off = answer(false)
    await rows(w)[0]!.find('[data-testid="file-detach"]').trigger('click')
    off()
    await flushPromises()
    expect(detachFile).not.toHaveBeenCalled()
  })

  it('détacher, confirmé : l’écriture ordinaire (le champ, le fichier vu, la version lue), puis la liste est relue', async () => {
    const { w, detachFile, fileLibrary } = setup()
    await flushPromises()
    const before = fileLibrary.mock.calls.length
    const off = answer(true)
    await rows(w)[0]!.find('[data-testid="file-detach"]').trigger('click')
    off()
    await flushPromises()
    expect(detachFile).toHaveBeenCalledWith({ doctype: 'Employee', name: 'E-1', field: 'photo', file_id: PHOTO, version: 3 })
    expect(fileLibrary.mock.calls.length).toBe(before + 1)
    expect(toasts.summaries()).toContain('photo.png a été détaché')

    const off2 = answer(true)
    await rows(w)[1]!.find('[data-testid="file-detach"]').trigger('click')
    off2()
    await flushPromises()
    expect(detachFile).toHaveBeenLastCalledWith({ attachment: 'c'.repeat(32) })
  })

  it('un refus du serveur est montré lisiblement : son message et le détail par champ (nommé par son libellé)', async () => {
    const detachFile = vi.fn(async () => {
      throw new ApiError(422, 'validation_failed', 'Données invalides', [{ field: 'photo', msg: 'Champ obligatoire', type: 'missing' }])
    })
    const { w, fileLibrary } = setup({ detachFile })
    await flushPromises()
    const before = fileLibrary.mock.calls.length
    const off = answer(true)
    await rows(w)[0]!.find('[data-testid="file-detach"]').trigger('click')
    off()
    await flushPromises()
    const problem = w.find('[data-testid="files-problem"]')
    expect(problem.text()).toContain('« photo.png » n’a pas été détaché')
    expect(problem.findAll('li').map((l) => l.text())).toEqual(['Données invalides', 'Photo : Champ obligatoire'])
    expect(fileLibrary.mock.calls.length).toBe(before) // rien n'a changé : la liste reste telle quelle
  })

  it('un refus de droit se dit « pas le droit », sans jargon', async () => {
    const detachFile = vi.fn(async () => {
      throw new ApiError(403, 'forbidden', 'Forbidden')
    })
    const { w } = setup({ detachFile })
    await flushPromises()
    const off = answer(true)
    await rows(w)[0]!.find('[data-testid="file-detach"]').trigger('click')
    off()
    await flushPromises()
    expect(w.find('[data-testid="files-problem"] li').text()).toBe('Vous n’avez pas le droit d’effectuer cette action')
  })

  it('un document modifié ou figé entre-temps : on le dit, et la liste est relue', async () => {
    for (const e of [new ApiError(409, 'version_conflict', 'conflit'), new ApiError(409, 'document_locked', 'Salarié E-1 est soumis : il n’est plus modifiable')]) {
      const detachFile = vi.fn(async () => {
        throw e
      })
      const { w, fileLibrary } = setup({ detachFile })
      await flushPromises()
      const before = fileLibrary.mock.calls.length
      const off = answer(true)
      await rows(w)[0]!.find('[data-testid="file-detach"]').trigger('click')
      off()
      await flushPromises()
      expect(fileLibrary.mock.calls.length).toBe(before + 1)
      const said = w.find('[data-testid="files-problem"]').text()
      expect(said).toContain(e.isConflict ? 'modifié depuis l’affichage' : 'il n’est plus modifiable')
      unmountAll()
    }
  })

  it('vide, vide après un filtre, panne, source indisponible : chacun est dit', async () => {
    const empty = setup({ fileLibrary: async () => result([], { doctypes: [] }) })
    await flushPromises()
    expect(empty.w.find('[data-testid="files-empty"]').text()).toBe('Aucun fichier dans les documents que vous pouvez lire.')
    unmountAll()

    const down = setup({
      fileLibrary: async () => {
        throw new ApiError(500, 'internal_error', 'Erreur interne')
      },
    })
    await flushPromises()
    expect(down.w.find('[data-testid="files-failed"]').text()).toContain('La liste des fichiers n’a pas pu être chargée.')
    unmountAll()

    // Un type dont la source ne répond pas est nommé par son LIBELLÉ, celui que l'utilisateur connaît, pas par son nom d'API.
    const partial = setup({ fileLibrary: async () => result([item()], { unavailable: [{ name: 'Member', label: 'Adhérent' }] }) })
    await flushPromises()
    const said = partial.w.find('[data-testid="files-unavailable"]').text()
    expect(said).toContain('Adhérent')
    expect(said).not.toContain('Member')
  })

  it('une page vidée entre-temps (sa dernière ligne détachée) : on revient à la page d’avant plutôt que d’afficher un tableau vide', async () => {
    const many = Array.from({ length: 20 }, (_, i) => item({ file_id: String(i).padStart(32, '0'), filename: `f${i}.png` }))
    let total = 21
    const fileLibrary = vi.fn(async (q: { offset?: number }) => (q.offset === 20 ? result([], { total }) : result(many, { total })))
    const { w } = setup({ fileLibrary })
    await flushPromises()
    total = 20 // la 21e a été détachée ailleurs
    fileLibrary.mockClear()
    w.findComponent(Paginator).vm.$emit('page', { first: 20, rows: 20, page: 1, pageCount: 2 })
    await flushPromises()
    expect(fileLibrary.mock.calls.map((c) => (c[0] as { offset: number }).offset)).toEqual([20, 0])
    expect(rows(w)).toHaveLength(20)
  })
})
