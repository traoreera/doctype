import { ref } from 'vue'
import { formatDatetime } from './datetime'
import { formatDecimal } from './decimal'
import type { DocTypeMeta, FieldMeta } from './types'

/** Les nombres et quantités s'alignent à droite : une colonne se lit d'un coup d'œil. */
export const isNumeric = (f: FieldMeta): boolean => f.fieldtype === 'Number' || f.fieldtype === 'Decimal' || f.fieldtype === 'Currency' || f.fieldtype === 'Rating'

/** Le nom d'un document n'a de sens pour un humain que s'il est un numéro de série (`MS-2026-00003`), pas un identifiant aléatoire. */
export const hasReadableName = (meta: DocTypeMeta | null): boolean => typeof meta?.naming === 'object' && meta.naming !== null

/** Les formats de date qu'une organisation peut choisir (valeur de `System Settings.date_format`) -> le motif du sélecteur de date de PrimeVue. */
export const DATE_FORMATS = { 'JJ/MM/AAAA': 'dd/mm/yy', 'AAAA-MM-JJ': 'yy-mm-dd', 'MM/JJ/AAAA': 'mm/dd/yy' } as const
export type DateFormat = keyof typeof DATE_FORMATS

// Réactif : changer le format dans les Paramètres met à jour les listes déjà affichées. Défaut : la convention française.
const dateFormat = ref<DateFormat>('JJ/MM/AAAA')

/** Choisit le format d'affichage des dates ; une valeur inconnue (réglage d'une autre version) ramène au défaut. */
export function setDateFormat(value: string | null | undefined): void {
  dateFormat.value = typeof value === 'string' && value in DATE_FORMATS ? (value as DateFormat) : 'JJ/MM/AAAA'
}

/** Le motif du sélecteur de date (PrimeVue : `dd/mm/yy`, `yy-mm-dd`…) : le même que celui de `formatDate`. */
export const datePattern = (): string => DATE_FORMATS[dateFormat.value]

/** `AAAA-MM-JJ` -> la date dans le format de l'organisation (`JJ/MM/AAAA` par défaut), sans fuseau horaire (une date n'a pas d'heure). */
export function formatDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return iso
  const [, y, mo, d] = m
  if (dateFormat.value === 'AAAA-MM-JJ') return `${y}-${mo}-${d}`
  return dateFormat.value === 'MM/JJ/AAAA' ? `${mo}/${d}/${y}` : `${d}/${mo}/${y}`
}

export function dateFromIso(iso: string | null | undefined): Date | null {
  const m = typeof iso === 'string' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso) : null
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}

/** Le jour LOCAL choisi par l'utilisateur, jamais décalé par l'UTC (toISOString() ferait reculer d'un jour à l'est de Greenwich). */
export function isoFromDate(d: Date): string {
  const p = (n: number, w = 2) => String(n).padStart(w, '0')
  return `${p(d.getFullYear(), 4)}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/**
 * La date d'aujourd'hui (`AAAA-MM-JJ`) dans le fuseau de l'ORGANISATION (celui du serveur), pas celui du navigateur : à Auckland, le
 * jour local est déjà « demain » pour un serveur en UTC, qui refuserait une date « dans le futur ». `now` est injectable pour les tests.
 */
export function todayIn(timeZone?: string, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now)
  } catch {
    return isoFromDate(now) // fuseau inconnu : le jour local vaut mieux que rien
  }
}

const numberFormat = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 10 })
export const formatNumber = (n: number): string => numberFormat.format(n)

const rtf = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' })
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31_536_000],
  ['month', 2_592_000],
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
]

/** « il y a 5 minutes », « hier »… `now` est injectable pour les tests. */
export function relativeTime(iso: string, now = Date.now()): string {
  const then = Date.parse(iso)
  if (Number.isNaN(then)) return iso
  const seconds = Math.round((then - now) / 1000)
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit)
  }
  return 'à l’instant'
}

export function formatTimestamp(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}

/** « 1,5 Mo », « 320 Ko » : la taille d'un fichier. */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} o`
  if (n < 1024 * 1024) return `${formatNumber(Math.round((n / 1024) * 10) / 10)} Ko`
  return `${formatNumber(Math.round((n / 1024 / 1024) * 10) / 10)} Mo`
}

/** Le symbole d'une devise (« EUR » -> « € »), ou le code lui-même si le navigateur ne le connaît pas. */
export function currencySymbol(code: string): string {
  try {
    return new Intl.NumberFormat('fr-FR', { style: 'currency', currency: code }).formatToParts(0).find((p) => p.type === 'currency')?.value ?? code
  } catch {
    return code
  }
}

/** « ★★★☆☆ » : une note sur `stars` étoiles. */
export const starsText = (value: number, stars: number): string => '★'.repeat(Math.max(0, Math.min(value, stars))) + '☆'.repeat(Math.max(0, stars - value))

/** Texte d'une cellule de liste. Les liens sont résolus par l'appelant (`links`), le reste se formate ici. */
export function formatCell(field: FieldMeta, value: unknown, links?: Record<string, string>): string {
  if (value === null || value === undefined || value === '') return ''
  switch (field.fieldtype) {
    case 'Number':
      return typeof value === 'number' ? formatNumber(value) : String(value)
    case 'Decimal':
      return typeof value === 'string' ? formatDecimal(value, field.decimals ?? 0) : String(value)
    case 'Currency': {
      const text = typeof value === 'string' ? formatDecimal(value, field.decimals ?? 2) : String(value)
      return typeof field.options === 'string' ? `${text} ${currencySymbol(field.options)}` : text
    }
    case 'Rating':
      return typeof value === 'number' ? starsText(value, field.stars ?? 5) : String(value)
    case 'Datetime':
      return typeof value === 'string' ? formatDatetime(value) : String(value)
    case 'LongText': {
      const first = String(value).split('\n', 1)[0] ?? ''
      return first.length > 80 || String(value).includes('\n') ? `${first.slice(0, 80)}…` : first
    }
    case 'Date':
      return typeof value === 'string' ? formatDate(value) : String(value)
    case 'Boolean':
      return value ? 'Oui' : 'Non'
    case 'Link':
      return typeof value === 'string' ? (links?.[value] ?? value) : String(value)
    default:
      return String(value)
  }
}
