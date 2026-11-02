<script setup lang="ts">
import Button from 'primevue/button'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, TrashItem } from '@/lib/types'
import { useMeta } from '@/stores/meta'
import { useUsers } from '@/stores/users'

/**
 * La corbeille d'un type (`#/trash/<Type>`) : ce que l'utilisateur pouvait LIRE parmi les documents supprimés, plus récents d'abord.
 * « Restaurer » remet le document à sa place, sous son nom, à son propriétaire — une création ordinaire, que le serveur refuse si le monde
 * a changé (nom ou valeur unique repris, lien vers un document disparu) : le refus est montré tel quel. « Supprimer définitivement » purge.
 * Les boutons suivent les droits du type (`create`, `delete`) ; le serveur juge chaque document.
 */
const props = defineProps<{ doctype: string }>()
const { api } = useServices()
const metaStore = useMeta()
const users = useUsers()
const notify = useNotify()
const confirm = useConfirm()
const router = useRouter()

const PAGE = 20
const meta = ref<DocTypeMeta | null>(null)
const items = ref<TrashItem[] | null>(null)
const total = ref(0)
const failure = ref<string | null>(null)
const busy = ref<string | null>(null)

const label = computed(() => meta.value?.label ?? props.doctype)
const canRestore = computed(() => !!meta.value && meta.value.rights.create !== 'none')
const canPurge = computed(() => !!meta.value && meta.value.rights.delete !== 'none')
const titleOf = (i: TrashItem) => i.title || i.name

async function load(more = false): Promise<void> {
  failure.value = null
  try {
    meta.value ??= await metaStore.get(props.doctype)
    const page = await api.trash(props.doctype, { limit: PAGE, offset: more ? (items.value?.length ?? 0) : 0 })
    items.value = more ? [...(items.value ?? []), ...page.items] : page.items
    total.value = page.total
    users.want(page.items.flatMap((i) => [i.owner, i.deleted_by]))
  } catch (e) {
    failure.value = describeError(e)
  }
}

function drop(id: string): void {
  items.value = (items.value ?? []).filter((i) => i.id !== id)
  total.value = Math.max(0, total.value - 1)
}

async function restore(item: TrashItem): Promise<void> {
  busy.value = item.id
  try {
    const doc = await api.restoreTrashed(props.doctype, item.id)
    drop(item.id)
    notify.success(t.trash.restored(titleOf(item)))
    void router.push({ name: 'form', params: { doctype: props.doctype, name: doc.name } })
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = null
  }
}

function purge(item: TrashItem): void {
  confirm.require({
    header: t.trash.purge,
    message: t.trash.confirmPurge(titleOf(item)),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.trash.purge,
    rejectLabel: t.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      busy.value = item.id
      try {
        await api.purgeTrashed(props.doctype, item.id)
        drop(item.id)
        notify.success(t.trash.purged(titleOf(item)))
      } catch (e) {
        notify.error(e)
      } finally {
        busy.value = null
      }
    },
  })
}

onMounted(() => void load())
watch(() => props.doctype, () => {
  meta.value = null
  items.value = null
  void load()
})
</script>

<template>
  <div class="desk-page">
    <div class="desk-page-head">
      <h1>{{ t.trash.title(label) }}</h1>
      <RouterLink class="desk-link" :to="{ name: 'list', params: { doctype } }">{{ label }}</RouterLink>
    </div>
    <p class="desk-muted trash-hint">{{ t.trash.hint }}</p>
    <p v-if="failure" class="desk-muted" data-testid="trash-failed">{{ t.trash.failed }} : {{ failure }}</p>
    <p v-else-if="items && !items.length" class="desk-muted" data-testid="trash-empty">{{ t.trash.empty }}</p>
    <ul v-else-if="items" class="desk-card trash-list" data-testid="trash-list">
      <li v-for="i in items" :key="i.id" :data-testid="`trash-${i.name}`">
        <span class="trash-title">{{ titleOf(i) }}</span>
        <span v-if="i.title" class="desk-muted">{{ i.name }}</span>
        <span v-if="i.workflow_state" class="status-pill">{{ i.workflow_state }}</span>
        <span class="desk-muted trash-meta">{{ t.trash.by(users.label(i.deleted_by), relativeTime(i.deleted_at)) }} · {{ t.trash.owner(users.label(i.owner)) }}</span>
        <span class="trash-actions">
          <Button
            v-if="canRestore" :label="t.trash.restore" icon="pi pi-replay" size="small" severity="secondary"
            :loading="busy === i.id" :disabled="busy !== null" :data-testid="`trash-restore-${i.name}`" @click="restore(i)"
          />
          <Button
            v-if="canPurge" :label="t.trash.purge" icon="pi pi-trash" size="small" severity="danger" text
            :disabled="busy !== null" :data-testid="`trash-purge-${i.name}`" @click="purge(i)"
          />
        </span>
      </li>
    </ul>
    <Button v-if="items && items.length < total" :label="t.trash.more" text data-testid="trash-more" @click="load(true)" />
  </div>
</template>

<style scoped>
.trash-hint {
  max-width: 60rem;
  margin-top: 0;
}

.trash-list {
  list-style: none;
  margin: 0;
  padding: 0.5rem 1rem;
  display: grid;
}

.trash-list li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid var(--p-content-border-color);
}

.trash-list li:last-child {
  border-bottom: none;
}

.trash-title {
  font-weight: 500;
}

.trash-meta {
  font-size: 0.85rem;
}

.trash-actions {
  margin-left: auto;
  display: flex;
  gap: 0.4rem;
}
</style>
