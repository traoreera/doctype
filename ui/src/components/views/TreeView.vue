<script setup lang="ts">
import Button from 'primevue/button'
import { computed, onMounted, ref, watch } from 'vue'
import { describeError } from '@/composables/useNotify'
import { t } from '@/i18n'
import { useServices } from '@/lib/services'
import type { DocTypeMeta, TreeNode } from '@/lib/types'

/**
 * L'arborescence d'un type (`views.tree`) : chaque document sous son parent, un chevron pour plier/déplier une branche.
 * Le serveur rend la forêt ENTIÈRE (une requête) ; le pliage est purement local. Un nœud mène au formulaire du document.
 */
const props = defineProps<{ doctype: string; meta: DocTypeMeta }>()

interface Row {
  node: TreeNode
  depth: number
  hasChildren: boolean
  collapsed: boolean
}

const { api } = useServices()
const nodes = ref<TreeNode[]>([])
const total = ref(0)
const truncated = ref(false)
const loading = ref(true)
const error = ref<string | null>(null)
const collapsed = ref<Set<string>>(new Set())

const canCreate = computed(() => props.meta.rights.create === 'all')

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await api.tree(props.doctype)
    nodes.value = res.items
    total.value = res.total
    truncated.value = res.truncated
    collapsed.value = new Set()
  } catch (e) {
    error.value = describeError(e)
    nodes.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const toggle = (name: string): void => {
  const next = new Set(collapsed.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  collapsed.value = next
}

const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const walk = (list: TreeNode[], depth: number): void => {
    for (const node of list) {
      const isCollapsed = collapsed.value.has(node.name)
      out.push({ node, depth, hasChildren: node.children.length > 0, collapsed: isCollapsed })
      if (node.children.length > 0 && !isCollapsed) walk(node.children, depth + 1)
    }
  }
  walk(nodes.value, 0)
  return out
})

onMounted(() => void load())
watch(() => props.doctype, () => void load())
</script>

<template>
  <div data-testid="tree">
    <div class="tree-bar">
      <span class="desk-muted" data-testid="tree-count">{{ t.tree.count(total) }}</span>
      <span class="desk-spacer" />
      <RouterLink v-if="canCreate" :to="{ name: 'new', params: { doctype } }" data-testid="tree-new">
        <Button :label="t.tree.newRoot" icon="pi pi-plus" size="small" />
      </RouterLink>
    </div>

    <p v-if="error" class="desk-muted block-error" data-testid="tree-error">{{ error }}</p>
    <p v-else-if="loading" class="desk-muted" data-testid="tree-loading">{{ t.loading }}</p>
    <p v-else-if="!rows.length" class="desk-muted" data-testid="tree-empty">{{ t.tree.empty }}</p>

    <ul v-else class="tree-list" role="tree">
      <li
        v-for="row in rows"
        :key="row.node.name"
        class="tree-row"
        role="treeitem"
        :aria-level="row.depth + 1"
        :data-testid="`tree-node-${row.node.name}`"
        :style="{ paddingLeft: `${row.depth * 1.25}rem` }"
      >
        <button
          v-if="row.hasChildren"
          type="button"
          class="tree-toggle"
          :aria-label="t.tree.expand(row.node.title)"
          :aria-expanded="!row.collapsed"
          :data-testid="`tree-toggle-${row.node.name}`"
          @click="toggle(row.node.name)"
        >
          <i :class="['pi', row.collapsed ? 'pi-chevron-right' : 'pi-chevron-down']" />
        </button>
        <span v-else class="tree-toggle tree-leaf" aria-hidden="true" />
        <RouterLink :to="{ name: 'form', params: { doctype, name: row.node.name } }" class="tree-title">
          {{ row.node.title }}
        </RouterLink>
        <span v-if="row.node.children.length" class="desk-muted tree-badge">{{ row.node.children.length }}</span>
      </li>
    </ul>

    <p v-if="truncated" class="desk-muted" data-testid="tree-truncated">{{ t.tree.truncated(total) }}</p>
  </div>
</template>

<style scoped>
.tree-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.tree-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.tree-row {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  min-height: 30px;
  border-radius: 6px;
}
.tree-row:hover {
  background: var(--desk-hover, rgba(0, 0, 0, 0.04));
}
.tree-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 0;
  background: transparent;
  color: var(--p-text-muted-color, #7c7c7c);
  cursor: pointer;
}
.tree-leaf {
  cursor: default;
}
.tree-title {
  color: inherit;
  text-decoration: none;
}
.tree-title:hover {
  text-decoration: underline;
}
.tree-badge {
  font-size: 0.8em;
}
</style>
