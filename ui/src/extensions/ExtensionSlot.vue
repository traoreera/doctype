<script setup lang="ts">
import { inject, onErrorCaptured, provide, ref, watch, type Component } from 'vue'
import { t } from '@/i18n'
import { EXTENSION_CONTEXT, EXTENSIONS } from './keys'

/**
 * Le cadre de TOUT composant d'extension (page, panneau de fiche, bloc d'espace) : il lui donne son contexte (`useDesk()`) et l'isole.
 * Une erreur de rendu ou de cycle de vie remplace le composant par un encart ; elle ne remonte jamais dans la page du Desk.
 */
const props = defineProps<{ extension: string; component: Component; props?: Record<string, unknown> }>()

const host = inject(EXTENSIONS, null)
if (host) provide(EXTENSION_CONTEXT, host.context(props.extension))
const broken = ref(false)
// Un autre composant ou d'autres props (une page d'extension à paramètre, un autre document) : on réessaie, l'échec précédent ne vaut plus.
watch(
  () => [props.component, props.props],
  () => (broken.value = false),
  { deep: true },
)
onErrorCaptured((e) => {
  console.error(`[extension ${props.extension}] composant en échec`, e)
  broken.value = true
  return false
})
</script>

<template>
  <div v-if="broken" class="desk-card ext-broken" role="status" data-testid="ext-broken" :data-extension="extension">{{ t.extensions.broken(extension) }}</div>
  <component :is="component" v-else v-bind="props.props ?? {}" />
</template>

<style scoped>
.ext-broken {
  padding: 0.75rem 1rem;
  color: var(--p-text-muted-color);
  font-size: 0.875rem;
}
</style>
