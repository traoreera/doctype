/**
 * Les champs Decimal du Desk : des CHAÎNES exactes, jamais des `number`.
 *
 * Le serveur stocke `"12.500"` (forme canonique, voir `decimals.py`) et refuse un flottant. Le navigateur fait donc tout en texte :
 * aucun `parseFloat` entre la frappe de l'utilisateur et l'envoi, sinon `0.1 + 0.2` reviendrait par la porte de derrière.
 * `canonicalDecimal` reprend EXACTEMENT les règles de `decimals.canonical` (mêmes vecteurs : tests/vectors/decimal_cases.json).
 */
const PATTERN = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/
export const MAX_DIGITS = 18

/** Ce que tape un utilisateur francophone (« 12,5 », « 12.5 ») devient un texte au format du serveur ; le reste est laissé tel quel. */
export const normalizeDecimal = (raw: string): string => raw.trim().replace(',', '.')

/** La forme canonique à `decimals` décimales, ou `null` si la valeur est refusée (mêmes cas que le serveur). */
export function canonicalDecimal(raw: unknown, decimals: number): string | null {
  if (typeof raw !== 'string' || !PATTERN.test(raw)) return null
  const negative = raw.startsWith('-')
  const [whole = '', fraction = ''] = raw.replace('-', '').split('.')
  if (fraction.replace(/0+$/, '').length > decimals) return null // trop de décimales : jamais d'arrondi silencieux
  if (whole.replace(/^0+/, '').length + decimals > MAX_DIGITS) return null
  const digits = fraction.slice(0, decimals).padEnd(decimals, '0')
  const text = decimals ? `${whole}.${digits}` : whole
  return negative && /[1-9]/.test(text) ? `-${text}` : text // « -0.000 » n'est pas une autre valeur que « 0.000 »
}

/** Message d'erreur de saisie, ou `null` si la valeur est acceptable. */
export const isValidDecimal = (raw: unknown, decimals: number): boolean => canonicalDecimal(raw, decimals) !== null

/** Affichage francophone (« 1 234,500 ») d'une valeur stockée. Au-delà de 15 chiffres un `number` ne serait plus exact : texte brut. */
export function formatDecimal(stored: string, decimals: number): string {
  const canon = canonicalDecimal(stored, decimals)
  if (canon === null) return stored
  const digits = canon.replace(/[-.]/g, '').length
  if (digits > 15) return canon.replace('.', ',')
  return new Intl.NumberFormat('fr-FR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Number(canon))
}

/**
 * La somme EXACTE de valeurs canoniques (des entiers 64 bits mis à l'échelle), rendue en forme canonique. Les valeurs illisibles sont
 * ignorées. Sert aux totaux affichés par le Desk (le centre d'un anneau) : jamais d'addition de flottants sur un montant.
 */
export function sumDecimals(values: readonly string[], decimals: number): string {
  let total = 0n
  for (const v of values) {
    const canon = canonicalDecimal(v, decimals)
    if (canon !== null) total += BigInt(canon.replace('.', ''))
  }
  const negative = total < 0n
  const digits = (negative ? -total : total).toString().padStart(decimals + 1, '0')
  const whole = digits.slice(0, digits.length - decimals)
  const text = decimals ? `${whole}.${digits.slice(digits.length - decimals)}` : whole
  return negative ? `-${text}` : text
}
