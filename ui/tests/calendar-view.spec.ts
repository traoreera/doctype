import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CalendarView from '@/components/views/CalendarView.vue'
import { ApiError } from '@/lib/api'
import type { DocRecord, DocTypeMeta, Filters } from '@/lib/types'
import { TASK, captureToasts, doc, flushPromises, mountWith, page } from './views-fixtures'

// « Aujourd'hui » est le samedi 17 octobre 2026 : octobre 2026 s'affiche du lundi 28 septembre au dimanche 1er novembre (35 jours).
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-17T10:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  document.body.innerHTML = ''
})
const toasts = captureToasts()

/** Une liste qui répond par `start` (la fenêtre) ou par `stop` (les événements en cours à son début), comme le serveur. */
function agenda(inWindow: DocRecord[], running: DocRecord[] = []) {
  return vi.fn(async (_d: string, q?: { filters?: Filters }) => {
    const f = q?.filters ?? {}
    const items = 'stop' in f ? running : inWindow
    return page(items)
  })
}

const open = async (api: Record<string, unknown>, over: { meta?: DocTypeMeta; filters?: Filters } = {}) => {
  const out = mountWith(CalendarView, { doctype: 'Task', meta: over.meta ?? TASK, filters: over.filters ?? {} }, api)
  await flushPromises()
  return out
}

const dayIds = (w: Awaited<ReturnType<typeof open>>['w']) => w.findAll('[data-testid^="cal-day-"]').map((d) => d.attributes('data-testid')!.slice(8))

describe('calendrier : la grille', () => {
  it('le mois courant, de semaines pleines du lundi au dimanche, avec le titre et les jours de la semaine', async () => {
    const { w } = await open({ list: agenda([]) })
    expect(w.find('[data-testid="cal-title"]').text().toLowerCase()).toBe('octobre 2026')
    const days = dayIds(w)
    expect(days).toHaveLength(35)
    expect(days[0]).toBe('2026-09-28')
    expect(days.at(-1)).toBe('2026-11-01')
    expect(w.findAll('.calendar-dow').map((x) => x.text())).toEqual(['lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.', 'dim.'])
  })

  it('aujourd’hui est marqué, les jours des mois voisins sont estompés', async () => {
    const { w } = await open({ list: agenda([]) })
    expect(w.find('[data-testid="cal-day-2026-10-17"]').classes()).toContain('today')
    expect(w.findAll('.calendar-day.today')).toHaveLength(1)
    expect(w.find('[data-testid="cal-day-2026-09-30"]').classes()).toContain('muted')
    expect(w.find('[data-testid="cal-day-2026-10-01"]').classes()).not.toContain('muted')
  })

  it('suivant, précédent et aujourd’hui', async () => {
    const { w } = await open({ list: agenda([]) })
    await w.find('[data-testid="cal-next"]').trigger('click')
    expect(w.find('[data-testid="cal-title"]').text().toLowerCase()).toBe('novembre 2026')
    await w.find('[data-testid="cal-next"]').trigger('click')
    await w.find('[data-testid="cal-next"]').trigger('click')
    expect(w.find('[data-testid="cal-title"]').text().toLowerCase()).toBe('janvier 2027')
    await w.find('[data-testid="cal-prev"]').trigger('click')
    expect(w.find('[data-testid="cal-title"]').text().toLowerCase()).toBe('décembre 2026')
    await w.find('[data-testid="cal-today"]').trigger('click')
    expect(w.find('[data-testid="cal-title"]').text().toLowerCase()).toBe('octobre 2026')
  })

  it('la vue semaine : sept jours, du lundi au dimanche, et suivant avance d’une semaine', async () => {
    const { w } = await open({ list: agenda([]) })
    await w.find('[data-testid="cal-mode-week"]').trigger('click')
    expect(dayIds(w)).toEqual(['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18'])
    await w.find('[data-testid="cal-next"]').trigger('click')
    expect(dayIds(w)[0]).toBe('2026-10-19')
    expect(w.find('[data-testid="cal-mode-week"]').attributes('aria-checked')).toBe('true')
  })
})

