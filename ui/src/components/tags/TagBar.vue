<script setup lang="ts">
import Button from 'primevue/button'
import { computed, nextTick, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import TagInput from '@/components/tags/TagInput.vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { MAX_TAGS_PER_DOC, TAG_FILTER, sameTag } from '@/lib/tags'
import { useServices } from '@/lib/services'

/**
 * Les étiquettes d'un document, sous son titre. Chacune mène à la liste du type filtrée sur elle ; qui peut ÉCRIRE le document (le serveur le
 * dit : `can_tag`) en ajoute et en retire. Une étiquette se pose tout de suite, sans « Enregistrer » : elle n'est pas le document (un document
 * soumis s'étiquette encore), et le serveur répond la liste à jour, qu'on affiche telle quelle.
 */
const props = defineProps<{ doctype: string; name: string }>()
const { api } = useServices()
const notify = useNotify()

const tags = ref<string[]>([])
const bar = ref<HTMLElement | null>(null)
const canTag = ref(false)
const adding = ref(false)
const busy = ref(false)
const inputId = computed(() => `tag-${props.doctype}-${props.name}`.replace(/[^\w-]/g, '_'))
let ticket = 0

async function load(): Promise<void> {
  const mine = ++ticket
  adding.value = false
  try {
    const found = await api.tags(props.doctype, props.name)
    if (mine !== ticket) return
    tags.value = found.tags
    canTag.value = found.can_tag
  } catch {
    if (mine !== ticket) return
    tags.value = [] // un serveur plus ancien, ou un document qu'on ne lit plus : la fiche reste utilisable sans étiquettes
    canTag.value = false
  }
}
watch(() => [props.doctype, props.name], () => void load(), { immediate: true })

/** Un changement à la fois (la saisie et les × sont inactifs pendant `busy`) : deux réponses croisées ne peuvent pas laisser une liste périmée. */
async function change(body: { add?: string[]; remove?: string[] }): Promise<boolean> {
  busy.value = true
  const mine = ticket
  try {
    const after = await api.changeTags(props.doctype, props.name, body)
    if (mine === ticket) tags.value = after
    return mine === ticket
  } catch (e) {
    notify.error(e)
    return false
  } finally {
    busy.value = false
  }
}

/** Retirer une étiquette ne perd pas le clavier : le focus passe à la suivante, sinon à la précédente, sinon au bouton « + ». */
async function remove(tag: string): Promise<void> {
  const at = tags.value.indexOf(tag)
  if (!(await change({ remove: [tag] }))) return
  await nextTick()
  const next = tags.value[Math.min(at, tags.value.length - 1)]
  const removers = [...(bar.value?.querySelectorAll<HTMLElement>('.tag-remove') ?? [])]
  const target = next !== undefined ? removers.find((b) => b.dataset.tag === next) : bar.value?.querySelector<HTMLElement>('[data-testid="tag-add"]')
  target?.focus()
}

async function add(tag: string): Promise<void> {
  if (tags.value.some((x) => sameTag(x, tag))) return
  if (tags.value.length >= MAX_TAGS_PER_DOC) {
    notify.warn(t.tags.tooMany(MAX_TAGS_PER_DOC))
    return
  }
  await change({ add: [tag] })
}

async function open(): Promise<void> {
  adding.value = true
  await nextTick()
  document.getElementById(inputId.value)?.focus()
}

const filterTo = (tag: string) => ({ name: 'list', params: { doctype: props.doctype }, query: { filters: JSON.stringify({ [TAG_FILTER]: tag }) } })
</script>

<template>
  <div v-if="tags.length || canTag" ref="bar" class="tag-bar" data-testid="tag-bar">
    <span class="pi pi-tag tag-bar-icon" aria-hidden="true" />
    <ul class="tag-chips" :aria-label="t.tags.title">
      <li v-for="tag in tags" :key="tag" class="tag-chip" :data-testid="`tag-${tag}`">
        <RouterLink :to="filterTo(tag)" :title="t.tags.filterBy(tag)">{{ tag }}</RouterLink>
        <button v-if="canTag" type="button" class="tag-remove" :data-tag="tag" :aria-label="t.tags.remove(tag)" :title="t.tags.remove(tag)" :disabled="busy" :data-testid="`tag-remove-${tag}`" @click="void remove(tag)">
          <span class="pi pi-times" aria-hidden="true" />
        </button>
      </li>
    </ul>
    <template v-if="canTag">
      <TagInput v-if="adding" :doctype="doctype" :input-id="inputId" :exclude="tags" :disabled="busy" @commit="void add($event)" @cancel="adding = false" />
      <Button v-else-if="tags.length < MAX_TAGS_PER_DOC" icon="pi pi-plus" :label="tags.length ? undefined : t.tags.add" :aria-label="t.tags.add" :title="t.tags.add" text size="small" severity="secondary" data-testid="tag-add" @click="void open()" />
    </template>
  </div>
</template>

<style scoped>
.tag-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin: -0.25rem 0 0.75rem;
}

.tag-bar-icon {
  color: var(--p-text-muted-color);
  font-size: 0.85rem;
}
</style>
