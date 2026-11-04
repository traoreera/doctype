/**
 * La galerie des STYLES DE TABLEAU de l'impression (`process/impression.md` §12) : une liste FERMÉE, la même pour
 * le tableau des lignes (`style` du bloc) et pour un tableau de mise en page (`<table data-style>`), le jumeau de
 * `print_document.TABLE_STYLES`. Les deux côtés relisent `tests/vectors/table_styles.json` et échouent s'ils
 * divergent ; le rendu à l'écran (`components/printdoc/table-styles.css`) suit les règles de `table_css()`.
 */
export const TABLE_STYLES = ['bordered', 'ruled', 'plain', 'striped', 'accent', 'boxed'] as const
export type TableStyle = (typeof TABLE_STYLES)[number]

export const isTableStyle = (v: unknown): v is TableStyle => typeof v === 'string' && (TABLE_STYLES as readonly string[]).includes(v)

/**
 * Le style d'un tableau de mise en page tel que l'HTML le dit : `data-style`, sinon l'ancien `data-borders`
 * (`none` = sans bordure, sinon quadrillé), la règle de `print_document.table_style` — un gabarit enregistré
 * avant la galerie s'ouvre avec le style qu'il imprimait.
 */
export function layoutTableStyle(el: { getAttribute(name: string): string | null }): TableStyle {
  const style = el.getAttribute('data-style')
  if (isTableStyle(style)) return style
  return el.getAttribute('data-borders') === 'none' ? 'plain' : 'bordered'
}

/** Les gestes d'un tableau de mise en page offerts par le panneau de droite (des commandes TipTap, par leur nom). */
export type TableCommand = 'addRowAfter' | 'addColumnAfter' | 'mergeOrSplit' | 'deleteRow' | 'deleteColumn' | 'deleteTable'
