import { ConfirmationEventBus } from 'primevue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ActivityPanel from '@/components/collab/ActivityPanel.vue'
import AssignmentsCard from '@/components/collab/AssignmentsCard.vue'
import AttachmentsCard from '@/components/collab/AttachmentsCard.vue'
import CollabPanel from '@/components/collab/CollabPanel.vue'
import AutoComplete from 'primevue/autocomplete'
import { buildFeed, isOverdue, sortAssignments } from '@/lib/feed'
import { fallbackName, useUsers } from '@/stores/users'
import type { AssignmentItem, AttachmentItem, Collab, CommentItem, VersionEntry } from '@/lib/types'
import TasksView from '@/views/TasksView.vue'
import { TASK, flushPromises, mountWith } from './views-fixtures'

afterEach(() => {
  document.body.innerHTML = ''
})

const comment = (id: string, owner: string, at: string, content = `c-${id}`): CommentItem => ({ id, doctype: 'Task', docname: 'T-1', owner, content, edited: false, creation: at, modified: at })
const task = (id: string, over: Partial<AssignmentItem> = {}): AssignmentItem => ({ id, doctype: 'Task', docname: 'T-1', assignee: 'rita', assigned_by: 'ada', description: null, due_date: null, status: 'open', creation: '2026-10-01T08:00:00Z', modified: '2026-10-01T08:00:00Z', ...over })
const file = (id: string, over: Partial<AttachmentItem> = {}): AttachmentItem => ({ id, file_id: id.padEnd(32, '0'), owner: 'ada', creation: '2026-10-01T09:00:00Z', filename: 'devis.pdf', content_type: 'application/pdf', size: 2048, ...over })
const version = (id: number, at: string, action: VersionEntry['action'] = 'update'): VersionEntry => ({ id, version: id, action, user: 'ada', owner: 'ada', creation: at, changes: action === 'update' ? { title: { old: 'a', new: 'b' } } : {} })
const collab = (over: Partial<Collab> = {}): Collab => ({ comments: [], assignments: [], attachments: [], can_assign: false, can_attach: false, me: 'rita', ...over })

describe('fil d’activité (lib)', () => {
  const all = {
    comments: [comment('1', 'rita', '2026-10-02T10:00:00Z')],
    assignments: [task('a', { creation: '2026-10-01T08:00:00Z', status: 'done', modified: '2026-10-03T08:00:00Z' })],
    attachments: [file('f', { creation: '2026-10-01T09:00:00Z' })],
    versions: [version(2, '2026-10-01T07:00:00+00:00'), version(1, '2026-10-01T06:00:00+00:00', 'create')],
  }

  it('du plus récent au plus ancien, tous genres mêlés ; une assignation close donne deux événements', () => {
    expect(buildFeed(all).map((i) => i.key)).toEqual(['z:a', 'c:1', 'f:f', 'a:a', 'v:2', 'v:1'])
  })

  it('compare des INSTANTS : `Z` et `+00:00` se mêlent sans erreur d’ordre', () => {
    const keys = buildFeed({ ...all, comments: [comment('9', 'x', '2026-10-01T07:30:00Z')], assignments: [], attachments: [] }).map((i) => i.key)
    expect(keys).toEqual(['c:9', 'v:2', 'v:1'])
  })

  it('les filtres : commentaires seuls, modifications seules', () => {
    expect(buildFeed(all, 'comments').map((i) => i.kind)).toEqual(['comment'])
    expect(new Set(buildFeed(all, 'changes').map((i) => i.kind))).toEqual(new Set(['version']))
  })

  it('une assignation ouverte n’a pas d’événement de clôture', () => {
    expect(buildFeed({ ...all, comments: [], attachments: [], versions: [], assignments: [task('o')] }).map((i) => i.kind)).toEqual(['assigned'])
  })

  it('isOverdue : ouverte et échue seulement ; sortAssignments : ouvertes d’abord, échéance la plus proche en tête', () => {
    expect(isOverdue({ status: 'open', due_date: '2026-10-01' }, '2026-10-02')).toBe(true)
    expect(isOverdue({ status: 'open', due_date: '2026-10-02' }, '2026-10-02')).toBe(false) // le jour même n'est pas en retard
    expect(isOverdue({ status: 'done', due_date: '2026-01-01' }, '2026-10-02')).toBe(false)
    expect(isOverdue({ status: 'open', due_date: null }, '2026-10-02')).toBe(false)
    const sorted = sortAssignments([task('x', { status: 'done' }), task('y', { due_date: '2026-12-01' }), task('z', { due_date: '2026-11-01' }), task('w')])
    expect(sorted.map((a) => a.id)).toEqual(['z', 'y', 'w', 'x'])
  })
})

