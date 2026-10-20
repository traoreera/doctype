import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ActivityPanel from '@/components/collab/ActivityPanel.vue'
import { activeTrigger, matchCommands, replaceTrigger, splitMentions, type SlashCommand } from '@/lib/mentions'
import type { AssignmentItem, Collab, CommentItem } from '@/lib/types'
import { TASK, flushPromises, mountWith } from './views-fixtures'

afterEach(() => {
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('mentions et commandes (lib/mentions)', () => {
  it('une mention se montre à part ; le reste, une adresse ordinaire comprise, reste du texte', () => {
    expect(splitMentions('voir avec @ada@acme.fr, merci')).toEqual([
      { text: 'voir avec ', mention: false },
      { text: '@ada@acme.fr', mention: true },
      { text: ', merci', mention: false },
    ])
    expect(splitMentions('écrire à ada@acme.fr')).toEqual([{ text: 'écrire à ada@acme.fr', mention: false }]) // pas de @ devant : pas une mention
    expect(splitMentions('x@ada@acme.fr')).toEqual([{ text: 'x@ada@acme.fr', mention: false }]) // collée à un mot : pas une mention (comme le serveur)
    expect(splitMentions('')).toEqual([])
    expect(splitMentions('<b>@a@b.co</b>').map((s) => s.text).join('')).toBe('<b>@a@b.co</b>') // jamais du HTML : chaque morceau est un texte
  })

  it('reconnaît ce qu’on tape au curseur : une mention, une commande, ou rien', () => {
    expect(activeTrigger('@ad', 3)).toEqual({ kind: '@', start: 0, query: 'ad' })
    expect(activeTrigger('voir @', 6)).toEqual({ kind: '@', start: 5, query: '' })
    expect(activeTrigger('(@ri', 4)).toEqual({ kind: '@', start: 1, query: 'ri' })
    expect(activeTrigger('/ass', 4)).toEqual({ kind: '/', start: 0, query: 'ass' })
    expect(activeTrigger('un mot /term', 12)).toEqual({ kind: '/', start: 7, query: 'term' })
    expect(activeTrigger('ligne\n/jo', 9)).toEqual({ kind: '/', start: 6, query: 'jo' })
    expect(activeTrigger('/terminé', 8)?.query).toBe('terminé') // un accent est une lettre
    // Rien à proposer : une adresse déjà tapée, un chemin, un curseur ailleurs, le mot fini.
    expect(activeTrigger('ada@ac', 6)).toBeNull()
    expect(activeTrigger('a/b', 3)).toBeNull()
    expect(activeTrigger('@ada@acme.fr', 12)).toBeNull()
    expect(activeTrigger('/assigner ensuite', 17)).toBeNull()
    expect(activeTrigger('bonjour', 7)).toBeNull()
    expect(activeTrigger('@ad plus loin', 3)).toEqual({ kind: '@', start: 0, query: 'ad' }) // le curseur est juste après « ad »
  })

  it('remplace ce qui a été tapé, du signe au curseur, et place le curseur après', () => {
    const tr = activeTrigger('voir @ri demain', 8)!
    expect(replaceTrigger('voir @ri demain', 8, tr, '@rita@acme.fr ')).toEqual({ text: 'voir @rita@acme.fr  demain', caret: 19 })
    const cmd = activeTrigger('/ass', 4)!
    expect(replaceTrigger('/ass', 4, cmd, '')).toEqual({ text: '', caret: 0 })
  })

  const COMMANDS: SlashCommand[] = [
    { id: 'assign', word: 'assigner', label: 'Assigner', hint: '', icon: '' },
    { id: 'attach', word: 'joindre', label: 'Joindre un fichier', hint: '', icon: '' },
    { id: 'done', word: 'terminer', label: 'Terminer ma tâche', hint: '', icon: '' },
  ]
  it('les commandes dont le mot commence par ce qui est tapé (sans accent ni casse), puis celles qui le contiennent', () => {
    expect(matchCommands(COMMANDS, '').map((c) => c.id)).toEqual(['assign', 'attach', 'done'])
    expect(matchCommands(COMMANDS, 'AS').map((c) => c.id)).toEqual(['assign'])
    expect(matchCommands(COMMANDS, 'ter').map((c) => c.id)).toEqual(['done'])
    expect(matchCommands(COMMANDS, 'fich').map((c) => c.id)).toEqual(['attach']) // le libellé compte aussi
    expect(matchCommands(COMMANDS, 'zzz')).toEqual([])
  })
})

// ── Dans la zone de commentaire ──────────────────────────────────────────────────────────────

const comment = (content: string): CommentItem => ({ id: 'c1', doctype: 'Task', docname: 'T-1', owner: 'ada', content, edited: false, creation: '2026-10-02T10:00:00Z', modified: '2026-10-02T10:00:00Z' })
const mine = (over: Partial<AssignmentItem> = {}): AssignmentItem => ({
  id: 'a1', doctype: 'Task', docname: 'T-1', assignee: 'rita', assigned_by: 'ada', description: null, due_date: null, status: 'open',
  creation: '2026-10-01T08:00:00Z', modified: '2026-10-01T08:00:00Z', ...over,
})
const collab = (over: Partial<Collab> = {}): Collab => ({ comments: [], assignments: [], attachments: [], can_assign: false, can_attach: false, me: 'rita', ...over })
const PEOPLE = [
  { id: 'u-ada', email: 'ada@acme.fr', name: 'ada@acme.fr' },
  { id: 'u-carl', email: 'carl@acme.fr', name: 'carl@acme.fr' },
]

function setup(c: Collab, api: Record<string, unknown> = {}) {
  const changed = vi.fn()
  const mentionable = vi.fn(async (_d: string, _n: string, q: string) => ({ items: PEOPLE.filter((p) => p.email.includes(q)), directory: true }))
  const { w } = mountWith(ActivityPanel, { meta: TASK, doctype: 'Task', name: 'T-1', version: 1, collab: c, onChanged: changed }, {
    history: async () => ({ items: [], total: 0, limit: 20, offset: 0 }), mentionable, ...api,
  })
  return { w, changed, mentionable }
}

const input = (w: ReturnType<typeof setup>['w']) => w.find('[data-testid="comment-input"]')
/** Tape du texte comme un utilisateur : la valeur, le curseur à la fin, puis l'évènement de saisie. */
async function type(w: ReturnType<typeof setup>['w'], text: string): Promise<void> {
  const el = input(w).element as HTMLTextAreaElement
  el.value = text
  el.setSelectionRange(text.length, text.length)
  await input(w).trigger('input')
}
const suggestions = () => [...document.body.querySelectorAll('[data-testid="suggestions"] li')].map((li) => [...li.children].map((c) => c.textContent?.trim()).filter(Boolean).join(' '))
const key = (w: ReturnType<typeof setup>['w'], k: string, mods: KeyboardEventInit = {}) => input(w).trigger('keydown', { key: k, ...mods })

describe('@ : citer un membre', () => {
  beforeEach(() => vi.useFakeTimers())

  it('propose les membres qui correspondent (une lecture après la frappe), et Entrée insère l’adresse sans envoyer', async () => {
    const addComment = vi.fn()
    const { w, mentionable } = setup(collab(), { addComment })
    await vi.advanceTimersByTimeAsync(0)
    await type(w, 'voir avec @ca')
    expect(mentionable).not.toHaveBeenCalled() // on attend la fin de la frappe
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(mentionable).toHaveBeenCalledTimes(1)
    expect(mentionable).toHaveBeenCalledWith('Task', 'T-1', 'ca')
    expect(suggestions()).toEqual(['carl@acme.fr'])
    await key(w, 'Enter')
    await flushPromises()
    expect((input(w).element as HTMLTextAreaElement).value).toBe('voir avec @carl@acme.fr ')
    expect(suggestions()).toEqual([]) // le menu se ferme
    expect(addComment).not.toHaveBeenCalled() // Entrée a servi à choisir, pas à envoyer
  })

  it('les flèches choisissent, Tab valide, un clic aussi ; Échap ferme sans rien changer', async () => {
    const { w } = setup(collab())
    await vi.advanceTimersByTimeAsync(0)
    await type(w, '@')
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(suggestions()).toEqual(['ada@acme.fr', 'carl@acme.fr'])
    await key(w, 'ArrowDown')
    expect(document.body.querySelector('[data-testid="suggest-u-carl"]')?.getAttribute('aria-selected')).toBe('true')
    await key(w, 'ArrowDown') // reboucle
    expect(document.body.querySelector('[data-testid="suggest-u-ada"]')?.getAttribute('aria-selected')).toBe('true')
    await key(w, 'Escape')
    expect(suggestions()).toEqual([])
    expect((input(w).element as HTMLTextAreaElement).value).toBe('@')
    await type(w, '@c') // le même @ dont on a refusé l'aide : elle ne revient pas à chaque lettre
    await vi.advanceTimersByTimeAsync(200)
    expect(suggestions()).toEqual([])
    await type(w, 'ok @ca') // une autre mention, ailleurs : le menu se rouvre
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    await key(w, 'Tab')
    await flushPromises()
    expect((input(w).element as HTMLTextAreaElement).value).toBe('ok @carl@acme.fr ')
  })

  it('une mention complète ou un texte ordinaire ne rouvre rien ; pas d’annuaire ou une panne : on peut écrire', async () => {
    const mentionable = vi.fn(async () => ({ items: [], directory: false }))
    const { w } = setup(collab(), { mentionable })
    await vi.advanceTimersByTimeAsync(0)
    await type(w, 'bonjour à tous')
    await type(w, '@ada@acme.fr')
    await vi.advanceTimersByTimeAsync(200)
    expect(mentionable).not.toHaveBeenCalled()
    await type(w, '@a')
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(suggestions()).toEqual([]) // sans annuaire : rien à proposer
    const broken = setup(collab(), { mentionable: async () => { throw new Error('hors ligne') } })
    await vi.advanceTimersByTimeAsync(0)
    await type(broken.w, '@a')
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(suggestions()).toEqual([])
  })

  it('Ctrl+Entrée envoie même menu ouvert ; la mention est dans le texte envoyé', async () => {
    const addComment = vi.fn(async () => comment('x'))
    const { w } = setup(collab(), { addComment })
    await vi.advanceTimersByTimeAsync(0)
    await type(w, 'merci @ada@acme.fr')
    await key(w, 'Enter', { ctrlKey: true })
    await flushPromises()
    expect(addComment).toHaveBeenCalledWith('Task', 'T-1', 'merci @ada@acme.fr')
  })

  it('une mention d’un commentaire s’affiche en évidence, le reste en texte', async () => {
    const { w } = setup(collab({ comments: [comment('<i>voir</i> @ada@acme.fr svp')] }))
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const text = w.find('[data-testid="comment-text"]')
    expect(text.findAll('.mention').map((m) => m.text())).toEqual(['@ada@acme.fr'])
    expect(text.find('i').exists()).toBe(false)
    expect(text.text()).toBe('<i>voir</i> @ada@acme.fr svp')
  })
})

describe('/ : les commandes sur le document', () => {
  beforeEach(() => vi.useFakeTimers())

  it('ne propose que ce que les droits permettent : assigner et joindre à qui écrit, terminer à qui a une tâche ouverte, citer à tous', async () => {
    const reader = setup(collab())
    await vi.advanceTimersByTimeAsync(0)
    await type(reader.w, '/')
    expect(suggestions()).toEqual(['/mention Le prévenir par une notification'])
    document.body.innerHTML = ''
    const writer = setup(collab({ can_assign: true, can_attach: true, assignments: [mine()] }))
    await vi.advanceTimersByTimeAsync(0)
    await type(writer.w, '/')
    expect(suggestions().map((s) => s?.split(' ')[0])).toEqual(['/assigner', '/joindre', '/mention', '/terminer'])
    await type(writer.w, '/TERM') // sans casse
    expect(suggestions().map((s) => s?.split(' ')[0])).toEqual(['/terminer'])
    await type(writer.w, '/zzz')
    expect(suggestions()).toEqual([])
    await type(writer.w, 'un/chemin') // pas en début de mot : pas une commande
    expect(suggestions()).toEqual([])
  })

  it('/assigner disparaît du texte et ouvre la boîte d’assignation', async () => {
    const { w, changed } = setup(collab({ can_assign: true }), { people: async () => [] })
    await vi.advanceTimersByTimeAsync(0)
    await type(w, 'à faire /ass')
    await key(w, 'Enter')
    await flushPromises()
    expect((input(w).element as HTMLTextAreaElement).value).toBe('à faire ') // la commande n'est jamais envoyée
    expect(document.body.querySelector('[data-testid="assign-dialog"]')).not.toBeNull()
    expect(changed).not.toHaveBeenCalled()
  })

  it('/terminer clôt MES assignations ouvertes sur ce document, pas celles des autres', async () => {
    const updateAssignment = vi.fn(async () => mine({ status: 'done' }))
    const { w, changed } = setup(collab({ assignments: [mine(), mine({ id: 'a2', assignee: 'ada' }), mine({ id: 'a3', status: 'done' })] }), { updateAssignment })
    await vi.advanceTimersByTimeAsync(0)
    await type(w, '/term')
    await key(w, 'Enter')
    await flushPromises()
    expect(updateAssignment).toHaveBeenCalledTimes(1)
    expect(updateAssignment).toHaveBeenCalledWith('a1', { status: 'done' })
    expect(changed).toHaveBeenCalled()
    expect((input(w).element as HTMLTextAreaElement).value).toBe('')
  })

  it('/joindre ouvre le choix de fichier ; les fichiers choisis sont envoyés et trop gros refusés', async () => {
    const attach = vi.fn(async () => ({}))
    const { w, changed } = setup(collab({ can_attach: true }), { attach })
    await vi.advanceTimersByTimeAsync(0)
    const picker = w.find('[data-testid="composer-attach-input"]')
    const click = vi.spyOn(picker.element as HTMLInputElement, 'click')
    await type(w, '/joi')
    await key(w, 'Enter')
    await flushPromises()
    expect(click).toHaveBeenCalledTimes(1)
    Object.defineProperty(picker.element, 'files', { value: [new File(['x'], 'a.txt'), new File([new Uint8Array(11 * 1024 * 1024)], 'gros.bin')], configurable: true })
    await picker.trigger('change')
    await flushPromises()
    expect(attach).toHaveBeenCalledTimes(1) // le trop gros n'est même pas envoyé
    expect(changed).toHaveBeenCalled()
  })

  it('/mention laisse un @ et ouvre aussitôt la liste des membres', async () => {
    const { w, mentionable } = setup(collab())
    await vi.advanceTimersByTimeAsync(0)
    await type(w, '/men')
    await key(w, 'Enter')
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect((input(w).element as HTMLTextAreaElement).value).toBe('@')
    expect(mentionable).toHaveBeenCalledWith('Task', 'T-1', '')
    expect(suggestions()).toEqual(['ada@acme.fr', 'carl@acme.fr'])
  })

  it('en modifiant un commentaire on peut citer, mais il n’y a pas de commandes', async () => {
    const { w } = setup(collab({ comments: [{ ...comment('bonjour'), owner: 'rita' }], can_assign: true }))
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    await w.find('[data-testid="comment-edit"]').trigger('click')
    const edit = w.find('[data-testid="comment-edit-input"]')
    const el = edit.element as HTMLTextAreaElement
    el.value = '/'
    el.setSelectionRange(1, 1)
    await edit.trigger('input')
    expect(suggestions()).toEqual([])
    el.value = '@'
    el.setSelectionRange(1, 1)
    await edit.trigger('input')
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(suggestions()).toEqual(['ada@acme.fr', 'carl@acme.fr'])
  })
})
