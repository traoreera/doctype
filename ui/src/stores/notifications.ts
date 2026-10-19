import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useServices } from '@/lib/services'
import type { AuthNotification } from '@/lib/xauth'

/**
 * Les notifications d'xauth (la cloche de la barre du haut). Une commodité : sans xauth (jeton collé) il n'y a pas de cloche, et une panne laisse la
 * liste telle qu'elle est. Le serveur échappe le HTML de `title`/`message` : on le défait pour l'AFFICHER (Vue échappe à son tour, jamais de `v-html`).
 */
const ENTITIES: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'" }
export const decodeEntities = (text: string | null): string => (text ?? '').replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (m) => ENTITIES[m] ?? m)

/**
 * Où mène une notification : une adresse du Desk (`#/…`) ou une adresse https. Tout le reste (`javascript:`, `data:`, chemin quelconque) ne mène nulle part :
 * n'importe quel membre peut en écrire une, son lien est une donnée d'autrui.
 */
export function safeTarget(link: string | null): { kind: 'desk'; path: string } | { kind: 'external'; url: string } | null {
  if (!link) return null
  if (/^#\/[^\s]*$/.test(link)) return { kind: 'desk', path: link.slice(1) }
  try {
    const url = new URL(link)
    return url.protocol === 'https:' ? { kind: 'external', url: url.href } : null
  } catch {
    return null
  }
}

export const useNotifications = defineStore('notifications', () => {
  const { auth } = useServices()
  const items = ref<AuthNotification[]>([])
  const failed = ref(false)
  const unread = computed(() => items.value.filter((n) => !n.is_read).length)
  let loading: Promise<void> | null = null

  function refresh(): Promise<void> {
    if (!auth) return Promise.resolve()
    loading ??= auth
      .notifications({ limit: 30 })
      .then((list) => {
        items.value = list
        failed.value = false
      })
      .catch(() => void (failed.value = true))
      .finally(() => (loading = null))
    return loading
  }

  async function markRead(id: string): Promise<void> {
    const n = items.value.find((x) => x.id === id)
    if (!auth || !n || n.is_read) return
    n.is_read = true // l'écran suit tout de suite ; un échec le remet
    try {
      await auth.markNotificationRead(id)
    } catch {
      n.is_read = false
    }
  }

  async function markAll(): Promise<void> {
    if (!auth || !unread.value) return
    const before = items.value.map((n) => n.is_read)
    items.value.forEach((n) => (n.is_read = true))
    try {
      await auth.markAllNotificationsRead()
    } catch {
      items.value.forEach((n, i) => (n.is_read = before[i] ?? false))
    }
  }

  async function dismiss(id: string): Promise<void> {
    if (!auth) return
    const kept = items.value
    items.value = kept.filter((n) => n.id !== id)
    try {
      await auth.deleteNotification(id)
    } catch {
      items.value = kept
    }
  }

  function reset(): void {
    items.value = []
    failed.value = false
    loading = null
  }

  return { items, failed, unread, refresh, markRead, markAll, dismiss, reset }
})
