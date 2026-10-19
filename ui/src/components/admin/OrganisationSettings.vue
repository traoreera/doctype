<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Textarea from 'primevue/textarea'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref, watch } from 'vue'
import { useAuth, useLoad } from '@/composables/useAuth'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { useSession } from '@/stores/session'

/**
 * Organisations (xauth) : celles de mon compte, le nom et les réglages libres de la courante, en créer une, la supprimer (plateforme). Le propriétaire de
 * l'organisation (ou l'administrateur de la plateforme) modifie ; xauth tranche, la page ne grise que ce qui serait refusé à coup sûr.
 */
const auth = useAuth()
const session = useSession()
const notify = useNotify()
const confirm = useConfirm()
const current = computed(() => session.tenant ?? '')

const tenants = useLoad(() => auth.tenants())
const mine = computed(() => (tenants.data.value ?? []).find((x) => x.id === current.value) ?? null)
const canEdit = computed(() => !!mine.value?.is_owner || session.platformAdmin)
onMounted(() => void tenants.reload())

// ── Nom ───────────────────────────────────────────────────
const name = ref('')
watch(mine, (m) => (name.value = m?.name ?? ''), { immediate: true })
const nameProblem = ref<string | null>(null)

async function rename(): Promise<void> {
  nameProblem.value = null
  try {
    await auth.renameTenant(current.value, name.value.trim())
    notify.success(t.admin.organisation.renamed)
    await tenants.reload()
    void session.loadTenants()
  } catch (e) {
    nameProblem.value = describeError(e)
  }
}

// ── Réglages libres (objet JSON) ──────────────────────────
const settingsText = ref('{}')
const settingsProblem = ref<string | null>(null)
const settingsReady = ref(false)

async function loadSettings(): Promise<void> {
  try {
    settingsText.value = JSON.stringify(await auth.tenantSettings(current.value), null, 2)
    settingsReady.value = true
  } catch {
    settingsReady.value = false // illisibles : la section ne s'affiche pas, le reste de la page reste utile
  }
}
onMounted(loadSettings)

