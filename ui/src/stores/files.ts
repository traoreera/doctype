import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { useServices } from '@/lib/services'
import type { FileLink } from '@/lib/types'

const BATCH = 100 // plafond du serveur
const MARGIN = 30 // secondes : un lien qui expire dans moins de 30 s est redemandé
const RETRY_MS = 30_000 // un fichier refusé n'est pas redemandé à chaque affichage

/**
 * Les liens signés des fichiers (images, pièces jointes). Le serveur ne sert un fichier que par une URL signée de courte durée, pour la raison
 * que `<img>` n'envoie pas de jeton : le Desk demande les liens par lots, les garde jusqu'à quelques secondes avant leur expiration, et ne
 * redemande pas à chaque affichage un fichier qu'il n'a pas le droit de lire.
 */
export const useFiles = defineStore('files', () => {
  const { api, config } = useServices()
  const links = reactive<Record<string, FileLink>>({})
  const refused = new Map<string, number>() // id -> quand on a su qu'on ne pouvait pas le lire
  const inflight = new Map<string, Promise<void>>()

  const nowSec = (): number => Date.now() / 1000
  const usable = (id: string): boolean => !!links[id] && links[id]!.expires - nowSec() > MARGIN

  /** Lit les liens de ces fichiers (un par lot, partagé avec les demandes en vol). `force` : même ceux qu'on croit valides (une image qui a échoué). */
  async function resolve(ids: Iterable<string>, opts: { force?: boolean } = {}): Promise<void> {
    const wanted = [...new Set(ids)].filter((id) => {
      if (!id) return false
      if (!opts.force && usable(id)) return false
      const lost = refused.get(id)
      return opts.force || lost === undefined || Date.now() - lost > RETRY_MS
    })
    const waiting: Promise<void>[] = []
    const fresh: string[] = []
    for (const id of wanted) {
      const pending = inflight.get(id)
      if (pending) waiting.push(pending)
      else fresh.push(id)
    }
    for (let i = 0; i < fresh.length; i += BATCH) {
      const chunk = fresh.slice(i, i + BATCH)
      const job = fetchLinks(chunk)
      for (const id of chunk) inflight.set(id, job)
      void job.finally(() => chunk.forEach((id) => inflight.delete(id)))
      waiting.push(job)
    }
    await Promise.all(waiting)
  }

  async function fetchLinks(ids: string[]): Promise<void> {
    try {
      const got = await api.fileLinks(ids)
      for (const id of ids) {
        const link = got[id]
        if (link) {
          links[id] = link
          refused.delete(id)
        } else {
          delete links[id]
          refused.set(id, Date.now())
        }
      }
    } catch {
      for (const id of ids) refused.set(id, Date.now()) // injoignable ou refusé : on réessaiera dans un moment
    }
  }

  /** L'adresse à mettre dans `src` ou `href`, ou `null` tant qu'on ne l'a pas (ou qu'on n'a pas le droit). */
  const url = (id: string | null | undefined): string | null => (id && links[id] ? `${config.baseUrl}/${links[id]!.path}` : null)
  const info = (id: string | null | undefined): FileLink | null => (id ? (links[id] ?? null) : null)

  function reset(): void {
    for (const k of Object.keys(links)) delete links[k]
    refused.clear()
    inflight.clear()
  }

  return { resolve, url, info, reset }
})
