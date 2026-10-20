import { ref } from 'vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { useServices } from '@/lib/services'

/** La limite du serveur pour une pièce jointe : on prévient avant d'envoyer, il tranche de toute façon. */
export const ATTACH_MAX_MB = 10

/** Joindre des fichiers à un document (le bouton de la carte, la commande `/joindre`) : trop gros, on n'envoie pas ; une panne n'efface pas ce qui est déjà parti. */
export function useAttach(doc: () => { doctype: string; name: string }, done: () => void) {
  const { api } = useServices()
  const notify = useNotify()
  const uploading = ref(false)

  async function upload(list: readonly File[]): Promise<void> {
    if (!list.length) return
    const { doctype, name } = doc()
    uploading.value = true
    try {
      for (const file of list) {
        if (file.size > ATTACH_MAX_MB * 1024 * 1024) {
          notify.error(new Error(t.collab.tooLarge(ATTACH_MAX_MB)))
          continue
        }
        await api.attach(doctype, name, file)
      }
      done()
    } catch (err) {
      notify.error(err)
      done() // ce qui a pu partir est là
    } finally {
      uploading.value = false
    }
  }

  return { uploading, upload }
}
