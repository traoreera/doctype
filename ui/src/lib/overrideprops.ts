import type { FieldBase, FieldMeta, FieldTweak, FieldType, FormOverride } from './types'

/**
 * Régler les propriétés d'écriture d'un champ dans l'habillage d'un formulaire (« Obligatoire », « Lecture seule », « Masqué », « Valeur par
 * défaut ») : des fonctions pures sur la configuration. Le SERVEUR valide et applique (`overrides.py`, `_tightening`) ; le Desk ne fait que
 * griser ce que le serveur refuserait de toute façon, pour qu'on ne découvre pas l'erreur à l'enregistrement.
 */

export type PropKey = 'mandatory' | 'read_only' | 'hidden' | 'default'

/**
 * Pourquoi une case est grisée : imposée par la `definition` ; `opaque` (le serveur écrit ce champ à sa façon) ; `condition` (une condition du
 * YAML l'exige ou le verrouille) ; `reserved` (règle réservée à qui écrit TOUS les documents du type) ; `unfilled` (masqué, un champ obligatoire
 * sans défaut ne se remplirait plus).
 */
export type LockReason = 'definition' | 'opaque' | 'condition' | 'reserved' | 'unfilled'

/** Les champs que le serveur écrit à sa façon (miroir de `overrides._opaque`) : ni obligatoires, ni verrouillés, ni pré-remplis par un habillage. */
const OPAQUE: readonly FieldType[] = ['Table', 'Password', 'Image', 'Attach']
/** Les types qui n'ont pas de valeur par défaut (miroir de `schema.NO_DEFAULT_TYPES`). */
const NO_DEFAULT: readonly FieldType[] = ['JSON', 'TextEditor', 'DynamicLink']

export const isOpaque = (f: FieldMeta): boolean => OPAQUE.includes(f.fieldtype) || !!f.fetch_from

/** Un défaut « vivant » : la date du jour (Date) ou l'instant de la création (Datetime), résolus par le serveur à chaque création. */
export const liveDefault = (f: FieldMeta): 'today' | 'now' | null => (f.fieldtype === 'Date' ? 'today' : f.fieldtype === 'Datetime' ? 'now' : null)

export interface PropRow {
  field: FieldMeta
  /** Ce que la DÉFINITION (le YAML) impose déjà : la case est cochée et grisée, un habillage ne la décoche pas. `default` : celui du YAML. */
  base: { required: boolean; readOnly: boolean; hidden: boolean; default: unknown }
  /** L'état affiché : celui de la définition, resserré par l'habillage. */
  mandatory: boolean
  readOnly: boolean
  hidden: boolean
  /** Le défaut de l'habillage (`undefined` : celui de la définition s'applique). */
  default: unknown
  /** Ce qu'on peut changer ici sans que le serveur le refuse. */
  can: Record<PropKey, boolean>
  /** Pour une case grisée, quand on sait le dire (sinon : obligatoire et masqué ou verrouillé s'excluent, la ligne le montre). */
  why: Partial<Record<PropKey, LockReason>>
}

export interface PropContext {
  /** Ce que la définition impose, habillage à part (`GET /override/form`, clé `definition`) : exact. */
  definition?: Record<string, FieldBase> | null
  /**
   * Sans `definition` (une lecture qui a échoué), on la DÉDUIT : `fields` vient de `/meta`, où l'habillage enregistré (`applied`) est déjà
   * appliqué ; un champ obligatoire dans `/meta` sans `mandatory` dans cet habillage l'est par le YAML. Approché : une retouche redondante
   * (obligatoire ET dans le YAML et dans l'habillage) passe pour une retouche de l'habillage.
   */
  applied?: FormOverride | null
  /** Le champ qui nomme le document (`naming: {field}`) : comme une valeur unique, il ne prend pas de défaut. */
  naming?: string | null
  /**
   * Peut-on régler obligatoire, lecture seule et défaut ? Ils s'imposent à tous : le serveur les réserve à qui peut modifier TOUS les documents
   * du type (`rights.write === 'all'` de `/meta`, le périmètre même qu'il vérifie ; refus `form_rules_reserved`). `false` : les cases restent
   * telles que l'habillage les a posées, grisées ; libellés, masquage et disposition restent permis. Absent : permis.
   */
  rules?: boolean
}

const isUnset = (v: unknown): boolean => v === undefined || v === null || v === ''

/** Le champ peut-il recevoir un défaut d'habillage (miroir de `overrides._no_default`) ? Une valeur unique (ou qui nomme le document) ne se
 *  devine pas : chaque création sans valeur recevrait la même. */
