/** Une durée est un nombre de SECONDES (champ `Duration`) ; ce module la lit et l'écrit comme un humain : « 1 h 30 min », « 90m », « 1:30:00 ». */

export const MAX_DURATION = 100 * 366 * 24 * 3600
const UNIT_SECONDS: Record<string, number> = { j: 86_400, d: 86_400, h: 3_600, min: 60, m: 60, s: 1 }

/** `1h 30m`, `1h30`, `2 j 4 h`, `90 min`, `1:30` (h:mm), `1:30:15`, `5400` (des secondes) -> secondes ; `null` si le texte n'est pas une durée. */
export function parseDuration(text: string): number | null {
  const s = text.trim().toLowerCase()
  if (!s) return null
  const clock = /^(\d+):([0-5]?\d)(?::([0-5]?\d))?$/.exec(s)
  if (clock) return bounded(Number(clock[1]) * 3600 + Number(clock[2]) * 60 + Number(clock[3] ?? 0))
  const frenchClock = /^(\d+)\s*h\s*([0-5]?\d)$/.exec(s) // « 1h30 » : les minutes sans unité
  if (frenchClock) return bounded(Number(frenchClock[1]) * 3600 + Number(frenchClock[2]) * 60)
  if (/^\d+$/.test(s)) return bounded(Number(s))
  const unit = /\s*(\d+)\s*(min|j|d|h|m|s)\b\s*/y
  let total = 0
  let at = 0
  let parts = 0
  while (at < s.length) {
    unit.lastIndex = at
    const m = unit.exec(s)
    if (!m) return null
    total += Number(m[1]) * UNIT_SECONDS[m[2]!]!
    at = unit.lastIndex
    parts++
  }
  return parts ? bounded(total) : null
}

const bounded = (n: number): number | null => (Number.isSafeInteger(n) && n >= 0 && n <= MAX_DURATION ? n : null)

/** `5400` -> « 1 h 30 min » ; `0` -> « 0 s ». */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return String(seconds)
  if (seconds === 0) return '0 s'
  const parts: string[] = []
  let rest = Math.floor(seconds)
  for (const [label, size] of [['j', 86_400], ['h', 3_600], ['min', 60], ['s', 1]] as const) {
    const n = Math.floor(rest / size)
    rest -= n * size
    if (n) parts.push(`${n} ${label}`)
  }
  return parts.join(' ')
}

/** `5400` -> « 1:30:00 » : la forme d'un tableur. */
export function clockDuration(seconds: number): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${Math.floor(seconds / 3600)}:${p(Math.floor((seconds % 3600) / 60))}:${p(seconds % 60)}`
}
