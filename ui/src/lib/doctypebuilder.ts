/**
 * Le constructeur de DocTypes du mode développeur (doc/programme.md §3.5) : une définition ENTIÈRE qu'on bâtit à l'écran, puis qu'on
 * télécharge en YAML pour la poser dans le dossier `doctypes/` d'un plugin. Rien n'est enregistré : le serveur juge (`POST /devtools/check`)
 * et rend le YAML ; c'est tout.
 *
 * Tout est pur ici, testé à part (`tests/doctypebuilder.spec.ts`). La disposition se manipule avec le modèle du Form Builder
 * (`lib/formbuilder`), les champs et le type avec ces fonctions, qui rendent toujours de NOUVEAUX objets. Ce que le constructeur n'édite
 * pas (hooks, permissions, workflow, vues…) voyage tel quel : on ne perd rien d'un type existant en le modifiant ici.
 */
import { fromLayout, placed, toLayout, unplace, type Board } from './formbuilder'
import type { FieldType, LayoutSection } from './types'

/** Un champ tel qu'il s'écrit dans le YAML : les clés absentes prennent leur valeur par défaut. */
export interface DevField {
  fieldname: string
  fieldtype: FieldType
  label?: string
  [key: string]: unknown
}

export type Naming = 'hash' | 'owner' | 'prompt' | { series: string } | { field: string }

/** Une définition de DocType (le schéma du serveur, `schema.DocTypeDef`) ; le reste des clés est gardé tel quel. */
export interface DevDefinition {
  schema_version: 1
  name: string
  label?: string
  module?: string
  icon?: string
  naming?: Naming
  title_field?: string
  istable?: boolean
  submittable?: boolean
  single?: boolean
  track_changes?: boolean
  allow_rename?: boolean
  fields: DevField[]
  layout?: LayoutSection[]
  [key: string]: unknown
}

/** Tous les types de champ du moteur (`schema.FieldType`), groupés comme dans la palette. */
export const FIELD_GROUPS: { label: string; types: FieldType[] }[] = [
  { label: 'Texte', types: ['Text', 'LongText', 'TextEditor', 'Code', 'Color'] },
  { label: 'Nombres', types: ['Int', 'Number', 'Decimal', 'Currency', 'Percent', 'Rating', 'Duration'] },
  { label: 'Dates', types: ['Date', 'Datetime', 'Time'] },
  { label: 'Choix', types: ['Select', 'Boolean'] },
  { label: 'Liens', types: ['Link', 'DynamicLink', 'Table'] },
  { label: 'Fichiers et autres', types: ['Image', 'Attach', 'JSON', 'Password'] },
]

/** Ce que `options` veut dire pour ce type : des valeurs (une par ligne), un type de document, un type enfant, le champ qui nomme le type. */
export function optionsKind(type: FieldType): 'values' | 'doctype' | 'child' | 'typeField' | null {
  return type === 'Select' ? 'values' : type === 'Link' ? 'doctype' : type === 'Table' ? 'child' : type === 'DynamicLink' ? 'typeField' : null
}

/** Un nom de champ tel que le schéma l'exige (`[a-z][a-z0-9_]{0,62}`), tiré d'un libellé : « Date d'échéance » -> `date_d_echeance`. */
export function fieldnameOf(label: string): string {
  const body = label.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  const name = /^[a-z]/.test(body) ? body : body ? `f_${body}` : ''
  return name.slice(0, 63).replace(/_+$/, '')
}

/** `base`, ou `base_2`, `base_3`… s'il est pris. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}_${n}`.slice(0, 63))) n++
  return `${base}_${n}`.slice(0, 63)
}

/** Une définition neuve : un champ obligatoire (un type sans champ est refusé), un module à choisir. */
export function emptyDefinition(): DevDefinition {
  return { schema_version: 1, name: '', label: '', module: '', fields: [{ fieldname: 'title', fieldtype: 'Text', label: 'Titre', required: true, in_list_view: true }], title_field: 'title' }
}

/** Le tableau du Form Builder pour cette définition : sa disposition, ou une section avec tous les champs visibles dans l'ordre. */
export function boardOf(def: DevDefinition): Board {
  if (def.layout?.length) return fromLayout(def.layout)
  return fromLayout([{ section: null, collapsible: false, collapsed: false, columns: [def.fields.filter((f) => !f.hidden).map((f) => f.fieldname)] }])
}

/** Ajoute un champ de ce type (libellé par défaut : le type), nommé sans collision. */
export function addField(def: DevDefinition, fieldtype: FieldType, label = ''): { def: DevDefinition; name: string } {
  const text = label.trim() || `Nouveau ${fieldtype}`
  const name = uniqueName(fieldnameOf(text) || 'champ', def.fields.map((f) => f.fieldname))
  const field: DevField = { fieldname: name, fieldtype, label: text }
  if (fieldtype === 'Select') field.options = ['Option 1', 'Option 2']
  if (fieldtype === 'Decimal' || fieldtype === 'Currency') field.decimals = 2
  return { def: { ...def, fields: [...def.fields, field] }, name }
}

