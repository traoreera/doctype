<script setup lang="ts">
import Button from 'primevue/button'
import { computed, ref, watch } from 'vue'
import BlockEditor from '@/components/BlockEditor.vue'
import { t } from '@/i18n'
import type { DeclaredBlock } from '@/lib/blocksedit'
import { useServices } from '@/lib/services'
import type { LayoutScope, Workspace, WorkspaceOverride } from '@/lib/types'
import { useWorkspaces } from '@/stores/workspaces'

const props = defineProps<{ name: string }>()
const store = useWorkspaces()
const { api } = useServices()

const ws = computed<Workspace | undefined>(() => store.byName(props.name))
const loaded = computed(() => store.items !== null)
const editing = ref(false)
const editor = ref<InstanceType<typeof BlockEditor> | null>(null)
void store.load().catch(() => {})
// D'un espace à l'autre la page reste montée : on n'emporte pas un brouillon dans l'espace suivant.
watch(() => props.name, () => (editing.value = false))

// Un habillage porte aussi l'étiquette, l'icône et la description de l'espace : on les garde telles qu'elles sont, seuls les blocs changent.
function body(blocks: DeclaredBlock[]): WorkspaceOverride {
  const w = ws.value!
  return { label: w.label, ...(w.icon ? { icon: w.icon } : {}), ...(w.description ? { description: w.description } : {}), blocks: blocks as never }
}

async function save(config: WorkspaceOverride, scope: LayoutScope): Promise<void> {
  await api.saveWorkspaceOverride(props.name, config, scope)
  await store.load(true)
}

async function reset(scope: LayoutScope): Promise<void> {
  await api.removeWorkspaceOverride(props.name, scope)
  await store.load(true)
}
</script>

<template>
  <div class="desk-page" data-testid="workspace">
    <template v-if="ws">
      <div class="desk-page-head">
        <i v-if="ws.icon" :class="`pi pi-${ws.icon}`" class="ws-icon" />
        <h1 data-testid="workspace-title">{{ ws.label }}</h1>
        <span class="desk-spacer" />
        <Button v-if="!editing" :label="t.editor.start" icon="pi pi-pencil" severity="secondary" text size="small" data-testid="workspace-edit" @click="editor?.start()" />
      </div>
      <p v-if="ws.description && !editing" class="desk-muted ws-description">{{ ws.description }}</p>
      <BlockEditor
        ref="editor"
        :key="ws.name"
        v-model:editing="editing"
        :blocks="ws.blocks"
        :layer="ws.layer"
        :shareable="ws.shareable"
        :save="(blocks, scope) => save(body(blocks), scope)"
        :reset="reset"
        :json="{ body, save: (config, scope) => save(config as WorkspaceOverride, scope) }"
      />
    </template>
    <div v-else-if="loaded" class="desk-card" style="padding: 2rem; text-align: center" data-testid="workspace-missing">
      <p class="desk-muted">{{ t.workspace.notFound }}</p>
      <RouterLink :to="{ name: 'home' }">{{ t.home }}</RouterLink>
    </div>
    <div v-else class="desk-muted">{{ t.loading }}</div>
  </div>
</template>
