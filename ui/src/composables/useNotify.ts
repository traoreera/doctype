import { useToast } from 'primevue/usetoast'
import { ApiError } from '@/lib/api'
import { t } from '@/i18n'
import { NetworkError } from '@/lib/transport'

/** Message lisible pour n'importe quelle erreur qui remonte d'un appel d'API. */
export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
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
    error: (e: unknown) => show({ severity: 'error', summary: describeError(e), life: 6000 }),
    warn: (detail: string) => show({ severity: 'warn', summary: detail, life: 6000 }),
  }
}
