import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useServices } from '@/lib/services'

/** Combien de tâches ouvertes m'attendent (la pastille de « Mes tâches »). Une commodité : un échec laisse la pastille telle qu'elle est. */
export const useTasks = defineStore('tasks', () => {
  const { api } = useServices()
  const openCount = ref(0)
  let loading: Promise<void> | null = null

  function refresh(): Promise<void> {
    loading ??= Promise.resolve()
      .then(() => api.myAssignments('open'))
      .then((items) => void (openCount.value = items.length))
      .catch(() => {})
      .finally(() => (loading = null))
    return loading
  }

  function reset(): void {
    openCount.value = 0
    loading = null
  }

  return { openCount, refresh, reset }
})
