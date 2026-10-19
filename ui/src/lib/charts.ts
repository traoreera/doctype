/**
 * Géométrie des graphiques : des fonctions pures (sans DOM), donc testables seules. Le Desk dessine en SVG, sans bibliothèque : les
 * couleurs viennent des variables CSS `--chart-N` du thème (clair et sombre), jamais d'une valeur écrite ici.
 */

export interface Scale {
  min: number
  max: number
  step: number
  ticks: number[]
}

const round = (n: number): number => Math.round(n * 1e10) / 1e10

/**
 * Une échelle « ronde » qui contient `lo` et `hi` ET zéro (une barre part toujours de zéro), avec environ `target` graduations :
 * 0, 5, 10, 15, 20 plutôt que 0, 4.7, 9.4… Les valeurs négatives (un solde, une variation) sont prises en charge.
 * `integer` : des graduations entières (un compteur).
 */
export function niceScale(lo: number, hi: number, target = 4, integer = false): Scale {
  let min = Math.min(lo, 0)
  let max = Math.max(hi, 0)
  if (min === max) max = min + 1
  const rough = (max - min) / Math.max(1, target)
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const residual = rough / magnitude
  let step = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * magnitude
  // Un décompte n'a pas de demi-document : jamais de graduation décimale (« 0,5 ») sur un compteur.
  if (integer && step < 1) step = 1
  min = round(Math.floor(round(min / step)) * step)
  max = round(Math.ceil(round(max / step)) * step)
  const ticks: number[] = []
  for (let v = min; v <= max + step / 2; v = round(v + step)) ticks.push(v)
  return { min, max, step, ticks }
}

/** La position (en pixels, de haut en bas) d'une valeur sur l'axe vertical. */
export const scaleY = (v: number, s: Scale, top: number, bottom: number): number => bottom - ((v - s.min) / (s.max - s.min)) * (bottom - top)

const compact = new Intl.NumberFormat('fr-FR', { notation: 'compact', maximumFractionDigits: 1 })

/** Une graduation courte : 1 500 -> « 1,5 k ». */
export const formatTick = (n: number): string => compact.format(n)

/** Un angle en radians, 0 en haut, dans le sens des aiguilles d'une montre : le point correspondant sur un cercle. */
export function polar(cx: number, cy: number, r: number, angle: number): [number, number] {
  return [round(cx + r * Math.sin(angle)), round(cy - r * Math.cos(angle))]
}

export interface Slice {
  index: number
  start: number
  end: number
  fraction: number
}

/** Les secteurs d'un camembert : seules les valeurs POSITIVES en font partie, chacune pour sa part du total. */
export function slices(values: readonly number[]): Slice[] {
  const total = values.reduce((sum, v) => sum + (v > 0 ? v : 0), 0)
  if (total <= 0) return []
  const out: Slice[] = []
  let angle = 0
  values.forEach((v, index) => {
    if (!(v > 0)) return
    const fraction = v / total
    out.push({ index, start: angle, end: angle + fraction * 2 * Math.PI, fraction })
    angle += fraction * 2 * Math.PI
  })
  return out
}

