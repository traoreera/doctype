/**
 * Logique du formulaire, SANS interface : ce que le Desk affiche, exige et verrouille, et ce qu'il envoie.
 *
 * Les règles reprennent EXACTEMENT celles que le serveur revérifie à l'écriture (`CompiledDocType._condition_errors`) :
 *  - visible    = `depends_on` (vrai si absent) ;
 *  - obligatoire = (required ET visible) OU `mandatory_depends_on` ;
 *  - lecture seule = `read_only_depends_on` évalué sur l'état ENREGISTRÉ du document (le serveur ne regarde pas autre chose).
 * Le navigateur n'est jamais l'autorité : il évite seulement un aller-retour inutile et guide l'utilisateur.
 */
import { ApiError } from './api'
import { nowUtc } from './datetime'
import { canonicalDecimal } from './decimal'
import { COLOR, MAX_LONG_TEXT, MAX_PASSWORD, isValidDatetime, isValidFormat, isValidInt, isValidJsonText, isValidPercent, isValidRating, isValidTime, normalizeTime } from './fieldrules'
import { MAX_DURATION, parseDuration } from './duration'
import { MAX_RICH } from './richtext'
import { evaluate } from './expr'
import { todayIn } from './format'
import { t } from '@/i18n'
import { DRAFT, SUBMITTED, isDecimalType } from './types'
import type { ChildMeta, DocData, DocRecord, DocTypeMeta, FieldMeta } from './types'

export type Values = Record<string, unknown>
/** Une ligne d'un champ Table : les valeurs de ses champs, comme un document en miniature. */
export type Row = Record<string, unknown>

export interface FieldState {
  /** Affiché dans le formulaire. */
  visible: boolean
  required: boolean
  readOnly: boolean
}
export type FieldStates = Record<string, FieldState>

/** Vide : rien, une chaîne vide, ou un tableau sans ligne (0 et `false` sont des valeurs). */
export const isEmpty = (v: unknown): boolean => v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)

/** Le DocType des lignes d'un champ Table (absent : métadonnées incomplètes, le champ est alors traité comme vide). */
export const childOf = (meta: DocTypeMeta, f: FieldMeta): ChildMeta | undefined =>
  f.fieldtype === 'Table' && typeof f.options === 'string' ? meta.children?.[f.options] : undefined

/** Dans une condition d'un champ de ligne, `parent_<champ>` lit le document qui porte la table (miroir de `schema.PARENT_PREFIX`). */
export const PARENT_PREFIX = 'parent_'

/** Le défaut d'un champ. `today` (dates) se lit dans le fuseau de l'organisation, au moment où le formulaire s'ouvre. */
const blank = (f: FieldMeta, timeZone?: string): unknown =>
  f.default === 'today' && f.fieldtype === 'Date'
    ? todayIn(timeZone)
    : f.default === 'now' && f.fieldtype === 'Datetime'
      ? nowUtc()
      : f.default !== undefined
        ? f.default
        : f.fieldtype === 'Boolean'
          ? false
          : null

/** Une ligne neuve : les défauts du DocType enfant, les cases décochées, le reste vide. */
export function newRow(child: ChildMeta): Row {
  const out: Row = {}
  for (const f of child.fields) out[f.fieldname] = blank(f)
  return out
}

function recordRow(child: ChildMeta, raw: unknown): Row {
  const src = (raw && typeof raw === 'object' ? raw : {}) as Row
  const out: Row = {}
  for (const f of child.fields) {
    const v = src[f.fieldname]
    out[f.fieldname] = v === undefined ? (f.fieldtype === 'Boolean' ? false : null) : v
  }
  return out
}

/** Valeurs d'un nouveau document : les défauts du DocType, les cases à cocher décochées, le reste vide. */
export function newValues(meta: DocTypeMeta): Values {
  const out: Values = {}
  for (const f of meta.fields) out[f.fieldname] = f.fieldtype === 'Table' ? [] : blank(f, meta.timezone)
  return out
}

