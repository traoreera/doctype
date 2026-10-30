/**
 * Le gabarit d'impression comme DOCUMENT LIBRE, façon traitement de texte (`process/impression.md` §12).
 *
 * Pur, comme `lib/printedit.ts` : aucune requête, aucun composant. Trois choses :
 *
 * - **les styles** que le serveur accepte (`print_document.STYLES`), pour NETTOYER ce qu'un collage apporte
 *   (« font-family: Calibri », « font-size: 1.2em ») avant d'enregistrer. Le serveur reste l'autorité — il
 *   refuse, il ne nettoie pas — et les deux côtés jouent les mêmes cas (`tests/vectors/print_style_cases.json`) ;
 * - **la conversion** d'un gabarit à BLOCS (celui d'un plugin, ou le fabriqué) en document : on ouvre l'éditeur
 *   sur ce qui s'imprime aujourd'hui, pas sur une page blanche ;
 * - **la page** : ses dimensions en millimètres, pour dessiner la feuille à l'échelle et placer une image.
 */
import type { DocTypeMeta } from './types'

// ── Les styles permis (le jumeau de `print_document.STYLES` / `_STYLE_ON`) ──
const COLOR = String.raw`#[0-9a-f]{3}|#[0-9a-f]{6}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d{1,3})\s*)?\)|[a-z]{3,20}`
const LEN = String.raw`\d{1,4}(\.\d{1,3})?(mm|px|%)`
export const STYLES: Record<string, RegExp> = {
  color: new RegExp(`^(${COLOR})$`),
  'background-color': new RegExp(`^(${COLOR})$`),
  'font-size': /^\d{1,2}(\.\d{1,2})?(pt|px)$/,
  'line-height': /^[0-4](\.\d{1,2})?$/,
  'text-align': /^(left|right|center|justify)$/,
  'vertical-align': /^(top|middle|bottom)$/,
  width: new RegExp(`^(${LEN})$`),
  'min-width': new RegExp(`^(${LEN})$`),
  height: new RegExp(`^(${LEN})$`),
  left: /^-?\d{1,3}(\.\d{1,2})?mm$/,
  top: /^-?\d{1,3}(\.\d{1,2})?mm$/,
}
const TEXT = ['text-align', 'line-height', 'color', 'background-color', 'font-size']
export const STYLE_ON: Record<string, string[]> = {
  p: TEXT,
  h1: TEXT,
  h2: TEXT,
  h3: TEXT,
  li: ['text-align', 'line-height'],
  span: ['color', 'background-color', 'font-size', 'line-height'],
  table: ['width', 'min-width'],
  col: ['width', 'min-width'],
  td: ['width', 'background-color', 'vertical-align', 'text-align'],
  th: ['width', 'background-color', 'vertical-align', 'text-align'],
  img: ['width', 'height', 'left', 'top'],
}

function bounded(prop: string, value: string): boolean {
  if (['text-align', 'vertical-align', 'color', 'background-color'].includes(prop)) return true
  const n = parseFloat(value)
  if (prop === 'font-size') return n >= 6 && n <= 96
  if (prop === 'line-height') return n >= 0.5 && n <= 4
  if (prop === 'left' || prop === 'top') return n >= -50 && n <= 500
  if (value.endsWith('%')) return n <= 100
  return n <= (value.endsWith('mm') ? 500 : 2000)
}

/** Ce que le serveur garderait d'un `style`, réécrit (`kept`), et ce qu'il refuserait (`dropped`). */
export function checkStyle(tag: string, raw: string): { kept: string; dropped: string[] } {
  const allowed = STYLE_ON[tag] ?? []
  const kept: string[] = []
  const dropped: string[] = []
  for (const part of raw.split(';')) {
    if (!part.trim()) continue
    const at = part.indexOf(':')
    const prop = (at < 0 ? part : part.slice(0, at)).trim().toLowerCase()
    const value = at < 0 ? '' : part.slice(at + 1).trim().toLowerCase()
    const re = STYLES[prop]
    if (at < 0 || !re || !allowed.includes(prop) || !re.test(value) || !bounded(prop, value)) dropped.push(part.trim())
    else kept.push(`${prop}:${value}`)
  }
  return { kept: kept.join(';'), dropped }
}

/**
 * Nettoie le HTML de l'éditeur avant l'envoi : chaque `style` réduit à ce que le serveur accepte. TipTap ne
 * produit que les balises de son schéma, mais il RECOPIE les styles d'un collage (une couleur nommée, une
 * taille en `em`) — c'est le seul endroit où un document écrit à l'écran pourrait être refusé sans qu'on sache
 * pourquoi.
 */
export function normalizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  for (const el of Array.from(doc.body.querySelectorAll('[style]'))) {
    const { kept } = checkStyle(el.tagName.toLowerCase(), el.getAttribute('style') ?? '')
    if (kept) el.setAttribute('style', kept)
    else el.removeAttribute('style')
  }
  return doc.body.innerHTML
}

