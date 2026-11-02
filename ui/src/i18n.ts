/**
 * Les textes de l'interface, dans la langue de l'utilisateur (`i18n/fr.ts`, `i18n/en.ts` : même forme, vérifiée par TypeScript).
 *
 * La langue est fixée au CHARGEMENT de la page : `t` est importé tel quel par tout le Desk, et le rendre réactif obligerait chaque texte
 * calculé une fois (titres, menus construits au montage) à se recalculer. Changer de langue enregistre le choix et RECHARGE la page, comme
 * changer d'organisation. Ordre : le choix gardé par ce navigateur (posé par `adoptLanguage` d'après « Mon profil », sinon la langue de
 * l'organisation), sinon la langue du navigateur, sinon le français.
 */
import { en } from './i18n/en'
import { fr, type Messages } from './i18n/fr'

export type { Messages }
export const LANGUAGES = ['fr', 'en'] as const
export type Language = (typeof LANGUAGES)[number]
const DICTIONARIES: Record<Language, Messages> = { fr, en }
/** Clé du choix gardé par ce navigateur. */
export const LANGUAGE_KEY = 'desk.language'

export const isLanguage = (v: unknown): v is Language => typeof v === 'string' && (LANGUAGES as readonly string[]).includes(v)

function stored(): Language | null {
  try {
    const v = globalThis.localStorage?.getItem(LANGUAGE_KEY)
    return isLanguage(v) ? v : null
  } catch {
    return null
  }
}

function fromBrowser(): Language | null {
  const tag = (globalThis.navigator?.language ?? '').slice(0, 2).toLowerCase()
  return isLanguage(tag) ? tag : null
}

export const language: Language = stored() ?? fromBrowser() ?? 'fr'
export const t: Messages = DICTIONARIES[language]
/** La locale des formats (`Intl`) : nombres, dates, devises. L'anglais britannique garde le jour avant le mois. */
export const intlLocale = language === 'en' ? 'en-GB' : 'fr-FR'

/**
 * La langue que le serveur dit (la sienne d'abord, sinon celle de l'organisation) : si elle diffère de celle de la page, on la garde et
 * on recharge. Rend `true` quand un rechargement est lancé. `reload` est injectable pour les tests.
 */
export function adoptLanguage(prefs: { my_language?: unknown; language?: unknown }, reload: () => void = () => globalThis.location?.reload()): boolean {
  const wanted = isLanguage(prefs.my_language) ? prefs.my_language : isLanguage(prefs.language) ? prefs.language : null
  if (wanted === null || wanted === language) return false
  try {
    globalThis.localStorage?.setItem(LANGUAGE_KEY, wanted)
  } catch {
    return false // sans stockage, on ne recharge pas en boucle : la page reste dans sa langue
  }
  reload()
  return true
}
