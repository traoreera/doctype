<script setup lang="ts">
import Button from 'primevue/button'
import Popover from 'primevue/popover'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import type { AuthNotification } from '@/lib/xauth'
import { decodeEntities, safeTarget, useNotifications } from '@/stores/notifications'

/** La cloche : les notifications d'xauth, relues toutes les minutes tant que l'onglet est visible. */
const store = useNotifications()
const router = useRouter()
const pop = ref()
const POLL_MS = 60_000
let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  void store.refresh()
  timer = setInterval(() => {
    if (typeof document === 'undefined' || document.visibilityState !== 'hidden') void store.refresh()
  }, POLL_MS)
})
onBeforeUnmount(() => clearInterval(timer))

function toggle(e: Event): void {
  pop.value.toggle(e)
  void store.refresh()
}

async function follow(n: AuthNotification): Promise<void> {
  void store.markRead(n.id)
  const target = safeTarget(n.link)
  if (!target) return
  pop.value.hide()
  if (target.kind === 'desk') await router.push(target.path)
  else globalThis.open(target.url, '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <button type="button" class="desk-bell" data-testid="notifications-open" :aria-label="t.notifications.open(store.unread)" :title="t.notifications.title" @click="toggle">
    <i class="pi pi-bell" />
    <span v-if="store.unread" class="desk-bell-count" data-testid="notifications-count">{{ store.unread > 9 ? '9+' : store.unread }}</span>
  </button>
  <Popover ref="pop" class="notif-pop">
    <div class="notif-panel" data-testid="notifications-panel">
      <header class="notif-head">
        <strong>{{ t.notifications.title }}</strong>
        <span class="desk-spacer" />
        <Button v-if="store.unread" :label="t.notifications.markAll" text size="small" data-testid="notifications-read-all" @click="store.markAll()" />
      </header>
      <p v-if="store.failed && !store.items.length" class="desk-muted" data-testid="notifications-failed">{{ t.notifications.failed }}</p>
      <p v-else-if="!store.items.length" class="desk-muted" data-testid="notifications-empty">{{ t.notifications.empty }}</p>
      <ul v-else class="notif-list">
        <li v-for="n in store.items" :key="n.id" class="notif-item" :class="{ unread: !n.is_read }" data-testid="notification">
          <button type="button" class="notif-body" :disabled="!safeTarget(n.link) && n.is_read" @click="follow(n)">
            <strong>{{ decodeEntities(n.title) }}</strong>
            <span v-if="n.message" class="notif-message">{{ decodeEntities(n.message) }}</span>
            <time class="desk-muted">{{ relativeTime(n.created_at) }}</time>
          </button>
          <Button icon="pi pi-times" text rounded size="small" severity="secondary" :aria-label="t.notifications.dismiss" data-testid="notification-dismiss" @click="store.dismiss(n.id)" />
        </li>
      </ul>
    </div>
  </Popover>
</template>
