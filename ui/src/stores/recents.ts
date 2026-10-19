import { defineStore } from 'pinia'
import { ref } from 'vue'
import { pushRecent, readRecents, recentsKey, writeRecents, type Recent } from '@/lib/recents'
import { useSession } from '@/stores/session'

/** Les derniers documents ouverts par CET utilisateur dans CETTE organisation (voir lib/recents.ts). */
export const useRecents = defineStore('recents', () => {
  const session = useSession()
  const items = ref<Recent[]>([])
  let loadedFor = ''

  const key = () => recentsKey(session.tenant, session.user)

  /** Relit la liste de l'utilisateur courant (à l'ouverture de l'accueil, et après un changement de compte). */
  function load(): void {
    loadedFor = key()
    items.value = readRecents(loadedFor)
  }

  function touch(doctype: string, name: string, title: string): void {
    if (key() !== loadedFor) load()
    items.value = pushRecent(items.value, { doctype, name, title: title || name, at: Date.now() })
    writeRecents(loadedFor, items.value)
  }

  function forget(): void {
    items.value = []
    loadedFor = ''
  }

  return { items, load, touch, forget }
})
