/**
 * L'éditeur de gabarits d'impression (`process/impression.md` §10) : le gabarit comme une PILE de blocs qu'on
 * manipule, et sa traduction exacte en ce que le serveur reçoit.
 *
 * Tout est pur ici, testé à part (`tests/printedit.spec.ts`) : la page affiche la pile, appelle ces fonctions —
 * qui rendent toujours une NOUVELLE pile — et demande l'aperçu au serveur. C'est le découpage de
 * `lib/formbuilder.ts`, qui a marché.
 *
 * **Une pile, pas une toile.** Les blocs se suivent ; on ne les place pas librement en deux dimensions. Ce
 * n'est pas une limite de l'éditeur mais du rendu : le moteur d'impression ne connaît pas CSS grid, et c'est
 * précisément ce qui permet de garantir ce qui sortira (§1). Un éditeur qui laisserait poser un bloc n'importe
 * où promettrait ce que le PDF ne tiendrait pas.
 *
 * Les règles du serveur (`printing.py`, §8) sont tenues ICI, avant l'enregistrement, pour qu'on ne les
 * découvre pas par un refus : 100 blocs au plus, 12 colonnes par tableau, des largeurs de 1 à 100 dont la
 * somme ne dépasse pas 100, un `subtotal` qui exige un `group_by` et ne nomme que des colonnes présentes, et
 * aucun champ qui ne s'imprime pas (secret, fichier, structure).
 */
import type { DocTypeMeta, FieldMeta } from './types'

export const MAX_BLOCKS = 100
export const MAX_COLUMNS = 12
export const MAX_FIELDS = 60
export const MAX_TEXT = 2000
/** Le type MIME d'un bloc qu'on glisse dans la pile. */
export const DRAG_BLOCK = 'text/x-xdt-print-block'

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
      columns: Array.isArray(b.columns) ? (b.columns as Record<string, unknown>[]).map((c) => ({ ...(c as unknown as Column), key: key('c') })) : undefined,
    })),
  }
}

/** Ce qu'on envoie au serveur : les clés d'édition retirées, les blocs vides écartés. */
export function toTemplate(draft: Draft): Record<string, unknown> {
  const blocks = draft.blocks.filter(isUseful).map((b) => {
    const { key: _k, ...rest } = b
    const out: Record<string, unknown> = { ...rest }
    if (Array.isArray(b.columns)) out.columns = b.columns.map(({ key: _ck, ...c }) => prune(c as unknown as Record<string, unknown>))
    if (b.type !== 'table') {
      delete out.field
      delete out.columns
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
  })
  const out: Record<string, unknown> = { name: draft.name, page: { ...draft.page }, blocks }
  if (draft.label) out.label = draft.label
  if (draft.letterhead && (draft.letterhead.name || draft.letterhead.pdf)) out.letterhead = prune({ ...draft.letterhead })
  return out
}

const prune = (o: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && !v.length)))

/**
 * Un bloc qui n'imprimerait RIEN n'est pas envoyé : un tableau sans colonne, une liste de champs vide, un
 * titre sans texte. Le serveur les refuserait (`min_length`), et un éditeur ne doit pas obliger à finir un
 * bloc qu'on vient d'ajouter avant de pouvoir enregistrer le reste.
 */
export function isUseful(b: Block): boolean {
  if (b.type === 'fields' || b.type === 'totals') return !!b.fields?.length
  if (b.type === 'table') return !!b.field && tableColumns(b).length > 0
  if (b.type === 'title' || b.type === 'text') return !!b.text?.trim()
  return true // spacer, page_break : toujours utiles
}

/** Un bloc neuf du genre demandé, avec les valeurs par défaut du serveur. */
export function newBlock(type: BlockKind, meta?: DocTypeMeta): Block {
  const base: Block = { key: key('b'), type }
  if (type === 'title') return { ...base, text: '{{ doctype }} {{ name }}' }
  if (type === 'text') return { ...base, text: '' }
  if (type === 'fields') return { ...base, fields: [] }
  if (type === 'totals') return { ...base, fields: [] }
  if (type === 'spacer') return { ...base, size: 5 }
  if (type === 'table') {
    const table = meta?.fields.find((f) => f.fieldtype === 'Table')
    return { ...base, field: table?.fieldname, columns: [] as Column[], style: 'bordered', density: 'normal', repeat_header: true }
  }
  return base
}

// ── Déplacer, ajouter, retirer ──────────────────────────────
export function addBlock(draft: Draft, block: Block, at: number | null = null): Draft {
  const blocks = [...draft.blocks]
  blocks.splice(at === null ? blocks.length : Math.max(0, Math.min(at, blocks.length)), 0, block)
  return { ...draft, blocks }
}

export function removeBlock(draft: Draft, blockKey: string): Draft {
  return { ...draft, blocks: draft.blocks.filter((b) => b.key !== blockKey) }
}

