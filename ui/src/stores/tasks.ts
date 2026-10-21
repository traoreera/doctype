import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useServices } from '@/lib/services'
import type { AssignmentItem } from '@/lib/types'

/** Combien de tâches ouvertes m'attendent (la pastille de « Mes tâches »). Une commodité : un échec laisse la pastille telle qu'elle est. */
export const useTasks = defineStore('tasks', () => {
  const { api } = useServices()
  const openCount = ref(0)
  /** Les tâches ouvertes elles-mêmes (l'accueil en montre les plus proches). */
  const items = ref<AssignmentItem[]>([])
  let loading: Promise<void> | null = null

  function refresh(): Promise<void> {
    loading ??= Promise.resolve()
      .then(() => api.myAssignments('open'))
      .then((found) => {
        items.value = found
        openCount.value = found.length
      })
      .catch(() => {})
      .finally(() => (loading = null))
    return loading
  }

  function reset(): void {
    openCount.value = 0
    items.value = []
    loading = null
  }

  return { openCount, items, refresh, reset }
})
