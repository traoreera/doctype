import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { describeError } from '@/composables/useNotify'
import { linkTargets } from '@/lib/aggregate'
import { useServices } from '@/lib/services'
import type { AggregateQuery, AggregateResult } from '@/lib/types'
import { useLinks } from '@/stores/links'

/**
 * Lit un agrégat pour un bloc (carte, graphique) et le relit à la demande. Une réponse tardive d'une demande périmée n'écrase pas la
 * dernière ; les titres des documents d'un regroupement Link sont lus AVANT d'afficher (un nom opaque ne se lit pas sur un graphique).
 */
export function useAggregate(source: () => { doctype: string; query: AggregateQuery }): {
  result: Ref<AggregateResult | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  reload: () => Promise<void>
} {
  const { api } = useServices()
  const links = useLinks()
  const result = shallowRef<AggregateResult | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let ticket = 0
  let alive = true

  async function reload(): Promise<void> {
    const mine = ++ticket
    const { doctype, query } = source()
    loading.value = true
    error.value = null
    try {
      const res = await api.aggregate(doctype, query)
      const targets = linkTargets(res)
      if (targets) await links.resolve(targets.target, targets.names)
      if (mine !== ticket || !alive) return
      result.value = res
    } catch (e) {
      if (mine !== ticket || !alive) return
      result.value = null
      error.value = describeError(e)
    } finally {
      if (mine === ticket) loading.value = false
    }
  }

  watch(() => JSON.stringify(source()), reload, { immediate: true })
  onBeforeUnmount(() => {
    alive = false
  })
  return { result, loading, error, reload }
}