async function saveSettings(): Promise<void> {
  settingsProblem.value = null
  let parsed: unknown
  try {
    parsed = JSON.parse(settingsText.value)
  } catch {
    return void (settingsProblem.value = t.admin.organisation.settingsInvalid)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return void (settingsProblem.value = t.admin.organisation.settingsInvalid)
  try {
    await auth.saveTenantSettings(current.value, parsed as Record<string, unknown>)
    notify.success(t.admin.organisation.settingsSaved)
  } catch (e) {
    settingsProblem.value = describeError(e)
  }
}

// ── Passer d'une organisation à l'autre ───────────────────
const switching = ref(false)
async function open(id: string): Promise<void> {
  if (switching.value) return
  switching.value = true
  try {
    const out = await session.switchTenant(id)
    if (out.status === 'ok') globalThis.location.reload()
    else notify.error(out.message)
  } finally {
    switching.value = false
  }
}

// ── Création ──────────────────────────────────────────────
const createOpen = ref(false)
const draft = ref({ name: '', slug: '' })
const createProblem = ref<string | null>(null)
const SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/
const slugOk = computed(() => SLUG.test(draft.value.slug))

function suggestSlug(): void {
  if (draft.value.slug) return
  draft.value.slug = draft.value.name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
}

async function create(): Promise<void> {
  createProblem.value = null
  try {
    const made = await auth.createTenant(draft.value.name.trim(), draft.value.slug)
    notify.success(t.admin.organisation.created(made.name))
    createOpen.value = false
    draft.value = { name: '', slug: '' }
    await tenants.reload()
    void session.loadTenants()
  } catch (e) {
    createProblem.value = describeError(e)
  }
}

// ── Suppression (plateforme) ──────────────────────────────
function askDelete(): void {
  const org = mine.value
  if (!org) return
  confirm.require({
    header: t.admin.organisation.delete,
    message: t.admin.organisation.confirmDelete(org.name),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.admin.organisation.delete,
    rejectLabel: t.admin.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: async () => {
      try {
        await auth.deleteTenant(org.id)
        notify.success(t.admin.organisation.deleted)
        void session.logout()
      } catch (e) {
        notify.error(e)
      }
    },
  })
}
</script>

<template>
  <div class="admin-page" data-testid="organisation-settings">
    <Message v-if="tenants.error.value" severity="error" class="banner-error" data-testid="admin-failed">{{ tenants.error.value }}</Message>

    <section class="desk-card admin-card" data-testid="organisations">
      <header class="collab-card-head">
        <h3>{{ t.admin.organisation.mine }}</h3>
        <span class="desk-spacer" />
        <Button :label="t.admin.organisation.create" icon="pi pi-plus" size="small" data-testid="organisation-create-open" @click="createOpen = true" />
      </header>
      <ul class="admin-list">
        <li v-for="o in tenants.data.value ?? []" :key="o.id" class="admin-row" data-testid="organisation">
          <div>
            <strong>{{ o.name }}</strong>
            <span v-if="o.id === current" class="status-pill ok">{{ t.admin.organisation.current }}</span>
            <span v-if="o.is_owner" class="status-pill">{{ t.admin.organisation.owner }}</span>
            <span class="desk-muted admin-sub admin-mono">{{ o.slug }}</span>
          </div>
          <Button v-if="o.id !== current" :label="t.admin.organisation.open" text size="small" severity="secondary" :loading="switching" data-testid="organisation-open" @click="open(o.id)" />
        </li>
      </ul>
    </section>

    <section v-if="mine" class="desk-card admin-card" data-testid="organisation-identity">
      <h3>{{ t.admin.organisation.identity }}</h3>
      <form class="admin-form" @submit.prevent="rename">
        <label class="bf-field"><span>{{ t.admin.organisation.name }}</span><InputText v-model="name" maxlength="120" :disabled="!canEdit" data-testid="organisation-name" /></label>
        <label class="bf-field"><span>{{ t.admin.organisation.slug }}</span><InputText :model-value="mine.slug" readonly data-testid="organisation-slug" /></label>
        <label class="bf-field"><span>{{ t.admin.organisation.id }}</span><InputText :model-value="mine.id" readonly class="admin-mono" data-testid="organisation-id" /></label>
        <p v-if="!canEdit" class="desk-muted admin-hint" data-testid="organisation-owner-only">{{ t.admin.organisation.ownerOnly }}</p>
        <Message v-if="nameProblem" severity="error" class="banner-error" data-testid="organisation-name-problem">{{ nameProblem }}</Message>
        <div v-if="canEdit"><Button type="submit" :label="t.admin.organisation.rename" size="small" :disabled="!name.trim() || name.trim() === mine.name" data-testid="organisation-rename" /></div>
      </form>
    </section>

    <section v-if="mine && settingsReady" class="desk-card admin-card" data-testid="organisation-settings-json">
      <h3>{{ t.admin.organisation.settings }}</h3>
      <p class="desk-muted admin-hint">{{ t.admin.organisation.settingsHint }}</p>
      <form class="admin-form admin-wide" @submit.prevent="saveSettings">
        <Textarea v-model="settingsText" rows="8" class="admin-mono" auto-resize :readonly="!canEdit" data-testid="organisation-settings-text" />
        <Message v-if="settingsProblem" severity="error" class="banner-error" data-testid="organisation-settings-problem">{{ settingsProblem }}</Message>
        <div v-if="canEdit"><Button type="submit" :label="t.admin.save" size="small" data-testid="organisation-settings-save" /></div>
      </form>
    </section>

    <section v-if="mine && session.platformAdmin" class="desk-card admin-card" data-testid="organisation-danger">
      <h3>{{ t.admin.organisation.danger }}</h3>
      <p class="desk-muted admin-hint">{{ t.admin.organisation.deleteHint }}</p>
      <div><Button :label="t.admin.organisation.delete" severity="danger" outlined size="small" data-testid="organisation-delete" @click="askDelete" /></div>
    </section>

    <Dialog v-model:visible="createOpen" modal :header="t.admin.organisation.create" :style="{ width: '440px', maxWidth: '95vw' }">
      <form class="admin-form" @submit.prevent="create">
        <p class="desk-muted admin-hint">{{ t.admin.organisation.createHint }}</p>
        <label class="bf-field"><span>{{ t.admin.organisation.createName }}</span><InputText v-model="draft.name" maxlength="120" data-testid="organisation-new-name" @blur="suggestSlug" /></label>
        <label class="bf-field"><span>{{ t.admin.organisation.createSlug }}</span><InputText v-model="draft.slug" maxlength="63" class="admin-mono" data-testid="organisation-new-slug" /></label>
        <Message v-if="createProblem" severity="error" class="banner-error" data-testid="organisation-create-problem">{{ createProblem }}</Message>
      </form>
      <template #footer>
        <Button :label="t.admin.cancel" text severity="secondary" @click="createOpen = false" />
        <Button :label="t.admin.organisation.create" :disabled="!draft.name.trim() || !slugOk" data-testid="organisation-create" @click="create" />
      </template>
    </Dialog>
  </div>
</template>
