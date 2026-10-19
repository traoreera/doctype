/** Les règles des types de champ simples, MIROIR de `fieldtypes.py` : le Desk refuse avant l'envoi ce que le serveur refuserait. */
import { dateFromUtc } from './datetime'

export const COLOR = /^#[0-9a-f]{6}$/
const EMAIL = /^[^@\s,;<>()[\]\\"]{1,64}@[^@\s,;<>()[\]\\"]{1,190}\.[^@\s,;<>()[\]\\"]{2,63}$/
const PHONE = /^\+?[0-9 ().-]{4,25}$/
const URL_RE = /^https?:\/\/[^\s<>"']{1,2040}$/i
export const MAX_LONG_TEXT = 100_000

export type TextFormat = 'email' | 'phone' | 'url'

export function isValidFormat(format: TextFormat, v: string): boolean {
  if (format === 'email') return v.length <= 254 && EMAIL.test(v)
  if (format === 'phone') return PHONE.test(v) && (v.match(/\d/g)?.length ?? 0) >= 4
  return URL_RE.test(v)
}

export const isValidRating = (v: unknown, stars: number): boolean => typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= stars

/** Un Datetime canonique (`AAAA-MM-JJThh:mm:ssZ`) qui EXISTE (le 30 février est refusé). */
export const isValidDatetime = (v: unknown): boolean => {
  const d = dateFromUtc(typeof v === 'string' ? v : null)
  return !!d && typeof v === 'string' && d.toISOString().slice(0, 19) + 'Z' === v
}

/** Le lien qu'un texte formaté ouvre : `mailto:`, `tel:` ou l'adresse web ; jamais un autre schéma. `null` si la valeur n'est pas valide. */
export function hrefFor(format: TextFormat | undefined, v: string): string | null {
  if (!format || !isValidFormat(format, v)) return null
  if (format === 'email') return `mailto:${v}`
  if (format === 'phone') return `tel:${v.replace(/[^\d+]/g, '')}`
  return v
}
