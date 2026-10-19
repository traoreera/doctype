import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import KanbanView from '@/components/views/KanbanView.vue'
import { ApiError } from '@/lib/api'
import type { DocRecord, DocTypeMeta, Filters } from '@/lib/types'
import { TASK, captureToasts, doc, flushPromises, mountWith, page } from './views-fixtures'

const columns = ['À faire', 'En cours', 'Fait']

/** Un `list` qui répond selon le filtre `state` de la requête : une colonne = une requête. */
function board(by: Record<string, DocRecord[]>, totals: Record<string, number> = {}) {
  return vi.fn(async (_doctype: string, q?: { filters?: Filters; offset?: number }) => {
    const state = (q?.filters?.state as string | undefined) ?? ''
    const items = by[state] ?? []
    return page(items.slice(q?.offset ?? 0), totals[state] ?? items.length)
  })
}

const open = async (api: Record<string, unknown>, over: { meta?: DocTypeMeta; filters?: Filters } = {}) => {
  const out = mountWith(KanbanView, { doctype: 'Task', meta: over.meta ?? TASK, filters: over.filters ?? {} }, api)
  await flushPromises()
  return out
}

const drag = async (w: ReturnType<typeof mountWith>['w'], card: string, toColumn: string) => {
  await w.find(`[data-testid="kanban-card-${card}"]`).trigger('dragstart', { dataTransfer: { setData() {}, effectAllowed: '' } })
  await w.find(`[data-testid="kanban-col-${toColumn}"]`).trigger('dragover')
  await w.find(`[data-testid="kanban-col-${toColumn}"]`).trigger('drop')
}

beforeEach(() => localStorage.clear())
afterEach(() => (document.body.innerHTML = ''))
const toasts = captureToasts()

describe('Kanban : les colonnes', () => {
  it('une colonne par valeur du Select, avec son nombre de documents et ses cartes', async () => {
    const list = board({ 'À faire': [doc('t1', { hours: 3 }), doc('t2', {})], 'En cours': [doc('t3', { state: 'En cours' })] }, { 'À faire': 2, 'En cours': 1 })
    const { w } = await open({ list })
    expect(w.findAll('.kanban-col').map((c) => c.find('.kanban-title').text())).toEqual(columns)
    expect(columns.map((c) => w.find(`[data-testid="kanban-count-${c}"]`).text())).toEqual(['2', '1', '0'])
    expect(w.findAll('[data-testid="kanban-col-À faire"] .kanban-card')).toHaveLength(2)
    expect(w.find('[data-testid="kanban-col-Fait"]').text()).toContain('Aucun document')
  })

  it('chaque colonne est UNE requête filtrée sur sa valeur, du plus récent au plus ancien', async () => {
    const list = board({})
    await open({ list })
    expect(list).toHaveBeenCalledTimes(3)
    for (const c of columns) expect(list).toHaveBeenCalledWith('Task', { filters: { state: c }, order_by: 'modified', order: 'desc', limit: 20, offset: 0 })
  })

  it('une carte : le titre qui ouvre le document, les champs choisis, rien pour un champ vide', async () => {
    const { w } = await open({ list: board({ 'À faire': [doc('t1', { hours: 3 }), doc('t2', {})] }) })
    const card = w.find('[data-testid="kanban-card-t1"]')
    expect(card.find('[data-testid="kanban-open-t1"]').attributes('href')).toBe('/app/Task/t1')
    expect([card.find('dt').text(), card.find('dd').text()]).toEqual(['Heures', '3'])
    expect(w.find('[data-testid="kanban-card-t2"] dd').exists()).toBe(false) // pas de ligne « Heures » vide
  })

  it('les filtres de l’utilisateur s’ajoutent à la requête de chaque colonne', async () => {
    const list = board({})
    await open({ list }, { filters: { hours: ['>', 2] } })
    for (const c of columns) expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ filters: { hours: ['>', 2], state: c } }))
  })

  it('un filtre de l’utilisateur sur le champ des colonnes décide quelles colonnes se remplissent, sans être écrasé', async () => {
    const list = board({ 'En cours': [doc('t3', { state: 'En cours' })], Fait: [doc('t4', { state: 'Fait' })] })
    const { w } = await open({ list }, { filters: { state: 'Fait' } })
    expect(list).toHaveBeenCalledTimes(1) // les autres colonnes ne sont même pas demandées
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ filters: { state: 'Fait' } }))
    expect(w.findAll('.kanban-card')).toHaveLength(1)
    const not = board({ 'En cours': [doc('t3', { state: 'En cours' })], 'À faire': [doc('t1', {})] })
    const second = await open({ list: not }, { filters: { state: ['!=', 'Fait'] } })
    expect(second.w.findAll('.kanban-card')).toHaveLength(2)
    expect(not).toHaveBeenCalledTimes(2) // « Fait » est écartée par le filtre
  })

  it('« Voir plus » charge la suite d’une colonne et disparaît quand tout est là', async () => {
    const many = Array.from({ length: 25 }, (_, i) => doc(`a${i}`, {}))
    const list = vi.fn(async (_d: string, q?: { filters?: Filters; offset?: number }) => {
      const state = q?.filters?.state
      if (state !== 'À faire') return page([], 0)
      const offset = q?.offset ?? 0
      return page(many.slice(offset, offset + 20), 25)
    })
    const { w } = await open({ list })
    expect(w.findAll('[data-testid="kanban-col-À faire"] .kanban-card')).toHaveLength(20)
    await w.find('[data-testid="kanban-more-À faire"]').trigger('click')
    await flushPromises()
    expect(list).toHaveBeenCalledWith('Task', expect.objectContaining({ offset: 20 }))
    expect(w.findAll('[data-testid="kanban-col-À faire"] .kanban-card')).toHaveLength(25)
    expect(w.find('[data-testid="kanban-more-À faire"]').exists()).toBe(false)
  })

  it('« + » d’une colonne crée un document déjà placé dans cette colonne ; pas de « + » sans droit de créer', async () => {
    const { w } = await open({ list: board({}) })
    expect(w.find('[data-testid="kanban-add-En cours"]').attributes('href')).toMatch(/^\/app\/Task\/new\?state=En(\+|%20)cours$/)
    const readOnly = { ...TASK, rights: { ...TASK.rights, create: 'none' as const, write: 'none' as const } }
    const second = await open({ list: board({}) }, { meta: readOnly })
    expect(second.w.find('[data-testid="kanban-add-En cours"]').exists()).toBe(false)
  })

  it('une erreur du serveur est dite sans casser le tableau', async () => {
    const list = vi.fn(async () => {
      throw new ApiError(500, 'internal_error', 'Erreur interne')
    })
    const { w } = await open({ list })
    expect(w.find('[data-testid="kanban-error"]').exists()).toBe(true)
    expect(w.findAll('.kanban-col')).toHaveLength(3)
  })
})

