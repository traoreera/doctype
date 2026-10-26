<script setup lang="ts">
import ActivityPanel from '@/components/collab/ActivityPanel.vue'
import AssignmentsCard from '@/components/collab/AssignmentsCard.vue'
import AttachmentsCard from '@/components/collab/AttachmentsCard.vue'
import SharesCard from '@/components/collab/SharesCard.vue'
import { useCollab } from '@/composables/useCollab'
import { t } from '@/i18n'
import { useTasks } from '@/stores/tasks'
import type { DocTypeMeta } from '@/lib/types'

/**
 * Tout ce qui entoure un document enregistré : à qui il est confié, ses pièces jointes, avec qui il est partagé, et son activité (commentaires + modifications).
 * Un changement relit tout (le serveur est la référence) et prévient « Mes tâches » (le compteur de la barre latérale).
 */
const props = defineProps<{ meta: DocTypeMeta; doctype: string; name: string; version: number }>()
const { data, error, reload } = useCollab(() => ({ doctype: props.doctype, name: props.name }))
const tasks = useTasks()

async function changed(): Promise<void> {
  await reload()
  void tasks.refresh()
}
</script>

<template>
  <div class="collab" data-testid="collab">
    <p v-if="error && !data" class="desk-muted" data-testid="collab-failed">{{ t.collab.failed }} · {{ error }}</p>
    <template v-else-if="data">
      <aside class="collab-side">
        <AssignmentsCard :collab="data" :doctype="doctype" :name="name" :timezone="meta.timezone" @changed="changed" />
        <AttachmentsCard :collab="data" :doctype="doctype" :name="name" @changed="changed" />
        <SharesCard v-if="!meta.virtual" :doctype="doctype" :name="name" />
      </aside>
      <ActivityPanel :meta="meta" :doctype="doctype" :name="name" :version="version" :collab="data" @changed="changed" />
    </template>
  </div>
</template>