/** Le tracé d'un secteur (anneau si `inner > 0`). Un secteur plein cercle est deux demi-arcs : un seul arc de 360° n'a pas de tracé. */
export function arcPath(cx: number, cy: number, outer: number, inner: number, start: number, end: number): string {
  const full = end - start >= 2 * Math.PI - 1e-9
  const stop = full ? start + Math.PI : end
  const large = stop - start > Math.PI ? 1 : 0
  const [x0, y0] = polar(cx, cy, outer, start)
  const [x1, y1] = polar(cx, cy, outer, stop)
  const arc = (to: [number, number], r: number, sweep: 0 | 1, flag: 0 | 1) => `A ${r} ${r} 0 ${flag} ${sweep} ${to[0]} ${to[1]}`
  if (full) {
    const [x2, y2] = polar(cx, cy, outer, start + 2 * Math.PI)
    const outerRing = `M ${x0} ${y0} ${arc([x1, y1], outer, 1, 0)} ${arc([x2, y2], outer, 1, 0)}`
    if (inner <= 0) return `${outerRing} Z`
    const [i0x, i0y] = polar(cx, cy, inner, start)
    const [i1x, i1y] = polar(cx, cy, inner, stop)
    return `${outerRing} M ${i0x} ${i0y} ${arc([i1x, i1y], inner, 0, 0)} ${arc([i0x, i0y], inner, 0, 0)} Z`
  }
  if (inner <= 0) return `M ${cx} ${cy} L ${x0} ${y0} ${arc([x1, y1], outer, 1, large as 0 | 1)} Z`
  const [i1x, i1y] = polar(cx, cy, inner, end)
  const [i0x, i0y] = polar(cx, cy, inner, start)
  return `M ${x0} ${y0} ${arc([x1, y1], outer, 1, large as 0 | 1)} L ${i1x} ${i1y} ${arc([i0x, i0y], inner, 0, large as 0 | 1)} Z`
}

/** Une étiquette sur `step` s'affiche : assez pour se lire, jamais deux qui se chevauchent (`each` : la largeur d'une étiquette). */
export function labelStep(count: number, available: number, each: number): number {
  const fit = Math.max(1, Math.floor(available / Math.max(1, each)))
  return Math.max(1, Math.ceil(count / fit))
}

/** Une étiquette d'axe trop longue est coupée (« Boulangerie Ma… ») : le nom entier reste dans l'infobulle et pour un lecteur d'écran. */
export const AXIS_LABEL_MAX = 14
export const shorten = (label: string, max = AXIS_LABEL_MAX): string => (label.length > max ? `${label.slice(0, max - 1).trimEnd()}…` : label)

/** La largeur à réserver à chaque étiquette d'axe (police de 11 px ≈ 6 px par caractère, plus de l'air), jamais moins de 58 px. */
export const tickWidth = (labels: readonly string[]): number => Math.max(58, Math.max(0, ...labels.map((l) => shorten(l).length)) * 6.2 + 14)

/** Les points d'une courbe, `[x, y]` par valeur, centrés dans leur bande. */
export function linePoints(values: readonly number[], s: Scale, left: number, right: number, top: number, bottom: number): [number, number][] {
  const band = (right - left) / Math.max(1, values.length)
  return values.map((v, i) => [round(left + band * (i + 0.5)), round(scaleY(v, s, top, bottom))])
}

/**
 * Les couleurs : `--chart-1` … `--chart-8`, dans l'ordre fixe de la palette validée (daltonisme compris, voir desk.css). On ne génère jamais
 * de neuvième teinte : la suite d'un camembert se replie en « Autres » (`--chart-other`, un gris neutre).
 */
export const CHART_COLORS = 8
export const colorVar = (index: number): string => `var(--chart-${(index % CHART_COLORS) + 1})`
export const OTHER_COLOR = 'var(--chart-other)'

/** Au plus ce nombre de parts dans un camembert : au-delà on ne compare plus des parts, on lit un tableau. */
export const MAX_PARTS = 6

/**
 * Une barre ancrée sur sa ligne de base (carrée côté base) dont le bout de la donnée est arrondi : `base` est la ordonnée de la ligne de base,
 * `tip` celle du bout, vers le haut (valeur positive) ou vers le bas (négative). Le rayon ne dépasse jamais la demi-largeur ni la hauteur.
 */
export function barPath(x: number, width: number, base: number, tip: number, radius = 4): string {
  const height = Math.abs(base - tip)
  const r = Math.max(0, Math.min(radius, width / 2, height))
  const dir = tip <= base ? -1 : 1 // -1 : la barre monte
  const e = tip - dir * r // l'ordonnée où commence l'arrondi, du côté de la base
  const right = x + width
  return `M ${x} ${base} V ${e} Q ${x} ${tip} ${x + r} ${tip} H ${right - r} Q ${right} ${tip} ${right} ${e} V ${base} Z`
}
