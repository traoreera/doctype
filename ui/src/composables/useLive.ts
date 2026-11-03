import { onBeforeUnmount, watch, type WatchSource } from 'vue'
import { useServices } from '@/lib/services'

/**
 * « Un document de ce type a changé dans mon organisation » (`lib/realtime.ts`) : `cb` est rappelé, au plus une fois par `debounceMs`
 * (une soumission qui écrit dix lignes de grand livre ne relit pas dix fois). Suit le type quand la page change de type ; se désabonne
 * au démontage. Sans temps réel (xpulse absent) : rien n'arrive, la page relit comme avant.
 */
export function useLiveDoctype(doctype: WatchSource<string | null | undefined>, cb: () => void, debounceMs = 400): void {
  const { realtime } = useServices()
  if (!realtime) return
  let off: (() => void) | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  const fire = () => {
    clearTimeout(timer)
    timer = setTimeout(cb, debounceMs)
  }
  watch(
    doctype,
    (dt) => {
      off?.()
      off = dt ? realtime.onDocChanged(dt, fire) : null
    },
    { immediate: true },
  )
  onBeforeUnmount(() => {
    off?.()
    clearTimeout(timer)
  })
}
