/**
 * Clair, sombre ou automatique (la préférence du système). Sans interface : testé seul.
 *
 * Le choix est une commodité de CET appareil (localStorage), pas une donnée du compte : il ne voyage pas. Tout accès au stockage est
 * enveloppé (navigation privée, stockage bloqué) : sans lui, le Desk suit simplement le système.
 */
export type ThemeMode = 'light' | 'dark' | 'system'

export const THEME_KEY = 'xdt.desk.theme'
export const DARK_CLASS = 'app-dark'

export const isMode = (v: unknown): v is ThemeMode => v === 'light' || v === 'dark' || v === 'system'

export function readMode(): ThemeMode {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return isMode(v) ? v : 'system'
  } catch {
    return 'system'
  }
}

export function writeMode(mode: ThemeMode): void {
  try {
    localStorage.setItem(THEME_KEY, mode)
  } catch {
    // stockage indisponible : le choix vaut pour cette session seulement
  }
}

export const systemPrefersDark = (): boolean => typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches

/** Le thème effectif : « automatique » suit le système. */
export const resolveDark = (mode: ThemeMode, systemDark: boolean): boolean => mode === 'dark' || (mode === 'system' && systemDark)

export function applyDark(dark: boolean, root: HTMLElement = document.documentElement): void {
  root.classList.toggle(DARK_CLASS, dark)
}