describe('commentaires', () => {
  const setup = (c: Collab, api: Record<string, unknown> = {}) => {
    const changed = vi.fn()
    const { w } = mountWith(ActivityPanel, { meta: TASK, doctype: 'Task', name: 'T-1', version: 1, collab: c, onChanged: changed }, { history: async () => ({ items: [], total: 0, limit: 20, offset: 0 }), ...api })
    return { w, changed }
  }

  it('on commente en lisant le document ; le commentaire part trimé, le champ se vide, et l’on prévient le parent', async () => {
    const addComment = vi.fn(async () => comment('n', 'rita', '2026-10-05T10:00:00Z'))
    const { w, changed } = setup(collab(), { addComment })
    await flushPromises()
    const send = w.find('[data-testid="comment-send"]')
    expect(send.attributes('disabled')).toBeDefined() // rien d'écrit : rien à envoyer
    await w.find('[data-testid="comment-input"]').setValue('  Bonjour  ')
    await send.trigger('click')
    await flushPromises()
    expect(addComment).toHaveBeenCalledWith('Task', 'T-1', 'Bonjour')
    expect((w.find('[data-testid="comment-input"]').element as HTMLTextAreaElement).value).toBe('')
    expect(changed).toHaveBeenCalledTimes(1)
  })

  it('un blanc n’est pas un commentaire', async () => {
    const addComment = vi.fn()
    const { w } = setup(collab(), { addComment })
    await flushPromises()
    await w.find('[data-testid="comment-input"]').setValue('   ')
    await w.find('[data-testid="comment-send"]').trigger('click')
    expect(addComment).not.toHaveBeenCalled()
  })

  it('un commentaire s’affiche en TEXTE (aucun HTML interprété), sauts de ligne gardés', async () => {
    const { w } = setup(collab({ comments: [comment('1', 'ada', '2026-10-02T10:00:00Z', '<img src=x onerror=alert(1)>\nligne 2')] }))
    await flushPromises()
    expect(w.find('[data-testid="comment-text"] img').exists()).toBe(false)
    expect(w.find('[data-testid="comment-text"]').text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('on modifie le sien, pas celui d’un autre ; qui écrit le document peut retirer celui d’un autre', async () => {
    const c = collab({ comments: [comment('1', 'rita', '2026-10-02T10:00:00Z'), comment('2', 'ada', '2026-10-02T11:00:00Z')] })
    const reader = setup(c)
    await flushPromises()
    const items = reader.w.findAll('[data-kind="comment"]')
    const byOwner = (who: string) => items.find((i) => i.text().includes(who))!
    expect(byOwner('rita').find('[data-testid="comment-edit"]').exists()).toBe(true)
    expect(byOwner('rita').find('[data-testid="comment-delete"]').exists()).toBe(true)
    expect(byOwner('ada').find('[data-testid="comment-edit"]').exists()).toBe(false)
    expect(byOwner('ada').find('[data-testid="comment-delete"]').exists()).toBe(false) // un lecteur ne modère pas
    const writer = setup({ ...c, can_assign: true })
    await flushPromises()
    const ada = writer.w.findAll('[data-kind="comment"]').find((i) => i.text().includes('ada'))!
    expect(ada.find('[data-testid="comment-edit"]').exists()).toBe(false) // modérer = retirer, pas réécrire
    expect(ada.find('[data-testid="comment-delete"]').exists()).toBe(true)
  })

  it('modifier : le texte part au serveur, trimé ; vide, il n’y va pas', async () => {
    const editComment = vi.fn(async () => comment('1', 'rita', '2026-10-02T10:00:00Z'))
    const { w, changed } = setup(collab({ comments: [comment('1', 'rita', '2026-10-02T10:00:00Z', 'avant')] }), { editComment })
    await flushPromises()
    await w.find('[data-testid="comment-edit"]').trigger('click')
    const box = w.find('[data-testid="comment-edit-input"]')
    expect((box.element as HTMLTextAreaElement).value).toBe('avant')
    await box.setValue('   ')
    expect(w.find('[data-testid="comment-edit-save"]').attributes('disabled')).toBeDefined()
    await box.setValue(' après ')
    await w.find('[data-testid="comment-edit-save"]').trigger('click')
    await flushPromises()
    expect(editComment).toHaveBeenCalledWith('1', 'après')
    expect(changed).toHaveBeenCalled()
  })

  it('retirer demande confirmation', async () => {
    const deleteComment = vi.fn(async () => undefined)
    const { w } = setup(collab({ comments: [comment('1', 'rita', '2026-10-02T10:00:00Z')] }), { deleteComment })
    await flushPromises()
    const no = (o: { reject?: () => void }) => o.reject?.()
    ConfirmationEventBus.on('confirm', no as never)
    await w.find('[data-testid="comment-delete"]').trigger('click')
    ConfirmationEventBus.off('confirm', no as never)
    expect(deleteComment).not.toHaveBeenCalled()
    const yes = (o: { accept?: () => void }) => o.accept?.()
    ConfirmationEventBus.on('confirm', yes as never)
    await w.find('[data-testid="comment-delete"]').trigger('click')
    await flushPromises()
    ConfirmationEventBus.off('confirm', yes as never)
    expect(deleteComment).toHaveBeenCalledWith('1')
  })

  it('les filtres du fil : « Commentaires » masque les modifications', async () => {
    const { w } = setup(collab({ comments: [comment('1', 'ada', '2026-10-02T10:00:00Z')] }), { history: async () => ({ items: [version(2, '2026-10-01T07:00:00Z')], total: 1, limit: 20, offset: 0 }) })
    await flushPromises()
    expect(w.findAll('[data-kind]').map((i) => i.attributes('data-kind'))).toEqual(['comment', 'version'])
    await w.find('[data-testid="feed-filter-comments"]').trigger('click')
    expect(w.findAll('[data-kind]').map((i) => i.attributes('data-kind'))).toEqual(['comment'])
  })

  it('les filtres du fil : navigation au clavier (flèches) et tabindex', async () => {
    const { w } = setup(collab())
    await flushPromises()
    const allBtn = w.find('[data-testid="feed-filter-all"]')
    const commentsBtn = w.find('[data-testid="feed-filter-comments"]')
    const changesBtn = w.find('[data-testid="feed-filter-changes"]')

    expect(allBtn.attributes('aria-checked')).toBe('true')
    expect(allBtn.attributes('tabindex')).toBe('0')
    expect(commentsBtn.attributes('aria-checked')).toBe('false')
    expect(commentsBtn.attributes('tabindex')).toBe('-1')

    await allBtn.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(allBtn.attributes('aria-checked')).toBe('false')
    expect(allBtn.attributes('tabindex')).toBe('-1')
    expect(commentsBtn.attributes('aria-checked')).toBe('true')
    expect(commentsBtn.attributes('tabindex')).toBe('0')

    await commentsBtn.trigger('keydown', { key: 'ArrowLeft' })
    await flushPromises()
    expect(allBtn.attributes('aria-checked')).toBe('true')

    await allBtn.trigger('keydown', { key: 'ArrowUp' })
    await flushPromises()
    expect(changesBtn.attributes('aria-checked')).toBe('true')
  })
})

describe('assignations', () => {
  const setup = (c: Collab, api: Record<string, unknown> = {}) => {
    const changed = vi.fn()
    const { w } = mountWith(AssignmentsCard, { collab: c, doctype: 'Task', name: 'T-1', timezone: 'UTC', onChanged: changed }, { people: async () => [{ id: 'u-ada', email: 'ada@acme.test', name: 'ada@acme.test' }], ...api })
    return { w, changed }
  }

  it('« Assigner » n’est offert qu’à qui écrit le document', async () => {
    expect(setup(collab({ can_assign: false })).w.find('[data-testid="assign-open"]').exists()).toBe(false)
    expect(setup(collab({ can_assign: true })).w.find('[data-testid="assign-open"]').exists()).toBe(true)
  })

  it('l’assigné termine SA tâche sans pouvoir gérer les autres', async () => {
    const updateAssignment = vi.fn(async () => task('a'))
    const { w, changed } = setup(collab({ assignments: [task('a', { assignee: 'rita' }), task('b', { assignee: 'bob' })] }), { updateAssignment })
    const mine = w.find('[data-testid="assignment-rita"]')
    expect(mine.find('[data-testid="assignment-done"]').exists()).toBe(true)
    expect(mine.find('[data-testid="assignment-cancel"]').exists()).toBe(false) // annuler : réservé à qui écrit
    expect(w.find('[data-testid="assignment-bob"] [data-testid="assignment-done"]').exists()).toBe(false)
    await mine.find('[data-testid="assignment-done"]').trigger('click')
    await flushPromises()
    expect(updateAssignment).toHaveBeenCalledWith('a', { status: 'done' })
    expect(changed).toHaveBeenCalled()
  })

  it('qui écrit gère toutes les tâches, et rouvre une tâche close', async () => {
    const { w } = setup(collab({ can_assign: true, assignments: [task('b', { assignee: 'bob' }), task('c', { assignee: 'cy', status: 'done' })] }))
    expect(w.find('[data-testid="assignment-bob"] [data-testid="assignment-done"]').exists()).toBe(true)
    expect(w.find('[data-testid="assignment-bob"] [data-testid="assignment-cancel"]').exists()).toBe(true)
    expect(w.find('[data-testid="assignment-cy"] [data-testid="assignment-reopen"]').exists()).toBe(true)
    expect(w.find('[data-testid="assignment-cy"] [data-testid="assignment-done"]').exists()).toBe(false)
  })

  it('une échéance dépassée est signalée, pas celle du jour ni d’une tâche terminée', async () => {
    const { w } = setup(collab({ assignments: [task('a', { assignee: 'x', due_date: '2020-01-01' }), task('b', { assignee: 'y', due_date: '2020-01-01', status: 'done' })] }))
    expect(w.find('[data-testid="assignment-x"] [data-testid="assignment-due"]').text()).toContain('en retard')
    expect(w.find('[data-testid="assignment-y"] [data-testid="assignment-due"]').text()).not.toContain('en retard')
  })

  it('le formulaire envoie qui, quoi, pour quand ; un refus du serveur s’affiche dans la fenêtre', async () => {
    const assign = vi.fn(async () => task('n'))
    const { w, changed } = setup(collab({ can_assign: true }), { assign })
    await w.find('[data-testid="assign-open"]').trigger('click')
    await flushPromises()
    const dialog = () => document.querySelector('[data-testid="assign-dialog"]')!
    const who = dialog().querySelector<HTMLInputElement>('[data-testid="assign-who"] input')!
    who.value = ' carol '
    who.dispatchEvent(new Event('input'))
    const note = dialog().querySelector<HTMLInputElement>('[data-testid="assign-description"]')!
    note.value = 'À relire'
    note.dispatchEvent(new Event('input'))
    await flushPromises()
    dialog().querySelector<HTMLElement>('[data-testid="assign-save"]')!.click()
    await flushPromises()
    expect(assign).toHaveBeenCalledWith('Task', 'T-1', { assignee: 'carol', description: 'À relire', due_date: null })
    expect(changed).toHaveBeenCalled()
  })

  it('sans nom, rien ne part', async () => {
    const assign = vi.fn()
    const { w } = setup(collab({ can_assign: true }), { assign })
    await w.find('[data-testid="assign-open"]').trigger('click')
    await flushPromises()
    document.querySelector<HTMLElement>('[data-testid="assign-save"]')!.click()
    await flushPromises()
    expect(assign).not.toHaveBeenCalled()
    expect(document.querySelector('[data-testid="assign-problem"]')).not.toBeNull()
  })

  it('un refus du serveur (déjà assigné) reste affiché, la fenêtre reste ouverte', async () => {
    const { ApiError } = await import('@/lib/api')
    const assign = vi.fn(async () => {
      throw new ApiError(409, 'already_assigned', 'carol a déjà une assignation ouverte sur ce document')
    })
    const { w, changed } = setup(collab({ can_assign: true }), { assign })
    await w.find('[data-testid="assign-open"]').trigger('click')
    await flushPromises()
    const who = document.querySelector<HTMLInputElement>('[data-testid="assign-who"] input')!
    who.value = 'carol'
    who.dispatchEvent(new Event('input'))
    await flushPromises()
    document.querySelector<HTMLElement>('[data-testid="assign-save"]')!.click()
    await flushPromises()
    expect(document.querySelector('[data-testid="assign-problem"]')?.textContent).toContain('déjà')
    expect(changed).not.toHaveBeenCalled()
  })
})

describe('pièces jointes', () => {
  const setup = (c: Collab, api: Record<string, unknown> = {}) => {
    const changed = vi.fn()
    const { w } = mountWith(AttachmentsCard, { collab: c, doctype: 'Task', name: 'T-1', onChanged: changed }, { fileLinks: async (ids: string[]) => Object.fromEntries(ids.map((i) => [i, { path: `files/acme/${i}?exp=1&sig=2`, filename: 'x', content_type: 'x', size: 1, expires: 9e9 }])), ...api })
    return { w, changed }
  }

  it('la liste : nom (lien de téléchargement signé), taille, auteur ; « Joindre » seulement à qui écrit', async () => {
    const a = setup(collab({ attachments: [file('f1')], can_attach: false }))
    await flushPromises()
    const link = a.w.find('[data-testid="attachment-link"]')
    expect(link.text()).toBe('devis.pdf')
    expect(link.attributes('href')).toContain('files/acme/')
    expect(link.attributes('download')).toBeDefined()
    expect(link.attributes('rel')).toContain('noopener')
    expect(a.w.text()).toContain('2 Ko')
    expect(a.w.find('[data-testid="attach-open"]').exists()).toBe(false)
    const writer = setup(collab({ can_attach: true }))
    await flushPromises() // sans cela le second montage finit de s'afficher APRÈS le test, quand la page est déjà vidée
    expect(writer.w.find('[data-testid="attach-open"]').exists()).toBe(true)
  })

  it('une vraie image s’affiche en vignette, un autre fichier par une icône', async () => {
    const { w } = setup(collab({ attachments: [file('i1', { content_type: 'image/png', filename: 'a.png' }), file('p1')] }))
    await flushPromises()
    const rows = w.findAll('[data-testid="attachment"]')
    expect(rows[0]!.find('img, [data-testid="thumb-empty"]').exists()).toBe(true)
    expect(rows[1]!.find('.pi-file').exists()).toBe(true)
  })

  it('on envoie chaque fichier choisi ; un fichier trop gros n’est même pas envoyé', async () => {
    const attach = vi.fn(async () => file('n'))
    const { w, changed } = setup(collab({ can_attach: true }), { attach })
    const input = w.find('[data-testid="attach-input"]')
    const small = new File(['x'], 'a.txt')
    const big = new File([new Uint8Array(11 * 1024 * 1024)], 'big.bin')
    Object.defineProperty(input.element, 'files', { value: [small, big], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(attach).toHaveBeenCalledTimes(1)
    expect(attach).toHaveBeenCalledWith('Task', 'T-1', small)
    expect(changed).toHaveBeenCalled()
  })

  it('retirer : l’auteur de la jointe, ou qui écrit le document', async () => {
    const detach = vi.fn(async () => undefined)
    const other = setup(collab({ attachments: [file('f1', { owner: 'ada' })], me: 'rita' }), { detach })
    await flushPromises()
    expect(other.w.find('[data-testid="attachment-remove"]').exists()).toBe(false)
    const mine = setup(collab({ attachments: [file('f1', { owner: 'rita' })], me: 'rita' }), { detach })
    await flushPromises()
    await mine.w.find('[data-testid="attachment-remove"]').trigger('click')
    await flushPromises()
    expect(detach).toHaveBeenCalledWith('f1')
    expect(setup(collab({ attachments: [file('f2', { owner: 'ada' })], can_attach: true })).w.find('[data-testid="attachment-remove"]').exists()).toBe(true)
  })
})

describe('panneau d’un document', () => {
  it('lit tout autour du document, et un commentaire envoyé relit le tout et la pastille de « Mes tâches »', async () => {
    const data = collab({ comments: [comment('1', 'ada', '2026-10-02T10:00:00Z')], assignments: [task('a')], attachments: [file('f1')] })
    const collabApi = vi.fn(async () => data)
    const myAssignments = vi.fn(async () => [task('a')])
    const addComment = vi.fn(async () => comment('2', 'rita', '2026-10-03T10:00:00Z'))
    const { w } = mountWith(CollabPanel, { meta: TASK, doctype: 'Task', name: 'T-1', version: 1 }, { collab: collabApi, myAssignments, addComment, history: async () => ({ items: [], total: 0, limit: 20, offset: 0 }), fileLinks: async () => ({}) })
    await flushPromises()
    expect(collabApi).toHaveBeenCalledWith('Task', 'T-1')
    expect(w.find('[data-testid="assignments"]').exists() && w.find('[data-testid="attachments"]').exists() && w.find('[data-testid="activity"]').exists()).toBe(true)
    await w.find('[data-testid="comment-input"]').setValue('salut')
    await w.find('[data-testid="comment-send"]').trigger('click')
    await flushPromises()
    expect(collabApi).toHaveBeenCalledTimes(2) // relu
    expect(myAssignments).toHaveBeenCalled() // la pastille aussi
  })

  it('une panne de lecture est dite, sans casser la fiche', async () => {
    const { w } = mountWith(CollabPanel, { meta: TASK, doctype: 'Task', name: 'T-1', version: 1 }, { collab: async () => { throw new Error('hors ligne') } })
    await flushPromises()
    expect(w.find('[data-testid="collab-failed"]').exists()).toBe(true)
  })

  it('changer de document relit, et une réponse tardive de l’ancien n’écrase pas le nouveau', async () => {
    let release!: (c: Collab) => void
    const collabApi = vi.fn((_d: string, name: string) => (name === 'T-1' ? new Promise<Collab>((r) => (release = r)) : Promise.resolve(collab({ comments: [comment('9', 'zed', '2026-10-02T10:00:00Z', 'du second')] }))))
    const { w } = mountWith(CollabPanel, { meta: TASK, doctype: 'Task', name: 'T-1', version: 1 }, { collab: collabApi, history: async () => ({ items: [], total: 0, limit: 20, offset: 0 }), fileLinks: async () => ({}) })
    await w.setProps({ name: 'T-2' })
    await flushPromises()
    release(collab({ comments: [comment('1', 'ada', '2026-10-02T10:00:00Z', 'du premier')] }))
    await flushPromises()
    expect(w.text()).toContain('du second')
    expect(w.text()).not.toContain('du premier')
  })
})

describe('Mes tâches', () => {
  it('liste ce qui m’est confié avec le titre du document, l’échéance, et termine une tâche', async () => {
    const mine = [task('a', { description: 'À relire', due_date: '2020-01-01', doctype_label: 'Tâche' })]
    const myAssignments = vi.fn(async (status: string) => (status === 'open' ? mine : []))
    const updateAssignment = vi.fn(async () => task('a'))
    const { w } = mountWith(TasksView, {}, { myAssignments, updateAssignment, list: async () => ({ items: [], total: 0, limit: 1, offset: 0 }), search: async () => [] })
    await flushPromises()
    const row = w.find('[data-testid="task"]')
    expect(row.find('[data-testid="task-link"]').attributes('href')).toBe('/app/Task/T-1')
    expect(row.text()).toContain('À relire')
    expect(row.text()).toContain('confié par ada')
    expect(row.find('[data-testid="task-due"]').text()).toContain('en retard')
    await row.find('[data-testid="task-toggle"]').trigger('click')
    await flushPromises()
    expect(updateAssignment).toHaveBeenCalledWith('a', { status: 'done' })
    expect(myAssignments).toHaveBeenCalledWith('open')
  })

  it('« Terminées » les relit avec le bon statut et propose de rouvrir ; rien à faire est dit', async () => {
    const myAssignments = vi.fn(async (status: string) => (status === 'done' ? [task('d', { status: 'done' })] : []))
    const updateAssignment = vi.fn(async () => task('d'))
    const { w } = mountWith(TasksView, {}, { myAssignments, updateAssignment })
    await flushPromises()
    expect(w.find('[data-testid="tasks-empty"]').exists()).toBe(true)
    await w.find('[data-testid="tasks-done"]').trigger('click')
    await flushPromises()
    expect(myAssignments).toHaveBeenLastCalledWith('done')
    await w.find('[data-testid="task-toggle"]').trigger('click')
    await flushPromises()
    expect(updateAssignment).toHaveBeenCalledWith('d', { status: 'open' })
  })

  it('une panne est dite', async () => {
    const { w } = mountWith(TasksView, {}, { myAssignments: async () => { throw new Error('hors ligne') } })
    await flushPromises()
    expect(w.find('[data-testid="tasks-failed"]').exists()).toBe(true)
  })
})


const UUID = '1f3a9c2e-7b44-4d0a-9c11-2d5e8a6b0f77'
const GHOST = 'deadbeef-0000-0000-0000-000000000000'

describe('des personnes, pas des identifiants', () => {
  it('un UUID est abrégé tant qu’on ne connaît pas la personne ; un identifiant lisible reste tel quel', () => {
    expect(fallbackName(UUID)).toBe('1f3a9c2e…')
    expect(fallbackName('alice')).toBe('alice')
    expect(fallbackName('alice.martin@x.fr')).toBe('alice.martin@x.fr')
  })

  it('les noms sont demandés en UN lot, une seule fois, et affichés dès qu’ils arrivent', async () => {
    const users = vi.fn(async (ids: readonly string[]) => Object.fromEntries(ids.filter((i) => i === UUID).map((i) => [i, { id: i, email: 'rita@acme.test', name: 'rita@acme.test' }])))
    const { w } = mountWith({ template: '<p>{{ s.label(a) }}|{{ s.label(a) }}|{{ s.label(b) }}</p>', setup: () => ({ s: useUsers(), a: UUID, b: GHOST }) }, {}, { users })
    expect(w.text()).toContain('1f3a9c2e…') // avant la réponse
    await flushPromises()
    expect(w.text()).toBe('rita@acme.test|rita@acme.test|deadbeef…') // une personne qui n'est plus membre reste abrégée
    expect(users).toHaveBeenCalledTimes(1)
    expect(users.mock.calls[0]![0]).toEqual([UUID, GHOST])
    await w.setProps({}) // un nouvel affichage ne redemande rien
    expect(users).toHaveBeenCalledTimes(1)
  })

  it('une panne est réessayée à un prochain affichage, sans casser l’écran', async () => {
    let fail = true
    const users = vi.fn(async (ids: readonly string[]) => {
      if (fail) throw new Error('hors ligne')
      return Object.fromEntries(ids.map((i) => [i, { id: i, email: 'x@y.test', name: 'x@y.test' }]))
    })
    const { w } = mountWith({ template: '<p>{{ s.label(a) }}</p>', setup: () => ({ s: useUsers(), a: UUID }) }, {}, { users })
    await flushPromises()
    expect(w.text()).toBe('1f3a9c2e…')
    fail = false
    useUsers().want([UUID])
    await flushPromises()
    expect(w.text()).toBe('x@y.test')
  })

  it('l’activité montre l’e-mail de l’auteur d’un commentaire, d’une modification et d’une assignation', async () => {
    const users = async (ids: readonly string[]) => Object.fromEntries(ids.map((i) => [i, { id: i, email: `${i.slice(0, 4)}@acme.test`, name: `${i.slice(0, 4)}@acme.test` }]))
    const ada = 'aaaa1111-0000-0000-0000-000000000000'
    const rita = 'bbbb2222-0000-0000-0000-000000000000'
    const c = collab({ me: rita, comments: [comment('1', ada, '2026-10-02T10:00:00Z')], assignments: [task('a', { assignee: rita, assigned_by: ada })] })
    const { w } = mountWith(ActivityPanel, { meta: TASK, doctype: 'Task', name: 'T-1', version: 1, collab: c }, { users, history: async () => ({ items: [{ ...version(2, '2026-10-01T07:00:00Z'), user: ada }], total: 1, limit: 20, offset: 0 }) })
    await flushPromises()
    const text = w.text()
    expect(text).toContain('aaaa@acme.test')
    expect(text).toContain('aaaa@acme.test a assigné bbbb@acme.test')
    expect(text).not.toContain('aaaa1111-0000') // l'identifiant ne s'affiche plus
    expect(w.findAll('.avatar').map((a) => a.text())).toContain('AA')
  })

  it('on choisit une personne de la liste : c’est son IDENTIFIANT qui part, pas son e-mail', async () => {
    const assign = vi.fn(async () => task('n'))
    const people = vi.fn(async () => [{ id: UUID, email: 'rita@acme.test', name: 'rita@acme.test' }])
    const changed = vi.fn()
    const { w } = mountWith(AssignmentsCard, { collab: collab({ can_assign: true }), doctype: 'Task', name: 'T-1', timezone: 'UTC', onChanged: changed }, { assign, people, users: async () => ({}) })
    await w.find('[data-testid="assign-open"]').trigger('click')
    await flushPromises()
    const box = w.findAllComponents(AutoComplete)[0]!
    box.vm.$emit('complete', { query: 'rit' })
    await flushPromises()
    expect(people).toHaveBeenCalledWith('Task', 'rit')
    box.vm.$emit('update:modelValue', { id: UUID, email: 'rita@acme.test', name: 'rita@acme.test' })
    await flushPromises()
    document.querySelector<HTMLElement>('[data-testid="assign-save"]')!.click()
    await flushPromises()
    expect(assign).toHaveBeenCalledWith('Task', 'T-1', { assignee: UUID, description: null, due_date: null })
    expect(changed).toHaveBeenCalled()
  })

  it('un refus « n’est pas membre » s’affiche dans la fenêtre', async () => {
    const { ApiError } = await import('@/lib/api')
    const assign = vi.fn(async () => {
      throw new ApiError(422, 'unknown_user', "'zed' n'est pas membre de cette organisation")
    })
    const { w } = mountWith(AssignmentsCard, { collab: collab({ can_assign: true }), doctype: 'Task', name: 'T-1', timezone: 'UTC' }, { assign, people: async () => [], users: async () => ({}) })
    await w.find('[data-testid="assign-open"]').trigger('click')
    await flushPromises()
    w.findAllComponents(AutoComplete)[0]!.vm.$emit('update:modelValue', 'zed')
    await flushPromises()
    document.querySelector<HTMLElement>('[data-testid="assign-save"]')!.click()
    await flushPromises()
    expect(document.querySelector('[data-testid="assign-problem"]')?.textContent).toContain('pas membre')
  })
})
