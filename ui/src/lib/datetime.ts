/**
 * Les champs Datetime : le serveur stocke TOUJOURS `AAAA-MM-JJThh:mm:ssZ` (UTC) ; l'utilisateur lit et saisit l'heure LOCALE de son
 * navigateur. Toute la conversion est ici, et nulle part ailleurs : un `toISOString()` dispersé décalerait d'un jour une date.
 */
const pad = (n: number, w = 2) => String(n).padStart(w, '0')

export const ISO_DATETIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/

/** L'instant stocké -> un `Date` (qui s'affichera à l'heure locale), ou `null` si la valeur n'est pas un Datetime canonique. */
export function dateFromUtc(iso: string | null | undefined): Date | null {
  const m = typeof iso === 'string' ? ISO_DATETIME.exec(iso) : null
  if (!m) return null
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])))
  return Number.isNaN(d.getTime()) ? null : d
}

/** Un `Date` -> la forme stockée (UTC, à la seconde). */
export const utcFromDate = (d: Date): string => `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`

export const nowUtc = (now: Date = new Date()): string => utcFromDate(now)

/** `JJ/MM/AAAA hh:mm`, à l'heure locale du navigateur ; une valeur illisible est rendue telle quelle. */
export function formatDatetime(iso: string): string {
  const d = dateFromUtc(iso)
  if (!d) return iso
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${pad(d.getFullYear(), 4)} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
