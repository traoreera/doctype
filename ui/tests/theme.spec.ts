import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { applyDark, DARK_CLASS, isMode, readMode, resolveDark, THEME_KEY, writeMode } from '@/lib/theme'
import { useTheme } from '@/stores/theme'

describe('choix du thème', () => {
  beforeEach(() => localStorage.clear())

  it('« automatique » suit le système, « clair » et « sombre » l’ignorent', () => {
    expect([resolveDark('system', true), resolveDark('system', false)]).toEqual([true, false])
    expect([resolveDark('dark', false), resolveDark('light', true)]).toEqual([true, false])
  })

  it('un choix mémorisé est relu ; n’importe quoi d’autre vaut « automatique »', () => {
    writeMode('dark')
    expect(readMode()).toBe('dark')
    localStorage.setItem(THEME_KEY, 'violet')
    expect(readMode()).toBe('system')
    expect(['light', 'dark', 'system', 'x', null, 1].map(isMode)).toEqual([true, true, true, false, false, false])
  })

  it('un stockage qui refuse (navigation privée) ne casse rien', () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('bloqué')
    })
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('bloqué')
    })
    expect(readMode()).toBe('system')
    expect(() => writeMode('dark')).not.toThrow()
    get.mockRestore()
    set.mockRestore()
  })

  it('la classe se pose et se retire sur la racine', () => {
    const root = document.createElement('html')
    applyDark(true, root)
    expect(root.classList.contains(DARK_CLASS)).toBe(true)
    applyDark(false, root)
    expect(root.classList.contains(DARK_CLASS)).toBe(false)
  })
})

describe('store du thème', () => {
  let listeners: ((e: { matches: boolean }) => void)[]
  let matches: boolean

  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove(DARK_CLASS)
    listeners = []
    matches = false
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        get matches() {
          return matches
        },
        addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => listeners.push(fn),
        removeEventListener: (_: string, fn: unknown) => (listeners = listeners.filter((l) => l !== fn)),
      })),
    )
    setActivePinia(createPinia())
  })
  afterEach(() => vi.unstubAllGlobals())

  it('en automatique, le thème suit le système EN COURS DE ROUTE', async () => {
    const theme = useTheme()
    const stop = theme.start()
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(false)
    listeners.forEach((l) => l({ matches: true })) // l'utilisateur passe son système en sombre
    await Promise.resolve()
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(true)
    stop()
    expect(listeners).toHaveLength(0) // plus d'écouteur après l'arrêt
  })

  it('un choix explicite l’emporte sur le système et se garde', async () => {
    matches = true
    const theme = useTheme()
    theme.start()
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(true) // le système est sombre
    theme.setMode('light')
    await Promise.resolve()
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(false)
    expect(readMode()).toBe('light')
  })

  it('le choix mémorisé est repris au démarrage suivant', () => {
    writeMode('dark')
    setActivePinia(createPinia())
    const theme = useTheme()
    theme.start()
    expect(theme.mode).toBe('dark')
    expect(document.documentElement.classList.contains(DARK_CLASS)).toBe(true)
  })
})