// ── La page ──
export const PAGE_MM: Record<string, [number, number]> = {
  A3: [297, 420], A4: [210, 297], A5: [148, 210], B4: [250, 353], B5: [176, 250], Letter: [216, 279], Legal: [216, 356], Ledger: [279, 432],
}
export interface PageGeometry {
  width: number
  height: number
  top: number
  right: number
  bottom: number
  left: number
}
/** La feuille en millimètres (format, orientation, marges). */
export function pageMm(page: { size: string; orientation: string; top: number; right: number; bottom: number; left: number }): PageGeometry {
  const [w, h] = PAGE_MM[page.size] ?? PAGE_MM.A4!
  const [width, height] = page.orientation === 'landscape' ? [h, w] : [w, h]
  return { width, height, top: page.top, right: page.right, bottom: page.bottom, left: page.left }
}
/** 1 mm à l'écran à l'échelle 1 (CSS : 96 px par pouce). */
export const PX_PER_MM = 96 / 25.4
export const round1 = (n: number): number => Math.round(n * 10) / 10

// ── Les gabarits à blocs, convertis en document ──
const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** Le libellé qu'un champ inséré montre dans l'éditeur (le serveur, lui, le remplace par la valeur). */
export function tokenLabel(token: string, meta: DocTypeMeta): string {
  if (token === 'name') return 'N° du document'
  if (token === 'doctype') return 'Type de document'
  const name = token.startsWith('doc.') ? token.slice(4) : token
  return meta.fields.find((f) => f.fieldname === name)?.label || name
}
export const tokenHtml = (token: string, meta: DocTypeMeta): string => `<span data-value="${esc(token)}">${esc(tokenLabel(token, meta))}</span>`

/** Un texte à jetons `{{ … }}` (titre, texte d'un bloc) en HTML de l'éditeur : les jetons deviennent des champs insérés. */
export function textToHtml(text: string, meta: DocTypeMeta): string {
  let out = ''
  let last = 0
  const re = /\{\{\s*(doc\.[a-z][a-z0-9_]*|name|doctype)\s*\}\}/g
  for (let m = re.exec(text); m; m = re.exec(text)) {
    out += esc(text.slice(last, m.index)) + tokenHtml(m[1]!, meta)
    last = m.index + m[0].length
  }
  return (out + esc(text.slice(last))).replace(/\n/g, '<br>')
}

/** Le document qui imprime ce que la pile imprime : le texte devient du texte, le reste des blocs incorporés. */
export function blocksToContent(blocks: Record<string, unknown>[], meta: DocTypeMeta): string {
  const out: string[] = []
  for (const b of blocks) {
    const type = b.type
    if (type === 'title') out.push(`<h1>${textToHtml(String(b.text ?? ''), meta)}</h1>`)
    else if (type === 'text') out.push(`<p>${textToHtml(String(b.text ?? ''), meta)}</p>`)
    else if (type === 'spacer') out.push('<p></p>')
    else if (type === 'page_break') out.push('<div data-page-break=""></div>')
    else if (type === 'fields' || type === 'table' || type === 'totals') out.push(`<div data-block="${esc(JSON.stringify(b))}"></div>`)
  }
  return out.join('') || '<p></p>'
}

/** Un bloc incorporé NEUF : le tableau des lignes avec ses premières colonnes, les totaux, une grille de champs. */
export function newEmbedded(type: 'table' | 'totals' | 'fields', meta: DocTypeMeta, tableField?: string): Record<string, unknown> {
  const printable = meta.fields.filter((f) => !['Table', 'Password', 'JSON', 'TextEditor', 'Image', 'Attach'].includes(f.fieldtype) && !f.hidden)
  if (type === 'totals') {
    const numeric = meta.fields.filter((f) => ['Number', 'Int', 'Percent', 'Decimal', 'Currency'].includes(f.fieldtype))
    return { type, fields: numeric.slice(-3).map((f) => f.fieldname) }
  }
  if (type === 'fields') return { type, columns: 2, fields: printable.slice(0, 4).map((f) => f.fieldname) }
  const table = meta.fields.find((f) => f.fieldtype === 'Table' && (!tableField || f.fieldname === tableField))
  const child = table && typeof table.options === 'string' ? meta.children?.[table.options] : undefined
  const columns = (child?.fields ?? [])
    .filter((f) => !['Table', 'Password', 'JSON', 'TextEditor', 'Image', 'Attach'].includes(f.fieldtype) && !f.hidden)
    .slice(0, 5)
    .map((f) => ({ field: f.fieldname, ...(['Number', 'Int', 'Percent', 'Decimal', 'Currency'].includes(f.fieldtype) ? { align: 'right' } : {}) }))
  return { type, field: table?.fieldname, style: 'bordered', density: 'normal', repeat_header: true, columns }
}
