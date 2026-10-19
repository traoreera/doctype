import { beforeEach, describe, expect, it, vi } from 'vitest'
import { initials, NAV_KEY, readNav, writeNav } from '@/lib/navstate'

beforeEach(() => localStorage.clear())

describe('mémoire de la barre latérale', () => {
  it('ce qu’on a choisi est relu', () => {
    writeNav({ collapsed: true, expanded: { stock: true, sales: false } })
    expect(readNav()).toEqual({ collapsed: true, expanded: { stock: true, sales: false } })
  })

  it('sans rien de mémorisé : dépliée, aucun choix', () => {
    expect(readNav()).toEqual({ collapsed: false, expanded: {} })
  })

  it('un contenu abîmé ou étranger ne casse rien et ne garde que ce qui est valide', () => {
    localStorage.setItem(NAV_KEY, '{pas du json')
    expect(readNav()).toEqual({ collapsed: false, expanded: {} })
    localStorage.setItem(NAV_KEY, JSON.stringify({ collapsed: 'oui', expanded: { a: 1, b: true, c: 'x' } }))
    expect(readNav()).toEqual({ collapsed: false, expanded: { b: true } })
    localStorage.setItem(NAV_KEY, 'null')
    expect(readNav()).toEqual({ collapsed: false, expanded: {} })
  })

  it('un stockage qui refuse ne casse rien', () => {
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('plein')
    })
    expect(() => writeNav({ collapsed: true, expanded: {} })).not.toThrow()
    set.mockRestore()
  })
})

describe('initiales', () => {
  it.each([
    ['alice.martin@example.com', 'AM'],
    ['bob@example.com', 'BO'],
    ['jean-pierre_dupont@x.fr', 'JP'],
    ['x', 'X'],
    ['', '?'],
    ['@seulement.fr', '?'],
  ])('%s -> %s', (user, expected) => {
    expect(initials(user)).toBe(expected)
  })
})
