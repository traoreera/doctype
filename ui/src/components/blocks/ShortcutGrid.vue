<script setup lang="ts">
import { reactive, watch } from 'vue'
import { formatNumber } from '@/lib/format'
import { useServices } from '@/lib/services'
import { t } from '@/i18n'
import type { WorkspaceShortcut } from '@/lib/types'

const props = defineProps<{ shortcuts: WorkspaceShortcut[] }>()
const { api } = useServices()

// Le nombre de documents de chaque raccourci (comme les pastilles d'un raccourci de Frappe), lu une fois, quelques requêtes à la fois.
// Le serveur compte DANS le périmètre de l'utilisateur : le chiffre est celui qu'il verrait en ouvrant la liste.
const counts = reactive<Record<string, number | null>>({})
let ticket = 0
watch(
  () => props.shortcuts.map((s) => s.doctype).join('|'),
  async () => {
    const mine = ++ticket
    // Un réglage (document unique) n'a pas de compteur : il y en a un, ou pas encore.
    const queue = [...new Set(props.shortcuts.filter((s) => !s.single).map((s) => s.doctype))].filter((d) => !(d in counts))
    const worker = async () => {
      for (let d = queue.shift(); d !== undefined; d = queue.shift()) {
        try {
          const res = await api.list(d, { limit: 1 })
          if (mine === ticket) counts[d] = res.total
        } catch {
          if (mine === ticket) counts[d] = null // illisible : la tuile reste sans chiffre
        }
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()])
  },
  { immediate: true },
)
const countOf = (d: string): string | null => (typeof counts[d] === 'number' ? formatNumber(counts[d] as number) : null)
</script>

<template>
  <div class="home-grid">
    <div v-for="sc in shortcuts" :key="sc.doctype" class="desk-card home-card">
      <RouterLink class="desk-link home-open" :to="sc.single ? { name: 'settings', params: { doctype: sc.doctype } } : { name: 'list', params: { doctype: sc.doctype } }" :data-testid="`shortcut-${sc.doctype}`">
        <i :class="`pi pi-${sc.icon ?? 'file'}`" />
        <span class="home-label">{{ sc.label }}</span>
        <span v-if="countOf(sc.doctype) !== null" class="count" :data-testid="`count-${sc.doctype}`">{{ countOf(sc.doctype) }}</span>
      </RouterLink>
      <RouterLink v-if="sc.create" class="home-new" :to="{ name: 'new', params: { doctype: sc.doctype } }" :aria-label="t.homeNew(sc.label)" :title="t.homeNew(sc.label)" :data-testid="`new-${sc.doctype}`">
        <i class="pi pi-plus" />
      </RouterLink>
    </div>
  </div>
</template>
