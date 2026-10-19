import { describe, expect, it } from 'vitest'
import { CHART_COLORS, MAX_PARTS, OTHER_COLOR, arcPath, barPath, colorVar, formatTick, labelStep, linePoints, shorten, tickWidth, niceScale, polar, scaleY, slices } from '@/lib/charts'

describe('échelle d’un graphique', () => {
  it.each([
    [[0, 17], { min: 0, max: 20, step: 5 }],
    [[0, 100], { min: 0, max: 100, step: 50 }],
    [[3, 9], { min: 0, max: 10, step: 5 }], // une barre part toujours de zéro
    [[0, 0.7], { min: 0, max: 0.8, step: 0.2 }],
    [[-30, 70], { min: -50, max: 100, step: 50 }], // un solde, une variation : le négatif est une vraie moitié du graphique
    [[0, 1234], { min: 0, max: 1500, step: 500 }],
    [[5, 5], { min: 0, max: 6, step: 2 }],
  ])('niceScale(%j)', ([lo, hi], expected) => {
    const s = niceScale(lo!, hi!)
    expect({ min: s.min, max: s.max, step: s.step }).toEqual(expected)
    expect(s.ticks[0]).toBe(s.min)
    expect(s.ticks.at(-1)).toBe(s.max)
  })

  it('un compteur n’a que des graduations entières', () => {
    expect(niceScale(0, 1).ticks).toEqual([0, 0.5, 1])
    expect(niceScale(0, 1, 4, true).ticks).toEqual([0, 1])
    expect(niceScale(0, 3, 4, true).ticks).toEqual([0, 1, 2, 3])
    expect(niceScale(0, 0, 4, true).ticks).toEqual([0, 1])
    expect(niceScale(0, 17, 4, true).step).toBe(5) // au-delà de 1, l'échelle ronde habituelle reste
  })

  it('aucune valeur : une échelle valide quand même (0 à 1), jamais NaN', () => {
    const s = niceScale(0, 0)
    expect(s.ticks.every(Number.isFinite)).toBe(true)
    expect(s.max).toBeGreaterThan(s.min)
  })

  it('pas de dérive de flottants dans les graduations', () => {
    expect(niceScale(0, 0.7).ticks).toEqual([0, 0.2, 0.4, 0.6, 0.8])
  })

  it('scaleY : le minimum en bas, le maximum en haut', () => {
    const s = niceScale(0, 100)
    expect(scaleY(0, s, 10, 210)).toBe(210)
    expect(scaleY(100, s, 10, 210)).toBe(10)
    expect(scaleY(50, s, 10, 210)).toBe(110)
  })

  it('formatTick abrège les grands nombres', () => {
    expect(formatTick(1500).replace(/\s/g, '')).toBe('1,5k')
    expect(formatTick(12)).toBe('12')
  })
})

describe('camembert', () => {
  it('chaque secteur est sa part du total, ensemble ils font le tour', () => {
    const s = slices([1, 1, 2])
    expect(s.map((x) => x.fraction)).toEqual([0.25, 0.25, 0.5])
    expect(s[0]!.start).toBe(0)
    expect(s.at(-1)!.end).toBeCloseTo(2 * Math.PI)
    expect(s[1]!.start).toBeCloseTo(s[0]!.end)
  })

  it('zéro et valeurs négatives n’ont pas de secteur, mais gardent leur rang', () => {
    const s = slices([0, 3, -2, 1])
    expect(s.map((x) => x.index)).toEqual([1, 3])
    expect(s.map((x) => x.fraction)).toEqual([0.75, 0.25])
  })

  it('rien de positif : aucun secteur', () => {
    expect(slices([])).toEqual([])
    expect(slices([0, -1])).toEqual([])
  })

  it('polar : 0 en haut, quart de tour à droite', () => {
    expect(polar(50, 50, 10, 0)).toEqual([50, 40])
    expect(polar(50, 50, 10, Math.PI / 2)).toEqual([60, 50])
    expect(polar(50, 50, 10, Math.PI)).toEqual([50, 60])
  })

  it('arcPath : un secteur, un anneau, et un cercle plein tracé en deux demi-arcs', () => {
    expect(arcPath(50, 50, 40, 0, 0, Math.PI / 2)).toBe('M 50 50 L 50 10 A 40 40 0 0 1 90 50 Z')
    expect(arcPath(50, 50, 40, 20, 0, Math.PI / 2)).toBe('M 50 10 A 40 40 0 0 1 90 50 L 70 50 A 20 20 0 0 0 50 30 Z')
    const big = arcPath(50, 50, 40, 0, 0, 1.5 * Math.PI)
    expect(big).toContain('A 40 40 0 1 1') // plus d’un demi-tour : « grand arc »
    const full = arcPath(50, 50, 40, 0, 0, 2 * Math.PI)
    expect(full.match(/A 40 40/g)).toHaveLength(2)
    expect(full).not.toContain('NaN')
  })
})