/**
 * Valeurs d'un nouveau document pré-remplies par l'adresse (`#/app/Facture/new?posting_date=2026-10-20`) : un clic sur un jour du calendrier,
 * sur « + » d'une colonne de Kanban. Chaque valeur est CONTRÔLÉE par le type de son champ (la même exigence qu'une saisie) ; une valeur
 * invalide, un champ inconnu ou un tableau est ignoré, jamais passé tel quel.
 */
export function presetValues(meta: DocTypeMeta, query: Record<string, unknown>): Values {
  const out: Values = {}
  for (const f of meta.fields) {
    const raw = query[f.fieldname]
    if (typeof raw !== 'string' || f.hidden || f.fieldtype === 'Table' || raw.length > 500) continue
    switch (f.fieldtype) {
      case 'Date':
        if (isValidIsoDate(raw)) out[f.fieldname] = raw
        break
      case 'Select':
        if (Array.isArray(f.options) && f.options.includes(raw)) out[f.fieldname] = raw
        break
      case 'Boolean':
        if (raw === 'true' || raw === 'false') out[f.fieldname] = raw === 'true'
        break
      case 'Number':
        if (raw.trim() !== '' && Number.isFinite(Number(raw))) out[f.fieldname] = Number(raw)
        break
      case 'Decimal':
      case 'Currency': {
        const canon = canonicalDecimal(raw, f.decimals ?? (f.fieldtype === 'Currency' ? 2 : 0))
        if (canon !== null) out[f.fieldname] = canon
        break
      }
      case 'Rating':
        if (/^\d+$/.test(raw) && isValidRating(Number(raw), f.stars ?? 5)) out[f.fieldname] = Number(raw)
        break
      case 'Int':
        if (/^-?\d+$/.test(raw) && isValidInt(Number(raw))) out[f.fieldname] = Number(raw)
        break
      case 'Percent':
        if (raw.trim() !== '' && isValidPercent(Number(raw))) out[f.fieldname] = Number(raw)
        break
      case 'Time':
        if (isValidTime(normalizeTime(raw))) out[f.fieldname] = normalizeTime(raw)
        break
      case 'Duration': {
        const seconds = parseDuration(raw)
        if (seconds !== null) out[f.fieldname] = seconds
        break
      }
      case 'JSON':
      case 'Password':
      case 'TextEditor':
        break // ni un secret, ni du HTML, ni un objet ne se pré-remplissent par l'adresse
      case 'Datetime':
        if (isValidDatetime(raw)) out[f.fieldname] = raw
        break
      case 'Color':
        if (COLOR.test(raw)) out[f.fieldname] = raw
        break
      default:
        out[f.fieldname] = raw
    }
  }
  return out
}

/** Le formulaire d'un nouveau document à son ouverture : les défauts, puis ce que l'adresse pré-remplit. */
export const startValues = (meta: DocTypeMeta, query: Record<string, unknown> = {}): Values => ({ ...newValues(meta), ...presetValues(meta, query) })

/** Valeurs d'un document existant. Un champ absent reste vide : un défaut ne s'applique qu'à la création. */
export function recordValues(meta: DocTypeMeta, rec: DocRecord): Values {
  const out: Values = {}
  for (const f of meta.fields) {
    const v = rec.data[f.fieldname]
    if (f.fieldtype === 'Table') {
      const child = childOf(meta, f)
      out[f.fieldname] = child && Array.isArray(v) ? v.map((r) => recordRow(child, r)) : []
    } else if (f.fieldtype === 'JSON') out[f.fieldname] = v === undefined || v === null ? null : JSON.stringify(v, null, 2) // le formulaire édite du TEXTE ; il repart en objet (`sendable`)
    else out[f.fieldname] = v === undefined ? (f.fieldtype === 'Boolean' ? false : null) : v
  }
  return out
}

/**
 * Les valeurs d'une COPIE de `rec` (« Dupliquer ») : tout ce qui se recopie. Ne partent pas : un champ `no_copy`, un champ `unique` (la copie le
 * dupliquerait : l'utilisateur en choisit un autre), un secret, un fichier (un fichier appartient à UN document), un champ rempli par `fetch_from`
 * (le serveur le relit) et le champ qui nomme le document.
 */
