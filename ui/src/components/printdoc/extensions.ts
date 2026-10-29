import { Extension, Node, VueNodeViewRenderer } from '@tiptap/vue-3'
import { Table } from '@tiptap/extension-table'
import { reactive } from 'vue'
import type { DocTypeMeta } from '@/lib/types'
import EmbeddedBlockView from './EmbeddedBlockView.vue'
import PageBreakView from './PageBreakView.vue'
import PrintImageView from './PrintImageView.vue'
import ValueTokenView from './ValueTokenView.vue'

/**
 * Les nœuds propres au document d'impression (`process/impression.md` §12). Chacun ne produit, dans `getHTML()`,
 * que ce que le serveur accepte (`print_document.TAGS`) : jamais de `class`, de `src` ni de `href`. Ce qui
 * s'AFFICHE dans l'éditeur (la valeur d'un champ, l'image elle-même, l'aperçu d'un tableau) vient des vues
 * (`*View.vue`), qui lisent le contexte partagé `editor.storage.printDoc`.
 */
export interface PrintDocContext {
  meta: DocTypeMeta | null
  /** Les valeurs du document d'exemple (ou `null`) : la page montre la facture, pas des étiquettes. */
  example: Record<string, unknown> | null
  name: string | null
  linkTitle: (target: string, name: string) => string
  imageUrl: (fileId: string) => string | null
  editable: boolean
  /** L'échelle de la feuille à l'écran (1 = un millimètre fait 3,78 px) : pour convertir un glisser en millimètres. */
  scale: number
  /** La zone imprimable (mm) : une image posée librement ne sort pas du papier. */
  area: { width: number; height: number; left: number; top: number; right: number; bottom: number }
}

// TipTap RECOPIE l'objet de stockage d'une extension au premier niveau (`{ ...addStorage() }`) : un `reactive`
// posé là perdrait sa réactivité, et les vues ne verraient jamais arriver le document d'exemple. Le contexte
// réactif vit donc UN niveau plus bas, où la copie garde la même référence.
export const PrintDocStorage = Extension.create<object, { ctx: PrintDocContext }>({
  name: 'printDoc',
  addStorage() {
    return { ctx: reactive<PrintDocContext>({ meta: null, example: null, name: null, linkTitle: (_t, n) => n, imageUrl: () => null, editable: true, scale: 1, area: { width: 180, height: 257, left: 15, top: 20, right: 15, bottom: 20 } }) }
  },
})

/** Le contexte partagé de CET éditeur, lu par les vues des nœuds. */
export function docContext(editor: { storage: unknown }): PrintDocContext {
  return (editor.storage as Record<string, { ctx: PrintDocContext }>).printDoc!.ctx
}

/** Une VALEUR du document dans le texte : `<span data-value="doc.client">Client</span>`, remplacée à l'impression. */
export const ValueToken = Node.create({
  name: 'valueToken',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      value: { default: 'name', parseHTML: (el) => el.getAttribute('data-value'), renderHTML: (a) => ({ 'data-value': a.value }) },
      label: { default: '', parseHTML: (el) => el.textContent ?? '', renderHTML: () => ({}) },
    }
  },
  // Avant le `span` du style de texte (priorité 50 par défaut) : un champ inséré n'est pas un texte coloré.
  parseHTML: () => [{ tag: 'span[data-value]', priority: 100 }],
  renderHTML: ({ node, HTMLAttributes }) => ['span', HTMLAttributes, String(node.attrs.label || node.attrs.value)],
  addNodeView: () => VueNodeViewRenderer(ValueTokenView),
})

const mm = (raw: string | undefined | null): number | null => {
  const m = /^(-?\d+(?:\.\d+)?)mm$/.exec((raw ?? '').trim())
  return m ? Number(m[1]) : null
}

/** Une IMAGE du gabarit : dans le texte, habillée à gauche/droite, ou « devant le texte » posée en millimètres. */
export const PrintImage = Node.create({
  name: 'printImage',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      file: { default: '', parseHTML: (el) => el.getAttribute('data-file') ?? '' },
      wrap: { default: 'inline', parseHTML: (el) => el.getAttribute('data-wrap') ?? 'inline' },
      width: { default: 40, parseHTML: (el) => mm((el as HTMLElement).style.width) ?? 40 },
      left: { default: 0, parseHTML: (el) => mm((el as HTMLElement).style.left) ?? 0 },
      top: { default: 0, parseHTML: (el) => mm((el as HTMLElement).style.top) ?? 0 },
    }
  },
  parseHTML: () => [{ tag: 'img[data-file]' }],
  renderHTML: ({ node }) => {
    const a = node.attrs
    const style = [`width: ${a.width}mm`]
    if (a.wrap === 'front') style.push(`left: ${a.left}mm`, `top: ${a.top}mm`)
    return ['img', { 'data-file': a.file, 'data-wrap': a.wrap, style: style.join('; ') }]
  },
  addNodeView: () => VueNodeViewRenderer(PrintImageView),
})

/** Un bloc du vocabulaire, INCORPORÉ : le tableau des lignes, les totaux, une grille de champs. */
export const EmbeddedBlock = Node.create({
  name: 'embeddedBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      block: {
        default: { type: 'totals', fields: [] },
        parseHTML: (el) => {
          try {
            return JSON.parse(el.getAttribute('data-block') ?? '{}')
          } catch {
            return { type: 'totals', fields: [] }
          }
        },
        renderHTML: (a) => ({ 'data-block': JSON.stringify(a.block) }),
      },
    }
  },
  parseHTML: () => [{ tag: 'div[data-block]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', HTMLAttributes],
  addNodeView: () => VueNodeViewRenderer(EmbeddedBlockView),
})

export const PageBreak = Node.create({
  name: 'pageBreak',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  parseHTML: () => [{ tag: 'div[data-page-break]' }],
  renderHTML: () => ['div', { 'data-page-break': '' }],
  addNodeView: () => VueNodeViewRenderer(PageBreakView),
})

/** Un tableau de MISE EN PAGE (deux colonnes : le logo à gauche, l'adresse à droite), avec ou sans bordures. */
export const LayoutTable = Table.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      borders: {
        default: 'all',
        parseHTML: (el) => (el.getAttribute('data-borders') === 'none' ? 'none' : 'all'),
        renderHTML: (a) => ({ 'data-borders': a.borders }),
      },
    }
  },
})

