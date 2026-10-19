/**
 * L'échelle de temps d'un Gantt : pures fonctions (jours -> pixels), testées sans interface. Les dates sont des `AAAA-MM-JJ`.
 */
import { addDays, daysBetween, firstOfMonth, parseIso, shiftMonth, weekStart } from './calendar'

export type Zoom = 'day' | 'week' | 'month'
/** Largeur d'un jour, en pixels, selon le zoom. */
export const DAY_PX: Record<Zoom, number> = { day: 34, week: 12, month: 4 }
export const ZOOMS: Zoom[] = ['day', 'week', 'month']

export interface GanttItem {
  name: string
  title: string
  start: string
  end: string
  color: number | null
}

/** La plage affichée : du plus ancien début à la plus tardive fin, avec une marge, jamais plus courte que `min` jours. */
export function ganttRange(items: readonly GanttItem[], today: string, pad = 3, min = 14): { from: string; to: string; days: number } {
  let from = items.length ? items.reduce((a, i) => (i.start < a ? i.start : a), items[0]!.start) : today
  let to = items.length ? items.reduce((a, i) => (i.end > a ? i.end : a), items[0]!.end) : today
  from = addDays(from, -pad)
  to = addDays(to, pad)
  if (daysBetween(from, to) + 1 < min) to = addDays(from, min - 1)
  return { from, to, days: daysBetween(from, to) + 1 }
}

/** La barre d'un élément : sa position et sa largeur (le dernier jour compris, au moins 6 px pour rester visible et cliquable). */
export function barBox(item: Pick<GanttItem, 'start' | 'end'>, from: string, px: number): { left: number; width: number } {
  return { left: daysBetween(from, item.start) * px, width: Math.max(6, (daysBetween(item.start, item.end) + 1) * px - 2) }
}

export interface Tick {
  label: string
  left: number
  /** Un repère de mois ou d'année plutôt que de jour. */
  major: boolean
}

const monthName = new Intl.DateTimeFormat('fr-FR', { month: 'short', year: 'numeric', timeZone: 'UTC' })
const dayNum = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', timeZone: 'UTC' })

/** Les graduations : chaque jour (zoom jour), chaque lundi (semaine), chaque début de mois (mois). */
export function ticks(from: string, days: number, zoom: Zoom): Tick[] {
  const px = DAY_PX[zoom]
  const out: Tick[] = []
  for (let i = 0; i < days; i++) {
    const d = addDays(from, i)
    const isMonth = d.endsWith('-01')
    if (zoom === 'day') out.push({ label: isMonth ? monthName.format(parseIso(d)) : dayNum.format(parseIso(d)), left: i * px, major: isMonth })
    else if (zoom === 'week' && (weekStart(d) === d || i === 0)) out.push({ label: dayNum.format(parseIso(d)) + (isMonth ? ` ${monthName.format(parseIso(d))}` : ''), left: i * px, major: isMonth })
    else if (zoom === 'month' && (isMonth || i === 0)) out.push({ label: monthName.format(parseIso(d)), left: i * px, major: true })
  }
  return out
}

/** La ligne « aujourd'hui », ou `null` si le jour est hors de la plage. */
export function todayLeft(from: string, days: number, today: string, px: number): number | null {
  const i = daysBetween(from, today)
  return i >= 0 && i < days ? i * px + px / 2 : null
}

export { firstOfMonth, shiftMonth }
