import { describe, expect, it } from 'vitest'
import { blankBlock, colsOf, declare, hasSize, missing, moveBlock, preview, removeBlock, resizeBlock, type DeclaredBlock } from '@/lib/blocksedit'
import type { WorkspaceBlock } from '@/lib/types'

describe('declare : ce que le serveur renvoie → ce qu’on lui renvoie', () => {
  it('retire les champs de LECTURE que le modèle d’écriture refuse (extra=forbid)', () => {
    const served: WorkspaceBlock[] = [
      { type: 'shortcut', doctype: 'Item', label: 'Article', icon: 'box', create: true, single: false },
      { type: 'shortcut', doctype: 'Bin', label: 'Solde', create: false },
      { type: 'quick_list', label: 'Derniers', doctype: 'Item', limit: 5, title_field: 'item_name', submittable: false, cols: 6, ...({ doctype_label: 'Article' } as object) },
      { type: 'card', title: 'Rapports', cols: 4, links: [{ doctype: 'Bin', label: 'Solde', icon: 'x' }] },
    ]
    const out = served.map(declare)
    expect(out[0]).toEqual({ type: 'shortcut', doctype: 'Item', label: 'Article', new: true })
    expect(out[1]).toEqual({ type: 'shortcut', doctype: 'Bin', label: 'Solde' })
    expect(out[2]).toEqual({ type: 'quick_list', label: 'Derniers', doctype: 'Item', limit: 5, cols: 6 })
    expect(out[3]).toEqual({ type: 'card', title: 'Rapports', cols: 4, links: [{ doctype: 'Bin', label: 'Solde' }] })
  })

  it('copie les autres blocs sans les lier à l’original', () => {
    const card: WorkspaceBlock = { type: 'number_card', label: 'N', doctype: 'Item', function: 'count', cols: 3 }
    const copy = declare(card)
    expect(copy).toEqual(card)
    expect(copy).not.toBe(card)
  })
})

describe('déplacer, redimensionner, retirer', () => {
  const abc = ['a', 'b', 'c', 'd']

  it('moveBlock : le bloc prend la place de la cible, dans les deux sens', () => {
    expect(moveBlock(abc, 0, 2)).toEqual(['b', 'c', 'a', 'd'])
    expect(moveBlock(abc, 3, 1)).toEqual(['a', 'd', 'b', 'c'])
    expect(moveBlock(abc, 1, 0)).toEqual(['b', 'a', 'c', 'd'])
  })

  it('moveBlock : une place hors liste ou la même place ne change rien et ne modifie pas l’original', () => {
    expect(moveBlock(abc, 1, 1)).toEqual(abc)
    expect(moveBlock(abc, 0, -1)).toEqual(abc)
    expect(moveBlock(abc, 0, 4)).toEqual(abc)
    expect(moveBlock(abc, 9, 0)).toEqual(abc)
    expect(abc).toEqual(['a', 'b', 'c', 'd'])
  })

  it('removeBlock retire exactement le bloc visé', () => {
    expect(removeBlock(abc, 2)).toEqual(['a', 'b', 'd'])
  })

  it('resizeBlock ne change que la largeur du bloc visé, et seulement s’il en a une', () => {
    const blocks: DeclaredBlock[] = [
      { type: 'header', text: 'T' },
      { type: 'number_card', label: 'N', doctype: 'Item', function: 'count', cols: 3 },
      { type: 'chart', label: 'C', doctype: 'Item', kind: 'bar', function: 'count', group_by: 'uom', limit: 10 },
    ]
    const out = resizeBlock(blocks, 2, 12)
    expect(out[2]).toMatchObject({ cols: 12 })
    expect(out[1]).toMatchObject({ cols: 3 })
    expect(resizeBlock(blocks, 0, 6)[0]).toEqual({ type: 'header', text: 'T' })
    expect(blocks[2]).not.toHaveProperty('cols')
  })

  it('colsOf : la largeur affichée est celle que le serveur appliquera', () => {
    expect(colsOf({ type: 'header', text: 'T' })).toBe(12)
    expect(colsOf({ type: 'shortcut', doctype: 'Item' })).toBe(3)
    expect(colsOf({ type: 'number_card', label: 'N', doctype: 'Item', function: 'count' })).toBe(3)
    expect(colsOf({ type: 'chart', label: 'C', doctype: 'Item', kind: 'bar', function: 'count', group_by: 'u', limit: 10 })).toBe(6)
    expect(colsOf({ type: 'card', title: 'x', links: [] })).toBe(4)
    expect(colsOf({ type: 'chart', label: 'C', doctype: 'Item', kind: 'bar', function: 'count', group_by: 'u', limit: 10, cols: 8 })).toBe(8)
    expect(hasSize({ type: 'spacer' })).toBe(false)
  })
})

describe('blocs neufs et contrôle avant enregistrement', () => {
  it('un bloc neuf est incomplet tant qu’on n’a pas choisi son type de document', () => {
    expect(missing(blankBlock('number_card'))).toBe('label')
    expect(missing({ ...(blankBlock('number_card') as object), label: 'N' } as DeclaredBlock)).toBe('doctype')
    expect(missing({ ...(blankBlock('number_card') as object), label: 'N', doctype: 'Item' } as DeclaredBlock)).toBeNull()
  })

  it('un calcul autre que le nombre exige un champ ; un graphique exige son regroupement', () => {
    const base = { ...(blankBlock('chart') as object), label: 'C', doctype: 'Item' }
    expect(missing(base as DeclaredBlock)).toBe('group_by')
    expect(missing({ ...base, group_by: 'uom', function: 'sum' } as DeclaredBlock)).toBe('field')
    expect(missing({ ...base, group_by: 'uom', function: 'sum', field: 'qty' } as DeclaredBlock)).toBeNull()
  })

  it('une carte de liens a besoin d’un titre et d’un lien ; un texte, de texte ; un espacement, de rien', () => {
    expect(missing({ type: 'card', title: 'R', links: [] })).toBe('links')
    expect(missing({ type: 'card', title: ' ', links: [{ doctype: 'Item' }] })).toBe('label')
    expect(missing({ type: 'header', text: '  ' })).toBe('text')
    expect(missing({ type: 'spacer' })).toBeNull()
  })
})

describe('preview : les champs de lecture d’une carte du Desk', () => {
  const known = (d: string) => ({ Item: { label: 'Article', icon: 'box', single: false, title_field: 'item_name', submittable: false } })[d as 'Item']

  it('un raccourci neuf prend le libellé et l’icône du type ; un libellé choisi l’emporte', () => {
    expect(preview({ type: 'shortcut', doctype: 'Item' }, known)).toMatchObject({ label: 'Article', icon: 'box', create: false })
    expect(preview({ type: 'shortcut', doctype: 'Item', label: 'Mes articles' }, known)).toMatchObject({ label: 'Mes articles' })
    expect(preview({ type: 'shortcut', doctype: 'Ghost' }, known)).toMatchObject({ label: 'Ghost', icon: null })
  })

  it('une liste récente reçoit le champ titre du type, une carte de liens les libellés de ses types', () => {
    expect(preview({ type: 'quick_list', label: 'D', doctype: 'Item', limit: 5 }, known)).toMatchObject({ title_field: 'item_name' })
    const card = preview({ type: 'card', title: 'R', links: [{ doctype: 'Item' }, { doctype: 'Ghost', label: 'Fantôme' }] }, known)
    expect(card).toMatchObject({ links: [{ doctype: 'Item', label: 'Article', icon: 'box' }, { doctype: 'Ghost', label: 'Fantôme', icon: null }] })
  })
})
