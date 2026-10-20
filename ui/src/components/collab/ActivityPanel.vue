<script setup lang="ts">
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { computed, ref, watch } from 'vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { buildFeed, type FeedFilter } from '@/lib/feed'
import { changeLines, linkValues, verbOf } from '@/lib/history'
import { formatTimestamp, relativeTime } from '@/lib/format'
import { initials } from '@/lib/navstate'
import { useServices } from '@/lib/services'
import type { Collab, CommentItem, DocTypeMeta, VersionEntry } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useUsers } from '@/stores/users'

/**
 * L'activité d'un document, en un seul fil : commentaires, assignations, pièces jointes et modifications, du plus récent au plus ancien.
 * On commente depuis ici (lire le document suffit) ; on modifie ou retire le sien. Les modifications viennent de l'historique, par pages.
 */
const props = defineProps<{ meta: DocTypeMeta; doctype: string; name: string; version: number; collab: Collab }>()
const emit = defineEmits<{ changed: [] }>()
const PAGE = 20

const { api } = useServices()
const links = useLinks()
const users = useUsers()
const notify = useNotify()
const confirm = useConfirm()

const filter = ref<FeedFilter>('all')
const draft = ref('')
const sending = ref(false)
const editing = ref<{ id: string; text: string } | null>(null)
const versions = ref<VersionEntry[]>([])
const total = ref(0)
const loadingVersions = ref(false)
const versionsFailed = ref(false)
let ticket = 0

async function loadVersions(reset: boolean): Promise<void> {
  const mine = ++ticket
  loadingVersions.value = true
  versionsFailed.value = false
  try {
    const page = await api.history(props.doctype, props.name, { limit: PAGE, offset: reset ? 0 : versions.value.length })
    if (mine !== ticket) return
    for (const [target, names] of linkValues(props.meta, page.items)) await links.resolve(target, names)
    versions.value = reset ? page.items : [...versions.value, ...page.items]
    total.value = page.total
  } catch {
    if (mine === ticket) versionsFailed.value = true
  } finally {
    if (mine === ticket) loadingVersions.value = false
  }
}

// Une sauvegarde change la version du document : les modifications se relisent ; un autre document, tout repart.
watch(() => [props.doctype, props.name, props.version] as const, () => void loadVersions(true), { immediate: true })

const feed = computed(() =>
  buildFeed({ comments: props.collab.comments, assignments: props.collab.assignments, attachments: props.collab.attachments, versions: versions.value }, filter.value),
)
const mine = (c: CommentItem): boolean => c.owner === props.collab.me
const canModerate = (c: CommentItem): boolean => mine(c) || props.collab.can_assign

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || sending.value) return
  sending.value = true
  try {
    await api.addComment(props.doctype, props.name, text)
    draft.value = ''
    emit('changed')
  } catch (e) {
    notify.error(e)
  } finally {
    sending.value = false
  }
}

async function saveEdit(): Promise<void> {
  const e = editing.value
  if (!e || !e.text.trim()) return
  try {
    await api.editComment(e.id, e.text.trim())
    editing.value = null
    emit('changed')
  } catch (err) {
    notify.error(err)
  }
}

function ask(c: CommentItem): void {
  confirm.require({
    header: t.collab.remove,
    message: t.collab.confirmDelete,
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.collab.remove,
    rejectLabel: t.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      try {
        await api.deleteComment(c.id)
        emit('changed')
      } catch (e) {
        notify.error(e)
      }
    },
  })
}

function onKey(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    void send()
  }
}

const filterKeys = computed(() => Object.keys(t.collab.filters) as FeedFilter[])
function moveFilter(delta: number): void {
  const keys = filterKeys.value
  const i = keys.indexOf(filter.value)
  const next = keys[(i + delta + keys.length) % keys.length]
  if (next) filter.value = next
}
</script>

