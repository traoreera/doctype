import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { useServices } from '@/lib/services'
import type { Person } from '@/lib/types'

const BATCH = 100 // plafond du serveur
const UUID_LIKE = /^[0-9a-f]{8}-?[0-9a-f-]{8,}$/i

/** Ce qu'on affiche quand on ne connaît pas la personne : un identifiant lisible tel quel, un UUID abrégé (« 1f3a9c2e… »). */
export const fallbackName = (id: string): string => (UUID_LIKE.test(id) ? `${id.slice(0, 8)}…` : id)

/**
 * Les personnes de l'organisation, par identifiant : un document porte des identités (auteur d'un commentaire, assigné, propriétaire), qui chez xauth
 * sont des UUID. `label(id)` rend l'e-mail dès qu'on le connaît (une requête par lot, partagée), l'identifiant abrégé en attendant ou si la personne
 * n'est plus membre. Une personne inconnue n'est pas redemandée à chaque affichage.
 */
export const useUsers = defineStore('users', () => {
  const { api } = useServices()
  const people = reactive<Record<string, Person>>({})
  const asked = new Set<string>()
  let queue: string[] = []
  let scheduled = false

  async function flush(): Promise<void> {
    scheduled = false
    const ids = queue
    queue = []
    for (let i = 0; i < ids.length; i += BATCH) {
      const chunk = ids.slice(i, i + BATCH)
      try {
        const got = await api.users(chunk)
        for (const id of chunk) people[id] = got[id] ?? { id, email: null, name: fallbackName(id) }
      } catch {
        for (const id of chunk) asked.delete(id) // injoignable : on réessaiera à un prochain affichage
      }
    }
  }

  /** Demande ces identifiants (ceux qu'on ne connaît pas), en un lot, à la fin du tour courant. */
  function want(ids: Iterable<string | null | undefined>): void {
    for (const id of ids) {
      if (!id || id in people || asked.has(id)) continue
      asked.add(id)
      queue.push(id)
    }
    if (queue.length && !scheduled) {
      scheduled = true
      queueMicrotask(() => void flush())
    }
  }

  /** Le nom à afficher pour cet identifiant (et le demande s'il est inconnu). */
  function label(id: string | null | undefined): string {
    if (!id) return ''
    want([id])
    return people[id]?.name ?? fallbackName(id)
  }

  function reset(): void {
    for (const k of Object.keys(people)) delete people[k]
    asked.clear()
    queue = []
  }

  return { people, want, label, reset }
})