export function duplicateValues(meta: DocTypeMeta, rec: DocRecord): Values {
  const source = recordValues(meta, rec)
  const out = newValues(meta)
  const naming = namingField(meta)
  const skip = (f: FieldMeta): boolean =>
    !!f.no_copy || !!f.unique || !!f.fetch_from || f.fieldtype === 'Password' || f.fieldtype === 'Image' || f.fieldtype === 'Attach' || f.fieldname === naming
  for (const f of meta.fields) {
    if (skip(f)) continue
    const child = childOf(meta, f)
    out[f.fieldname] = child
      ? (source[f.fieldname] as Row[]).map((row) => Object.fromEntries(Object.entries(row).filter(([k]) => { const cf = child.fields.find((c) => c.fieldname === k); return !cf || !(cf.fetch_from || cf.fieldtype === 'Image' || cf.fieldtype === 'Attach') })))
      : source[f.fieldname]
  }
  return out
}

const OPAQUE_FOR_QUICK_ENTRY = ['Table', 'Image', 'Attach', 'Password', 'DynamicLink']

/**
 * Les champs de la SAISIE RAPIDE d'un type (créer un document depuis un champ Link) : les obligatoires et le titre, rien d'autre. `null` : le type exige ce que cette
 * saisie ne sait pas remplir (un tableau, un fichier, une obligation qui dépend d'une condition) : il faut alors la fiche complète.
 */
export function quickEntryFields(meta: DocTypeMeta): FieldMeta[] | null {
  const shown = meta.fields.filter((f) => !f.hidden)
  if (shown.some((f) => f.mandatory_depends_on)) return null
  const wanted = shown.filter((f) => !f.read_only && !f.fetch_from && (f.required || f.fieldname === meta.title_field))
  if (wanted.some((f) => OPAQUE_FOR_QUICK_ENTRY.includes(f.fieldtype) || f.depends_on)) return null
  if (shown.some((f) => f.required && (f.read_only || f.fetch_from))) return null // obligatoire mais jamais saisi ici
  return wanted
}

/** Les types qui prennent toute la largeur d'un formulaire en deux colonnes : du texte long, du code, un tableau. */
export const WIDE_TYPES: readonly string[] = ['LongText', 'TextEditor', 'Code', 'JSON', 'Table']
export const isWide = (f: FieldMeta): boolean => WIDE_TYPES.includes(f.fieldtype)

/**
 * Une section SANS titre et à une seule colonne est celle que personne n'a composée (les champs d'un type sans `layout`, ou ceux que le `layout` n'a pas placés) :
 * le Desk la range en deux colonnes quand la place le permet, les champs longs gardant toute la largeur. Une section composée (titre, plusieurs colonnes) reste telle quelle.
 */
export const isAutoSection = (s: { section: string | null; collapsible: boolean; columns: readonly unknown[] }): boolean => s.section === null && !s.collapsible && s.columns.length === 1

/** Les champs qui lisent leur valeur du document que le Link `linkName` désigne (`fetch_from: lien.champ`). */
export const fetchDependents = (fields: readonly FieldMeta[], linkName: string): FieldMeta[] => fields.filter((f) => f.fetch_from?.split('.')[0] === linkName)
/** Le champ de la cible dont ce champ copie la valeur. */
export const fetchSource = (f: FieldMeta): string => f.fetch_from?.split('.')[1] ?? ''

/** Le champ qui NOMME le document (`naming: {field: code}`) : il ne change plus une fois le document créé. */
export const namingField = (meta: DocTypeMeta): string | null => (typeof meta.naming === 'object' && 'field' in meta.naming ? meta.naming.field : null)

/**
 * `status` : l'état du document (`docstatus`). Soumis, il est figé SAUF ses champs `allow_on_submit` ; le serveur le revérifie.
 * Un champ `fetch_from` n'est jamais saisi (le serveur l'écrase), un `set_only_once` se fige dès qu'il a une valeur ENREGISTRÉE.
 */
