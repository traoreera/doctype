import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '@/lib/api'
import { useServices } from '@/lib/services'
import { useMeta } from '@/stores/meta'
import { useSession } from '@/stores/session'

/**
 * MON profil (DocType `User Profile` du plugin `iam`, un document par utilisateur nommé comme son compte) : le nom affiché et la photo de la barre du haut.
 * Une commodité : sans ce DocType (plugin absent, droit retiré) ou sans profil encore créé, le Desk garde l'adresse e-mail et les initiales.
 */
export const PROFILE_DOCTYPE = 'User Profile'

export const useProfile = defineStore('profile', () => {
  const { api } = useServices()
  const session = useSession()
  const meta = useMeta()
  const fullName = ref<string | null>(null)
  const photo = ref<string | null>(null)
  const jobTitle = ref<string | null>(null)
  /** Le DocType est-il offert à cet utilisateur ? (`null` : on ne le sait pas encore) */
  const available = computed(() => (meta.summaries === null ? null : meta.summaries.some((s) => s.name === PROFILE_DOCTYPE)))
  /** Le nom à montrer : celui du profil, sinon l'adresse. */
  const label = computed(() => fullName.value || session.user)
  let loading: Promise<void> | null = null

  function load(force = false): Promise<void> {
    const me = session.claims?.sub
    if (!me || available.value !== true) return Promise.resolve()
    if (loading && !force) return loading
    loading = api
      .get(PROFILE_DOCTYPE, me)
      .then((doc) => {
        const text = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)
        fullName.value = text(doc.data.full_name)
        photo.value = text(doc.data.photo)
        jobTitle.value = text(doc.data.job_title)
      })
      .catch((e) => {
        if (!(e instanceof ApiError) || e.status !== 404) return // une panne ne change rien ; un 404 = pas encore de profil
        fullName.value = photo.value = jobTitle.value = null
      })
      .finally(() => (loading = null))
    return loading
  }

  /** Le profil existe-t-il ? (pour ouvrir « Mon profil » sur sa fiche ou sur sa création) */
  async function exists(): Promise<boolean> {
    const me = session.claims?.sub
    if (!me) return false
    try {
      await api.get(PROFILE_DOCTYPE, me)
      return true
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return false
      throw e
    }
  }

  function reset(): void {
    fullName.value = photo.value = jobTitle.value = null
    loading = null
  }

  return { fullName, photo, jobTitle, available, label, load, exists, reset }
})