export function moveBlock(draft: Draft, blockKey: string, to: number): Draft {
  const from = draft.blocks.findIndex((b) => b.key === blockKey)
  if (from < 0) return draft
  const blocks = [...draft.blocks]
  const [moved] = blocks.splice(from, 1)
  blocks.splice(Math.max(0, Math.min(to, blocks.length)), 0, moved!)
  return { ...draft, blocks }
}

/** Monter ou descendre d'un cran : l'équivalent clavier du glisser-déposer (une page doit s'utiliser sans souris). */
export function nudgeBlock(draft: Draft, blockKey: string, how: 'up' | 'down'): Draft {
  const at = draft.blocks.findIndex((b) => b.key === blockKey)
  if (at < 0) return draft
  const to = how === 'up' ? at - 1 : at + 1
  return to < 0 || to >= draft.blocks.length ? draft : moveBlock(draft, blockKey, to)
}

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

export const tableFields = (meta: DocTypeMeta): FieldMeta[] => meta.fields.filter((f) => f.fieldtype === 'Table')

/** Les colonnes possibles pour un tableau : les champs de ses LIGNES, hors structures et secrets. */
export function columnFields(meta: DocTypeMeta, tableField: string | undefined): FieldMeta[] {
  const table = meta.fields.find((f) => f.fieldname === tableField)
  const child = table && typeof table.options === 'string' ? meta.children?.[table.options] : undefined
  return (child?.fields ?? []).filter((f) => f.fieldtype !== 'Table' && !UNPRINTABLE.includes(f.fieldtype as never))
}

export const groupableColumns = (meta: DocTypeMeta, tableField: string | undefined): FieldMeta[] =>
  columnFields(meta, tableField).filter((f) => GROUPABLE.includes(f.fieldtype as never))

/**
 * Ce que le serveur refuserait, dit AVANT d'enregistrer. Les mêmes règles que `printing.py` §8 — un éditeur qui
 * laisse enregistrer puis montre un 422 fait deviner à l'utilisateur ce qu'un contrôle sait déjà.
 */
export function draftProblems(draft: Draft, meta: DocTypeMeta): string[] {
  const problems: string[] = []
  const useful = draft.blocks.filter(isUseful)
  if (!useful.length) problems.push('Un gabarit imprime au moins un bloc.')
  if (useful.length > MAX_BLOCKS) problems.push(`${MAX_BLOCKS} blocs au plus.`)
  const printable = new Set(printableFields(meta).map((f) => f.fieldname))
  for (const b of draft.blocks) {
    const where = blockLabel(b, meta)
    if (b.type === 'fields' || b.type === 'totals') {
      for (const f of b.fields ?? []) if (!printable.has(f)) problems.push(`${where} : « ${f} » ne s'imprime pas.`)
      if ((b.fields?.length ?? 0) > MAX_FIELDS) problems.push(`${where} : ${MAX_FIELDS} champs au plus.`)
    }
    if (b.type === 'table') {
      const columns = tableColumns(b)
      if (!b.field) problems.push(`${where} : choisissez le tableau à imprimer.`)
      if (columns.length > MAX_COLUMNS) problems.push(`${where} : ${MAX_COLUMNS} colonnes au plus.`)
      const widths = columns.map((c) => c.width ?? 0).reduce((a, b2) => a + b2, 0)
      if (widths > 100) problems.push(`${where} : la somme des largeurs dépasse 100 % (${widths} %).`)
      const named = new Set(columns.map((c) => c.field))
      for (const f of b.subtotal ?? []) {
        if (!named.has(f)) problems.push(`${where} : le sous-total « ${f} » ne correspond à aucune colonne.`)
      }
      if ((b.subtotal?.length ?? 0) && !b.group_by) problems.push(`${where} : un sous-total se calcule par groupe, choisissez un regroupement.`)
    }
    if ((b.type === 'title' || b.type === 'text') && (b.text?.length ?? 0) > MAX_TEXT) {
      problems.push(`${where} : ${MAX_TEXT} caractères au plus.`)
    }
  }
  return problems
}

export const KIND_LABELS: Record<BlockKind, string> = {
  title: 'Titre',
  fields: 'Champs',
  table: 'Tableau',
  totals: 'Totaux',
  text: 'Texte',
  spacer: 'Espace',
  page_break: 'Saut de page',
}

/** Comment nommer un bloc dans la pile et dans un message : son genre, et ce qu'il imprime. */
export function blockLabel(b: Block, meta?: DocTypeMeta): string {
  const kind = KIND_LABELS[b.type]
  if (b.type === 'table') {
    const table = meta?.fields.find((f) => f.fieldname === b.field)
    return `${kind} ${table?.label || b.field || '—'}`
  }
  if (b.type === 'fields' || b.type === 'totals') return `${kind} (${b.fields?.length ?? 0})`
  if (b.type === 'title' || b.type === 'text') return `${kind} ${(b.text ?? '').slice(0, 24)}`.trimEnd()
  return kind
}