const defaultable = (field: FieldMeta, ctx: PropContext): boolean =>
  !isOpaque(field) && !NO_DEFAULT.includes(field.fieldtype) && !field.unique && field.fieldname !== ctx.naming

function baseOf(field: FieldMeta, ctx: PropContext): PropRow['base'] {
  const def = ctx.definition
  if (def) {
    const b = def[field.fieldname] ?? {}
    return { required: b.required === true, readOnly: b.read_only === true, hidden: b.hidden === true, default: b.default }
  }
  const was: FieldTweak = ctx.applied?.fields?.[field.fieldname] ?? {}
  return {
    required: field.required === true && was.mandatory !== true,
    readOnly: field.read_only === true && was.read_only !== true,
    hidden: field.hidden === true && was.hidden !== true,
    default: was.default === undefined ? field.default : undefined,
  }
}

/** Une ligne par champ. `config` : l'habillage en cours d'édition, dont on montre l'état. */
export function propRows(fields: readonly FieldMeta[], config: FormOverride | null | undefined, ctx: PropContext = {}): PropRow[] {
  const tweaks = config?.fields ?? {}
  const rules = ctx.rules !== false
  return fields.map((field) => {
    const tw: FieldTweak = tweaks[field.fieldname] ?? {}
    const base = baseOf(field, ctx)
    const mandatory = base.required || tw.mandatory === true
    const readOnly = base.readOnly || tw.read_only === true
    const hidden = base.hidden || tw.hidden === true
    const opaque = isOpaque(field)
    const hasDefault = !isUnset(base.default) || !isUnset(tw.default)
    // Exigé par le YAML, toujours ou sous condition : masqué sans défaut, personne ne le remplirait (miroir de `overrides._unhideable`).
    const duty = base.required || !!field.mandatory_depends_on
    // Ce que la définition impose ne se décoche pas. Sinon, une retouche posée se retire toujours (sa case reste cliquable) ; les
    // conditions ne valent que pour en poser une. Obligatoire, lecture seule et défaut : seulement pour qui peut régler les règles (`rules`).
    const can: Record<PropKey, boolean> = {
      // Un champ obligatoire doit pouvoir être rempli : ni caché, ni en lecture seule, ni verrouillé par sa condition.
      mandatory: rules && !base.required && (tw.mandatory === true || (!opaque && !hidden && !readOnly && !field.read_only_depends_on)),
      // Un champ obligatoire ne se verrouille qu'avec une valeur par défaut (le Desk ne saisit pas un champ verrouillé, même à la création),
      // et jamais quand une condition le rend obligatoire (il ne se remplirait plus ce jour-là).
      read_only:
        rules && !base.readOnly && (tw.read_only === true || (!opaque && tw.mandatory !== true && !field.mandatory_depends_on && (!base.required || hasDefault))),
      hidden: !base.hidden && (tw.hidden === true || (tw.mandatory !== true && (!duty || hasDefault))),
      default: rules && defaultable(field, ctx),
    }
    const why: PropRow['why'] = {}
    const lock = (key: PropKey, reason: LockReason | null): void => {
      if (!can[key] && reason) why[key] = reason
    }
    lock('mandatory', base.required ? 'definition' : opaque ? 'opaque' : !rules ? 'reserved' : field.read_only_depends_on ? 'condition' : null)
    lock('read_only', base.readOnly ? 'definition' : opaque ? 'opaque' : !rules ? 'reserved' : field.mandatory_depends_on ? 'condition' : null)
    lock('hidden', base.hidden ? 'definition' : duty && !hasDefault ? 'unfilled' : null)
    lock('default', defaultable(field, ctx) && !rules ? 'reserved' : null)
    return { field, base, mandatory, readOnly, hidden, default: tw.default ?? undefined, can, why }
  })
}

/**
 * La configuration avec une propriété changée, sans toucher au reste (disposition, libellés). Une case décochée ou un défaut vidé RETIRE
 * la retouche (la définition reprend la main) ; un champ sans retouche disparaît, et `fields` aussi quand il n'en reste aucun.
 */
export function withProp(config: FormOverride | null | undefined, fieldname: string, key: PropKey, value: unknown): FormOverride {
  const out: FormOverride = { ...(config ?? {}) }
  const fields: Record<string, FieldTweak> = { ...(out.fields ?? {}) }
  const tw: FieldTweak = { ...(fields[fieldname] ?? {}) }
  const keep = key === 'default' ? !isUnset(value) : value === true
  if (keep) (tw as Record<string, unknown>)[key] = key === 'default' ? value : true
  else delete tw[key]
  if (Object.keys(tw).length) fields[fieldname] = tw
  else delete fields[fieldname]
  if (Object.keys(fields).length) out.fields = fields
  else delete out.fields
  return out
}
