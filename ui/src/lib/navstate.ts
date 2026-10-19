/**
 * Ce que la barre latérale se rappelle de CET appareil : repliée ou non, quels espaces sont dépliés. Une commodité, pas une donnée du
 * compte : localStorage, enveloppé (navigation privée) ; sans lui, tout part de zéro.
 */
export interface NavState {
  /** La barre latérale est repliée (grand écran). */
  collapsed: boolean
  /** Choix explicites de l'utilisateur, par espace : `true` déplié, `false` replié. Absent : suit la page courante. */
  expanded: Record<string, boolean>
}

export const NAV_KEY = 'xdt.desk.nav'

export function readNav(): NavState {
  try {
    const raw = JSON.parse(localStorage.getItem(NAV_KEY) ?? '{}') as Partial<NavState> | null
    const expanded: Record<string, boolean> = {}
    for (const [k, v] of Object.entries(raw?.expanded ?? {})) if (typeof v === 'boolean') expanded[k] = v
    return { collapsed: raw?.collapsed === true, expanded }
  } catch {
    return { collapsed: false, expanded: {} }
  }
}

export function writeNav(state: NavState): void {
  try {
    localStorage.setItem(NAV_KEY, JSON.stringify(state))
  } catch {
    // le choix vaut pour cette session seulement
  }
}

/** Initiales d'un nom d'utilisateur ou d'une adresse : « alice.martin@x.fr » -> « AM », « bob » -> « BO ». */
export function initials(user: string): string {
  const local = user.split('@')[0] ?? ''
  const words = local.split(/[._\-\s]+/).filter(Boolean)
  const letters = words.length > 1 ? `${words[0]![0]}${words[1]![0]}` : (words[0] ?? '').slice(0, 2)
  return letters.toUpperCase() || '?'
}