<template>
  <section class="desk-card form-section activity" data-testid="activity">
    <header class="activity-head">
      <h3>{{ t.collab.title }}</h3>
      <span class="desk-spacer" />
      <div class="view-switcher" role="radiogroup" :aria-label="t.collab.title">
        <button
          v-for="(label, key) in t.collab.filters"
          :key="key"
          type="button"
          role="radio"
          :aria-checked="filter === key"
          :tabindex="filter === key ? 0 : -1"
          :class="['view-btn', { active: filter === key }]"
          :data-testid="`feed-filter-${key}`"
          @click="filter = key as FeedFilter"
          @keydown.right.prevent="moveFilter(1)"
          @keydown.down.prevent="moveFilter(1)"
          @keydown.left.prevent="moveFilter(-1)"
          @keydown.up.prevent="moveFilter(-1)"
        >
          <span>{{ label }}</span>
        </button>
      </div>
    </header>

    <form class="composer" @submit.prevent="send">
      <Textarea v-model="draft" rows="2" auto-resize maxlength="4000" :placeholder="t.collab.writeComment" :aria-label="t.collab.writeComment" data-testid="comment-input" @keydown="onKey" />
      <Button type="submit" :label="t.collab.comment" icon="pi pi-send" size="small" :loading="sending" :disabled="!draft.trim()" data-testid="comment-send" />
    </form>

    <p v-if="versionsFailed && filter !== 'comments'" class="desk-muted" data-testid="activity-failed">{{ t.collab.failed }}</p>
    <p v-if="!feed.length && !loadingVersions" class="desk-muted" data-testid="activity-empty">{{ t.collab.empty }}</p>

    <ol class="feed">
      <li v-for="item in feed" :key="item.key" :class="['feed-item', item.kind]" :data-kind="item.kind" :data-action="item.kind === 'version' ? item.entry.action : undefined" data-testid="feed-item">
        <span class="avatar" aria-hidden="true">{{ item.user ? initials(users.label(item.user)) : '' }}</span>
        <div class="feed-body">
          <template v-if="item.kind === 'comment'">
            <div class="who">
              <strong>{{ users.label(item.comment.owner) }}</strong>
              <time class="desk-muted" :datetime="item.at" :title="formatTimestamp(item.at)">· {{ relativeTime(item.at) }}</time>
              <span v-if="item.comment.edited" class="desk-muted">({{ t.collab.edited }})</span>
              <span class="desk-spacer" />
              <template v-if="editing?.id !== item.comment.id">
                <Button v-if="mine(item.comment)" icon="pi pi-pencil" text rounded size="small" severity="secondary" :aria-label="t.collab.edit" :title="t.collab.edit" data-testid="comment-edit" @click="editing = { id: item.comment.id, text: item.comment.content }" />
                <Button v-if="canModerate(item.comment)" icon="pi pi-trash" text rounded size="small" severity="secondary" :aria-label="t.collab.remove" :title="t.collab.remove" data-testid="comment-delete" @click="ask(item.comment)" />
              </template>
            </div>
            <div v-if="editing?.id === item.comment.id" class="comment-edit">
              <Textarea v-model="editing.text" rows="2" auto-resize maxlength="4000" :aria-label="t.collab.edit" data-testid="comment-edit-input" />
              <div class="comment-edit-actions">
                <Button :label="t.collab.cancel" text size="small" severity="secondary" @click="editing = null" />
                <Button :label="t.collab.save" size="small" :disabled="!editing.text.trim()" data-testid="comment-edit-save" @click="saveEdit" />
              </div>
            </div>
            <p v-else class="comment-text" data-testid="comment-text">{{ item.comment.content }}</p>
          </template>

          <template v-else-if="item.kind === 'version'">
            <div class="who">
              <strong>{{ users.label(item.entry.user) }}</strong> {{ verbOf(item.entry.action) }}
              <time class="desk-muted" :datetime="item.at" :title="formatTimestamp(item.at)">· {{ relativeTime(item.at) }}</time>
            </div>
            <ul v-if="item.entry.action === 'update'" class="changes">
              <li v-for="c in changeLines(meta, item.entry, links.title)" :key="c.field"><span class="desk-muted">{{ c.label }} :</span> <del>{{ c.old }}</del> → <ins>{{ c.new }}</ins></li>
            </ul>
          </template>

          <div v-else-if="item.kind === 'assigned'" class="who event">
            <i class="pi pi-user-plus" aria-hidden="true" /> {{ t.collab.events.assigned(users.label(item.assignment.assigned_by), users.label(item.assignment.assignee)) }}
            <time class="desk-muted" :datetime="item.at" :title="formatTimestamp(item.at)">· {{ relativeTime(item.at) }}</time>
          </div>

          <div v-else-if="item.kind === 'closed'" class="who event">
            <i class="pi pi-check-circle" aria-hidden="true" /> {{ t.collab.events.closed(t.collab.status[item.assignment.status] ?? item.assignment.status, users.label(item.assignment.assignee)) }}
            <time class="desk-muted" :datetime="item.at" :title="formatTimestamp(item.at)">· {{ relativeTime(item.at) }}</time>
          </div>

          <div v-else class="who event">
            <i class="pi pi-paperclip" aria-hidden="true" /> {{ t.collab.events.attached(users.label(item.attachment.owner), item.attachment.filename) }}
            <time class="desk-muted" :datetime="item.at" :title="formatTimestamp(item.at)">· {{ relativeTime(item.at) }}</time>
          </div>
        </div>
      </li>
    </ol>
    <Button v-if="filter !== 'comments' && versions.length < total" :label="t.collab.more" text size="small" :loading="loadingVersions" data-testid="activity-more" @click="loadVersions(false)" />
  </section>
</template>
