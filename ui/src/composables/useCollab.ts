import { ref, watch } from 'vue'
import { describeError } from '@/composables/useNotify'
import { useServices } from '@/lib/services'
import type { Collab } from '@/lib/types'

/** Les commentaires, assignations et pièces jointes d'un document : lus à l'ouverture et relus après chaque changement (le serveur reste la référence). */
export function useCollab(doc: () => { doctype: string; name: string }) {
  const { api } = useServices()
  const data = ref<Collab | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let ticket = 0

  async function reload(): Promise<void> {
    const mine = ++ticket
    const { doctype, name } = doc()
    loading.value = true
    try {
      const got = await api.collab(doctype, name)
      if (mine !== ticket) return // on a changé de document pendant la lecture : cette réponse n'est plus la bonne
      data.value = got
      error.value = null
    } catch (e) {
      if (mine === ticket) error.value = describeError(e)
    } finally {
      if (mine === ticket) loading.value = false
    }
  }

  watch(
    () => `${doc().doctype}\u0000${doc().name}`,
    () => {
      data.value = null
      void reload()
    },
    { immediate: true },
  )

  return { data, loading, error, reload }
}
