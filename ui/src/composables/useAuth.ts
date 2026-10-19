import { ref } from 'vue'
import { ApiError } from '@/lib/api'
import { describeError } from '@/composables/useNotify'
import { useServices } from '@/lib/services'
import type { XauthClient } from '@/lib/xauth'
import { t } from '@/i18n'

/** Le client d'xauth, ou une erreur claire : ces pages n'ont de sens que connecté à xauth (adresse de connexion configurée). */
export function useAuth(): XauthClient {
  const { auth } = useServices()
  if (!auth) throw new Error('xauth n’est pas configuré (login_url)')
  return auth
}

/** Charge quelque chose en distinguant « réservé » (403) d'une panne : l'état d'une page d'administration. */
export function useLoad<T>(load: () => Promise<T>) {
  const data = ref<T | null>(null) as { value: T | null }
  const forbidden = ref(false)
  const error = ref<string | null>(null)
  const loading = ref(false)
  let ticket = 0

  async function run(): Promise<void> {
    const mine = ++ticket
    loading.value = true
    try {
      const got = await load()
      if (mine !== ticket) return
      data.value = got
      forbidden.value = false
      error.value = null
    } catch (e) {
      if (mine !== ticket) return
      forbidden.value = e instanceof ApiError && e.status === 403
      error.value = forbidden.value ? null : describeError(e) || t.admin.failed
    } finally {
      if (mine === ticket) loading.value = false
    }
  }
  return { data, forbidden, error, loading, reload: run }
}
