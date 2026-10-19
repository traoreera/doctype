/**
 * Lire un résultat d'agrégat : la requête d'un bloc, les libellés d'un groupe, le format d'une mesure. Sans interface : testé seul.
 * Les montants Decimal restent des CHAÎNES exactes jusqu'à l'affichage (`lib/decimal.ts`) ; seul le dessin d'un graphique les lit
 * comme des nombres, et un dessin n'a pas besoin d'être exact.
 */
import { t } from '@/i18n'
import { formatDecimal, sumDecimals } from './decimal'
import { statusLabel } from './docstatus'
import { formatNumber } from './format'
import type { AggregateGroup, AggregateMetric, AggregateQuery, AggregateResult, AggregateRow, ChartBlock, NumberCardBlock } from './types'

const average = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 })
const month = new Intl.DateTimeFormat('fr-FR', { month: 'short', year: '2-digit', timeZone: 'UTC' })
const day = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', timeZone: 'UTC' })

/** La requête d'agrégat qui répond à un bloc (carte ou graphique). */
export function queryOf(block: NumberCardBlock | ChartBlock): AggregateQuery {
  const q: AggregateQuery = { metrics: [block.function === 'count' ? 'count' : `${block.function}:${block.field}`] }
  if (block.filters && Object.keys(block.filters).length) q.filters = block.filters
  if (block.type === 'chart') {
    q.group_by = block.group_by
    if (block.bucket) q.bucket = block.bucket
    if (block.periods) q.periods = block.periods
    q.limit = block.limit
  }
  return q
}

/** `sum:qty` -> la mesure demandée à `/aggregate`. */
export const metricSpec = (fn: string, field?: string | null): string => (fn === 'count' || !field ? 'count' : `${fn}:${field}`)

/** La valeur d'une mesure, lisible : une quantité exacte en texte, un nombre en nombre, aucune valeur en tiret. */
export function formatMetric(value: number | string | null | undefined, metric?: Pick<AggregateMetric, 'function' | 'decimals'>): string {
  if (value === null || value === undefined) return t.charts.none
  if (typeof value === 'string') return formatDecimal(value, metric?.decimals ?? 0)
  return metric?.function === 'avg' ? average.format(value) : formatNumber(value)
}

/** La valeur d'une mesure comme NOMBRE, pour dessiner (jamais pour afficher un montant : voir `formatMetric`). */
export const plotValue = (value: number | string | null | undefined): number => {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : 0
}

/** Le libellé d'une période : « oct. 26 » (mois), « 17 oct. » (jour), « 2026 » (année). */
export function bucketLabel(key: string, bucket: string | null): string {
  const date = new Date(`${key.length === 4 ? `${key}-01-01` : key.length === 7 ? `${key}-01` : key}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return key
  if (bucket === 'month') return month.format(date)
  if (bucket === 'day') return day.format(date)
  return key
}

/**
 * Le libellé d'un groupe. `title` donne le titre d'un document lié (Link) : un nom opaque (`a1b2c3`) ne se lit pas sur un graphique.
 * Un groupe sans valeur s'appelle « (vide) ».
 */
export function groupLabel(group: AggregateGroup | null, key: AggregateRow['key'], title?: (target: string, name: string) => string): string {
  if (key === null || key === undefined || key === '') return t.charts.empty
  if (!group) return String(key)
  if (group.bucket) return bucketLabel(String(key), group.bucket)
  if (group.field === 'docstatus') return statusLabel(Number(key))
  if (group.fieldtype === 'Boolean') return key ? t.yes : t.no
  if (group.fieldtype === 'Link' && typeof group.options === 'string' && title) return title(group.options, String(key))
  return String(key)
}

/** Les noms de documents cités par un groupe Link : à résoudre en titres avant de dessiner. */
export const linkTargets = (result: AggregateResult): { target: string; names: string[] } | null =>
  result.group?.fieldtype === 'Link' && typeof result.group.options === 'string'
    ? { target: result.group.options, names: result.rows.map((r) => r.key).filter((k): k is string => typeof k === 'string') }
    : null

export interface Part {
  label: string
  raw: number | string | null
}

/**
 * Les parts d'un camembert : les plus grosses d'abord, au plus `max` ; le reste se replie en UNE part « Autres » (jamais une septième
 * teinte). Seules les valeurs positives sont des parts. Sert à ce qui s'additionne (comptes, sommes) : le repli est exact pour un Decimal.
 * `folded` dit que la dernière part est ce repli (le dessin la peint d'un gris neutre).
 */
export function foldParts(parts: readonly Part[], max: number, decimals: number | null, other: string): { parts: Part[]; folded: boolean } {
  const positive = parts.filter((p) => plotValue(p.raw) > 0).sort((a, b) => plotValue(b.raw) - plotValue(a.raw))
  if (positive.length <= max) return { parts: positive, folded: false }
  const head = positive.slice(0, max - 1)
  const tail = positive.slice(max - 1)
  const raw = decimals !== null ? sumDecimals(tail.map((p) => String(p.raw)), decimals) : tail.reduce((sum, p) => sum + plotValue(p.raw), 0)
  return { parts: [...head, { label: other, raw }], folded: true }
}
