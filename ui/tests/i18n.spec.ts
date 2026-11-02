import { afterEach, describe, expect, it, vi } from 'vitest'
import { en } from '@/i18n/en'
import { fr } from '@/i18n/fr'
import { actionLabel, formatCell, optionLabel, stateLabel } from '@/lib/format'
import type { FieldMeta } from '@/lib/types'

/**
 * L'i18n du Desk (`src/i18n.ts`) : deux dictionnaires de même forme, une langue fixée au chargement de la page, la langue du serveur
 * adoptée en rechargeant. Les tests tournent en français (tests/setup-language.ts) ; l'anglais se vérifie en rechargeant le module.
 */
afterEach(() => {
  localStorage.setItem('desk.language', 'fr')
  vi.unstubAllGlobals()
  vi.resetModules()
})

/** Les chemins des feuilles d'un dictionnaire, avec la nature de chacune (texte, fonction de n paramètres, liste). */
function shape(o: unknown, at = ''): string[] {
  if (typeof o === 'function') return [`${at}:fn${o.length}`]
  if (Array.isArray(o)) return [`${at}:list${o.length}`]
  if (o && typeof o === 'object') return Object.entries(o).flatMap(([k, v]) => shape(v, `${at}.${k}`))
  return [`${at}:${typeof o}`]
}

async function loadWith(stored: string | null, browser = 'en-US') {
  vi.resetModules()
  if (stored === null) localStorage.removeItem('desk.language')
  else localStorage.setItem('desk.language', stored)
  vi.stubGlobal('navigator', { language: browser })
  return import('@/i18n')
}

describe('les dictionnaires', () => {
  it('ont exactement la même forme (clés, fonctions et leur nombre de paramètres, listes)', () => {
    expect(shape(en)).toEqual(shape(fr))
  })

  it('l’anglais est vraiment traduit : pas un texte du français recopié tel quel (hors noms propres et symboles)', () => {
    const same: string[] = []
    const walk = (a: unknown, b: unknown, at: string) => {
      if (typeof a === 'string' && a === b && /[a-zà-ÿ]{4,}/i.test(a) && !SAME_IN_BOTH.has(a) && !at.includes('.icons.')) same.push(`${at}: ${a}`)
      else if (a && typeof a === 'object' && !Array.isArray(a)) for (const k of Object.keys(a)) walk((a as Record<string, unknown>)[k], (b as Record<string, unknown>)?.[k], `${at}.${k}`)
    }
    walk(en, fr, '')
    expect(same.join('\n')).toBe('') // une ligne par texte non traduit : le message d'échec les montre toutes
  })
})

// Des mots identiques dans les deux langues (ou des noms) : les seuls permis.
const SAME_IN_BOTH = new Set([
  'Desk', 'Extensions', 'Extension', 'Transition', 'Description', 'Module', 'Kanban', 'Gantt', 'Notifications', 'Image', 'Code', 'Total',
  'Minimum', 'Maximum', 'Orange', 'Portrait', 'Normal', 'Tenant', 'Actions', 'Action', 'Permissions', 'Plugin', 'Condition', 'Message',
  'Logo', 'Type', 'Documents', 'Document', 'Options', 'Format', 'Page', 'Section', 'Sections', 'Rapport', 'JSON', 'Version', 'Mode', 'Ctrl G',
  'Champs', 'Filtres', 'Valeurs', 'Rôles', 'Dates', 'Date', 'Science', 'Contact', 'Note', 'Notes', 'Article', 'Articles', 'Statut', 'Contexte',
  'Collapsible', 'Thème', 'Compte', 'Organisation', 'Profil', 'Total', 'Jeton', 'Langue',
  'French', 'English', 'Français', 'MFA', 'Connections', 'API', 'Zoom', 'Tags', 'Label', 'Labels',
  'Menu', 'inactive', 'mention', 'auto', 'Orientation', 'Centre', 'Image…', 'Routes', 'unique',
])

describe('la langue de la page', () => {
  it('le choix gardé par ce navigateur passe avant tout', async () => {
    const m = await loadWith('en', 'fr-FR')
    expect([m.language, m.t.save, m.intlLocale]).toEqual(['en', 'Save', 'en-GB'])
  })

  it('sans choix gardé : la langue du navigateur si le Desk la parle, sinon le français', async () => {
    expect((await loadWith(null, 'en-US')).language).toBe('en')
    expect((await loadWith(null, 'de-DE')).language).toBe('fr')
    expect((await loadWith('xx', 'fr-FR')).t.save).toBe('Enregistrer')
  })

  it('la langue du serveur est adoptée en rechargeant, la sienne avant celle de l’organisation, et jamais en boucle', async () => {
    const m = await loadWith('fr')
    const reload = vi.fn()
    expect(m.adoptLanguage({ language: 'fr' }, reload)).toBe(false) // déjà la bonne
    expect(m.adoptLanguage({ language: 'de' }, reload)).toBe(false) // une langue que le Desk ne parle pas : rien
    expect(m.adoptLanguage({ language: 'fr', my_language: 'en' }, reload)).toBe(true)
    expect(reload).toHaveBeenCalledOnce()
    expect(localStorage.getItem('desk.language')).toBe('en')
    expect((await loadWith(localStorage.getItem('desk.language'))).t.save).toBe('Save') // après le rechargement : l'anglais
  })
})

describe('les libellés traduits par le serveur', () => {
  const purpose = { fieldname: 'purpose', fieldtype: 'Select', options: ['Receipt', 'Issue'], option_labels: { Receipt: 'Réception' } } as unknown as FieldMeta

  it('une option se lit par son libellé, se choisit par sa valeur ; sans libellé, la valeur', () => {
    expect(optionLabel(purpose, 'Receipt')).toBe('Réception')
    expect(optionLabel(purpose, 'Issue')).toBe('Issue')
    expect(formatCell(purpose, 'Receipt')).toBe('Réception')
  })

  it('les états et actions de workflow aussi', () => {
    const meta = { state_labels: { Draft: 'Brouillon' }, action_labels: { Approve: 'Approuver' } }
    expect([stateLabel(meta, 'Draft'), stateLabel(meta, 'Approved'), actionLabel(meta, 'Approve'), actionLabel(null, 'Reject')]).toEqual([
      'Brouillon', 'Approved', 'Approuver', 'Reject',
    ])
  })

  it('un booléen suit la langue de la page', () => {
    expect(formatCell({ fieldname: 'ok', fieldtype: 'Boolean' } as FieldMeta, true)).toBe(fr.yes)
  })
})
