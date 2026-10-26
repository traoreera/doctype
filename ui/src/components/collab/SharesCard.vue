<script setup lang="ts">
import AutoComplete from 'primevue/autocomplete'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import { ref, watch } from 'vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { useServices } from '@/lib/services'
import type { Person, SharesResult } from '@/lib/types'
import { useUsers } from '@/stores/users'

/**
 * Avec qui ce document est partagé (permissions.py) : un partage l'ouvre à un membre, en lecture ou en écriture, même sans rôle sur le type.
 * Qui peut ÉCRIRE le document partage et retire ; un autre ne voit que son propre partage, et peut y renoncer. Le serveur juge chaque appel.
 */
const props = defineProps<{ doctype: string; name: string }>()
const { api } = useServices()
const notify = useNotify()
const users = useUsers()

const data = ref<SharesResult | null>(null)
const member = ref<Person | string>('')
const write = ref(false)
const suggestions = ref<Person[]>([])
const busy = ref(false)
let ticket = 0

async function load(): Promise<void> {
  const mine = ++ticket
  try {
    const got = await api.shares(props.doctype, props.name)
    if (mine !== ticket) return
    data.value = got
  } catch {
    if (mine === ticket) data.value = null // un serveur plus ancien : la fiche reste utilisable sans cette carte
  }
}
watch(() => [props.doctype, props.name], () => void load(), { immediate: true })

async function search(e: { query: string }): Promise<void> {
  try {
    suggestions.value = await api.people(props.doctype, e.query)
  } catch {
    suggestions.value = [] // sans droit d'écrire le type (un partage) : la saisie libre d'un e-mail reste possible
  }
}

async function add(): Promise<void> {
  const who = typeof member.value === 'string' ? member.value.trim() : member.value.id
  if (!who) return
  busy.value = true
  try {
    await api.share(props.doctype, props.name, who, write.value)
    member.value = ''
    write.value = false
    await load()
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = false
  }
}

async function remove(who: string): Promise<void> {
  busy.value = true
  try {
    await api.unshare(props.doctype, props.name, who)
    await load()
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="data && (data.can_share || data.items.length)" class="desk-card collab-card" data-testid="shares">
    <header class="collab-card-head">
      <h3>{{ t.share.title }}</h3>
    </header>
    <p v-if="!data.items.length" class="desk-muted collab-empty" data-testid="shares-empty">{{ t.share.none }}</p>
    <ul v-else class="share-list">
      <li v-for="s in data.items" :key="s.member" class="share" :data-testid="`share-${s.member}`">
        <span class="share-who">{{ users.label(s.member) }}</span>
        <span class="status-pill">{{ s.write ? t.share.write : t.share.read }}</span>
        <Button
          v-if="data.can_share || s.member === data.me"
          icon="pi pi-times"
          text
          rounded
          size="small"
          severity="secondary"
          :aria-label="s.member === data.me ? t.share.leave : t.share.remove(users.label(s.member))"
          :title="s.member === data.me ? t.share.leave : t.share.remove(users.label(s.member))"
          :disabled="busy"
          :data-testid="`share-remove-${s.member}`"
          @click="remove(s.member)"
        />
      </li>
    </ul>
    <form v-if="data.can_share" class="share-form" @submit.prevent="add">
      <AutoComplete
        v-model="member"
        :suggestions="suggestions"
        option-label="name"
        :force-selection="false"
        fluid
        :placeholder="t.share.memberHint"
        :aria-label="t.share.member"
        input-id="share-member"
        data-testid="share-member"
        @complete="search"
      />
      <label class="share-write"><Checkbox v-model="write" binary input-id="share-write" data-testid="share-write" /> {{ t.share.canWrite }}</label>
      <Button type="submit" :label="t.share.add" icon="pi pi-share-alt" size="small" :loading="busy" data-testid="share-add" />
    </form>
  </section>
</template>

<style scoped>
.share-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}

.share {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.share-who {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.share-form {
  display: grid;
  gap: 0.5rem;
  margin-top: 0.75rem;
}

.share-write {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.875rem;
  min-height: 24px;
}
</style>