const isBlank = (v: unknown): boolean => v === undefined || v === null || v === '' || v === false || (Array.isArray(v) && !v.length)

/** Change des propriétés d'un champ. Une valeur vide (`''`, `false`, `[]`, `undefined`) RETIRE la clé : le YAML ne garde que ce qui compte. */
export function patchField(def: DevDefinition, name: string, patch: Record<string, unknown>): DevDefinition {
  return {
    ...def,
    fields: def.fields.map((f) => {
      if (f.fieldname !== name) return f
      const out: DevField = { ...f }
      for (const [k, v] of Object.entries(patch)) {
        if (k === 'fieldname' || k === 'fieldtype') continue // renommer et changer de type ont leurs fonctions
        if (isBlank(v)) delete out[k]
        else out[k] = v
      }
      return out
    }),
  }
}

/** Change le type d'un champ : les propriétés propres à l'ancien type (options, décimales, étoiles, format) partent avec lui. */
export function retypeField(def: DevDefinition, name: string, fieldtype: FieldType): DevDefinition {
  return {
    ...def,
    fields: def.fields.map((f) => {
      if (f.fieldname !== name || f.fieldtype === fieldtype) return f
      const { options: _o, decimals: _d, stars: _s, format: _f, default: _def, ...rest } = f
      const out: DevField = { ...rest, fieldtype }
      if (fieldtype === 'Select') out.options = ['Option 1', 'Option 2']
      if (fieldtype === 'Decimal' || fieldtype === 'Currency') out.decimals = 2
      return out
    }),
  }
}

/** Renomme un champ PARTOUT où le type le cite : disposition, champ titre, champ qui nomme, `fetch_from` et `options` d'un DynamicLink. */
export function renameField(def: DevDefinition, board: Board, from: string, to: string): { def: DevDefinition; board: Board } | null {
  if (!to || to === from || def.fields.some((f) => f.fieldname === to) || !/^[a-z][a-z0-9_]{0,62}$/.test(to)) return null
  const swap = (v: unknown) => (v === from ? to : v)
  const fields = def.fields.map((f) => {
    const out: DevField = { ...f, fieldname: swap(f.fieldname) as string }
    if (typeof f.fetch_from === 'string' && f.fetch_from.startsWith(`${from}.`)) out.fetch_from = `${to}.${f.fetch_from.slice(from.length + 1)}`
    if (f.fieldtype === 'DynamicLink') out.options = swap(f.options)
    return out
  })
  const naming = def.naming && typeof def.naming === 'object' && 'field' in def.naming ? { field: swap(def.naming.field) as string } : def.naming
  const next: DevDefinition = { ...def, fields, ...(def.title_field ? { title_field: swap(def.title_field) as string } : {}), ...(naming ? { naming } : {}) }
  const renamed: Board = { tabs: board.tabs.map((t) => ({ ...t, sections: t.sections.map((s) => ({ ...s, columns: s.columns.map((c) => c.map((n) => (n === from ? to : n))) })) })) }
  return { def: next, board: renamed }
}

/** Supprime un champ, et ce qui le citait (disposition, champ titre, champ qui nomme). */
export function removeField(def: DevDefinition, board: Board, name: string): { def: DevDefinition; board: Board } {
  const next: DevDefinition = { ...def, fields: def.fields.filter((f) => f.fieldname !== name) }
  if (next.title_field === name) delete next.title_field
  if (next.naming && typeof next.naming === 'object' && 'field' in next.naming && next.naming.field === name) delete next.naming
  return { def: next, board: unplace(board, name) }
}

/** La définition à juger et à télécharger : la disposition du tableau (un champ masqué n'y est jamais : le schéma le refuserait). */
export function assemble(def: DevDefinition, board: Board): DevDefinition {
  const hidden = new Set(def.fields.filter((f) => f.hidden).map((f) => f.fieldname))
  let b = board
  for (const name of placed(board)) if (hidden.has(name) || !def.fields.some((f) => f.fieldname === name)) b = unplace(b, name)
  const layout = toLayout(b)
  const out: DevDefinition = { ...def }
  if (layout.length) out.layout = layout
  else delete out.layout
  return out
}

/** Le nom du fichier à poser dans `doctypes/` : « Stock Entry » -> `stock_entry.yaml` (la convention des plugins du dépôt). */
export const yamlFileName = (def: DevDefinition): string => `${fieldnameOf(def.name || 'nouveau_type') || 'nouveau_type'}.yaml`

/** La valeur par défaut tapée, dans le type du champ : un entier pour Int/Rating/Duration, un nombre pour Number/Percent, sinon le texte
 *  (un décimal reste une chaîne canonique). Vide : pas de défaut. Le serveur juge ensuite, comme pour toute définition. */
export function typedDefault(fieldtype: FieldType, text: string): unknown {
  const v = text.trim()
  if (!v) return undefined
  if (['Int', 'Rating', 'Duration'].includes(fieldtype) && /^-?[0-9]+$/.test(v)) return Number(v)
  if (['Number', 'Percent'].includes(fieldtype) && /^-?([0-9]+\.?[0-9]*|\.[0-9]+)$/.test(v)) return Number(v)
  return v
}
