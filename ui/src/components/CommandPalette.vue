<script setup lang="ts">
import Dialog from 'primevue/dialog'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { t } from '@/i18n'
import { piIcon } from '@/lib/icons'
import { buildCommands, documentCommands, rank, step, type ActionName, type Command } from '@/lib/commands'
import { useServices } from '@/lib/services'
import { useMeta } from '@/stores/meta'
import { useRecents } from '@/stores/recents'
import { useSession } from '@/stores/session'
import { useTheme } from '@/stores/theme'
import { useWorkspaces } from '@/stores/workspaces'

const open = defineModel<boolean>('open', { required: true })

const router = useRouter()
const { api } = useServices()
const meta = useMeta()
const recents = useRecents()
const workspaces = useWorkspaces()
const theme = useTheme()
const session = useSession()

const query = ref('')
const active = ref(0)
const input = ref<HTMLInputElement | null>(null)

const commands = computed(() => buildCommands({ workspaces: workspaces.items ?? [], summaries: meta.summaries ?? [], recents: recents.items }))

// La recherche GLOBALE (documents, tous types confondus) s'ajoute aux commandes dès deux caractères saisis. Débouncée, et gardée
// contre l'ordre d'arrivée : une réponse tardive ne doit pas écraser celle d'une saisie plus récente.
const docResults = ref<Command[]>([])
let seq = 0
let timer: ReturnType<typeof setTimeout> | undefined
function scheduleSearch(text: string): void {
  if (timer) clearTimeout(timer)
  const q = text.trim()
  if (q.length < 2) {
    docResults.value = []
    return
  }
  timer = setTimeout(() => {
    const mine = ++seq
    void api
      .globalSearch(q, 8)
      .then((items) => {
        if (mine === seq) docResults.value = documentCommands(items)
      })
      .catch(() => {
        if (mine === seq) docResults.value = []
      })
  }, 150)
}

const results = computed(() => rank([...commands.value, ...docResults.value], query.value, 12))
watch(query, (text) => {
  active.value = 0
  scheduleSearch(text)
})
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

// À l'ouverture : champ vide, les récents à jour, le curseur dans le champ.
watch(
  open,
  async (isOpen) => {
    if (!isOpen) return
    query.value = ''
    active.value = 0
    docResults.value = []
    recents.load()
    await nextTick()
    input.value?.focus()
  },
  { immediate: true },
)

const ACTIONS: Record<ActionName, () => void> = {
  'theme-light': () => theme.setMode('light'),
  'theme-dark': () => theme.setMode('dark'),
  'theme-system': () => theme.setMode('system'),
  logout: () => session.logout(),
}

function run(c: Command | undefined): void {
  if (!c) return
  open.value = false
  if (c.action) ACTIONS[c.action]()
  else if (c.to) void router.push(c.to)
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = step(active.value, 1, results.value.length)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = step(active.value, -1, results.value.length)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    run(results.value[active.value])
  }
}

// Le titre de groupe n'est montré qu'au changement de groupe : « Récents » une fois, pas devant chaque ligne.
const rows = computed(() => results.value.map((c, i) => ({ c, i, group: i === 0 || results.value[i - 1]!.group !== c.group ? c.group : null })))
</script>

<template>
  <Dialog v-model:visible="open" modal dismissable-mask :show-header="false" position="top" :draggable="false" class="palette" :aria-label="t.palette.title" :pt="{ root: { 'data-testid': 'palette' } }">
    <div class="palette-box" @keydown="onKey">
      <div class="palette-input">
        <i class="pi pi-search" />
        <input
          ref="input"
          v-model="query"
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          :aria-activedescendant="results[active] ? `palette-${results[active]!.id}` : undefined"
          :placeholder="t.palette.placeholder"
          :aria-label="t.palette.placeholder"
          autocomplete="off"
          spellcheck="false"
          data-testid="palette-input"
        />
        <kbd>Esc</kbd>
      </div>
      <ul id="palette-list" class="palette-list" role="listbox">
        <template v-for="r in rows" :key="r.c.id">
          <li v-if="r.group" class="palette-group" role="presentation">{{ t.palette.groups[r.group] }}</li>
          <li :id="`palette-${r.c.id}`" role="option" :aria-selected="r.i === active" class="palette-item" :class="{ active: r.i === active }" :data-testid="`cmd-${r.c.id}`" @mousemove="active = r.i" @click="run(r.c)">
            <i :class="piIcon(r.c.icon, 'file')" />
            <span class="palette-label">{{ r.c.label }}</span>
            <span v-if="r.c.hint" class="desk-muted palette-hint">{{ r.c.hint }}</span>
          </li>
        </template>
        <li v-if="!results.length" class="palette-empty desk-muted" data-testid="palette-empty">{{ t.palette.empty }}</li>
      </ul>
    </div>
  </Dialog>
</template>
