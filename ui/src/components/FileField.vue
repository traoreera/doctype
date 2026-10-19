<script setup lang="ts">
import Button from 'primevue/button'
import { computed, ref, watch } from 'vue'
import FileThumb from '@/components/FileThumb.vue'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { formatBytes } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { FieldMeta } from '@/lib/types'
import { useFiles } from '@/stores/files'

/**
 * La saisie d'un champ Image ou Attach : choisir un fichier (ou le déposer sur la zone), le voir, le remplacer, le retirer. Le fichier part tout de
 * suite au serveur, qui le contrôle (taille, contenu : une image est reconnue à son contenu) ; le document ne garde que son IDENTIFIANT, enregistré
 * avec le reste du formulaire. Retirer ne supprime rien tout de suite : le fichier disparaît quand le document est enregistré sans lui.
 */
const props = defineProps<{ field: FieldMeta; modelValue: unknown; doctype: string; inputId: string; invalid?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const { api } = useServices()
const files = useFiles()
const isImage = computed(() => props.field.fieldtype === 'Image')
const id = computed(() => (typeof props.modelValue === 'string' && props.modelValue ? props.modelValue : null))
const limitMb = computed(() => props.field.max_mb ?? 10)
const busy = ref(false)
const problem = ref<string | null>(null)
const over = ref(false)
const picker = ref<HTMLInputElement | null>(null)

watch(id, (v) => v && void files.resolve([v]), { immediate: true })
const info = computed(() => files.info(id.value))
const href = computed(() => files.url(id.value))

async function send(file: File | undefined): Promise<void> {
  if (!file) return
  problem.value = null
  // Les contrôles du navigateur évitent un aller-retour ; le serveur, lui, vérifie TOUT (le navigateur ne fait foi de rien).
  if (file.size > limitMb.value * 1024 * 1024) return void (problem.value = t.file.tooLarge(limitMb.value))
  if (isImage.value && !file.type.startsWith('image/')) return void (problem.value = t.file.notImage)
  busy.value = true
  try {
    const meta = await api.upload(props.doctype, props.field.fieldname, file)
    await files.resolve([meta.id])
    emit('update:modelValue', meta.id)
  } catch (e) {
    problem.value = describeError(e)
  } finally {
    busy.value = false
    if (picker.value) picker.value.value = '' // choisir le même fichier une seconde fois doit déclencher l'événement
  }
}

const onPick = (e: Event): void => void send((e.target as HTMLInputElement).files?.[0])
function onDrop(e: DragEvent): void {
  over.value = false
  void send(e.dataTransfer?.files?.[0])
}
</script>

<template>
  <div class="file-field" :class="{ over, invalid }" data-testid="file-field" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
    <FileThumb v-if="isImage && id" :id="id" :size="72" :alt="field.label ?? field.fieldname" />
    <span v-else-if="isImage" class="thumb thumb-empty file-empty" style="width: 72px; height: 72px" aria-hidden="true"><i class="pi pi-image" /></span>

    <div class="file-body">
      <template v-if="id">
        <a v-if="!isImage && href" class="cell-link file-name" :href="href" :download="info?.filename" rel="noopener" data-testid="file-name">
          <i class="pi pi-paperclip" /> {{ info?.filename }}
        </a>
        <span v-else-if="!isImage" class="file-name" data-testid="file-name"><i class="pi pi-paperclip" /> {{ info?.filename ?? '…' }}</span>
        <span v-if="info" class="desk-muted file-size">{{ formatBytes(info.size) }}</span>
      </template>
      <span v-else class="desk-muted">{{ t.file.drop }}</span>

      <div class="file-actions">
        <Button :label="id ? t.file.replace : t.file.choose" :icon="busy ? 'pi pi-spinner pi-spin' : 'pi pi-upload'" size="small" severity="secondary" outlined :disabled="busy" data-testid="file-pick" @click="picker?.click()" />
        <Button v-if="id" :label="t.file.remove" icon="pi pi-times" size="small" severity="secondary" text :disabled="busy" data-testid="file-remove" @click="emit('update:modelValue', null)" />
      </div>
      <p v-if="problem" class="error" role="alert" data-testid="file-error">{{ problem }}</p>
    </div>
    <input :id="inputId" ref="picker" type="file" class="visually-hidden" :accept="isImage ? 'image/png,image/jpeg,image/gif,image/webp' : undefined" data-testid="file-input" @change="onPick" />
  </div>
</template>
