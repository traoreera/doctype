import type { ChartBlock, ExtensionBlock, LinksCardBlock, NumberCardBlock, QuickListBlock, WorkspaceBlock, WorkspaceHeader, WorkspaceParagraph, WorkspaceShortcut, WorkspaceSpacer } from './types'

/**
 * Éditer les blocs d'un espace : des fonctions pures sur une liste. Le Desk n'invente aucune règle de disposition : il manipule la
 * DESCRIPTION des blocs (ce que le YAML d'un plugin dit) et le serveur la valide à l'enregistrement, avec les règles du chargement.
 */

/** Un raccourci tel qu'on l'écrit : `new` demande un « + » (le serveur l'offre seulement à qui peut créer). */
export interface DeclaredShortcut {
  type: 'shortcut'
  doctype: string
  label?: string
  new?: boolean
}

export type DeclaredQuickList = Omit<QuickListBlock, 'title_field' | 'submittable'> & { doctype_label?: never }
export type DeclaredCard = { type: 'card'; title: string; links: { doctype: string; label?: string }[]; cols?: number }

export type DeclaredBlock = WorkspaceHeader | WorkspaceParagraph | WorkspaceSpacer | DeclaredShortcut | NumberCardBlock | ChartBlock | DeclaredQuickList | DeclaredCard | ExtensionBlock

export type BlockKind = DeclaredBlock['type']

/** Les largeurs d'une grille de 12 colonnes (un quart, un tiers, la moitié, deux tiers, la ligne). */
export const SIZES = [3, 4, 6, 8, 12] as const

export const MAX_BLOCKS = 100
/** Un espace composé (son fichier + les contributions d'autres plugins, D27) s'habille jusqu'à cette taille (serveur : `MAX_COMPOSED_BLOCKS`). */
export const MAX_COMPOSED_BLOCKS = 1000

const DEFAULT_COLS: Partial<Record<BlockKind, number>> = { number_card: 3, chart: 6, quick_list: 6, card: 4, paragraph: 12, extension: 6 }

/** La largeur d'un bloc dans la grille d'ÉDITION : un titre ou un espacement prend la ligne, un raccourci un quart. */
export function colsOf(block: DeclaredBlock): number {
  if (block.type === 'header' || block.type === 'spacer') return 12
  if (block.type === 'shortcut') return 3
  return ('cols' in block && block.cols) || DEFAULT_COLS[block.type] || 6
}

/** Un bloc a-t-il une largeur à choisir ? (un titre, un espacement et un raccourci n'en ont pas) */
export const hasSize = (block: DeclaredBlock): boolean => !['header', 'spacer', 'shortcut'].includes(block.type)

/** Un bloc a-t-il quelque chose à modifier dans la fenêtre ? (un espacement, non) */
export const isEditable = (block: DeclaredBlock): boolean => block.type !== 'spacer' && block.type !== 'extension' // un bloc d'extension se règle dans son plugin

/**
 * Ce que le serveur renvoie → ce qu'on lui renvoie. Le serveur AJOUTE des champs de lecture (`create`, `icon`, `single` d'un raccourci ;
 * `title_field`, `submittable` d'une liste) que son modèle refuse en écriture (`extra=forbid`) : on les retire, et `create` redevient `new`.
 */
export function declare(block: WorkspaceBlock): DeclaredBlock {
  switch (block.type) {
    case 'shortcut': {
      const s = block as WorkspaceShortcut
      return { type: 'shortcut', doctype: s.doctype, label: s.label, ...(s.create ? { new: true } : {}) }
    }
    case 'quick_list': {
      const { title_field: _t, submittable: _s, ...rest } = block as QuickListBlock & { doctype_label?: string }
      delete (rest as { doctype_label?: string }).doctype_label
      return rest as DeclaredQuickList
    }
    case 'card': {
      const c = block as LinksCardBlock
      return { type: 'card', title: c.title, ...(c.cols ? { cols: c.cols } : {}), links: c.links.map((l) => ({ doctype: l.doctype, label: l.label })) }
    }
    default:
      return { ...block } as DeclaredBlock
  }
}

/** Le même bloc, avec les champs de LECTURE qu'une carte du Desk attend (libellé, icône, titre d'un document) : l'aperçu pendant l'édition. */
export interface Known {
  label?: string
  icon?: string | null
  single?: boolean
  title_field?: string
  submittable?: boolean
}