describe('Kanban : déplacer une carte', () => {
  it('glisser une carte vers une autre colonne change la valeur du champ, par une modification ordinaire (avec la version lue)', async () => {
    const t1 = doc('t1', { hours: 3 }, { version: 4 })
    const update = vi.fn(async (_d: string, name: string, version: number, data: Record<string, unknown>) => doc(name, data, { version: version + 1 }))
    const { w } = await open({ list: board({ 'À faire': [t1] }), update })
    await drag(w, 't1', 'En cours')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('Task', 't1', 4, { title: 't1', state: 'En cours', hours: 3 })
    expect(w.find('[data-testid="kanban-col-En cours"] [data-testid="kanban-card-t1"]').exists()).toBe(true)
    expect(w.find('[data-testid="kanban-col-À faire"] [data-testid="kanban-card-t1"]').exists()).toBe(false)
    expect(['À faire', 'En cours'].map((c) => w.find(`[data-testid="kanban-count-${c}"]`).text())).toEqual(['0', '1'])
  })

  it('la carte change de colonne AVANT la réponse du serveur', async () => {
    let release!: (d: DocRecord) => void
    const update = vi.fn(() => new Promise<DocRecord>((r) => (release = r)))
    const { w } = await open({ list: board({ 'À faire': [doc('t1', {})] }), update })
    await drag(w, 't1', 'Fait')
    expect(w.find('[data-testid="kanban-col-Fait"] [data-testid="kanban-card-t1"]').exists()).toBe(true) // déjà là, sans attendre
    release(doc('t1', { state: 'Fait' }, { version: 2 }))
    await flushPromises()
    expect(w.find('[data-testid="kanban-col-Fait"] [data-testid="kanban-card-t1"]').exists()).toBe(true)
  })

  it('si le serveur refuse, la carte revient là où elle était, avec ses compteurs, et le motif est dit', async () => {
    const update = vi.fn(async () => {
      throw new ApiError(422, 'hook_rejected', 'Passage interdit')
    })
    const { w } = await open({ list: board({ 'À faire': [doc('t1', {}), doc('t2', {})] }), update })
    await drag(w, 't1', 'Fait')
    await flushPromises()
    expect(w.find('[data-testid="kanban-col-À faire"] [data-testid="kanban-card-t1"]').exists()).toBe(true)
    expect(w.find('[data-testid="kanban-col-Fait"] .kanban-card').exists()).toBe(false)
    expect(['À faire', 'Fait'].map((c) => w.find(`[data-testid="kanban-count-${c}"]`).text())).toEqual(['2', '0'])
    expect(toasts.summaries()).toContain('Passage interdit')
  })

  it('un conflit de version recharge les colonnes : le document a changé ailleurs', async () => {
    const list = board({ 'À faire': [doc('t1', {})] })
    const update = vi.fn(async () => {
      throw new ApiError(409, 'version_conflict', 'modifié')
    })
    const { w } = await open({ list, update })
    expect(list).toHaveBeenCalledTimes(3)
    await drag(w, 't1', 'En cours')
    await flushPromises()
    expect(list.mock.calls.length).toBeGreaterThanOrEqual(5) // la colonne d'origine et la colonne cible relues
    expect(toasts.summaries().join()).toContain('modifié ailleurs')
  })

  it('déposer sur sa propre colonne ne fait rien', async () => {
    const update = vi.fn()
    const { w } = await open({ list: board({ 'À faire': [doc('t1', {})] }), update })
    await drag(w, 't1', 'À faire')
    expect(update).not.toHaveBeenCalled()
  })

  it('un dépôt sans carte glissée (un fichier, un texte) ne fait rien', async () => {
    const update = vi.fn()
    const { w } = await open({ list: board({}), update })
    await w.find('[data-testid="kanban-col-Fait"]').trigger('drop')
    expect(update).not.toHaveBeenCalled()
  })

  it('sans droit d’écriture, ou pour un document soumis, la carte ne se glisse pas', async () => {
    const submitted = doc('s1', {}, { docstatus: 1 })
    const draft = doc('d1', {})
    const submittable = { ...TASK, submittable: true }
    const { w } = await open({ list: board({ 'À faire': [submitted, draft] }) }, { meta: submittable })
    expect(w.find('[data-testid="kanban-card-s1"]').attributes('draggable')).toBe('false')
    expect(w.find('[data-testid="kanban-card-d1"]').attributes('draggable')).toBe('true')
    expect(w.find('[data-testid="kanban-menu-s1"]').exists()).toBe(false)
    expect(w.find('[data-testid="kanban-card-s1"] .status-pill').text()).toBe('Soumis')
    const update = vi.fn()
    const frozen = await open({ list: board({ 'À faire': [submitted] }), update }, { meta: submittable })
    await drag(frozen.w, 's1', 'Fait')
    expect(update).not.toHaveBeenCalled()
    const ro = await open({ list: board({ 'À faire': [draft] }) }, { meta: { ...TASK, rights: { ...TASK.rights, write: 'none' as const } } })
    expect(ro.w.find('[data-testid="kanban-card-d1"]').attributes('draggable')).toBe('false')
  })

  it('le menu « Déplacer vers » offre les autres colonnes (clavier et toucher)', async () => {
    const update = vi.fn(async (_d: string, name: string, v: number, data: Record<string, unknown>) => doc(name, data, { version: v + 1 }))
    const { w } = await open({ list: board({ 'À faire': [doc('t1', {})] }), update })
    await w.find('[data-testid="kanban-menu-t1"]').trigger('click')
    await flushPromises()
    const items = [...document.body.querySelectorAll('.p-menu-item-label')].map((x) => x.textContent)
    expect(items).toEqual(['En cours', 'Fait']) // pas la colonne où elle est déjà
    ;(document.body.querySelector('.p-menu-item-link') as HTMLElement).click()
    await flushPromises()
    expect(update).toHaveBeenCalledWith('Task', 't1', 1, expect.objectContaining({ state: 'En cours' }))
  })
})

describe('Kanban : recharger', () => {
  it('de nouveaux filtres relisent les colonnes ; une réponse périmée ne remplace pas la dernière', async () => {
    const resolvers: ((r: ReturnType<typeof page>) => void)[] = []
    const list = vi.fn((_d: string, q?: { filters?: Filters }) => {
      if (q?.filters?.state !== 'À faire') return Promise.resolve(page([], 0))
      return new Promise<ReturnType<typeof page>>((r) => resolvers.push(r))
    })
    const { w } = await open({ list })
    await w.setProps({ filters: { hours: ['>', 1] } })
    await flushPromises()
    resolvers[1]!(page([doc('récent', {})]))
    await flushPromises()
    resolvers[0]!(page([doc('périmé', {})]))
    await flushPromises()
    expect(w.find('[data-testid="kanban-card-récent"]').exists()).toBe(true)
    expect(w.find('[data-testid="kanban-card-périmé"]').exists()).toBe(false)
  })
})
