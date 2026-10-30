/**
 * Les BLOCS du vocabulaire d'impression (`process/impression.md` §4) et leur édition pure : ce que le document
 * libre (§12) incorpore — le tableau des lignes, les totaux, une grille de champs — se règle avec ces fonctions,
 * dans le panneau de droite de l'éditeur (`BlockPanel`). Le texte, lui, s'écrit dans l'éditeur (`lib/printdoc`).
 *
 * Tout est pur, testé à part (`tests/printedit.spec.ts`) : chaque fonction rend une NOUVELLE forme. Les règles
 * du serveur (`printing.py`, §8) sont tenues ici avant l'enregistrement : 12 colonnes par tableau, des largeurs
 * de 1 à 100 dont la somme ne dépasse pas 100, un `subtotal` qui exige un `group_by` et ne nomme que des colonnes
 * présentes, aucun champ qui ne s'imprime pas (secret, fichier, structure).
 */
import type { DocTypeMeta, FieldMeta } from './types'

export const MAX_BLOCKS = 100
export const MAX_COLUMNS = 12
export const MAX_FIELDS = 60
export const MAX_TEXT = 2000

/** Ce qu'un bloc ne peut pas imprimer : le serveur le refuse (`printing.UNPRINTABLE`). */
export const UNPRINTABLE = ['Password', 'JSON', 'TextEditor', 'Image', 'Attach'] as const
/** Ce qu'une colonne de tableau peut sommer, et ce qu'un regroupement accepte. */
export const NUMERIC = ['Number', 'Int', 'Percent', 'Decimal', 'Currency'] as const
export const GROUPABLE = ['Text', 'Select', 'Link', 'DynamicLink', 'Date', 'Boolean', 'Int'] as const

export type BlockKind = 'title' | 'fields' | 'table' | 'totals' | 'text' | 'spacer' | 'page_break'

export interface Column {
  key: string
  field: string
  label?: string | null
  align?: 'left' | 'right' | 'center' | null
  width?: number | null
}

export interface Block {
  /** Clé stable le temps de l'édition (Vue suit un bloc qu'on déplace) ; jamais envoyée. */
  key: string
  type: BlockKind
  text?: string
  label?: string | null
  /** `fields`, `totals` : les champs imprimés, dans cet ordre. */
  fields?: string[]
  /** `table` : le champ Table, ses colonnes, et sa présentation. */
  field?: string
  /**
   * Deux sens selon le genre de bloc, parce que le format du SERVEUR est ainsi : un NOMBRE de colonnes pour un
   * bloc `fields` (`FieldsBlock.columns`, 1 à 4), la LISTE des colonnes pour un `table` (`TableBlock.columns`).
   * On garde la même clé plutôt que d'en inventer une et de traduire dans les deux sens — les accesseurs
   * `tableColumns` et `fieldColumns` lèvent l'ambiguïté là où elle compte.
   */
  columns?: Column[] | number
  style?: 'bordered' | 'ruled' | 'plain'
  density?: 'compact' | 'normal'
  repeat_header?: boolean
  group_by?: string | null
  subtotal?: string[]
  /** `spacer` : millimètres. */
  size?: number
}

export interface Letterhead {
  /** Un `Letter Head` par son nom (`default` : celui que l'organisation a marqué), ou rien. */
  name?: string | null
  /** L'identifiant d'un fichier PDF du tenant : nos blocs s'impriment PAR-DESSUS (§5). */
  pdf?: string | null
  pages?: 'first' | 'all'
  offset_top?: number
  offset_bottom?: number
}

export interface Page {
  size: 'A3' | 'A4' | 'A5' | 'B4' | 'B5' | 'Letter' | 'Legal' | 'Ledger'
  orientation: 'portrait' | 'landscape'
  top: number
  bottom: number
  left: number
  right: number
}

export interface Draft {
  name: string
  label: string | null
  page: Page
  letterhead: Letterhead | null
  blocks: Block[]
}

let seq = 0
const key = (prefix: string): string => `${prefix}${++seq}`

/** Les colonnes d'un bloc `table` (jamais le nombre de colonnes d'un bloc `fields`). */
export const tableColumns = (b: Block): Column[] => (Array.isArray(b.columns) ? b.columns : [])
/** Le nombre de colonnes d'un bloc `fields`. */
export const fieldColumns = (b: Block): number => (typeof b.columns === 'number' ? b.columns : 2)

