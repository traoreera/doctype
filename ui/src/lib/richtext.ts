/**
 * Le texte enrichi (champ `TextEditor`) : du HTML d'un sous-ensemble FERMÉ, miroir de `fieldtypes.rich_text` côté serveur.
 *
 * L'éditeur ne produit que cela ; le serveur REFUSE tout le reste (jamais « nettoyé en silence »). `sanitizeRich` reconstruit un HTML conforme
 * (balises permises, `href` http, https ou mailto seulement, aucun autre attribut) : il passe sur ce que l'éditeur rend avant l'envoi, et sur ce qu'on
 * affiche (`v-html`) avant de l'insérer dans la page : jamais de HTML qu'un autre a écrit sans ce filtre.
 */

const TAGS = new Set(['p', 'br', 'strong', 'em', 'u', 's', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'hr', 'a'])
const VOID = new Set(['br', 'hr'])
const DROP_WITH_CONTENT = new Set(['script', 'style', 'template', 'iframe', 'object', 'embed', 'noscript'])
const HREF = /^(https?:\/\/[^\s<>"']{1,2040}|mailto:[^\s<>"']{3,254})$/i
export const MAX_RICH = 100_000

const escapeText = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const escapeAttr = (s: string): string => escapeText(s).replace(/"/g, '&quot;')

function render(node: Node): string {
  if (node.nodeType === 3) return escapeText(node.textContent ?? '')
  if (node.nodeType !== 1) return ''
  const el = node as Element
  const tag = el.tagName.toLowerCase()
  if (DROP_WITH_CONTENT.has(tag)) return ''
  const inner = Array.from(el.childNodes).map(render).join('')
  if (!TAGS.has(tag)) return tag === 'div' ? (inner ? `<p>${inner}</p>` : '') : inner // un bloc inconnu perd ses balises, jamais son texte
  if (VOID.has(tag)) return `<${tag}>`
  if (tag === 'a') {
    const href = el.getAttribute('href') ?? ''
    return HREF.test(href) ? `<a href="${escapeAttr(href)}">${inner}</a>` : inner
  }
  return `<${tag}>${inner}</${tag}>`
}

/** Le HTML réduit au sous-ensemble permis. `''` si rien n'en reste. */
export function sanitizeRich(html: unknown): string {
  if (typeof html !== 'string' || !html) return ''
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  return Array.from(doc.body.childNodes).map(render).join('')
}

/** Le texte brut, pour une cellule de liste ou une recherche (les blocs se séparent par un espace). */
export function plainText(html: unknown): string {
  const clean = sanitizeRich(html).replace(/<\/(p|li|h[1-4]|blockquote|pre)>|<br>|<hr>/g, ' ')
  const doc = new DOMParser().parseFromString(`<body>${clean}</body>`, 'text/html')
  return (doc.body.textContent ?? '').replace(/\s+/g, ' ').trim()
}

/** Un éditeur vide rend `<p></p>` : ce n'est pas une valeur. */
export const isRichEmpty = (html: unknown): boolean => plainText(html) === '' && !/<hr>/.test(sanitizeRich(html))
