import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { DocTypeMeta, DocTypeSummary } from '@/lib/types'
import { useServices } from '@/lib/services'

/** Cache des définitions de DocType. Elles ne changent qu'au rechargement du plugin : on les relit à la reconnexion. */
export const useMeta = defineStore('meta', () => {
  const { api } = useServices()
  const summaries = ref<DocTypeSummary[] | null>(null)
  const metas = ref<Record<string, DocTypeMeta>>({})
  const loading = new Map<string, Promise<DocTypeMeta>>()

  async function loadSummaries(force = false): Promise<DocTypeSummary[]> {
    if (!summaries.value || force) summaries.value = await api.doctypes()
    return summaries.value
  }

  /** Une seule requête en vol par DocType, même si dix composants le demandent en même temps. */
  function get(name: string): Promise<DocTypeMeta> {
    const known = metas.value[name]
    if (known) return Promise.resolve(known)
    let pending = loading.get(name)
    if (!pending) {
      pending = api
        .meta(name)
        .then((m) => {
          metas.value[name] = m
          return m
        })
        .finally(() => loading.delete(name))
      loading.set(name, pending)
    }
    return pending
  }

  function reset(): void {
    summaries.value = null
    metas.value = {}
    loading.clear()
  }

  /** Relit une définition (après un habillage de formulaire : la disposition peut avoir changé). */
  function refresh(name: string): Promise<DocTypeMeta> {
    delete metas.value[name]
    return get(name)
  }

  return { summaries, metas, loadSummaries, get, refresh, reset }
})
