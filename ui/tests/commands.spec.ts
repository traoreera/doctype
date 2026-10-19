import { describe, expect, it } from 'vitest'
import { buildCommands, normalize, rank, step, type CommandSources } from '@/lib/commands'
import type { Recent } from '@/lib/recents'
import type { DocTypeSummary, Workspace } from '@/lib/types'

const SUMMARIES: DocTypeSummary[] = [
  { name: 'Item', label: 'Article', module: 'stock', create: true, icon: 'tag' },
  { name: 'Bin', label: 'Solde', module: 'stock', create: false },
  { name: 'Stock Entry', label: 'Mouvement de stock', module: 'stock', create: true, icon: 'arrow-right-arrow-left' },
  { name: 'Invoice', label: 'Facture', module: 'sales', create: true },
]
const WORKSPACES: Workspace[] = [
  { name: 'stock', label: 'Stock', icon: 'warehouse', description: 'Réceptions, sorties et transferts', blocks: [] },
  { name: 'sales', label: 'Ventes', icon: 'shopping-cart', blocks: [] },
]
const RECENTS: Recent[] = [{ doctype: 'Invoice', name: 'FA-2026-00001', title: 'Facture ACME', at: 1 }]
const src = (over: Partial<CommandSources> = {}): CommandSources => ({ workspaces: WORKSPACES, summaries: SUMMARIES, recents: RECENTS, ...over })
const ids = (cs: { id: string }[]) => cs.map((c) => c.id)

describe('normalisation', () => {
  it('sans accent ni majuscule', () => {
    expect(normalize('  Écrou M8 Réception ')).toBe('ecrou m8 reception')
  })
})

describe('commandes proposées', () => {
  const all = buildCommands(src())

  it('un type = « aller à sa liste » ; « créer » seulement là où l’on peut créer', () => {
    expect(all.find((c) => c.id === 'go:Item')).toMatchObject({ label: 'Article', to: { name: 'list', params: { doctype: 'Item' } }, icon: 'tag' })
    expect(ids(all)).toEqual(expect.arrayContaining(['new:Item', 'new:Invoice', 'new:Stock Entry']))
    expect(ids(all)).not.toContain('new:Bin')
  })

  it('un réglage mène à la page Paramètres, pas à une liste', () => {
    const cs = buildCommands(src({ summaries: [...SUMMARIES, { name: 'Stock Settings', label: 'Paramètres du stock', module: 'stock', single: true }] }))
    expect(cs.find((c) => c.id === 'go:Stock Settings')).toMatchObject({ to: { name: 'settings', params: { doctype: 'Stock Settings' } }, hint: 'Paramètres' })
  })

  it('un réglage global mène à « Général », sans paramètre', () => {
    const cs = buildCommands(src({ summaries: [...SUMMARIES, { name: 'System Settings', label: 'Paramètres généraux', module: 'system', single: true, settings_group: 'general' }] }))
    expect(cs.find((c) => c.id === 'go:System Settings')?.to).toEqual({ name: 'settings' })
  })

  it('les espaces, les récents et les actions y sont', () => {
    expect(all.find((c) => c.id === 'space:stock')).toMatchObject({ to: { name: 'workspace', params: { name: 'stock' } }, icon: 'warehouse' })
    expect(all.find((c) => c.id === 'recent:Invoice/FA-2026-00001')).toMatchObject({ label: 'Facture ACME', hint: 'Facture' })
    expect(all.filter((c) => c.group === 'action').map((c) => c.action)).toEqual(['theme-light', 'theme-dark', 'theme-system', 'logout'])
  })

  it('un récent d’un type que le compte ne voit plus n’est pas proposé', () => {
    const cs = buildCommands(src({ recents: [{ doctype: 'Secret', name: 'x', title: 'Dossier', at: 1 }] }))
    expect(cs.some((c) => c.group === 'recent')).toBe(false)
  })

  it('un compte sans aucun droit n’a que l’accueil et les actions', () => {
    const cs = buildCommands(src({ workspaces: [], summaries: [], recents: [] }))
    expect(ids(cs)).toEqual(['home', 'tasks', 'theme-light', 'theme-dark', 'theme-system', 'logout'])
  })
})

describe('classement', () => {
  const all = buildCommands(src())

  it('sans saisie : les récents d’abord, puis les espaces, puis les types ; jamais les actions ni « créer »', () => {
    const out = rank(all, '', 20)
    expect(out[0]!.group).toBe('recent')
    expect(out.every((c) => ['recent', 'space', 'go'].includes(c.group))).toBe(true)
  })

  it('trouve sans accent ni majuscule', () => {
    expect(ids(rank(all, 'REGLEMENT'))).toEqual([]) // rien de tel
    expect(ids(rank(all, 'mouvement'))).toContain('go:Stock Entry')
    expect(ids(rank(buildCommands(src({ summaries: [{ name: 'X', label: 'Écrou', module: 'm' }] })), 'ecrou'))).toContain('go:X')
  })

  it('un mot qui COMMENCE le libellé passe devant un mot seulement contenu', () => {
    const out = rank(all, 'stock')
    // « Stock » (espace) commence par stock ; « Mouvement de stock » ne le contient que comme dernier mot.
    expect(out[0]!.id).toBe('space:stock')
    expect(ids(out).indexOf('go:Stock Entry')).toBeGreaterThan(0)
  })

  it('tous les mots doivent figurer', () => {
    expect(ids(rank(all, 'nouveau facture'))).toEqual(['new:Invoice'])
    expect(rank(all, 'facture zzz')).toEqual([])
  })

  it('« créer » une facture se trouve avec ses synonymes', () => {
    expect(ids(rank(all, 'creer facture'))).toContain('new:Invoice')
    expect(ids(rank(all, 'ajouter article'))).toContain('new:Item')
  })

  it('les actions se trouvent par ce qu’elles font', () => {
    expect(ids(rank(all, 'sombre'))).toContain('theme-dark')
    expect(ids(rank(all, 'quitter'))).toEqual(['logout'])
  })

  it('la limite est respectée', () => {
    expect(rank(all, 'e', 3)).toHaveLength(3)
  })

  it('un type est atteint par son nom technique aussi', () => {
    expect(ids(rank(all, 'stock entry'))).toContain('go:Stock Entry')
  })
})

describe('flèches', () => {
  it('on boucle dans les deux sens, et une liste vide ne casse rien', () => {
    expect([step(0, 1, 3), step(2, 1, 3), step(0, -1, 3), step(1, -1, 3)]).toEqual([1, 0, 2, 0])
    expect(step(0, 1, 0)).toBe(0)
  })
})
