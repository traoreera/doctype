import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { useServices } from '@/lib/services'
import { useMeta } from '@/stores/meta'

const BATCH = 100 // plafond de `in` côté serveur

/**
 * Titres des documents cibles des champs Link (« FR » -> « France »). Un titre inconnu ou illisible (pas de droit de lecture,
 * pas de `title_field`, document supprimé) retombe sur le nom : une liste ne doit jamais échouer pour un libellé.
 */
export const useLinks = defineStore('links', () => {
  const { api } = useServices()
  const meta = useMeta()
  // cible -> nom -> titre ; `null` : cherché sans résultat (on ne le redemande pas)
  const titles = reactive<Record<string, Record<string, string | null>>>({})
  // cible -> nom -> contenu du document : de quoi lire l'unité d'un article (`item.uom`) sans le rouvrir
  const docs = reactive<Record<string, Record<string, Record<string, unknown> | null>>>({})

  function title(target: string, name: string): string {
    return titles[target]?.[name] ?? name
  }

  /** Un champ du document cible (`undefined` tant qu'on ne l'a pas lu : voir `resolve(…, { data: true })`). */
  function field(target: string, name: string, fieldname: string): unknown {
    return docs[target]?.[name]?.[fieldname]
  }

  // Lectures en cours, par nom : « d|Item|vis » (titre ET contenu) ou « t|Item|vis » (titre seulement). Dix cellules qui citent le même
  // article, ou une grille et chacune de ses cellules, ne font qu'UNE requête : les suivantes attendent la première.
  const inflight = new Map<string, Promise<void>>()

  /** `data` : lit aussi le CONTENU des documents (pour les unités), pas seulement leur titre. */
  async function resolve(target: string, names: Iterable<string>, opts: { data?: boolean } = {}): Promise<void> {
    // Lu À TRAVERS le proxy réactif : `titles[target] ??= {}` rendrait l'objet brut, et les titres écrits ensuite ne rafraîchiraient
    // aucun composant qui affiche `title()` (un lien en lecture seule resterait sur l'identifiant).
    titles[target] ??= {}
    docs[target] ??= {}
    const known = titles[target]!
    const seen = docs[target]!
    const missing = [...new Set(names)].filter((n) => n && !(opts.data ? n in seen : n in known))
    if (!missing.length) return
    // Une lecture « contenu » répond aussi à une demande de titre : on ne redemande pas ce qui est déjà en route.
    const keys = (n: string) => (opts.data ? [`d|${target}|${n}`] : [`d|${target}|${n}`, `t|${target}|${n}`])
    const waiting: Promise<void>[] = []
    const fresh: string[] = []
    for (const n of missing) {
      const pending = keys(n).map((k) => inflight.get(k)).find(Boolean)
      if (pending) waiting.push(pending)
      else fresh.push(n)
    }
    if (fresh.length) {
      const mine = fetchDocs(target, fresh, !!opts.data, known, seen)
      const mark = `${opts.data ? 'd' : 't'}|${target}|`
      for (const n of fresh) inflight.set(mark + n, mine)
      void mine.finally(() => fresh.forEach((n) => inflight.delete(mark + n)))
      waiting.push(mine)
    }
    await Promise.all(waiting)
  }

  async function fetchDocs(
    target: string,
    missing: string[],
    wantData: boolean,
    known: Record<string, string | null>,
    seen: Record<string, Record<string, unknown> | null>,
  ): Promise<void> {
    let titleField: string | undefined
    try {
      titleField = (await meta.get(target)).title_field
    } catch {
      for (const n of missing) known[n] = null
      return
    }
    if (!titleField && !wantData) {
      for (const n of missing) known[n] = null
      return
    }
    for (let i = 0; i < missing.length; i += BATCH) {
      const chunk = missing.slice(i, i + BATCH)
      try {
        const res = await api.list(target, { filters: { name: ['in', chunk] }, limit: chunk.length })
        for (const item of res.items) {
          const v = titleField ? item.data[titleField] : undefined
          known[item.name] = typeof v === 'string' && v ? v : null
          seen[item.name] = item.data
        }
      } catch {
        // illisible : les noms bruts feront l'affaire
      }
      for (const n of chunk) {
        if (!(n in known)) known[n] = null
        if (!(n in seen)) seen[n] = null
      }
    }
  }

  /** Un document qu'on vient de créer : son titre et son contenu sont connus d'emblée (le champ qui le cite l'affiche sans le relire). */
  function seed(target: string, name: string, title: string | null, data: Record<string, unknown>): void {
    titles[target] ??= {}
    docs[target] ??= {}
    titles[target]![name] = title
    docs[target]![name] = data
  }

  function reset(): void {
    inflight.clear()
    for (const k of Object.keys(titles)) delete titles[k]
    for (const k of Object.keys(docs)) delete docs[k]
  }

  return { title, field, resolve, seed, reset }
})
