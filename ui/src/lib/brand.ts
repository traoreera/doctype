/**
 * La couleur de l'organisation : une teinte pour les boutons et les éléments actifs. Le Desk ne l'applique que si c'est EXACTEMENT `#rrggbb`
 * (elle finit dans une feuille de style : jamais une chaîne libre), avec un texte noir ou blanc selon la luminance (WCAG), et éclaircie en
 * thème sombre quand elle y serait illisible.
 */
export const HEX = /^#[0-9a-fA-F]{6}$/

const channel = (v: number): number => {
  const c = v / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

const parse = (hex: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number]
const toHex = (rgb: number[]): string => `#${rgb.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0')).join('')}`

/** Luminance relative (0 noir … 1 blanc). */
export function luminance(hex: string): number {
  const [r, g, b] = parse(hex)
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

/** Le texte lisible sur cette teinte : noir ou blanc, celui qui contraste le plus. */
export const contrastOn = (hex: string): '#ffffff' | '#111111' => (luminance(hex) > 0.4 ? '#111111' : '#ffffff')

/** Mélange vers le blanc (`amount` 0..1) ou le noir (< 0). */
export function mix(hex: string, amount: number): string {
  const target = amount >= 0 ? 255 : 0
  const k = Math.abs(amount)
  return toHex(parse(hex).map((v) => v + (target - v) * k))
}

export interface BrandSet {
  color: string
  hover: string
  active: string
  contrast: string
}

/** Les teintes du thème clair et du thème sombre pour une couleur d'organisation, ou `null` si ce n'est pas un `#rrggbb`. */
export function brandPalette(hex: string | null | undefined): { light: BrandSet; dark: BrandSet } | null {
  if (typeof hex !== 'string' || !HEX.test(hex)) return null
  const base = hex.toLowerCase()
  const set = (color: string, direction: 1 | -1): BrandSet => ({ color, hover: mix(color, direction * 0.12), active: mix(color, direction * 0.22), contrast: contrastOn(color) })
  // En thème clair les états « survol » et « appui » s'assombrissent ; en thème sombre ils s'éclaircissent, et une teinte trop sombre est éclaircie.
  const lifted = luminance(base) < 0.18 ? mix(base, 0.45) : base
  return { light: set(base, -1), dark: set(lifted, 1) }
}

/** Le CSS à injecter : les variables de teinte de PrimeVue, par thème. Vide si la couleur n'est pas valide. */
export function brandCss(hex: string | null | undefined): string {
  const p = brandPalette(hex)
  if (!p) return ''
  const vars = (s: BrandSet) => `--p-primary-color:${s.color};--p-primary-hover-color:${s.hover};--p-primary-active-color:${s.active};--p-primary-contrast-color:${s.contrast};`
  return `:root{${vars(p.light)}}:root.app-dark{${vars(p.dark)}}`
}

/** Les teintes de la palette qu'un bloc peut nommer (`color: orange`) : le reste, pour un bloc, est une couleur libre `#rrggbb`. */
export const TONES = ['blue', 'green', 'orange', 'red', 'purple', 'teal', 'gray'] as const

export const isHexTone = (value: unknown): value is string => typeof value === 'string' && HEX.test(value)

/**
 * La couleur LIBRE d'un bloc, pour les deux thèmes : une variable pour le clair, une pour le sombre (éclaircie si elle y serait illisible).
 * Le CSS choisit selon le thème (`.tone-custom`, `.chart[data-custom]`) : le composant n'a pas à connaître le thème.
 * `null` si ce n'est pas un `#rrggbb` : une valeur libre ne doit jamais finir dans un attribut de style.
 */
export function customTone(value: unknown): Record<string, string> | null {
  if (!isHexTone(value)) return null
  const p = brandPalette(value)!
  return { '--custom-light': p.light.color, '--custom-dark': p.dark.color }
}
