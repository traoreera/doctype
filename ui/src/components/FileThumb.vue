<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useFiles } from '@/stores/files'

/** La vignette d'une image (un avatar, une photo d'article). Pendant le chargement, ou sans droit, ou si l'image échoue : une icône, jamais une image cassée. */
const props = withDefaults(defineProps<{ id: unknown; size?: number; round?: boolean; alt?: string }>(), { size: 32, round: false, alt: '' })
const files = useFiles()
const failed = ref(false)
const fileId = computed(() => (typeof props.id === 'string' && props.id ? props.id : null))
const src = computed(() => files.url(fileId.value))

watch(
  fileId,
  (id) => {
    failed.value = false
    if (id) void files.resolve([id])
  },
  { immediate: true },
)

// Le lien a expiré pendant que la page restait ouverte : on le redemande UNE fois, sinon on montre l'icône.
let retried = false
function onError(): void {
  if (retried || !fileId.value) {
    failed.value = true
    return
  }
  retried = true
  void files.resolve([fileId.value], { force: true })
}
</script>

<template>
  <img v-if="src && !failed" class="thumb" :class="{ round }" :src="src" :alt="alt" :width="size" :height="size" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-testid="thumb" @error="onError" />
  <span v-else class="thumb thumb-empty" :class="{ round }" :style="{ width: `${size}px`, height: `${size}px` }" aria-hidden="true" data-testid="thumb-empty"><i class="pi pi-image" /></span>
</template>
