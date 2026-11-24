import { HostRefusal } from '@/lib/host'
import { useToast } from 'primevue/usetoast'
import { ApiError } from '@/lib/api'
import { t } from '@/i18n'
import { NetworkError } from '@/lib/transport'

/**
 * Un texte ÉCRIT POUR L'UTILISATEUR, porté comme une erreur (traoreera/doctype#86).
 *
 * `describeError` ne rend le message que de ce qu'elle sait écrit pour être lu : une `ApiError` (le serveur nomme la
 * cause), et ceci. Une `Error` ordinaire reste générique à dessein — un `TypeError: x is undefined` venu d'une faute de
 * programmation n'est pas une phrase à montrer. D'où un porteur dédié, plutôt que de deviner : l'auteur DIT que son
 * texte est destiné à l'écran (`throw new UserMessage(t.collab.tooLarge(n))`, ou `notify.fail(texte)` sans rien lever).
 */
export class UserMessage extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'UserMessage'
  }
}

/** Message lisible pour n'importe quelle erreur qui remonte d'un appel d'API. */
export function describeError(e: unknown): string {
  if (e instanceof UserMessage) return e.message || t.errors.generic
  // Un refus de l'HÔTE (`lib/host.ts` : adresse non ouverte, téléchargement refusé) est écrit pour être lu, comme un `UserMessage`.
  if (e instanceof HostRefusal) return e.message || t.errors.generic
  if (e instanceof ApiError) {
    // Un module hors abonnement ou une licence à renouveler : la raison du serveur dit quoi faire (s'abonner), un « interdit » ne le dirait pas.
    if (e.isModuleNotLicensed || e.code === 'license_required') return e.message || t.errors.generic
    if (e.status === 403) return t.errors.forbidden
    return e.message || t.errors.generic
  }
  if (e instanceof NetworkError) return t.errors.unreachable
  return t.errors.generic
}

export function useNotify() {
  const toast = useToast()
  // Une seule notification à l'écran : la dernière remplace la précédente. Cinq « Document créé » empilés ne disent rien de plus, et
  // cachent la page.
  const show = (message: { severity: 'success' | 'info' | 'warn' | 'error'; summary: string; life: number }) => {
    toast.removeAllGroups()
    toast.add({ ...message, closable: true })
  }
  return {
    success: (detail: string) => show({ severity: 'success', summary: detail, life: 3000 }),
    info: (detail: string) => show({ severity: 'info', summary: detail, life: 4000 }),
    /** Une erreur ATTRAPÉE : elle est décrite ici (`describeError`), on ne lui passe jamais un texte. */
    error: (e: unknown) => show({ severity: 'error', summary: describeError(e), life: 6000 }),
    /** Un refus dont l'AUTEUR a écrit la raison : le texte arrive tel quel à l'écran (vide : le message générique). */
    fail: (text: string | null | undefined) => show({ severity: 'error', summary: text?.trim() || t.errors.generic, life: 6000 }),
    warn: (detail: string) => show({ severity: 'warn', summary: detail, life: 6000 }),
  }
}
