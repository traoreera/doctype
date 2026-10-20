<script setup lang="ts">
import { ref, watch } from 'vue'
import { t } from '@/i18n'
import { useServices } from '@/lib/services'
import type { Connection } from '@/lib/types'

/**
 * Les documents qui CITENT celui-ci (les factures d'un client, les contacts d'une société…), par type, avec leur nombre : un clic ouvre la liste de
 * ces documents. Le serveur ne compte que ce que l'utilisateur peut lire. Un lien porté par les LIGNES d'un tableau se compte mais ne s'ouvre pas : une
 * liste ne se filtre pas sur le contenu d'un tableau. Rien à montrer : le panneau n'apparaît pas.
 */
const props = defineProps<{ doctype: string; name: string; version: number }>()
const { api } = useServices()
const items = ref<Connection[]>([])

let ticket = 0
async function load(): Promise<void> {
  const mine = ++ticket
  try {
    const found = await api.connections(props.doctype, props.name)
    if (mine === ticket) items.value = found
  } catch {
    if (mine === ticket) items.value = [] // un panneau d'agrément : une panne ne gêne pas le formulaire
  }
}
watch(() => [props.doctype, props.name, props.version] as const, load, { immediate: true })

/** Les filtres de la liste des documents qui citent celui-ci (`?filters=`, lus par la liste). */
function filtersOf(c: Connection): string {
  return JSON.stringify({ [c.field]: props.name, ...(c.type_field ? { [c.type_field]: props.doctype } : {}) })
}
const key = (c: Connection) => `${c.doctype}.${c.table ?? ''}.${c.field}`
const via = (c: Connection) => (c.table ? t.connections.rows : t.connections.via(c.field_label))
</script>

<template>
  <section v-if="items.length" class="desk-card connections" :aria-label="t.connections.title" data-testid="connections">
    <header>{{ t.connections.title }}</header>
    <ul>
      <li v-for="c in items" :key="key(c)" :data-testid="`connection-${c.doctype}`">
        <RouterLink v-if="!c.table" :to="{ name: 'list', params: { doctype: c.doctype }, query: { filters: filtersOf(c) } }" class="chip">
          <strong>{{ c.label }}</strong> <span class="count">{{ c.count }}</span>
          <small v-if="items.filter((x) => x.doctype === c.doctype).length > 1" class="desk-muted">{{ via(c) }}</small>
        </RouterLink>
        <span v-else class="chip static">
          <strong>{{ c.label }}</strong> <span class="count">{{ c.count }}</span> <small class="desk-muted">{{ via(c) }}</small>
        </span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.connections {
  padding: 0.875rem 1rem;
  margin-top: 1rem;
}

.connections header {
  font-weight: 600;
  margin-bottom: 0.5rem;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.chip {
  display: inline-flex;
  align-items: baseline;
  gap: 0.4rem;
  padding: 0.3rem 0.7rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 999px;
  text-decoration: none;
  color: inherit;
}

a.chip:hover {
  background: var(--p-content-hover-background);
}

.count {
  font-variant-numeric: tabular-nums;
  color: var(--p-primary-color);
  font-weight: 600;
}

small {
  font-size: 0.75rem;
}
</style>
