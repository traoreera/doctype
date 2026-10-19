/**
 * Les dates d'un calendrier, en `AAAA-MM-JJ` : une date n'a pas d'heure, donc aucun fuseau. Tout se calcule en UTC (un changement d'heure ne
 * décale jamais un jour). La semaine commence le lundi.
 */
export interface CalEvent {
  name: string
  start: string
  end: string
  title: string
  /** Rang de la teinte (une par valeur du Select de couleur), ou `null`. */
  color: number | null
  /** Seul un brouillon se déplace. */
  movable: boolean
}

const MS = 86_400_000
const pad = (n: number, w = 2) => String(n).padStart(w, '0')

export function parseIso(iso: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) throw new Error(`date invalide : ${iso}`)
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])))
}

export const toIso = (d: Date): string => `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`

export const addDays = (iso: string, n: number): string => toIso(new Date(parseIso(iso).getTime() + n * MS))

/** Le nombre de jours de `a` à `b` (négatif si `b` précède `a`). */
export const daysBetween = (a: string, b: string): number => Math.round((parseIso(b).getTime() - parseIso(a).getTime()) / MS)

/** Le lundi de la semaine qui contient ce jour. */
export function weekStart(iso: string): string {
  const day = (parseIso(iso).getUTCDay() + 6) % 7 // lundi = 0
  return addDays(iso, -day)
}

export const weekDays = (iso: string): string[] => Array.from({ length: 7 }, (_, i) => addDays(weekStart(iso), i))

export const firstOfMonth = (iso: string): string => `${iso.slice(0, 7)}-01`

/** Le premier jour du mois, `delta` mois plus tôt ou plus tard. */
export function shiftMonth(iso: string, delta: number): string {
  const d = parseIso(firstOfMonth(iso))
  return toIso(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + delta, 1)))
}

export const lastOfMonth = (iso: string): string => addDays(shiftMonth(iso, 1), -1)

/** Les jours affichés pour un mois : de la semaine du 1er à celle du dernier jour (4 à 6 semaines pleines). */
export function monthGrid(iso: string): string[] {
  const start = weekStart(firstOfMonth(iso))
  const end = weekDays(lastOfMonth(iso))[6]!
  return Array.from({ length: daysBetween(start, end) + 1 }, (_, i) => addDays(start, i))
}

/** Début et fin d'un document : la fin manquante (ou antérieure au début) est le début même, un événement d'un jour. */
export function span(start: unknown, end: unknown): { start: string; end: string } | null {
  if (typeof start !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(start)) return null
  const e = typeof end === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(end) && end >= start ? end : start
  return { start, end: e }
}

/** Les événements de chaque jour affiché : un événement sur plusieurs jours figure sur chacun, dans la limite de la fenêtre. */
export function eventsByDay(days: readonly string[], events: readonly CalEvent[]): Record<string, CalEvent[]> {
  const out: Record<string, CalEvent[]> = Object.fromEntries(days.map((d) => [d, [] as CalEvent[]]))
  for (const ev of events) {
    for (const d of days) if (d >= ev.start && d <= ev.end) out[d]!.push(ev)
  }
  for (const list of Object.values(out)) list.sort((a, b) => a.start.localeCompare(b.start) || a.title.localeCompare(b.title, 'fr'))
  return out
}

/** Déplacer un événement sur un autre jour : le même nombre de jours, la même durée. */
export function moved(ev: Pick<CalEvent, 'start' | 'end'>, toDay: string): { start: string; end: string } {
  const delta = daysBetween(ev.start, toDay)
  return { start: addDays(ev.start, delta), end: addDays(ev.end, delta) }
}

/** Les dates qu'on demande au serveur pour une fenêtre : `in` accepte 100 valeurs, une fenêtre en fait 42 au plus. */
export const windowDates = (days: readonly string[]): string[] => [...days]
