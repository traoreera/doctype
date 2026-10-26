import { canonicalDecimal, normalizeDecimal } from './decimal'
import { parseDuration } from './duration'
import { isValidTime, normalizeTime } from './fieldrules'
import { isDecimalType, FILTER_OPS, type FieldMeta, type FilterValue, type Filters, type FieldType } from './types'
import { TAG_FILTER, parseTagFilter, type TagFilter } from './tags'

/** Une ligne de la barre de filtres. Un seul filtre par champ : le serveur n'en accepte pas deux sur le même. */
export interface FilterRow {
  id: number
  field: string
  op: string
  value: unknown
}

export function opsFor(type: FieldType): readonly string[] {
  return FILTER_OPS[type]
}

/** L'opérateur proposé en premier : « contient » pour un texte, « égal à » ailleurs. */
export function defaultOp(type: FieldType): string {
  return type === 'Text' || type === 'LongText' || type === 'Link' || type === 'Code' ? 'like' : '='
}

/** Les types dont le filtre porte un NOMBRE (une durée se saisit « 1h30 », elle part en secondes). */
export const NUMERIC_FILTER: readonly FieldType[] = ['Number', 'Rating', 'Int', 'Percent', 'Duration']

function coerce(field: FieldMeta, op: string, raw: unknown): unknown {
  if (op === 'in') {
    // Pour un Decimal la virgule est la virgule DÉCIMALE (« 2,5 ») : les valeurs d'une liste s'y séparent par des points-virgules.
    const parts = (Array.isArray(raw) ? raw : String(raw ?? '').split(isDecimalType(field.fieldtype) ? ';' : ','))
      .map((p) => (typeof p === 'string' ? p.trim() : p))
      .filter((p) => p !== '' && p !== null && p !== undefined)
    return parts.map((p) => coerce(field, '=', p))
  }
  switch (field.fieldtype) {
    case 'Number':
    case 'Rating':
    case 'Int':
    case 'Percent':
      return typeof raw === 'number' ? raw : Number(raw)
    case 'Duration':
      return typeof raw === 'number' ? raw : (parseDuration(String(raw ?? '')) ?? Number.NaN) // « 1h30 » ou des secondes : le serveur reçoit des secondes
    case 'Time':
      return typeof raw === 'string' ? normalizeTime(raw.trim()) : raw
    case 'Decimal':
    case 'Currency':
      return typeof raw === 'string' ? normalizeDecimal(raw) : raw // le serveur reçoit le texte, jamais un number
    case 'Boolean':
      return raw === true || raw === 'true'
    default:
      return typeof raw === 'string' ? raw.trim() : raw
  }
}

function complete(field: FieldMeta, op: string, value: unknown): boolean {
  if (field.fieldtype === 'Boolean') return value === true || value === false
  if (op === 'in') {
    const parts = coerce(field, op, value)
    if (!Array.isArray(parts) || !parts.length) return false
    if (isDecimalType(field.fieldtype)) return parts.every((p) => canonicalDecimal(p, field.decimals ?? 0) !== null)
    if (NUMERIC_FILTER.includes(field.fieldtype)) return parts.every((p) => Number.isFinite(p))
    if (field.fieldtype === 'Time') return parts.every((p) => isValidTime(p))
    return true
  }
  if (value === null || value === undefined || value === '') return false
  if (NUMERIC_FILTER.includes(field.fieldtype)) return Number.isFinite(coerce(field, op, value) as number)
  if (field.fieldtype === 'Time') return isValidTime(coerce(field, op, value))
  if (isDecimalType(field.fieldtype)) return canonicalDecimal(coerce(field, op, value), field.decimals ?? 0) !== null
  return true
}

/**
 * Les filtres envoyés à `/list`. Les lignes incomplètes sont ignorées (on ne filtre pas sur un champ vide).
 * `quick` : la recherche rapide de l'en-tête, « contient » sur le titre du document (ou sur son nom).
 */
export function buildFilters(rows: readonly FilterRow[], fields: readonly FieldMeta[], quick?: { field: string; text: string }): Filters {
  const byName = new Map(fields.map((f) => [f.fieldname, f]))
  const out: Filters = {}
  if (quick && quick.text.trim()) out[quick.field] = ['like', quick.text.trim()]
  for (const row of rows) {
    const field = byName.get(row.field)
    if (!field || !opsFor(field.fieldtype).includes(row.op) || !complete(field, row.op, row.value)) continue
    const value = coerce(field, row.op, row.value)
    out[row.field] = row.op === '=' ? value : [row.op, value]
  }
  return out
}

