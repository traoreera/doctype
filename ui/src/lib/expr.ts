/**
 * Évaluateur des conditions `depends_on` (process/doctype.md D22). Miroir EXACT de `src/xcore_doctype/expr.py`.
 *
 * Il n'y a pas de parseur ici : le serveur compile la chaîne du YAML en arbre JSON (`/meta`), et ce module n'évalue
 * que cet arbre. Les deux implémentations sont vérifiées par les mêmes vecteurs (`tests/vectors/`), y compris les
 * cas où JavaScript et Python divergent naturellement (égalité entre types, ordre des chaînes par point de code).
 */

export type Json = null | boolean | number | string

export type LitNode = { t: 'lit'; v: Json }
export type ListNode = { t: 'list'; items: LitNode[] }
export type ExprNode =
  | LitNode
  | { t: 'field'; name: string }
  | { t: 'cmp'; op: '==' | '!=' | '<' | '<=' | '>' | '>='; l: ExprNode; r: ExprNode }
  | { t: 'in'; neg: boolean; l: ExprNode; r: ListNode }
  | { t: 'and' | 'or'; items: ExprNode[] }
  | { t: 'not'; x: ExprNode }

type Kind = 'null' | 'bool' | 'num' | 'str' | 'other'

function kind(v: unknown): Kind {
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'boolean') return 'bool'
  if (typeof v === 'number') return 'num'
  if (typeof v === 'string') return 'str'
  return 'other'
}

/** `==` exige le MÊME type : `1 == true` et `"1" == 1` sont faux. */
function equal(a: unknown, b: unknown): boolean {
  const ka = kind(a)
  return ka === kind(b) && ka !== 'other' && (ka === 'null' || a === b)
}

function truthy(v: unknown): boolean {
  switch (kind(v)) {
    case 'bool':
      return v as boolean
    case 'num':
      return (v as number) !== 0
    case 'str':
      return v !== ''
    default:
      return false
  }
}

/** Ordre des chaînes par POINT DE CODE (comme Python), pas par unité UTF-16 (comme `<` en JavaScript). */
function compareCodePoints(a: string, b: string): number {
  const ia = a[Symbol.iterator]()
  const ib = b[Symbol.iterator]()
  for (;;) {
    const x = ia.next()
    const y = ib.next()
    if (x.done && y.done) return 0
    if (x.done) return -1
    if (y.done) return 1
    const cx = x.value.codePointAt(0) as number
    const cy = y.value.codePointAt(0) as number
    if (cx !== cy) return cx < cy ? -1 : 1
  }
}

function ordered(op: '<' | '<=' | '>' | '>=', a: unknown, b: unknown): boolean {
  const ka = kind(a)
  if (ka !== kind(b) || (ka !== 'num' && ka !== 'str')) return false
  const c = ka === 'num' ? ((a as number) < (b as number) ? -1 : (a as number) > (b as number) ? 1 : 0) : compareCodePoints(a as string, b as string)
  return op === '<' ? c < 0 : op === '<=' ? c <= 0 : op === '>' ? c > 0 : c >= 0
}

function value(node: ExprNode, values: Readonly<Record<string, unknown>>): unknown {
  if (node.t === 'lit') return node.v
  if (node.t === 'field') return values[node.name] ?? null
  return test(node, values)
}

function test(node: ExprNode, values: Readonly<Record<string, unknown>>): boolean {
  switch (node.t) {
    case 'lit':
    case 'field':
      return truthy(value(node, values))
    case 'and':
      return node.items.every((i) => test(i, values))
    case 'or':
      return node.items.some((i) => test(i, values))
    case 'not':
      return !test(node.x, values)
    case 'cmp': {
      const a = value(node.l, values)
      const b = value(node.r, values)
      if (node.op === '==') return equal(a, b)
      if (node.op === '!=') return !equal(a, b)
      return ordered(node.op, a, b)
    }
    case 'in': {
      const a = value(node.l, values)
      const found = node.r.items.some((item) => equal(a, item.v))
      return node.neg ? !found : found
    }
  }
}

/** Évalue un arbre reçu de `/meta` sur les valeurs courantes du formulaire. Retourne toujours un booléen. */
export function evaluate(ast: ExprNode, values: Readonly<Record<string, unknown>>): boolean {
  return test(ast, values)
}

/** Les champs que la condition lit : le formulaire ne la réévalue que quand l'un d'eux change. */
export function referencedFields(ast: ExprNode): Set<string> {
  switch (ast.t) {
    case 'field':
      return new Set([ast.name])
    case 'cmp':
      return new Set([...referencedFields(ast.l), ...referencedFields(ast.r)])
    case 'in':
      return referencedFields(ast.l)
    case 'and':
    case 'or':
      return new Set(ast.items.flatMap((i) => [...referencedFields(i)]))
    case 'not':
      return referencedFields(ast.x)
    default:
      return new Set()
  }
}
