<script setup lang="ts">
import { computed, watch } from 'vue'
import FileThumb from '@/components/FileThumb.vue'
import { hrefFor } from '@/lib/fieldrules'
import { formatCell, starsText } from '@/lib/format'
import type { FieldMeta } from '@/lib/types'
import { useFiles } from '@/stores/files'
import { useLinks } from '@/stores/links'

/**
 * Une valeur de champ, LISIBLE ET CLIQUABLE là où elle désigne quelque chose : un lien ouvre le document cité (par son titre), une adresse
 * e-mail ouvre le courrier, un téléphone compose, une adresse web s'ouvre dans un nouvel onglet, une couleur montre son échantillon, une note ses étoiles.
 * `text` : le texte déjà calculé par l'appelant (avec l'unité d'une quantité), qui remplace le texte par défaut.
 */
const props = defineProps<{ field: FieldMeta; value: unknown; text?: string }>()
const links = useLinks()
const files = useFiles()

const empty = computed(() => props.value === null || props.value === undefined || props.value === '')
const shown = computed(() => props.text ?? formatCell(props.field, props.value))
const target = computed(() => (props.field.fieldtype === 'Link' && typeof props.field.options === 'string' ? props.field.options : null))
const href = computed(() => (props.field.fieldtype === 'Text' && typeof props.value === 'string' ? hrefFor(props.field.format, props.value) : null))
const external = computed(() => props.field.format === 'url')
// Le titre du document cité : le store ne relit pas ce qu'il connaît, et partage les lectures déjà en vol (une liste les a lancées par lot).
watch(
  () => [target.value, props.value] as const,
  ([to, v]) => {
    if (to && typeof v === 'string' && v) void links.resolve(to, [v])
  },
  { immediate: true },
)
// Image, Attach : la valeur est l'identifiant d'un fichier ; son nom et son lien signé viennent du serveur (par lot : une liste les a demandés).
const fileId = computed(() => ((props.field.fieldtype === 'Image' || props.field.fieldtype === 'Attach') && typeof props.value === 'string' ? props.value : null))
watch(fileId, (id) => id && void files.resolve([id]), { immediate: true })
const fileInfo = computed(() => files.info(fileId.value))
const fileHref = computed(() => files.url(fileId.value))
const stars = computed(() => (typeof props.value === 'number' ? starsText(props.value, props.field.stars ?? 5) : ''))
</script>

<template>
  <template v-if="empty" />
  <FileThumb v-else-if="fileId && field.fieldtype === 'Image'" :id="fileId" :size="28" data-testid="cell-image" />
  <a v-else-if="fileId" class="cell-link" :href="fileHref ?? undefined" :download="fileInfo?.filename" rel="noopener" data-testid="cell-file" @click.stop>
    <i class="pi pi-paperclip" /> {{ fileInfo?.filename ?? '…' }}
  </a>
  <RouterLink v-else-if="target && typeof value === 'string'" class="cell-link" :to="{ name: 'form', params: { doctype: target, name: value } }" :data-testid="`cell-link-${value}`" @click.stop>
    {{ links.title(target, value) }}
  </RouterLink>
  <a v-else-if="href" class="cell-link" :href="href" :target="external ? '_blank' : undefined" :rel="external ? 'noopener noreferrer' : undefined" @click.stop>{{ value }}</a>
  <span v-else-if="field.fieldtype === 'Color' && typeof value === 'string'" class="cell-color">
    <span class="cell-swatch" :style="{ background: value }" aria-hidden="true" />
    <code>{{ value }}</code>
  </span>
  <span v-else-if="field.fieldtype === 'Rating'" class="cell-stars" :aria-label="`${value} / ${field.stars ?? 5}`" :title="`${value} / ${field.stars ?? 5}`">{{ stars }}</span>
  <span v-else>{{ shown }}</span>
</template>
