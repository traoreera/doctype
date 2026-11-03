import { onBeforeUnmount, ref, shallowRef, watch, type Ref } from 'vue'
import { describeError } from '@/composables/useNotify'
import { linkTargets } from '@/lib/aggregate'
import { useServices } from '@/lib/services'
import type { AggregateQuery, AggregateResult } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useLiveDoctype } from '@/composables/useLive'

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

  /** `quiet` : relu sur un signal temps réel, la carte garde son chiffre jusqu'au nouveau (pas de clignotement). */
  async function reload(quiet = false): Promise<void> {
    const mine = ++ticket
    const { doctype, query } = source()
    if (!quiet || !result.value) loading.value = true
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

  watch(() => JSON.stringify(source()), () => reload(), { immediate: true })
  // Temps réel : un document du type compté a changé, le chiffre suit (relu avec les droits de l'utilisateur).
  useLiveDoctype(() => source().doctype, () => void reload(true))
  onBeforeUnmount(() => {
    alive = false
  })
  return { result, loading, error, reload: () => reload() } // jamais `quiet` depuis un bouton (@click passerait l'événement)
}