// ── Filtres portés par l'URL, et évaluation côté client ──────────────────────────────────────

/** Les filtres de l'URL (`?filters={…}`), tels qu'une carte de chiffres ou un lien les envoie : un objet JSON, sinon rien. */
export function parseFiltersParam(raw: unknown): Filters {
  if (typeof raw !== 'string' || !raw) return {}
  try {
    const v: unknown = JSON.parse(raw)
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Filters) : {}
  } catch {
    return {}
  }
}

/**
 * Les filtres de l'API → les lignes de la barre de filtres, pour que l'utilisateur VOIE ce qui filtre la liste que sa carte a ouverte.
 * `docstatus` n'est pas un champ : c'est le sélecteur d'état (`status`) ; `_tag` non plus : c'est le filtre d'étiquette (`tag`, voir
 * `lib/tags.TagFilter`). Un filtre que la barre ne sait pas montrer est écarté.
 */
export function rowsFromFilters(filters: Filters, fields: readonly FieldMeta[], nextId: () => number): { rows: FilterRow[]; status: number | null; tag: TagFilter | null } {
  const byName = new Map(fields.map((f) => [f.fieldname, f]))
  const rows: FilterRow[] = []
  let status: number | null = null
  let tag: TagFilter | null = null
  for (const [name, spec] of Object.entries(filters)) {
    if (name === 'docstatus') {
      if (typeof spec === 'number' && [0, 1, 2].includes(spec)) status = spec
      continue
    }
    if (name === TAG_FILTER) {
      tag = parseTagFilter(spec)
      continue
    }
    const field = byName.get(name)
    if (!field || field.fieldtype === 'Table') continue
    const [op, value] = Array.isArray(spec) && typeof spec[0] === 'string' ? [spec[0], spec[1]] : ['=', spec]
    if (!opsFor(field.fieldtype).includes(op)) continue
    const shown = op === 'in' && Array.isArray(value) ? value.join(isDecimalType(field.fieldtype) ? ';' : ',') : value
    rows.push({ id: nextId(), field: name, op, value: shown })
  }
  return { rows, status, tag }
}

/**
 * Un document satisfait-il ce filtre ? Le serveur n'accepte qu'UN filtre par champ : quand une vue en pose un à elle (la colonne d'un Kanban,
 * la plage d'un calendrier) sur un champ que l'utilisateur filtre déjà, celui de l'utilisateur s'applique ici, sur ce que le serveur a rendu.
 * Mêmes opérateurs que le serveur ; des chaînes (dates ISO, décimaux canoniques) et des nombres se comparent comme lui.
 */
export function matchesFilter(value: unknown, spec: FilterValue): boolean {
  const [op, operand] = Array.isArray(spec) && typeof spec[0] === 'string' ? [spec[0] as string, spec[1]] : ['=', spec]
  const missing = value === null || value === undefined
  switch (op) {
    case '=':
      return operand === null ? missing : value === operand
    case '!=':
      return missing || value !== operand
    case 'in':
      return Array.isArray(operand) && operand.includes(value)
    case 'like':
      return typeof value === 'string' && typeof operand === 'string' && value.toLowerCase().includes(operand.toLowerCase())
  }
  if (missing || operand === null || operand === undefined) return false
  const a = typeof value === 'string' && typeof operand === 'string' && /^-?\d+(\.\d+)?$/.test(value) && /^-?\d+(\.\d+)?$/.test(operand) ? Number(value) : (value as never)
  const b = typeof a === 'number' ? Number(operand) : (operand as never)
  switch (op) {
    case '>':
      return a > b
    case '>=':
      return a >= b
    case '<':
      return a < b
    case '<=':
      return a <= b
  }
  return false
}

/**
 * Les filtres d'une requête de vue : ceux de l'utilisateur, plus ceux de la vue. Quand les deux portent sur le même champ, celui de la vue
 * l'emporte dans la requête et celui de l'utilisateur est rendu à l'appelant (`later`) pour s'appliquer aux lignes reçues.
 */
export function layerFilters(user: Filters, own: Filters): { request: Filters; later: Filters } {
  const later: Filters = {}
  for (const k of Object.keys(own)) if (k in user) later[k] = user[k]
  return { request: { ...user, ...own }, later }
}

export const passesLater = (data: Record<string, unknown>, later: Filters): boolean => Object.entries(later).every(([k, spec]) => matchesFilter(data[k], spec))
