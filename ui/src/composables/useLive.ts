import { onBeforeUnmount, watch } from 'vue'
import { useServices } from '@/lib/services'

/**
 * « Un document de ces types a changé dans mon organisation » (`lib/realtime.ts`) : `cb(doctype)` est rappelé, au plus une fois par type et par
 * `debounceMs` (une soumission qui écrit dix lignes de grand livre ne relit pas dix fois). Suit la liste des types quand elle change ; se
 * désabonne au démontage. Sans temps réel (xpulse absent) : rien n'arrive, la page relit comme avant.
 */
export function useLiveDoctypes(doctypes: () => readonly (string | null | undefined)[], cb: (doctype: string) => void, debounceMs = 400): void {
  const { realtime } = useServices()
  if (!realtime) return
  const offs = new Map<string, () => void>()
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const fire = (dt: string) => {
    clearTimeout(timers.get(dt))
    timers.set(dt, setTimeout(() => (timers.delete(dt), cb(dt)), debounceMs))
  }
  watch(
    () => [...new Set(doctypes().filter((d): d is string => !!d))].sort().join('\u0000'),
    (key) => {
      const wanted = new Set(key ? key.split('\u0000') : [])
      for (const [dt, off] of offs) if (!wanted.has(dt)) (off(), offs.delete(dt))
      for (const dt of wanted) if (!offs.has(dt)) offs.set(dt, realtime.onDocChanged(dt, () => fire(dt)))
    },
    { immediate: true },
  )
  onBeforeUnmount(() => {
    offs.forEach((off) => off())
    offs.clear()
    timers.forEach((timer) => clearTimeout(timer))
  })
}

/** Le cas d'une page qui montre UN type (liste, fiche, vue) : voir `useLiveDoctypes`. */
export function useLiveDoctype(doctype: () => string | null | undefined, cb: () => void, debounceMs = 400): void {
  useLiveDoctypes(() => [doctype()], () => cb(), debounceMs)
}
