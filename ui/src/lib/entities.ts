/** Défaire l'échappement HTML qu'xauth et xpulse appliquent à leurs textes, pour les AFFICHER (Vue échappe à son tour, jamais de `v-html`). */
const ENTITIES: Record<string, string> = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#x27;': "'", '&#39;': "'" }
export const decodeEntities = (text: string | null): string => (text ?? '').replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, (m) => ENTITIES[m] ?? m)
