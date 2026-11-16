import { computed, inject, type ComputedRef } from 'vue'
import { routeLocationKey, routerKey } from 'vue-router'

export interface QueryHandle {
  /** La query de l'adresse, réactive ; une clé répétée (`?a=1&a=2`) vaut sa première valeur. */
  readonly query: ComputedRef<Record<string, string>>
  /**
   * Remplace TOUTE la query de la page (une valeur `null`, `undefined` ou vide retire la clé). `replace` (défaut : vrai) n'ajoute pas
   * d'entrée à l'historique : un filtre qui change ne doit pas demander dix « Précédent » pour quitter la page.
   */
  setQuery(next: Record<string, string | number | boolean | null | undefined>, options?: { replace?: boolean }): Promise<void>
}

/**
 * L'état d'une page d'extension gardé dans l'adresse (`#/x/<plugin>/etats?etat=bilan&date=…`) : un rechargement ou un lien partagé
 * rouvre le même écran. La page n'est PAS remontée quand seule la query change (`ExtensionPage` : clé = le chemin) ; elle observe `query`.
 *
 * Aussi servi par `useDesk()` (`const { query, setQuery } = useDesk()`). Hors de toute route (un composant monté sans routeur), la query
 * est vide et `setQuery` refuse.
 */
export function useQuery(): QueryHandle {
  // `inject` et non `useRoute()` / `useRouter()` : `useDesk()` sert aussi des composants montés sans routeur, sans avertissement.
  const route = inject(routeLocationKey, null)
  const router = inject(routerKey, null)
  const query = computed(() => {
    const out: Record<string, string> = {}
    for (const [key, value] of Object.entries(route?.query ?? {})) {
      const first = Array.isArray(value) ? value[0] : value
      if (typeof first === 'string') out[key] = first
    }
    return out
  })
  async function setQuery(next: Record<string, string | number | boolean | null | undefined>, options: { replace?: boolean } = {}) {
    if (!route || !router) throw new Error('setQuery() : ce composant n’est pas affiché dans une page du Desk')
    const clean: Record<string, string> = {}
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === undefined || value === '') continue
      clean[key] = String(value)
    }
    const target = { path: route.path, query: clean }
    await (options.replace === false ? router.push(target) : router.replace(target))
  }
  return { query, setQuery }
}
