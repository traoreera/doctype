import type { FieldBase, FieldMeta, FieldTweak, FieldType, FormOverride } from './types'

/**
 * Régler les propriétés d'écriture d'un champ dans l'habillage d'un formulaire (« Obligatoire », « Lecture seule », « Masqué », « Valeur par
 * défaut ») : des fonctions pures sur la configuration. Le SERVEUR valide et applique (`overrides.py`, `_tightening`) ; le Desk ne fait que
 * griser ce que le serveur refuserait de toute façon, pour qu'on ne découvre pas l'erreur à l'enregistrement.
 */

export type PropKey = 'mandatory' | 'read_only' | 'hidden' | 'default'

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
}

const isUnset = (v: unknown): boolean => v === undefined || v === null || v === ''

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
  return fields.map((field) => {
    const tw: FieldTweak = tweaks[field.fieldname] ?? {}
    const base = baseOf(field, ctx)
    const mandatory = base.required || tw.mandatory === true
    const readOnly = base.readOnly || tw.read_only === true
    const hidden = base.hidden || tw.hidden === true
    const opaque = isOpaque(field)
    const hasDefault = !isUnset(base.default) || !isUnset(tw.default)
    return {
      field,
      base,
      mandatory,
      readOnly,
      hidden,
      default: tw.default ?? undefined,
      // Ce que la définition impose ne se décoche pas. Sinon, une retouche posée se retire toujours (sa case reste cliquable) ; les
      // conditions ne valent que pour en poser une.
      can: {
        // Un champ obligatoire doit pouvoir être rempli : ni caché, ni en lecture seule.
        mandatory: !base.required && (tw.mandatory === true || (!opaque && !hidden && !readOnly)),
        // Un champ obligatoire ne se verrouille qu'avec une valeur par défaut : le Desk ne saisit pas un champ verrouillé, même à la création.
        read_only: !base.readOnly && (tw.read_only === true || (!opaque && tw.mandatory !== true && (!base.required || hasDefault))),
        hidden: !base.hidden && (tw.hidden === true || tw.mandatory !== true),
        // Une valeur unique (ou qui nomme le document) ne se devine pas : chaque création sans valeur recevrait la même.
        default: !opaque && !NO_DEFAULT.includes(field.fieldtype) && !field.unique && field.fieldname !== ctx.naming,
      },
    }
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
