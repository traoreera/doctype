import { describe, expect, it } from 'vitest'
import {
  addSection, addTab, boardProblems, fromLayout, locate, moveField, moveSection, nudge, placed, removeSection, removeTab, renameTab, setColumns,
  toLayout, unplace, updateSection, withDisplay,
} from '@/lib/formbuilder'
import type { LayoutSection } from '@/lib/types'

const sec = (columns: string[][], extra: Partial<LayoutSection> = {}): LayoutSection => ({ section: null, collapsible: false, collapsed: false, columns, ...extra })
const PLAIN: LayoutSection[] = [sec([['a', 'b'], ['c']], { section: 'Général' }), sec([['d']])]
const TABBED: LayoutSection[] = [
  sec([['a']], { tab: 'Détails', section: 'Un' }),
  sec([['b']], { tab: 'Détails' }),
  sec([['c', 'd']], { tab: 'Suivi' }),
]

describe('le Form Builder : la disposition comme un tableau', () => {
  it('l’aller-retour rend la disposition telle quelle, avec ou sans onglets', () => {
    expect(toLayout(fromLayout(PLAIN))).toEqual(PLAIN)
    expect(toLayout(fromLayout(TABBED))).toEqual(TABBED)
    expect(fromLayout(TABBED).tabs.map((t) => [t.name, t.sections.length])).toEqual([['Détails', 2], ['Suivi', 1]])
    expect(fromLayout([]).tabs[0]!.sections).toHaveLength(1) // un formulaire vide garde une section où déposer
  })

  it('une colonne vide ou une section vide ne partent jamais au serveur (il les refuse)', () => {
    let b = fromLayout(PLAIN)
    b = setColumns(b, 0, 1, 3) // deux colonnes vides de plus
    b = addSection(b, 0) // une section sans champ
    expect(toLayout(b)).toEqual(PLAIN)
  })

  it('déposer un champ ailleurs, ou plus bas dans sa propre colonne', () => {
    let b = fromLayout(PLAIN)
    b = moveField(b, 'd', { tab: 0, section: 0, column: 1, index: 0 })
    expect(toLayout(b)).toEqual([sec([['a', 'b'], ['d', 'c']], { section: 'Général' })])
    b = moveField(b, 'a', { tab: 0, section: 0, column: 0, index: 2 }) // sous « b » : retirer « a » a décalé « b »
    expect(toLayout(b)[0]!.columns[0]).toEqual(['b', 'a'])
    expect(locate(b, 'a')).toEqual({ tab: 0, section: 0, column: 0, index: 1 })
  })

  it('un champ de la palette (non placé) se dépose comme un autre, et un champ retiré y retourne', () => {
    let b = unplace(fromLayout(PLAIN), 'b')
    expect(placed(b)).toEqual(['a', 'c', 'd'])
    b = moveField(b, 'b', { tab: 0, section: 1, column: 0, index: 99 })
    expect(toLayout(b)[1]!.columns).toEqual([['d', 'b']])
  })

  it('au clavier : monter, descendre, changer de colonne ou de section', () => {
    let b = fromLayout(PLAIN)
    b = nudge(b, 'b', 'up')
    expect(toLayout(b)[0]!.columns[0]).toEqual(['b', 'a'])
    b = nudge(b, 'b', 'down')
    expect(toLayout(b)[0]!.columns[0]).toEqual(['a', 'b'])
    b = nudge(b, 'a', 'right')
    expect(toLayout(b)[0]!.columns).toEqual([['b'], ['c', 'a']])
    b = nudge(b, 'a', 'nextSection')
    expect(toLayout(b)[1]!.columns[0]).toEqual(['a', 'd']) // en tête de la section suivante
    b = nudge(b, 'a', 'prevSection')
    expect(toLayout(b)[0]!.columns[0]).toEqual(['b', 'a']) // en fin de la précédente
    expect(nudge(b, 'b', 'up')).toBe(b) // déjà en haut : rien ne bouge
  })

  it('moins de colonnes : les champs de la colonne retirée passent dans la dernière, rien ne se perd', () => {
    const b = setColumns(fromLayout(PLAIN), 0, 0, 1)
    expect(toLayout(b)[0]!.columns).toEqual([['a', 'b', 'c']])
    expect(setColumns(fromLayout(PLAIN), 0, 0, 9).tabs[0]!.sections[0]!.columns).toHaveLength(4) // 4 au plus
  })

  it('une section se retire (ses champs retournent à la palette), se renomme, se déplace', () => {
    let b = removeSection(fromLayout(PLAIN), 0, 0)
    expect(placed(b)).toEqual(['d'])
    b = updateSection(fromLayout(PLAIN), 0, 1, { section: 'Notes', collapsible: true, collapsed: true })
    expect(toLayout(b)[1]).toMatchObject({ section: 'Notes', collapsible: true, collapsed: true })
    b = updateSection(b, 0, 1, { collapsible: false })
    expect(toLayout(b)[1]!.collapsed).toBe(false) // replié exige repliable (le serveur le refuse sinon)
    b = moveSection(fromLayout(PLAIN), { tab: 0, section: 1 }, { tab: 0, index: 0 })
    expect(toLayout(b).map((s) => s.columns[0]![0])).toEqual(['d', 'a'])
  })

  it('le premier onglet ajouté fait passer tout le formulaire en onglets ; le dernier retiré l’en sort', () => {
    let b = addTab(fromLayout(PLAIN), 'Suivi')
    b = moveField(b, 'd', { tab: 1, section: 0, column: 0, index: 0 })
    expect(toLayout(b)).toEqual([
      sec([['a', 'b'], ['c']], { tab: 'Détails', section: 'Général' }),
      sec([['d']], { tab: 'Suivi' }),
    ])
    b = renameTab(b, 1, 'Historique')
    expect(toLayout(b)[1]!.tab).toBe('Historique')
    b = removeTab(b, 1)
    expect(toLayout(b)).toEqual([sec([['a', 'b'], ['c']], { section: 'Général' }), sec([['d']])]) // ses sections rejoignent l'onglet d'avant
  })

  it('ce que le serveur refuserait est dit avant d’enregistrer', () => {
    expect(boardProblems(fromLayout(TABBED))).toEqual([])
    expect(boardProblems(renameTab(fromLayout(TABBED), 1, ' '))).toContain('Chaque onglet doit avoir un nom.')
    expect(boardProblems(renameTab(fromLayout(TABBED), 1, 'détails'))).toContain('Deux onglets portent le même nom.')
    expect(boardProblems(unplace(unplace(fromLayout([sec([['a', 'b']])]), 'a'), 'b'))).toContain('Placez au moins un champ.')
  })

  it('libellé et description d’un champ du type : une retouche d’affichage, retirée quand on la vide', () => {
    let c = withDisplay({ layout: PLAIN }, 'a', 'label', ' Client ')
    expect(c).toEqual({ layout: PLAIN, fields: { a: { label: 'Client' } } })
    c = withDisplay(c, 'a', 'label', '')
    expect(c).toEqual({ layout: PLAIN })
  })
})