export const DEFAULT_PAGE: Page = { size: 'A4', orientation: 'portrait', top: 20, bottom: 20, left: 15, right: 15 }

/** Le gabarit tel que le serveur l'a rendu, en pile éditable. Les clés sont ajoutées, rien d'autre ne change. */
export function fromTemplate(raw: unknown): Draft {
  const t = (raw ?? {}) as Record<string, unknown>
  const page = { ...DEFAULT_PAGE, ...((t.page as Partial<Page>) ?? {}) }
  const blocks = Array.isArray(t.blocks) ? (t.blocks as Record<string, unknown>[]) : []
  return {
    name: typeof t.name === 'string' && t.name ? t.name : 'standard',
    label: typeof t.label === 'string' ? t.label : null,
    page,
    letterhead: (t.letterhead as Letterhead | null) ?? null,
    blocks: blocks.map((b) => ({
      ...(b as unknown as Block),
      key: key('b'),
      // Un bloc `fields` porte un NOMBRE de colonnes : le garder tel quel (le perdre ramenait tout gabarit à 2).
      columns: Array.isArray(b.columns)
        ? (b.columns as Record<string, unknown>[]).map((c) => ({ ...(c as unknown as Column), key: key('c') }))
        : typeof b.columns === 'number'
          ? b.columns
          : undefined,
    })),
  }
}

/** Un bloc dans la forme du serveur : clés d'édition retirées, propriétés d'un autre genre de bloc écartées. */
export function rawBlock(b: Block): Record<string, unknown> {
  const { key: _k, ...rest } = b
  const out: Record<string, unknown> = { ...rest }
  if (Array.isArray(b.columns)) out.columns = b.columns.map(({ key: _ck, ...c }) => prune(c as unknown as Record<string, unknown>))
  if (b.type !== 'table') {
    delete out.field
    // `columns` d'un bloc `fields` est son NOMBRE de colonnes (1-4), qu'il faut envoyer : le retirer ici
    // rendait le réglage « Colonnes » sans effet à l'impression.
    if (b.type !== 'fields' || typeof b.columns !== 'number') delete out.columns
    delete out.style
    delete out.density
    delete out.repeat_header
    delete out.group_by
    delete out.subtotal
  }
  if (b.type !== 'fields' && b.type !== 'totals') delete out.fields
  if (b.type !== 'spacer') delete out.size
  if (b.type !== 'title' && b.type !== 'text') delete out.text
  return prune(out)
}

const prune = (o: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && !v.length)))

export function updateBlock(draft: Draft, blockKey: string, patch: Partial<Block>): Draft {
  return { ...draft, blocks: draft.blocks.map((b) => (b.key === blockKey ? { ...b, ...patch } : b)) }
}

// ── Les champs d'un bloc `fields` / `totals` ───────────────
export function toggleField(draft: Draft, blockKey: string, field: string): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  if (!block) return draft
  const current = block.fields ?? []
  const next = current.includes(field) ? current.filter((f) => f !== field) : [...current, field].slice(0, MAX_FIELDS)
  return updateBlock(draft, blockKey, { fields: next })
}

export function moveFieldInBlock(draft: Draft, blockKey: string, field: string, how: 'up' | 'down'): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  const current = [...(block?.fields ?? [])]
  const at = current.indexOf(field)
  const to = how === 'up' ? at - 1 : at + 1
  if (at < 0 || to < 0 || to >= current.length) return draft
  current.splice(to, 0, current.splice(at, 1)[0]!)
  return updateBlock(draft, blockKey, { fields: current })
}

/** Poser un champ (venu de la liste des champs disponibles) à un emplacement d'un bloc. */
export function insertField(draft: Draft, blockKey: string, field: string, slot: number): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  const current = block?.fields ?? []
  if (!block || current.includes(field) || current.length >= MAX_FIELDS) return draft
  const next = [...current]
  next.splice(Math.max(0, Math.min(slot, next.length)), 0, field)
  return updateBlock(draft, blockKey, { fields: next })
}

// ── Les colonnes d'un tableau ──────────────────────────────
export function addColumn(draft: Draft, blockKey: string, field: string): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  if (!block) return draft
  const current = tableColumns(block)
  if (current.length >= MAX_COLUMNS || current.some((c) => c.field === field)) return draft
  return updateBlock(draft, blockKey, { columns: [...current, { key: key('c'), field }] })
}

