import { describe, expect, it } from 'vitest'
import { addDays, daysBetween, eventsByDay, firstOfMonth, lastOfMonth, monthGrid, moved, shiftMonth, span, weekDays, weekStart, type CalEvent } from '@/lib/calendar'
import { DAY_PX, barBox, ganttRange, ticks, todayLeft, type GanttItem } from '@/lib/gantt'

describe('dates du calendrier', () => {
  it('ajouter des jours franchit mois, années et années bissextiles', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
    expect(addDays('2024-02-28', 1)).toBe('2024-02-29')
    expect(addDays('2026-02-28', 1)).toBe('2026-03-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })

  it('un changement d’heure ne décale jamais un jour (le calcul est en UTC)', () => {
    expect(addDays('2026-03-28', 1)).toBe('2026-03-29') // passage à l'heure d'été en Europe
    expect(addDays('2026-03-29', 1)).toBe('2026-03-30')
    expect(addDays('2026-10-24', 2)).toBe('2026-10-26') // retour à l'heure d'hiver
    expect(daysBetween('2026-03-28', '2026-03-30')).toBe(2)
  })

  it('daysBetween, signé', () => {
    expect(daysBetween('2026-10-17', '2026-10-20')).toBe(3)
    expect(daysBetween('2026-10-20', '2026-10-17')).toBe(-3)
    expect(daysBetween('2026-10-17', '2026-10-17')).toBe(0)
  })

  it('la semaine commence le lundi', () => {
    expect(weekStart('2026-10-17')).toBe('2026-10-12') // un samedi
    expect(weekStart('2026-10-12')).toBe('2026-10-12') // un lundi
    expect(weekStart('2026-10-18')).toBe('2026-10-12') // un dimanche appartient à la semaine qui finit
    expect(weekDays('2026-10-17')).toEqual(['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18'])
  })

  it('un mois : premier et dernier jour, mois précédent et suivant', () => {
    expect(firstOfMonth('2026-10-17')).toBe('2026-10-01')
    expect(lastOfMonth('2026-10-17')).toBe('2026-10-31')
    expect(lastOfMonth('2024-02-10')).toBe('2024-02-29')
    expect(shiftMonth('2026-01-31', -1)).toBe('2025-12-01')
    expect(shiftMonth('2026-12-15', 1)).toBe('2027-01-01')
    expect(shiftMonth('2026-10-17', 0)).toBe('2026-10-01')
  })

  it('la grille d’un mois : des semaines pleines, du lundi au dimanche', () => {
    const oct = monthGrid('2026-10-17') // le 1er octobre 2026 est un jeudi, le 31 un samedi
    expect(oct[0]).toBe('2026-09-28')
    expect(oct.at(-1)).toBe('2026-11-01')
    expect(oct).toHaveLength(35)
    expect(monthGrid('2027-02-10')).toHaveLength(28) // février 2027 commence un lundi et finit un dimanche : quatre semaines exactement
    expect(monthGrid('2026-02-10')[0]).toBe('2026-01-26') // février 2026 commence un dimanche : la grille remonte au lundi précédent
    expect(monthGrid('2026-02-10')).toHaveLength(35)
    expect(monthGrid('2026-08-10')).toHaveLength(42) // août 2026 : un samedi 1er et 31 jours, six semaines
  })
})

describe('événements d’un calendrier', () => {
  it('début seul : un événement d’un jour ; une fin antérieure ou illisible ne le rend pas négatif', () => {
    expect(span('2026-10-17', null)).toEqual({ start: '2026-10-17', end: '2026-10-17' })
    expect(span('2026-10-17', '2026-10-19')).toEqual({ start: '2026-10-17', end: '2026-10-19' })
    expect(span('2026-10-17', '2026-10-10')).toEqual({ start: '2026-10-17', end: '2026-10-17' })
    expect(span('2026-10-17', 'demain')).toEqual({ start: '2026-10-17', end: '2026-10-17' })
    expect(span(null, '2026-10-19')).toBeNull()
    expect(span('17/10/2026', null)).toBeNull()
  })

  const ev = (name: string, start: string, end: string, title = name): CalEvent => ({ name, start, end, title, color: null, movable: true })

  it('chaque jour reçoit ses événements, un événement long figure sur chacun de ses jours', () => {
    const days = weekDays('2026-10-12')
    const by = eventsByDay(days, [ev('a', '2026-10-13', '2026-10-15'), ev('b', '2026-10-15', '2026-10-15'), ev('hors', '2026-11-01', '2026-11-02')])
    expect(by['2026-10-12']).toEqual([])
    expect(by['2026-10-13']!.map((e) => e.name)).toEqual(['a'])
    expect(by['2026-10-15']!.map((e) => e.name)).toEqual(['a', 'b'])
    expect(Object.values(by).flat().map((e) => e.name)).not.toContain('hors')
  })

  it('un événement qui commence avant la fenêtre apparaît sur ses premiers jours visibles', () => {
    const by = eventsByDay(weekDays('2026-10-12'), [ev('long', '2026-10-01', '2026-10-13')])
    expect(by['2026-10-12']!.map((e) => e.name)).toEqual(['long'])
    expect(by['2026-10-13']!.map((e) => e.name)).toEqual(['long'])
    expect(by['2026-10-14']).toEqual([])
  })

  it('tri stable dans un jour : par début, puis par titre', () => {
    const by = eventsByDay(['2026-10-15'], [ev('3', '2026-10-15', '2026-10-15', 'Zèbre'), ev('1', '2026-10-14', '2026-10-16', 'Beta'), ev('2', '2026-10-15', '2026-10-15', 'Alpha')])
    expect(by['2026-10-15']!.map((e) => e.title)).toEqual(['Beta', 'Alpha', 'Zèbre'])
  })

  it('déplacer un événement garde sa durée', () => {
    expect(moved({ start: '2026-10-13', end: '2026-10-15' }, '2026-10-20')).toEqual({ start: '2026-10-20', end: '2026-10-22' })
    expect(moved({ start: '2026-10-13', end: '2026-10-13' }, '2026-10-10')).toEqual({ start: '2026-10-10', end: '2026-10-10' })
    expect(moved({ start: '2026-10-30', end: '2026-11-02' }, '2026-12-30')).toEqual({ start: '2026-12-30', end: '2027-01-02' })
  })
})

describe('échelle d’un Gantt', () => {
  const item = (name: string, start: string, end: string): GanttItem => ({ name, title: name, start, end, color: null })

  it('la plage couvre tous les éléments avec une marge, jamais moins de 14 jours', () => {
    const r = ganttRange([item('a', '2026-10-10', '2026-10-12'), item('b', '2026-10-20', '2026-10-25')], '2026-10-17')
    expect(r).toEqual({ from: '2026-10-07', to: '2026-10-28', days: 22 })
    expect(ganttRange([item('a', '2026-10-10', '2026-10-10')], '2026-10-17').days).toBe(14)
  })

  it('sans élément, la plage est autour d’aujourd’hui', () => {
    const r = ganttRange([], '2026-10-17')
    expect(r.from <= '2026-10-17' && r.to >= '2026-10-17').toBe(true)
  })

  it('une barre : à la position de son début, large de ses jours (le dernier compris)', () => {
    const px = DAY_PX.day
    expect(barBox(item('a', '2026-10-10', '2026-10-12'), '2026-10-07', px)).toEqual({ left: 3 * px, width: 3 * px - 2 })
    expect(barBox(item('a', '2026-10-10', '2026-10-10'), '2026-10-10', DAY_PX.month)).toEqual({ left: 0, width: 6 }) // visible et cliquable même à l'échelle du mois
  })

  it('les graduations suivent le zoom', () => {
    const days = ticks('2026-10-26', 10, 'day')
    expect(days).toHaveLength(10)
    expect(days.find((t) => t.major)!.label).toMatch(/nov/)
    expect(days[1]!.left).toBe(DAY_PX.day)
    const weeks = ticks('2026-10-12', 21, 'week')
    expect(weeks.map((t) => t.left / DAY_PX.week)).toEqual([0, 7, 14]) // les lundis
    const months = ticks('2026-09-15', 60, 'month')
    expect(months.map((t) => t.label.replace(/\./g, ''))).toEqual(expect.arrayContaining([expect.stringMatching(/sept/), expect.stringMatching(/oct/), expect.stringMatching(/nov/)]))
  })

  it('« aujourd’hui » : au milieu de sa colonne, ou rien hors de la plage', () => {
    expect(todayLeft('2026-10-10', 20, '2026-10-12', 10)).toBe(25)
    expect(todayLeft('2026-10-10', 20, '2026-10-09', 10)).toBeNull()
    expect(todayLeft('2026-10-10', 20, '2026-10-30', 10)).toBeNull()
  })
})
