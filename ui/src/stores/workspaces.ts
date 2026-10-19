import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useServices } from '@/lib/services'
import { workspaceSections } from '@/lib/sections'
import type { Workspace, WorkspaceShortcut } from '@/lib/types'

/** Les espaces de travail visibles par l'utilisateur (le serveur les a déjà filtrés par ses droits). Relus à la reconnexion. */
export const useWorkspaces = defineStore('workspaces', () => {
  const { api } = useServices()
  const items = ref<Workspace[] | null>(null)
  let loading: Promise<Workspace[]> | null = null

  /** Une seule requête en vol, même si la barre latérale et la page la demandent ensemble. */
  function load(force = false): Promise<Workspace[]> {
    if (items.value && !force) return Promise.resolve(items.value)
    loading ??= api
      .workspaces()
      .then((list) => (items.value = list))
      .finally(() => (loading = null))
    return loading
  }

  const byName = (name: string): Workspace | undefined => items.value?.find((w) => w.name === name)

  /** Les raccourcis d'un espace (sans les titres) : ce que la barre latérale affiche dessous. */
  const shortcuts = (w: Workspace): WorkspaceShortcut[] => w.blocks.filter((b): b is WorkspaceShortcut => b.type === 'shortcut')

  const sections = workspaceSections

  /** L'espace qui contient ce type de document (le premier raccourci qui y mène), pour déplier le bon dans la barre latérale. */
  const owning = (doctype: string): Workspace | undefined => items.value?.find((w) => shortcuts(w).some((s) => s.doctype === doctype))

  function reset(): void {
    items.value = null
    loading = null
  }

  return { items, load, byName, shortcuts, sections, owning, reset }
})
