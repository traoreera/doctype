import type { Workspace, WorkspaceShortcut } from './types'

export interface Section {
  title: string | null
  items: WorkspaceShortcut[]
}

/**
 * Les sous-modules d'un espace pour la barre latérale : ses raccourcis rangés sous le titre qui les précède (« Opérations », « Référentiel »).
 * Un titre sans raccourci (celui des chiffres et graphiques) n'y est pas ; des raccourcis sans titre forment une section anonyme.
 * Un réglage (document unique) n'y est jamais : il est dans Paramètres.
 */
export function workspaceSections(w: Workspace): Section[] {
  const out: Section[] = []
  let title: string | null = null
  let current: Section | null = null
  for (const b of w.blocks) {
    if (b.type === 'header') {
      title = b.text
      current = null
    } else if (b.type === 'shortcut' && !b.single) {
      if (!current) out.push((current = { title, items: [] }))
      current.items.push(b)
    }
  }
  return out
}
