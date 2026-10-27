import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useServices } from '@/lib/services'
import type { XflowAvailability } from '@/lib/xflow'

/**
 * xflow est-il installé, et puis-je lire ses automatisations ? Demandé UNE fois par session (le lien de la barre latérale, la page). Une
 * panne réseau ne dit rien de l'installation : on redemandera à la prochaine ouverture de la page.
 */
export const useAutomations = defineStore('automations', () => {
  const { flows } = useServices()
  const availability = ref<XflowAvailability | 'unknown'>('unknown')
  let probing: Promise<void> | null = null

  function probe(): Promise<void> {
    if (!flows) {
      availability.value = 'absent'
      return Promise.resolve()
    }
    probing ??= flows
      .availability()
      .then((a) => void (availability.value = a))
      .catch(() => {})
      .finally(() => (probing = null))
    return probing
  }

  function reset(): void {
    availability.value = 'unknown'
    probing = null
  }

  return { availability, probe, reset }
})