export function preview(block: DeclaredBlock, known: (doctype: string) => Known | undefined): WorkspaceBlock {
  switch (block.type) {
    case 'shortcut': {
      const k = known(block.doctype)
      return { type: 'shortcut', doctype: block.doctype, label: block.label || k?.label || block.doctype, icon: k?.icon ?? null, create: false, single: k?.single }
    }
    case 'quick_list': {
      const k = known(block.doctype)
      return { ...block, title_field: k?.title_field, submittable: k?.submittable }
    }
    case 'card':
      return { type: 'card', title: block.title, cols: block.cols, links: block.links.map((l) => ({ doctype: l.doctype, label: l.label || known(l.doctype)?.label || l.doctype, icon: known(l.doctype)?.icon ?? null })) }
    default:
      return block
  }
}

/** Le bloc `from` prend la place du bloc `to` (les autres se décalent) : ce que fait un glisser-déposer, et les flèches (`to = from ± 1`). */
export function moveBlock<T>(blocks: readonly T[], from: number, to: number): T[] {
  const out = [...blocks]
  if (from === to || from < 0 || from >= out.length || to < 0 || to >= out.length) return out
  const [moved] = out.splice(from, 1)
  out.splice(to, 0, moved!)
  return out
}

export const removeBlock = <T>(blocks: readonly T[], index: number): T[] => blocks.filter((_, i) => i !== index)

export const replaceBlock = <T>(blocks: readonly T[], index: number, block: T): T[] => blocks.map((b, i) => (i === index ? block : b))

export function resizeBlock(blocks: readonly DeclaredBlock[], index: number, cols: number): DeclaredBlock[] {
  return blocks.map((b, i) => (i === index && hasSize(b) ? ({ ...b, cols } as DeclaredBlock) : b))
}

/** Un bloc neuf, avec des valeurs que le serveur accepte (le type de document est à choisir : `doctype` vide tant qu'il ne l'est pas). */
export function blankBlock(kind: BlockKind, doctype = ''): DeclaredBlock {
  switch (kind) {
    case 'number_card':
      return { type: 'number_card', label: '', doctype, function: 'count', color: 'blue', cols: 3 }
    case 'chart':
      return { type: 'chart', label: '', doctype, kind: 'bar', function: 'count', group_by: '', limit: 10, cols: 6 }
    case 'quick_list':
      return { type: 'quick_list', label: '', doctype, limit: 5, cols: 6 }
    case 'card':
      return { type: 'card', title: '', links: [], cols: 4 }
    case 'shortcut':
      return { type: 'shortcut', doctype, new: true }
    case 'header':
      return { type: 'header', text: '' }
    case 'paragraph':
      return { type: 'paragraph', text: '', cols: 12 }
    case 'spacer':
      return { type: 'spacer' }
    case 'extension':
      return { type: 'extension', component: '', cols: 6 } // jamais proposé à l'ajout : un bloc d'extension vient du YAML de son plugin
  }
}

/** Les graphiques qui disent « la part de chacun » : une moyenne ou un extremum ne s'additionne pas (le serveur les refuse). */
export const isPartToWhole = (kind: string): boolean => kind === 'pie' || kind === 'donut' || kind === 'percentage'

/** Ce qui manque à un bloc pour être enregistré (le serveur recontrôle tout : ceci évite seulement un aller-retour évident). */
export function missing(block: DeclaredBlock): 'label' | 'doctype' | 'group_by' | 'field' | 'links' | 'text' | null {
  switch (block.type) {
    case 'number_card':
      if (!(block.label ?? '').trim()) return 'label'
      if (!block.doctype) return 'doctype'
      return block.function !== 'count' && !block.field ? 'field' : null
    case 'chart':
      if (!(block.label ?? '').trim()) return 'label'
      if (!block.doctype) return 'doctype'
      if (!block.group_by) return 'group_by'
      return block.function !== 'count' && !block.field ? 'field' : null
    case 'quick_list':
      return !(block.label ?? '').trim() ? 'label' : !block.doctype ? 'doctype' : null
    case 'card':
      return !(block.title ?? '').trim() ? 'label' : block.links.length === 0 ? 'links' : null
    case 'shortcut':
      return block.doctype ? null : 'doctype'
    case 'header':
    case 'paragraph':
      return (block.text ?? '').trim() ? null : 'text'
    case 'spacer':
    case 'extension':
      return null
  }
}
