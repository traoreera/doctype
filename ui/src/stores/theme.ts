import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { applyDark, readMode, resolveDark, systemPrefersDark, writeMode, type ThemeMode } from '@/lib/theme'

/** Le thème du Desk : le choix de l'utilisateur et, en « automatique », la préférence du système (qui peut changer en cours de route). */
export const useTheme = defineStore('theme', () => {
  const mode = ref<ThemeMode>(readMode())
  const systemDark = ref(systemPrefersDark())
  const dark = computed(() => resolveDark(mode.value, systemDark.value))

  function setMode(next: ThemeMode): void {
    mode.value = next
    writeMode(next)
  }

  /** À appeler une fois au démarrage : applique le thème tout de suite et le garde à jour. Rend la fonction d'arrêt. */
  function start(): () => void {
    applyDark(dark.value)
    const stop = watch(dark, (d) => applyDark(d))
    let unlisten = () => {}
    if (typeof matchMedia === 'function') {
      const query = matchMedia('(prefers-color-scheme: dark)')
      const onChange = (e: MediaQueryListEvent) => (systemDark.value = e.matches)
      query.addEventListener('change', onChange)
      unlisten = () => query.removeEventListener('change', onChange)
    }
    return () => {
      stop()
      unlisten()
    }
  }

  return { mode, dark, setMode, start }
})
