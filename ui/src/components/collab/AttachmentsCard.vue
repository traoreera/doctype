<script setup lang="ts">
import Button from 'primevue/button'
import { onMounted, ref, watch } from 'vue'
import FileThumb from '@/components/FileThumb.vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { formatBytes, relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { AttachmentItem, Collab } from '@/lib/types'
import { useFiles } from '@/stores/files'
import { useUsers } from '@/stores/users'

/** Les pièces jointes d'un document. Un fichier se télécharge par un lien signé de courte durée (jamais affiché dans la page, sauf une vraie image en vignette). */
const props = defineProps<{ collab: Collab; doctype: string; name: string }>()
const emit = defineEmits<{ changed: [] }>()
const { api } = useServices()
const files = useFiles()
const notify = useNotify()
const users = useUsers()
const input = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const MAX_MB = 10 // la limite du serveur pour une pièce jointe : on prévient avant d'envoyer, il tranche de toute façon

function resolve(): void {
  const ids = props.collab.attachments.map((a) => a.file_id)
  if (ids.length) void files.resolve(ids)
}
onMounted(resolve)
watch(() => props.collab.attachments.map((a) => a.file_id).join('|'), resolve)

const isImage = (a: AttachmentItem): boolean => a.content_type.startsWith('image/')
const mine = (a: AttachmentItem): boolean => a.owner === props.collab.me

async function pick(e: Event): Promise<void> {
  const target = e.target as HTMLInputElement
  const list = [...(target.files ?? [])]
  target.value = '' // le même fichier peut être rechoisi
  if (!list.length) return
  uploading.value = true
  try {
    for (const file of list) {
      if (file.size > MAX_MB * 1024 * 1024) {
        notify.error(new Error(t.collab.tooLarge(MAX_MB)))
        continue
      }
      await api.attach(props.doctype, props.name, file)
    }
    emit('changed')
  } catch (err) {
    notify.error(err)
    emit('changed') // ce qui a pu partir est là
  } finally {
    uploading.value = false
  }
}

async function remove(a: AttachmentItem): Promise<void> {
  try {
    await api.detach(a.id)
    emit('changed')
  } catch (e) {
    notify.error(e)
  }
}
</script>

<template>
  <section class="desk-card collab-card" data-testid="attachments">
    <header class="collab-card-head">
      <h3>{{ t.collab.attachments }}</h3>
      <span class="desk-spacer" />
      <template v-if="collab.can_attach">
        <input ref="input" type="file" multiple hidden data-testid="attach-input" @change="pick" />
        <Button :label="t.collab.attach" icon="pi pi-paperclip" text size="small" :loading="uploading" data-testid="attach-open" @click="input?.click()" />
      </template>
    </header>
    <p v-if="!collab.attachments.length" class="desk-muted collab-empty" data-testid="attachments-empty">{{ t.collab.noAttachments }}</p>
    <ul v-else class="attachment-list">
      <li v-for="a in collab.attachments" :key="a.id" class="attachment" data-testid="attachment">
        <FileThumb v-if="isImage(a)" :id="a.file_id" :size="36" :alt="a.filename" />
        <span v-else class="thumb thumb-empty" style="width: 36px; height: 36px" aria-hidden="true"><i class="pi pi-file" /></span>
        <div class="attachment-body">
          <a v-if="files.url(a.file_id)" class="desk-link attachment-name" :href="files.url(a.file_id)!" download :title="t.collab.download" rel="noopener noreferrer" data-testid="attachment-link">{{ a.filename }}</a>
          <span v-else class="attachment-name">{{ a.filename }}</span>
          <span class="desk-muted attachment-meta">{{ formatBytes(a.size) }} · {{ users.label(a.owner) }} · {{ relativeTime(a.creation) }}</span>
        </div>
        <Button v-if="collab.can_attach || mine(a)" icon="pi pi-trash" text rounded size="small" severity="secondary" :aria-label="t.collab.removeFile" :title="t.collab.removeFile" data-testid="attachment-remove" @click="remove(a)" />
      </li>
    </ul>
  </section>
</template>