describe('étiquettes et courbes', () => {
  it('shorten / tickWidth : une étiquette longue est coupée, et sa place réservée en tient compte', () => {
    expect(shorten('Atelier')).toBe('Atelier')
    expect(shorten('Boulangerie Martin')).toBe('Boulangerie M…')
    expect(shorten('Boulangerie Martin')).toHaveLength(14)
    expect(tickWidth(['nov. 25', 'déc. 25'])).toBe(58) // des mois : le minimum
    expect(tickWidth(['Boulangerie Martin'])).toBeGreaterThan(95) // des noms : de la place
    expect(tickWidth([])).toBe(58)
    // Six noms de 14 caractères dans 600 px : une étiquette sur deux, jamais de chevauchement.
    expect(labelStep(6, 270, tickWidth(Array(6).fill('Boulangerie Martin')))).toBeGreaterThan(1)
  })

  it('labelStep : toutes les étiquettes quand elles tiennent, une sur n sinon', () => {
    expect(labelStep(6, 600, 50)).toBe(1)
    expect(labelStep(30, 600, 50)).toBe(3)
    expect(labelStep(12, 100, 50)).toBe(6)
    expect(labelStep(5, 10, 50)).toBe(5) // jamais zéro ni division par zéro
    expect(labelStep(0, 100, 50)).toBe(1)
  })

  it('linePoints : une valeur par bande, centrée', () => {
    const s = niceScale(0, 10)
    const pts = linePoints([0, 10], s, 0, 100, 0, 100)
    expect(pts).toEqual([[25, 100], [75, 0]])
  })

  it('la palette a huit teintes dans un ordre fixe, jamais une neuvième générée', () => {
    expect(CHART_COLORS).toBe(8)
    expect(colorVar(0)).toBe('var(--chart-1)')
    expect(colorVar(7)).toBe('var(--chart-8)')
    expect(colorVar(8)).toBe('var(--chart-1)') // le repli est à la charge de l'appelant (« Autres »)
    expect(OTHER_COLOR).toBe('var(--chart-other)')
    expect(MAX_PARTS).toBe(6)
  })

  it('une barre est carrée sur sa ligne de base et arrondie au bout de la donnée', () => {
    expect(barPath(10, 20, 100, 40)).toBe('M 10 100 V 44 Q 10 40 14 40 H 26 Q 30 40 30 44 V 100 Z') // monte
    expect(barPath(10, 20, 100, 160)).toBe('M 10 100 V 156 Q 10 160 14 160 H 26 Q 30 160 30 156 V 100 Z') // descend (valeur négative)
  })

  it('le rayon d’une barre ne dépasse ni sa demi-largeur ni sa hauteur', () => {
    expect(barPath(0, 6, 100, 98)).toBe('M 0 100 V 100 Q 0 98 2 98 H 4 Q 6 98 6 100 V 100 Z') // 2 px de haut : rayon 2
    expect(barPath(0, 4, 100, 50)).toContain('Q 0 50 2 50') // 4 px de large : rayon 2
    expect(barPath(0, 10, 100, 100)).not.toContain('NaN') // hauteur nulle
  })
})
