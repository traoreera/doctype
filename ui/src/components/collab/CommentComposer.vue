<script setup lang="ts">
import Textarea from 'primevue/textarea'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { t } from '@/i18n'
import { activeTrigger, matchCommands, replaceTrigger, type CommandId, type SlashCommand, type Trigger } from '@/lib/mentions'
import { useServices } from '@/lib/services'
import type { Person } from '@/lib/types'

/**
 * La zone où l'on écrit un commentaire, avec deux aides à la frappe :
 *  - `@` propose les membres de l'organisation ; choisir en insère l'adresse (`@ada@acme.fr`), ce qui est exactement ce que le serveur reconnaît pour les prévenir ;
 *  - `/` (en début de ligne ou après une espace) propose des commandes sur le document (`/assigner`, `/joindre`…) : choisie, la commande disparaît du texte et s'exécute.
 * Le menu se pilote au clavier (flèches, Entrée ou Tab pour choisir, Échap pour fermer) ; Ctrl+Entrée envoie, menu ouvert ou non.
 */
const props = withDefaults(defineProps<{ modelValue: string; doctype: string; name: string; commands?: readonly SlashCommand[]; label: string; placeholder?: string; testid?: string }>(), {
  commands: () => [],
  placeholder: undefined,
  testid: 'comment-input',
})
const emit = defineEmits<{ 'update:modelValue': [value: string]; submit: []; command: [id: CommandId] }>()

const { api } = useServices()
const field = ref<{ $el: HTMLTextAreaElement } | null>(null)
const uid = `cc${Math.random().toString(36).slice(2, 8)}`

interface Choice {
  key: string
  label: string
  hint?: string
  icon?: string
  /** Ce que le choix met dans le texte ; `null` : rien (une commande, qui s'exécute à la place). */
  insert: string | null
  command?: CommandId
}

const trigger = ref<Trigger | null>(null)
const people = ref<Person[]>([])
const active = ref(0)
const dismissed = ref<string | null>(null) // le déclencheur qu'on a fermé avec Échap : il ne se rouvre pas tant qu'on n'a pas changé de mot

const choices = computed<Choice[]>(() => {
  const tr = trigger.value
  if (!tr) return []
  if (tr.kind === '/') {
    return matchCommands(props.commands, tr.query).map((c) => ({ key: c.id, label: `/${c.word}`, hint: c.hint, icon: c.icon, insert: null, command: c.id }))
  }
  return people.value.map((p) => ({ key: p.id, label: p.email ?? p.name, hint: p.email && p.name !== p.email ? p.name : undefined, icon: 'user', insert: `@${p.email ?? p.name} ` }))
})
const open = computed(() => choices.value.length > 0 && dismissed.value !== signature(trigger.value))

function signature(tr: Trigger | null): string | null {
  return tr ? `${tr.kind}${tr.start}` : null
}

const el = (): HTMLTextAreaElement | null => field.value?.$el ?? null

/** Relit ce que l'utilisateur est en train de taper, au curseur. */
function refresh(): void {
  const area = el()
  if (!area) return
  const next = activeTrigger(area.value, area.selectionStart ?? area.value.length)
  if (signature(next) !== signature(trigger.value)) active.value = 0
  trigger.value = next
  if (next?.kind === '@') searchPeople(next.query)
  else people.value = []
  if (!next) dismissed.value = null
}

// ── Les membres : une lecture après la frappe, la dernière seule compte ──────────────────
let timer: ReturnType<typeof setTimeout> | undefined
let ticket = 0
function searchPeople(query: string): void {
  clearTimeout(timer)
  const mine = ++ticket
  timer = setTimeout(async () => {
    try {
      const found = await api.mentionable(props.doctype, props.name, query)
      if (mine === ticket && trigger.value?.kind === '@') people.value = found.items
    } catch {
      if (mine === ticket) people.value = [] // pas d'annuaire, pas de droit : on n'aide pas, on n'empêche pas d'écrire
    }
  }, 150)
}
onBeforeUnmount(() => clearTimeout(timer))
watch(() => props.doctype + props.name, () => (people.value = []))

// ── Choisir ─────────────────────────────────────────────────
async function choose(c: Choice): Promise<void> {
  const area = el()
  const tr = trigger.value
  if (!area || !tr) return
  const caret = area.selectionStart ?? area.value.length
  // Une commande disparaît du texte ; sauf « mention », qui laisse un `@` pour ouvrir aussitôt la liste des membres.
  const insert = c.command === 'mention' ? '@' : (c.insert ?? '')
  const replaced = replaceTrigger(area.value, caret, tr, insert)
  emit('update:modelValue', replaced.text)
  trigger.value = null
  people.value = []
  await nextTick()
  area.focus()
  area.setSelectionRange(replaced.caret, replaced.caret)
  if (c.command === 'mention') refresh()
  else if (c.command) emit('command', c.command)
}

function onKey(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    emit('submit')
    return
  }
  if (!open.value) return
  const n = choices.value.length
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    active.value = (active.value + 1) % n
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    active.value = (active.value - 1 + n) % n
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault()
    void choose(choices.value[active.value]!)
  } else if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    dismissed.value = signature(trigger.value)
  }
}

const activeId = computed(() => (open.value ? `${uid}-${active.value}` : undefined))
</script>

<template>
  <div class="composer-field">
    <Textarea
      ref="field"
      :model-value="modelValue"
      rows="2"
      auto-resize
      maxlength="4000"
      :placeholder="placeholder"
      :aria-label="label"
      aria-autocomplete="list"
      :aria-expanded="open"
      :aria-controls="`${uid}-list`"
      :aria-activedescendant="activeId"
      :data-testid="testid"
      @update:model-value="emit('update:modelValue', $event as string)"
      @input="refresh"
      @click="refresh"
      @keyup="(e: KeyboardEvent) => ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) && refresh()"
      @keydown="onKey"
      @blur="trigger = null"
    />
    <ul v-if="open" :id="`${uid}-list`" class="suggest" role="listbox" :aria-label="trigger?.kind === '/' ? t.collab.commandsTitle : t.collab.mentionTitle" data-testid="suggestions">
      <li
        v-for="(c, i) in choices"
        :id="`${uid}-${i}`"
        :key="c.key"
        role="option"
        :aria-selected="i === active"
        :class="{ on: i === active }"
        :data-testid="`suggest-${c.key}`"
        @mousedown.prevent="choose(c)"
        @mousemove="active = i"
      >
        <i v-if="c.icon" :class="['pi', `pi-${c.icon}`]" aria-hidden="true" />
        <span class="label">{{ c.label }}</span>
        <small v-if="c.hint" class="desk-muted">{{ c.hint }}</small>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.composer-field {
  position: relative;
  width: 100%;
  min-width: 0;
}

.composer-field :deep(textarea) {
  width: 100%;
}

.suggest {
  position: absolute;
  z-index: 30;
  left: 0;
  right: 0;
  top: calc(100% + 0.25rem);
  margin: 0;
  padding: 0.25rem;
  list-style: none;
  max-height: 14rem;
  overflow: auto;
  border: 1px solid var(--p-content-border-color);
  border-radius: 8px;
  background: var(--p-content-background);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
}

.suggest li {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.875rem;
}

.suggest li.on {
  background: var(--p-content-hover-background);
}

.suggest .label {
  font-weight: 600;
}

.suggest .pi {
  color: var(--p-text-muted-color);
  font-size: 0.8rem;
}
</style>