export function fieldStates(meta: DocTypeMeta, values: Values, saved: Values | null, status: number = DRAFT): FieldStates {
  const out: FieldStates = {}
  const naming = namingField(meta)
  for (const f of meta.fields) {
    const conditionVisible = f.depends_on ? evaluate(f.depends_on, values) : true
    const mandatory = f.mandatory_depends_on ? evaluate(f.mandatory_depends_on, values) : false
    const lockedOnce = saved !== null && (f.set_only_once === true || f.fieldname === naming) && !isEmpty(saved[f.fieldname])
    const frozenField = status === SUBMITTED && f.allow_on_submit !== true
    // Un document existant : le serveur évalue la lecture seule sur l'état d'AVANT. Un nouveau : l'interface suit les valeurs.
    const readOnly = f.read_only === true || !!f.fetch_from || lockedOnce || frozenField || (f.read_only_depends_on ? evaluate(f.read_only_depends_on, saved ?? values) : false)
    // Figé (soumis, hors `allow_on_submit`), un champ ne se remplit plus : l'exiger bloquerait toute modification du document. Le serveur
    // n'exige d'ailleurs l'obligation d'un habillage, rendue après la soumission, que des champs encore modifiables.
    const required = !frozenField && ((f.required && conditionVisible) || mandatory)
    out[f.fieldname] = { visible: !f.hidden && conditionVisible, required, readOnly }
  }
  return out
}

/**
 * Ce champ d'un document ENREGISTRÉ est-il verrouillé pour l'utilisateur ? Les mêmes règles que la fiche (`fieldStates` : `read_only` du YAML
 * ou de l'habillage de l'organisation, `read_only_depends_on` sur l'état enregistré, `set_only_once`, `fetch_from`, document soumis). Le Kanban
 * et le calendrier ne déplacent pas une carte dont le serveur refuserait la modification.
 */
export const savedFieldLocked = (meta: DocTypeMeta, fieldname: string, rec: DocRecord): boolean =>
  fieldStates(meta, rec.data, rec.data, rec.docstatus)[fieldname]?.readOnly ?? false

export interface CellState {
  /** La cellule existe pour cette ligne : ni `hidden`, ni écartée par sa condition `depends_on`. */
  visible: boolean
  required: boolean
  /** La condition `depends_on` est vraie (ou absente) : sinon la valeur ne part pas, elle ne correspond plus à la nature du document. */
  active: boolean
}

/** Ce que lit la condition d'un champ de ligne : la ligne elle-même, et les champs du parent sous `parent_<champ>`. */
export function rowContext(row: Row, parent: Values): Values {
  const ctx: Values = { ...row }
  for (const [k, v] of Object.entries(parent)) if (!Array.isArray(v)) ctx[`${PARENT_PREFIX}${k}`] = v
  return ctx
}

