import { describe, expect, it } from 'vitest'
import cases from '../../tests/vectors/expr_cases.json'
import asts from '../../tests/vectors/expr_asts.json'
import { evaluate, referencedFields, type ExprNode } from '../src/lib/expr'

// Les cas sont écrits À LA MAIN côté Python (résultat attendu jamais calculé) ; les arbres sont ceux que le serveur envoie.
// pytest exécute les mêmes cas avec `expr.py` : si les deux langages divergent, l'un des deux échoue.
const trees = asts as Record<string, ExprNode>

describe('conditions : mêmes vecteurs que le serveur', () => {
  it.each(cases.cases)('$expr', (c) => {
    const ast = trees[c.expr]
    expect(ast, `arbre manquant pour ${c.expr} (python scripts/gen_expr_asts.py)`).toBeDefined()
    expect(evaluate(ast as ExprNode, c.values as Record<string, unknown>)).toBe(c.expect)
  })
})

const field = (name: string): ExprNode => ({ t: 'field', name })
const lit = (v: null | boolean | number | string): ExprNode => ({ t: 'lit', v })

describe('évaluateur', () => {
  it('ne lève jamais, quelle que soit la valeur', () => {
    const ast: ExprNode = {
      t: 'or',
      items: [
        { t: 'cmp', op: '==', l: field('a'), r: lit(1) },
        { t: 'cmp', op: '>', l: field('a'), r: lit(1) },
        { t: 'in', neg: false, l: field('a'), r: { t: 'list', items: [{ t: 'lit', v: 1 }, { t: 'lit', v: 'x' }] } },
        { t: 'not', x: field('a') },
      ],
    }
    for (const v of [null, undefined, true, 0, 1.5, 's', '', [1], { x: 1 }, NaN, Infinity, 1e30]) {
      expect(typeof evaluate(ast, { a: v })).toBe('boolean')
    }
  })

  it('un champ absent vaut null', () => {
    expect(evaluate({ t: 'cmp', op: '==', l: field('x'), r: lit(null) }, {})).toBe(true)
  })

  it('liste les champs lus', () => {
    const ast: ExprNode = {
      t: 'and',
      items: [
        { t: 'cmp', op: '==', l: field('a'), r: lit(1) },
        { t: 'or', items: [field('b'), { t: 'not', x: field('c') }] },
        { t: 'in', neg: true, l: field('n'), r: { t: 'list', items: [{ t: 'lit', v: 1 }] } },
      ],
    }
    expect([...referencedFields(ast)].sort()).toEqual(['a', 'b', 'c', 'n'])
    expect(referencedFields(lit(true)).size).toBe(0)
  })
})
