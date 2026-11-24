import { onScopeDispose, ref, type Ref } from 'vue'
import { describeError } from '@/composables/useNotify'
import type { ApiClient } from '@/lib/api'
import type { Job } from '@/lib/types'

/** Une tâche finie ne change plus : inutile de la relire. */
export const jobOver = (job: Job | null): boolean => job?.status === 'done' || job?.status === 'failed'

/**
 * Suit UNE tâche de fond jusqu'à sa fin (traoreera/doctype#71) : « plan comptable en cours d'installation… 42/100 comptes ».
 *
 * Relit son état à intervalle fixe tant qu'elle est en attente ou en cours, s'arrête dès qu'elle est finie, et quand le
 * composant qui l'affiche disparaît. Une relecture qui échoue (réseau coupé) n'arrête pas le suivi : la suivante réessaie.
 *
 *   const { job, failure } = useJob(api, () => record.value?.data.install_job as string)
 */
export function useJob(api: Pick<ApiClient, 'job'>, id: () => string | null | undefined, everyMs = 2000): { job: Ref<Job | null>; failure: Ref<string | null>; refresh: () => Promise<void>; stop: () => void } {
  const job = ref<Job | null>(null)
  const failure = ref<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let stopped = false

  async function refresh(): Promise<void> {
    const wanted = id()
    if (!wanted) return
    try {
      job.value = await api.job(wanted)
      failure.value = null
    } catch (e) {
      failure.value = describeError(e)
    }
  }

  async function tick(): Promise<void> {
    timer = null
    await refresh()
    if (!stopped && !jobOver(job.value)) timer = setTimeout(() => void tick(), everyMs)
  }

  function stop(): void {
    stopped = true
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  void tick()
  onScopeDispose(stop)
  return { job, failure, refresh, stop }
}