/** Visibilité et obligation de chaque cellule d'une ligne : les règles du document (`fieldStates`), appliquées à la ligne et à son parent. */
export function cellStates(child: ChildMeta, row: Row, parent: Values): Record<string, CellState> {
  const ctx = rowContext(row, parent)
  const out: Record<string, CellState> = {}
  for (const f of child.fields) {
    const active = f.depends_on ? evaluate(f.depends_on, ctx) : true
    const mandatory = f.mandatory_depends_on ? evaluate(f.mandatory_depends_on, ctx) : false
    out[f.fieldname] = { visible: !f.hidden && active, required: (f.required && active) || mandatory, active }
  }
  return out
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

export function isValidIsoDate(s: unknown): boolean {
  if (typeof s !== 'string') return false
  const m = ISO_DATE.exec(s)
  if (!m) return false
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const date = new Date(Date.UTC(y, mo - 1, d))
  return date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d
}

function typeError(f: FieldMeta, v: unknown): string | null {
  switch (f.fieldtype) {
    case 'Number':
      return typeof v === 'number' && Number.isFinite(v) ? null : t.form.invalid
    case 'Decimal':
    case 'Currency': {
      // Même règle que le serveur : au plus `decimals` décimales (jamais d'arrondi silencieux), 18 chiffres au plus.
      const decimals = f.decimals ?? (f.fieldtype === 'Currency' ? 2 : 0)
      if (typeof v !== 'string') return t.form.invalid
      if (canonicalDecimal(v, decimals) !== null) return null
      return /^-?\d+(\.\d+)?$/.test(v) && (v.split('.')[1]?.replace(/0+$/, '').length ?? 0) > decimals ? t.form.tooManyDecimals(decimals) : t.form.invalid
    }
    case 'Rating':
      return isValidRating(v, f.stars ?? 5) ? null : t.form.invalid
    case 'Int':
      return isValidInt(v) ? null : t.form.invalidInt
    case 'Percent':
      return isValidPercent(v) ? null : t.form.invalidPercent
    case 'Time':
      return isValidTime(v) ? null : t.form.invalidTime
    case 'Duration':
      return typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= MAX_DURATION ? null : t.form.invalidDuration
    case 'Code':
      return typeof v === 'string' && v.length <= MAX_LONG_TEXT ? null : t.form.invalid
    case 'JSON':
      return isValidJsonText(v) ? null : t.form.invalidJson
    case 'TextEditor':
      return typeof v === 'string' && v.length <= MAX_RICH ? null : t.form.invalid
    case 'Password':
      return typeof v === 'string' && v.length >= 1 && v.length <= MAX_PASSWORD ? null : t.form.invalid
    case 'Datetime':
      return isValidDatetime(v) ? null : t.form.invalid
    case 'Color':
      return typeof v === 'string' && COLOR.test(v) ? null : t.form.invalid
    case 'LongText':
      return typeof v === 'string' && v.length <= MAX_LONG_TEXT ? null : t.form.invalid
    case 'Text':
      return typeof v === 'string' && (!f.format || isValidFormat(f.format, v)) ? null : (t.form.invalidFormat[f.format ?? 'email'] ?? t.form.invalid)
    case 'Boolean':
      return typeof v === 'boolean' ? null : t.form.invalid
    case 'Date':
      return isValidIsoDate(v) ? null : t.form.invalid
    case 'Select':
      return typeof v === 'string' && Array.isArray(f.options) && f.options.includes(v) ? null : t.form.invalid
    case 'Table':
      return Array.isArray(v) ? null : t.form.invalid
    default:
      return typeof v === 'string' ? null : t.form.invalid
  }
}

/** Clé d'une erreur de cellule : `items.0.qty` (champ Table, numéro de ligne à partir de 0, champ de la ligne). */
export const cellKey = (table: string, row: number, field: string): string => `${table}.${row}.${field}`

/** Erreurs de chaque cellule d'un champ Table. Les lignes se contrôlent comme un document : obligatoire, puis type. */
function validateRows(f: FieldMeta, child: ChildMeta, rows: unknown, errors: Record<string, string>, parent: Values): void {
  if (!Array.isArray(rows)) return
  rows.forEach((row: Row, i) => {
    const states = cellStates(child, row ?? {}, parent)
    for (const cf of child.fields) {
      const st = states[cf.fieldname]!
      if (cf.hidden || (!st.visible && !st.required)) continue
      const v = row?.[cf.fieldname]
      if (isEmpty(v)) {
        if (st.required) errors[cellKey(f.fieldname, i, cf.fieldname)] = t.form.required
        continue
      }
      const bad = typeError(cf, v)
      if (bad) errors[cellKey(f.fieldname, i, cf.fieldname)] = bad
    }
  })
}

/** Erreurs par champ (et par cellule : `items.0.qty`). Un champ masqué par sa condition et non obligatoire n'est pas contrôlé. */
export function validate(meta: DocTypeMeta, values: Values, states: FieldStates): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const f of meta.fields) {
    const st = states[f.fieldname]
    if (!st || f.hidden || (!st.visible && !st.required)) continue
    const v = values[f.fieldname]
    if (isEmpty(v)) {
      if (st.required) errors[f.fieldname] = t.form.required
      continue
    }
    const bad = typeError(f, v)
    if (bad) errors[f.fieldname] = bad
    else if (f.fieldtype === 'Table') {
      const child = childOf(meta, f)
      if (child) validateRows(f, child, v, errors, values)
    }
  }
  return errors
}