describe('calendrier : les documents', () => {
  it('une requête pour la fenêtre (les dates affichées), une pour les événements commencés avant et encore en cours', async () => {
    const list = agenda([])
    await open({ list })
    const calls = list.mock.calls.map((c) => c[1]!.filters!)
    expect(calls).toHaveLength(2)
    const window = calls.find((f) => !('stop' in f))!
    expect((window.start as [string, string[]])[0]).toBe('in')
    expect((window.start as [string, string[]])[1]).toHaveLength(35)
    expect(calls.find((f) => 'stop' in f)).toEqual({ start: ['<', '2026-09-28'], stop: ['>=', '2026-09-28'] })
  })

  it('sans champ de fin, une seule requête', async () => {
    const list = agenda([])
    const meta = { ...TASK, views: { ...TASK.views!, calendar: { start: 'start', title: 'title' } } }
    await open({ list }, { meta })
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('un document est sur son jour, un événement de plusieurs jours sur chacun, avec son titre qui ouvre le document', async () => {
    const list = agenda([doc('t1', { title: 'Livraison', start: '2026-10-20', stop: '2026-10-22' }), doc('t2', { title: 'Réunion', start: '2026-10-20' })])
    const { w } = await open({ list })
    expect(w.findAll('[data-testid^="cal-event-t1-"]').map((e) => e.attributes('data-testid'))).toEqual(['cal-event-t1-2026-10-20', 'cal-event-t1-2026-10-21', 'cal-event-t1-2026-10-22'])
    expect(w.find('[data-testid="cal-event-t2-2026-10-20"]').text()).toBe('Réunion')
    expect(w.find('[data-testid="cal-event-t2-2026-10-20"]').attributes('href')).toBe('/app/Task/t2')
    expect(w.findAll('[data-testid^="cal-event-t2-"]')).toHaveLength(1) // sans fin : un jour
  })

  it('un événement commencé le mois dernier et encore en cours apparaît sur les premiers jours', async () => {
    const running = doc('long', { title: 'Chantier', start: '2026-09-20', stop: '2026-09-29' })
    const { w } = await open({ list: agenda([], [running]) })
    expect(w.find('[data-testid="cal-event-long-2026-09-28"]').exists()).toBe(true)
    expect(w.find('[data-testid="cal-event-long-2026-09-29"]').exists()).toBe(true)
    expect(w.find('[data-testid="cal-event-long-2026-09-30"]').exists()).toBe(false)
  })

  it('un même document rendu par les deux requêtes n’apparaît qu’une fois', async () => {
    const both = doc('t1', { start: '2026-09-28', stop: '2026-09-29' })
    const { w } = await open({ list: agenda([both], [both]) })
    expect(w.findAll('[data-testid="cal-event-t1-2026-09-28"]')).toHaveLength(1)
  })

  it('la teinte suit la VALEUR du champ de couleur, pas le rang du document', async () => {
    const list = agenda([doc('a', { start: '2026-10-20', prio: 'Haute' }), doc('b', { start: '2026-10-21', prio: 'Basse' }), doc('c', { start: '2026-10-22' })])
    const { w } = await open({ list })
    expect(w.find('[data-testid="cal-event-a-2026-10-20"]').classes()).toContain('c-1') // « Haute » est la 2e valeur
    expect(w.find('[data-testid="cal-event-b-2026-10-21"]').classes()).toContain('c-0')
    expect(w.find('[data-testid="cal-event-c-2026-10-22"]').classes()).toContain('c-none')
  })

  it('un document sans date valide est ignoré, une fin antérieure au début donne un événement d’un jour', async () => {
    const list = agenda([doc('ok', { start: '2026-10-20', stop: '2026-10-10' }), doc('sans', {}), doc('mal', { start: 'demain' })])
    const { w } = await open({ list })
    expect(w.findAll('[data-testid^="cal-event-"]').map((e) => e.attributes('data-testid'))).toEqual(['cal-event-ok-2026-10-20'])
  })

  it('au-delà de trois par jour : « +n autres », qui ouvre la semaine de ce jour', async () => {
    const five = Array.from({ length: 5 }, (_, i) => doc(`e${i}`, { start: '2026-10-20', title: `Ev ${i}` }))
    const { w } = await open({ list: agenda(five) })
    expect(w.findAll('[data-testid^="cal-event-"]')).toHaveLength(3)
    expect(w.find('[data-testid="cal-more-2026-10-20"]').text()).toBe('+2 autres')
    await w.find('[data-testid="cal-more-2026-10-20"]').trigger('click')
    expect(w.findAll('.calendar-day')).toHaveLength(7)
    expect(w.findAll('[data-testid^="cal-event-"]')).toHaveLength(5) // la semaine les montre tous
  })

  it('le filtre de l’utilisateur sur le champ de date s’applique aux lignes reçues, la fenêtre restant dans la requête', async () => {
    const list = vi.fn(async (_d: string, q?: { filters?: Filters }) => {
      const f = q?.filters ?? {}
      return page('stop' in f ? [] : [doc('tot', { start: '2026-10-05' }), doc('tard', { start: '2026-10-25' })])
    })
    const { w } = await open({ list }, { filters: { start: ['>=', '2026-10-20'] } })
    expect((list.mock.calls[0]![1]!.filters!.start as unknown[])[0]).toBe('in') // la fenêtre l'emporte dans la requête…
    expect(w.find('[data-testid="cal-event-tard-2026-10-25"]').exists()).toBe(true)
    expect(w.find('[data-testid="cal-event-tot-2026-10-05"]').exists()).toBe(false) // …le filtre de l'utilisateur écarte le reste
  })

  it('les autres filtres de l’utilisateur partent au serveur', async () => {
    const list = agenda([])
    await open({ list }, { filters: { state: 'Fait' } })
    for (const c of list.mock.calls) expect(c[1]!.filters).toMatchObject({ state: 'Fait' })
  })

  it('plus de documents que la limite : le dit', async () => {
    const list = vi.fn(async () => page([doc('a', { start: '2026-10-20' })], 350))
    const { w } = await open({ list })
    expect(w.find('[data-testid="cal-truncated"]').text()).toContain('Affichage limité à 1 document :')
  })

  it('une erreur du serveur est dite, la grille reste', async () => {
    const list = vi.fn(async () => {
      throw new ApiError(500, 'internal_error', 'Erreur interne')
    })
    const { w } = await open({ list })
    expect(w.find('[data-testid="cal-error"]').exists()).toBe(true)
    expect(w.findAll('.calendar-day')).toHaveLength(35)
  })
})

describe('calendrier : créer et déplacer', () => {
  it('le « + » d’un jour crée un document à cette date ; pas de « + » sans droit de créer', async () => {
    const { w } = await open({ list: agenda([]) })
    expect(w.find('[data-testid="cal-add-2026-10-20"]').attributes('href')).toBe('/app/Task/new?start=2026-10-20')
    const ro = await open({ list: agenda([]) }, { meta: { ...TASK, rights: { ...TASK.rights, create: 'none' as const } } })
    expect(ro.w.find('[data-testid="cal-add-2026-10-20"]').exists()).toBe(false)
  })

  const dragTo = async (w: Awaited<ReturnType<typeof open>>['w'], event: string, day: string) => {
    await w.find(`[data-testid="${event}"]`).trigger('dragstart', { dataTransfer: { setData() {} } })
    await w.find(`[data-testid="cal-day-${day}"]`).trigger('dragover')
    await w.find(`[data-testid="cal-day-${day}"]`).trigger('drop')
  }

  it('glisser un événement sur un autre jour déplace sa date en gardant sa durée (modification ordinaire, avec la version)', async () => {
    const update = vi.fn(async (_d: string, n: string, v: number, data: Record<string, unknown>) => doc(n, data, { version: v + 1 }))
    const list = agenda([doc('t1', { title: 'Livraison', start: '2026-10-20', stop: '2026-10-22' }, { version: 3 })])
    const { w } = await open({ list, update })
    await dragTo(w, 'cal-event-t1-2026-10-20', '2026-10-27')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('Task', 't1', 3, { title: 'Livraison', state: 'À faire', start: '2026-10-27', stop: '2026-10-29' })
    expect(w.find('[data-testid="cal-event-t1-2026-10-27"]').exists()).toBe(true)
    expect(w.find('[data-testid="cal-event-t1-2026-10-20"]').exists()).toBe(false)
    expect(toasts.summaries().join()).toContain('27/10/2026')
  })

  it('un événement sans date de fin ne reçoit pas de fin inventée', async () => {
    const update = vi.fn(async (_d: string, n: string, v: number, data: Record<string, unknown>) => doc(n, data, { version: v + 1 }))
    const { w } = await open({ list: agenda([doc('t1', { start: '2026-10-20' })]), update })
    await dragTo(w, 'cal-event-t1-2026-10-20', '2026-10-21')
    await flushPromises()
    expect(update.mock.calls[0]![3]).toEqual({ title: 't1', state: 'À faire', start: '2026-10-21' })
  })

  it('le déplacement s’affiche tout de suite, et se défait si le serveur refuse (le motif est dit)', async () => {
    const update = vi.fn(async () => {
      throw new ApiError(422, 'hook_rejected', 'Date impossible')
    })
    const { w } = await open({ list: agenda([doc('t1', { start: '2026-10-20' })]), update })
    await dragTo(w, 'cal-event-t1-2026-10-20', '2026-10-21')
    await flushPromises()
    expect(w.find('[data-testid="cal-event-t1-2026-10-20"]').exists()).toBe(true) // revenu
    expect(w.find('[data-testid="cal-event-t1-2026-10-21"]').exists()).toBe(false)
    expect(toasts.summaries()).toContain('Date impossible')
  })

  it('un document soumis, ou sans droit d’écriture, ne se déplace pas', async () => {
    const update = vi.fn()
    const submittable = { ...TASK, submittable: true }
    const { w } = await open({ list: agenda([doc('s1', { start: '2026-10-20' }, { docstatus: 1 }), doc('d1', { start: '2026-10-21' })]), update }, { meta: submittable })
    expect(w.find('[data-testid="cal-event-s1-2026-10-20"]').attributes('draggable')).toBe('false')
    expect(w.find('[data-testid="cal-event-d1-2026-10-21"]').attributes('draggable')).toBe('true')
    await dragTo(w, 'cal-event-s1-2026-10-20', '2026-10-27')
    expect(update).not.toHaveBeenCalled()
    const ro = await open({ list: agenda([doc('d1', { start: '2026-10-21' })]) }, { meta: { ...TASK, rights: { ...TASK.rights, write: 'none' as const } } })
    expect(ro.w.find('[data-testid="cal-event-d1-2026-10-21"]').attributes('draggable')).toBe('false')
  })

  it('une date de début, ou de fin quand le document en a une, en lecture seule : le document ne se déplace pas', async () => {
    const lock = (name: string): DocTypeMeta => ({ ...TASK, fields: TASK.fields.map((f) => (f.fieldname === name ? { ...f, read_only: true } : f)) })
    const update = vi.fn()
    const start = await open({ list: agenda([doc('t1', { start: '2026-10-20' })]), update }, { meta: lock('start') })
    expect(start.w.find('[data-testid="cal-event-t1-2026-10-20"]').attributes('draggable')).toBe('false')
    await dragTo(start.w, 'cal-event-t1-2026-10-20', '2026-10-27')
    expect(update).not.toHaveBeenCalled() // le serveur l'aurait refusé (422 read_only)
    // La fin n'est déplacée que si le document en a une : sans fin, une fin verrouillée ne gêne pas.
    const stop = await open({ list: agenda([doc('a1', { start: '2026-10-20', stop: '2026-10-21' }), doc('b1', { start: '2026-10-22' })]) }, { meta: lock('stop') })
    expect(stop.w.find('[data-testid="cal-event-a1-2026-10-20"]').attributes('draggable')).toBe('false')
    expect(stop.w.find('[data-testid="cal-event-b1-2026-10-22"]').attributes('draggable')).toBe('true')
  })

  it('déposer sur le même jour ne fait rien', async () => {
    const update = vi.fn()
    const { w } = await open({ list: agenda([doc('t1', { start: '2026-10-20' })]), update })
    await dragTo(w, 'cal-event-t1-2026-10-20', '2026-10-20')
    expect(update).not.toHaveBeenCalled()
  })
})
