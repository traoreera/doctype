/**
 * La barre de commande (Ctrl+G) : ce qu'elle propose et comment elle classe, sans interface : testé seul.
 *
 * Elle ne cherche PAS dans les documents (la recherche globale est la phase E du palier 40 %) : elle va aux types de document, aux
 * espaces de travail, à « Nouveau … », aux derniers documents ouverts, et lance quelques actions (thème, déconnexion).
 */
import type { RouteLocationRaw } from 'vue-router'
import { t } from '@/i18n'
import type { Recent } from './recents'
import type { DocTypeSummary, GlobalSearchItem, Workspace } from './types'

export type CommandGroup = 'recent' | 'space' | 'go' | 'new' | 'doc' | 'action'
export type ActionName = 'theme-light' | 'theme-dark' | 'theme-system' | 'logout'

export interface Command {
  id: string
  label: string
  /** Précision affichée à droite (« Stock », « Liste »…). */
  hint?: string
  icon: string
  group: CommandGroup
  to?: RouteLocationRaw
  action?: ActionName
  /** Texte normalisé sur lequel on cherche (libellé + précision + mots-clés). */
  search: string
}

/** Sans accent ni majuscule : « Écrou » se trouve en tapant « ecrou ». */
export const normalize = (s: string): string => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim()

const make = (c: Omit<Command, 'search'>, keywords = ''): Command => ({ ...c, search: normalize(`${c.label} ${c.hint ?? ''} ${keywords}`) })

export interface CommandSources {
  workspaces: readonly Workspace[]
  summaries: readonly DocTypeSummary[]
  recents: readonly Recent[]
}

export function buildCommands({ workspaces, summaries, recents }: CommandSources): Command[] {
  const out: Command[] = []
  const labelOf = (doctype: string) => summaries.find((d) => d.name === doctype)?.label ?? doctype
  for (const r of recents) {
    // Un récent d'un type qu'on ne voit plus n'est pas proposé.
    if (!summaries.some((d) => d.name === r.doctype)) continue
    out.push(make({ id: `recent:${r.doctype}/${r.name}`, label: r.title, hint: labelOf(r.doctype), icon: 'clock', group: 'recent', to: { name: 'form', params: { doctype: r.doctype, name: r.name } } }, r.name))
  }
  out.push(make({ id: 'home', label: t.home, icon: 'home', group: 'space', to: { name: 'home' } }))
  out.push(make({ id: 'tasks', label: t.collab.tasks.title, icon: 'check-square', group: 'space', to: { name: 'tasks' } }, 'taches a faire assignations'))
  for (const w of workspaces) {
    out.push(make({ id: `space:${w.name}`, label: w.label, hint: t.palette.workspace, icon: w.icon ?? 'folder', group: 'space', to: { name: 'workspace', params: { name: w.name } } }, w.description ?? ''))
  }
  for (const d of [...summaries].sort((a, b) => a.label.localeCompare(b.label, 'fr'))) {
    // Un réglage s'ouvre dans la page Paramètres, pas dans une liste.
    const to = d.single ? (d.settings_group ? { name: 'settings' } : { name: 'settings', params: { doctype: d.name } }) : { name: 'list', params: { doctype: d.name } }
    out.push(make({ id: `go:${d.name}`, label: d.label, hint: d.single ? t.settings.title : t.palette.list, icon: d.icon ?? 'file', group: 'go', to }, d.name))
    if (d.create) out.push(make({ id: `new:${d.name}`, label: t.homeNew(d.label), icon: 'plus', group: 'new', to: { name: 'new', params: { doctype: d.name } } }, `creer ajouter ${d.name}`))
  }
  if (summaries.length) out.push(make({ id: 'api-docs', label: t.apiDocs.title, icon: 'code', group: 'go', to: { name: 'api-docs' } }, 'api rest swagger curl documentation developpeur'))
  out.push(make({ id: 'theme-light', label: t.palette.themeLight, icon: 'sun', group: 'action', action: 'theme-light' }, 'theme clair'))
  out.push(make({ id: 'theme-dark', label: t.palette.themeDark, icon: 'moon', group: 'action', action: 'theme-dark' }, 'theme sombre nuit'))
  out.push(make({ id: 'theme-system', label: t.palette.themeSystem, icon: 'desktop', group: 'action', action: 'theme-system' }, 'theme systeme automatique'))
  out.push(make({ id: 'logout', label: t.nav.logout, icon: 'sign-out', group: 'action', action: 'logout' }, 'quitter sortir'))
  return out
}

/** Les documents trouvés par la recherche GLOBALE (Awesomebar), en commandes : ouvrir le formulaire du document. */
export function documentCommands(items: readonly GlobalSearchItem[]): Command[] {
  return items.map((it) =>
    make(
      {
        id: `doc:${it.doctype}/${it.name}`,
        label: it.title || it.name,
        hint: it.label,
        icon: 'file',
        group: 'doc',
        to: { name: 'form', params: { doctype: it.doctype, name: it.name } },
      },
      it.name,
    ),
  )
}

/** Les groupes qui passent devant, quand on ne tape rien. */
const EMPTY_ORDER: CommandGroup[] = ['recent', 'space', 'go']
const WEIGHT: Record<CommandGroup, number> = { recent: -0.5, space: 0, doc: 0.1, go: 0.25, new: 0.5, action: 1 }

/**
 * Sans saisie : les derniers documents, les espaces, puis les types. Avec saisie : chaque mot tapé doit figurer dans la commande ; un mot
 * qui COMMENCE le libellé (ou un de ses mots) passe devant un mot qui n'est que contenu ; à égalité, l'ordre des sources.
 */
export function rank(commands: readonly Command[], query: string, limit = 10): Command[] {
  const words = normalize(query).split(/\s+/).filter(Boolean)
  if (!words.length) return commands.filter((c) => EMPTY_ORDER.includes(c.group)).slice(0, limit)
  const scored: { c: Command; score: number; i: number }[] = []
  commands.forEach((c, i) => {
    let score = WEIGHT[c.group]
    const label = normalize(c.label)
    for (const w of words) {
      if (!c.search.includes(w)) return
      score += label.startsWith(w) ? 0 : label.split(/[\s\-_/]+/).some((x) => x.startsWith(w)) ? 1 : c.search.includes(w) && label.includes(w) ? 2 : 3
    }
    scored.push({ c, score, i })
  })
  return scored.sort((a, b) => a.score - b.score || a.i - b.i).slice(0, limit).map((x) => x.c)
}

/** Déplacement dans la liste avec les flèches, en bouclant. */
export const step = (index: number, delta: 1 | -1, length: number): number => (length ? (index + delta + length) % length : 0)