/** Un Decimal valide part sous sa forme canonique (« 12.5 » -> « 12.500 ») : ressaisir la même valeur n'est pas une modification. */
const sendable = (f: FieldMeta, v: unknown): unknown => {
  if (isDecimalType(f.fieldtype)) return canonicalDecimal(v, f.decimals ?? (f.fieldtype === 'Currency' ? 2 : 0)) ?? v
  if (f.fieldtype === 'JSON' && typeof v === 'string') {
    try {
      return JSON.parse(v) // le formulaire édite du texte, le serveur reçoit l'objet ; un texte invalide est arrêté avant (`validate`)
    } catch {
      return v
    }
  }
  return v
}

function rowPayload(child: ChildMeta, row: Row, parent: Values): DocData {
  const out: DocData = {}
  const states = cellStates(child, row ?? {}, parent)
  for (const f of child.fields) {
    const v = row?.[f.fieldname]
    // Une cellule que la nature du document écarte (l'entrepôt source d'une réception) ne part pas : l'utilisateur ne la voit plus.
    if (!isEmpty(v) && states[f.fieldname]!.active) out[f.fieldname] = sendable(f, v)
  }
  return out
}

/** Ce que le serveur reçoit : les champs renseignés. `false` et `0` sont des valeurs ; vide et « » n'en sont pas. */
export function toPayload(meta: DocTypeMeta, values: Values): DocData {
  const out: DocData = {}
  for (const f of meta.fields) {
    const v = values[f.fieldname]
    if (isEmpty(v)) continue
    if (f.fieldtype === 'Table') {
      const child = childOf(meta, f)
      if (child && Array.isArray(v)) out[f.fieldname] = v.map((r: Row) => rowPayload(child, r, values))
    } else out[f.fieldname] = sendable(f, v)
  }
  return out
}

export function isDirty(meta: DocTypeMeta, values: Values, original: Values): boolean {
  return JSON.stringify(toPayload(meta, values)) !== JSON.stringify(toPayload(meta, original))
}

export interface MappedErrors {
  /** Erreurs rattachées à un champ du formulaire. */
  fields: Record<string, string>
  /** Message à afficher en haut : refus d'un hook, erreur sans champ… */
  general: string | null
}

/** Traduit une réponse d'erreur du serveur pour le formulaire. Les champs inconnus du formulaire passent en message général. */
export function mapServerErrors(err: ApiError, meta: DocTypeMeta): MappedErrors {
  const known = new Set(meta.fields.map((f) => f.fieldname))
  if (meta.naming === 'prompt') known.add('name') // le nom saisi à la création est un champ du formulaire
  const fields: Record<string, string> = {}
  const loose: string[] = []
  for (const d of err.details) {
    const msg = typeof d.msg === 'string' && d.msg ? d.msg : t.form.invalid
    // `items.0.qty` : l'erreur d'une cellule se range sous sa clé complète, si `items` est un champ du formulaire.
    if (typeof d.field === 'string' && known.has(d.field.split('.')[0] ?? '')) fields[d.field] ??= translateServerMessage(d, msg)
    else if (typeof d.msg === 'string' && d.msg) loose.push(d.msg)
  }
  let general: string | null = null
  switch (err.code) {
    case 'validation_failed':
      general = Object.keys(fields).length ? t.form.fixErrors : (loose[0] ?? err.message)
      break
    case 'hook_rejected':
      general = err.message // le message du hook est écrit pour l'utilisateur
      break
    case 'hook_unavailable':
      general = t.errors.hookUnavailable
      break
    case 'hook_timeout':
      general = t.errors.hookTimeout
      break
    case 'hook_payload_too_large':
      general = t.errors.tooLarge
      break
    case 'permission_denied':
      general = t.errors.forbidden
      break
    default:
      general = err.message || t.errors.generic
  }
  return { fields, general }
}

/** Les messages des contrôles du serveur sont en français, sauf ceux de Pydantic : on ne laisse pas de l'anglais brut. */
function translateServerMessage(d: { type?: string; msg?: string }, msg: string): string {
  if (d.type === 'missing') return t.form.required
  if (d.type === 'read_only') return t.form.readOnly
  // Types Pydantic courants : le message anglais n'aide pas l'utilisateur.
  if (d.type && /^(literal_error|string_type|bool_type|date|value_error|extra_forbidden)/.test(d.type)) return t.form.invalid
  return msg
}