export function removeColumn(draft: Draft, blockKey: string, columnKey: string): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  if (!block) return draft
  const columns = tableColumns(block).filter((c) => c.key !== columnKey)
  const gone = tableColumns(block).find((c) => c.key === columnKey)
  // Un sous-total ne survit pas à la colonne qu'il somme : le serveur le refuserait.
  const subtotal = (block.subtotal ?? []).filter((f) => f !== gone?.field)
  return updateBlock(draft, blockKey, { columns, subtotal })
}

export function moveColumn(draft: Draft, blockKey: string, columnKey: string, how: 'left' | 'right'): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  const columns = block ? [...tableColumns(block)] : []
  const at = columns.findIndex((c) => c.key === columnKey)
  const to = how === 'left' ? at - 1 : at + 1
  if (at < 0 || to < 0 || to >= columns.length) return draft
  columns.splice(to, 0, columns.splice(at, 1)[0]!)
  return updateBlock(draft, blockKey, { columns })
}

/**
 * La largeur d'une colonne qu'on tire à la souris, en % : arrondie, au moins 1, et jamais au point que la somme
 * des largeurs dépasse 100 (le serveur refuserait le gabarit). Les colonnes sans largeur ne comptent pas : elles
 * se partagent ce qui reste.
 */
export function setColumnWidth(draft: Draft, blockKey: string, columnKey: string, width: number): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  if (!block || !Number.isFinite(width)) return draft
  const others = tableColumns(block)
    .filter((c) => c.key !== columnKey)
    .reduce((sum, c) => sum + (c.width ?? 0), 0)
  const room = Math.max(1, 100 - others)
  return updateColumn(draft, blockKey, columnKey, { width: Math.max(1, Math.min(Math.round(width), room)) })
}

export function updateColumn(draft: Draft, blockKey: string, columnKey: string, patch: Partial<Column>): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  if (!block) return draft
  return updateBlock(draft, blockKey, { columns: tableColumns(block).map((c) => (c.key === columnKey ? { ...c, ...patch } : c)) })
}

/** Regrouper les lignes, ou ne plus les regrouper — ce qui emporte les sous-totaux, que le serveur lie au regroupement. */
export function setGroupBy(draft: Draft, blockKey: string, field: string | null): Draft {
  return updateBlock(draft, blockKey, field ? { group_by: field } : { group_by: null, subtotal: [] })
}

export function toggleSubtotal(draft: Draft, blockKey: string, field: string): Draft {
  const block = draft.blocks.find((b) => b.key === blockKey)
  if (!block || !block.group_by) return draft // un sous-total sans regroupement n'a pas de sens
  const current = block.subtotal ?? []
  return updateBlock(draft, blockKey, { subtotal: current.includes(field) ? current.filter((f) => f !== field) : [...current, field] })
}

// ── Ce que le serveur accepterait ──────────────────────────
/** Les champs du document qu'un bloc `fields` peut imprimer. */
export const printableFields = (meta: DocTypeMeta): FieldMeta[] =>
  meta.fields.filter((f) => f.fieldtype !== 'Table' && !UNPRINTABLE.includes(f.fieldtype as never))

/** Les champs qu'un bloc `totals` peut imprimer : des nombres, alignés à droite par le rendu. */
export const totalFields = (meta: DocTypeMeta): FieldMeta[] => meta.fields.filter((f) => NUMERIC.includes(f.fieldtype as never))

/** Les colonnes possibles pour un tableau : les champs de ses LIGNES, hors structures et secrets. */
export function columnFields(meta: DocTypeMeta, tableField: string | undefined): FieldMeta[] {
  const table = meta.fields.find((f) => f.fieldname === tableField)
  const child = table && typeof table.options === 'string' ? meta.children?.[table.options] : undefined
  return (child?.fields ?? []).filter((f) => f.fieldtype !== 'Table' && !UNPRINTABLE.includes(f.fieldtype as never))
}

export const groupableColumns = (meta: DocTypeMeta, tableField: string | undefined): FieldMeta[] =>
  columnFields(meta, tableField).filter((f) => GROUPABLE.includes(f.fieldtype as never))

export const KIND_LABELS: Record<BlockKind, string> = {
  title: 'Titre',
  fields: 'Champs',
  table: 'Tableau',
  totals: 'Totaux',
  text: 'Texte',
  spacer: 'Espace',
  page_break: 'Saut de page',
}

