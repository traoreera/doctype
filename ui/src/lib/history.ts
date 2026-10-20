/** Mise en texte d'une entrée d'historique. Sans interface : testé seul. */
import { t } from '@/i18n'
import { formatCell } from './format'
import type { DocTypeMeta, FieldMeta, VersionEntry } from './types'

export interface ChangeLine {
  field: string
  label: string
  old: string
  new: string
}

/**
 * Un champ Table se résume par ses lignes, pas par son contenu : « 2 lignes → 3 lignes · 1 ajoutée, 1 modifiée ».
 * Les lignes se comparent par position (elles n'ont pas d'identité) : ajoutées/supprimées en bout, modifiées au milieu.
 */
export function summarizeRows(before: unknown, after: unknown): { old: string; new: string } {
  const a = Array.isArray(before) ? before : []
  const b = Array.isArray(after) ? after : []
  const common = Math.min(a.length, b.length)
  let changed = 0
  for (let i = 0; i < common; i++) if (JSON.stringify(a[i]) !== JSON.stringify(b[i])) changed++
  const details = [
    b.length > a.length ? t.history.rowsAdded(b.length - a.length) : '',
    a.length > b.length ? t.history.rowsRemoved(a.length - b.length) : '',
    changed ? t.history.rowsChanged(changed) : '',
  ].filter(Boolean)
  const count = (rows: unknown[]) => (rows.length ? t.history.rows(rows.length) : t.history.nothing)
  return { old: count(a), new: details.length ? `${count(b)} · ${details.join(', ')}` : count(b) }
}

/** Le texte d'une valeur d'avant ou d'après. `links` résout les Link en titres ; un tableau se résume par son nombre de lignes. */
export function showValue(field: FieldMeta | undefined, value: unknown, links?: (target: string, name: string) => string): string {
  if (value === null || value === undefined || value === '') return t.history.nothing
  if (Array.isArray(value)) return t.history.rows(value.length)
  if (!field) return String(value)
  if (field.fieldtype === 'Link' && typeof field.options === 'string' && typeof value === 'string') return links?.(field.options, value) ?? value
  return formatCell(field, value) || t.history.nothing
}

/** Les champs modifiés par une entrée, dans l'ordre de la définition (les champs disparus de la définition suivent, par nom). */
export function changeLines(meta: DocTypeMeta, entry: VersionEntry, links?: (target: string, name: string) => string): ChangeLine[] {
  const known = new Map(meta.fields.map((f, i) => [f.fieldname, { f, i }]))
  return Object.entries(entry.changes)
    .sort(([a], [b]) => (known.get(a)?.i ?? 1e9) - (known.get(b)?.i ?? 1e9) || a.localeCompare(b))
    .map(([name, change]) => {
      const field = known.get(name)?.f
      if (field?.fieldtype === 'Table' || Array.isArray(change.old) || Array.isArray(change.new)) {
        return { field: name, label: field?.label ?? name, ...summarizeRows(change.old, change.new) }
      }
      return {
        field: name,
        label: field?.label ?? (name === 'name' ? t.form.nameLabel : name),
        old: showValue(field, change.old, links),
        new: showValue(field, change.new, links),
      }
    })
}

/** Les valeurs Link citées par des entrées, par DocType cible : de quoi résoudre leurs titres d'un coup. */
export function linkValues(meta: DocTypeMeta, entries: readonly VersionEntry[]): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  for (const e of entries) {
    for (const [name, change] of Object.entries(e.changes)) {
      const f = meta.fields.find((x) => x.fieldname === name)
      if (f?.fieldtype !== 'Link' || typeof f.options !== 'string') continue
      for (const v of [change.old, change.new]) if (typeof v === 'string' && v) (out.get(f.options) ?? out.set(f.options, new Set()).get(f.options)!).add(v)
    }
  }
  return out
}

const VERBS = { create: 'created', update: 'updated', delete: 'deleted', submit: 'submitted', cancel: 'cancelled', rename: 'renamed' } as const
export const verbOf = (action: VersionEntry['action']): string => t.history[VERBS[action]]
