<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import { computed, onMounted, ref } from 'vue'
import { useAuth } from '@/composables/useAuth'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { relativeTime } from '@/lib/format'
import type { AuthSession, AuthUser, LinkedAccount, MfaSetup, ReceivedInvite } from '@/lib/xauth'

/**
 * Mon compte (xauth) : mon adresse, mon mot de passe, la double authentification et mes sessions ouvertes. Chacun ne touche qu'à SON compte :
 * xauth l'impose, cette page ne fait que le montrer.
 */
const auth = useAuth()
const notify = useNotify()
const me = ref<AuthUser | null>(null)
const sessions = ref<AuthSession[]>([])
const failure = ref<string | null>(null)
const linked = ref<LinkedAccount[]>([])
const received = ref<ReceivedInvite[]>([])

/** Ce qui est accessoire (comptes liés, invitations) ne doit jamais empêcher d'afficher le reste : une panne ici donne une liste vide. */
async function soft<T>(read: () => Promise<T[]>): Promise<T[]> {
  try {
    return await read()
  } catch {
    return []
  }
}

async function load(): Promise<void> {
  try {
    ;[me.value, sessions.value] = await Promise.all([auth.me(), auth.sessions()])
    failure.value = null
  } catch (e) {
    failure.value = describeError(e)
  }
  ;[linked.value, received.value] = await Promise.all([soft(() => auth.linkedAccounts()), soft(() => auth.myInvites())])
}
onMounted(load)

// ── Mot de passe ──────────────────────────────────────────
const pw = ref({ current: '', next: '', confirm: '', otp: '' })
const pwProblem = ref<string | null>(null)
const pwBusy = ref(false)
const policy = (p: string): boolean => p.length >= 8 && /[A-Z]/.test(p) && /\d/.test(p)

async function changePassword(): Promise<void> {
  pwProblem.value = null
  if (pw.value.next !== pw.value.confirm) return void (pwProblem.value = t.admin.account.passwordMismatch)
  if (!policy(pw.value.next)) return void (pwProblem.value = t.admin.account.passwordRules)
  pwBusy.value = true
  try {
    await auth.changePassword(pw.value.current, pw.value.next, pw.value.otp.trim() || undefined)
    pw.value = { current: '', next: '', confirm: '', otp: '' }
    notify.success(t.admin.account.passwordChanged)
  } catch (e) {
    pwProblem.value = describeError(e)
  } finally {
    pwBusy.value = false
  }
}

async function setPassword(): Promise<void> {
  pwProblem.value = null
  if (pw.value.next !== pw.value.confirm) return void (pwProblem.value = t.admin.account.passwordMismatch)
  if (!policy(pw.value.next)) return void (pwProblem.value = t.admin.account.passwordRules)
  pwBusy.value = true
  try {
    await auth.setPassword(pw.value.next)
    pw.value = { current: '', next: '', confirm: '', otp: '' }
    notify.success(t.admin.account.passwordSet)
    await load()
  } catch (e) {
    pwProblem.value = describeError(e)
  } finally {
    pwBusy.value = false
  }
}

async function unlink(a: LinkedAccount): Promise<void> {
  try {
    await auth.unlinkProvider(a.provider)
    notify.success(t.admin.account.unlinked(a.provider))
  } catch (e) {
    notify.error(e)
  }
  await load()
}

// ── Adresse e-mail ────────────────────────────────────────
const mailDialog = ref(false)
const mail = ref({ email: '', password: '', otp: '' })
const mailProblem = ref<string | null>(null)

async function changeEmail(): Promise<void> {
  mailProblem.value = null
  try {
    await auth.changeEmail(mail.value.email.trim(), mail.value.password, mail.value.otp.trim() || undefined)
    mailDialog.value = false
    mail.value = { email: '', password: '', otp: '' }
    notify.success(t.admin.account.emailChanged)
    await load()
  } catch (e) {
    mailProblem.value = describeError(e)
  }
}

// ── Nom d'utilisateur ─────────────────────────────────────
const nameDialog = ref(false)
const name = ref({ username: '', password: '', otp: '' })
const nameProblem = ref<string | null>(null)
const USERNAME = /^[a-z0-9][a-z0-9._-]{2,31}$/
const nameValid = computed(() => USERNAME.test(name.value.username.trim().toLowerCase()))

function openName(): void {
  name.value = { username: me.value?.username ?? '', password: '', otp: '' }
  nameProblem.value = null
  nameDialog.value = true
}

async function changeUsername(): Promise<void> {
  nameProblem.value = null
  try {
    await auth.changeUsername(name.value.username.trim(), name.value.password, name.value.otp.trim() || undefined)
    nameDialog.value = false
    notify.success(t.admin.account.usernameChanged)
    await load()
  } catch (e) {
    nameProblem.value = describeError(e)
  }
}

// ── Double authentification ───────────────────────────────
const setup = ref<MfaSetup | null>(null)
const mfaCode = ref('')
const mfaProblem = ref<string | null>(null)

async function startMfa(): Promise<void> {
  mfaProblem.value = null
  try {
    setup.value = await auth.mfaSetup()
  } catch (e) {
    notify.error(e)
  }
}

async function finishMfa(): Promise<void> {
  mfaProblem.value = null
  try {
    if (me.value?.mfa_enabled) await auth.mfaDisable(mfaCode.value.trim())
    else await auth.mfaEnable(mfaCode.value.trim())
    notify.success(me.value?.mfa_enabled ? t.admin.account.mfaDisabled : t.admin.account.mfaEnabled)
    setup.value = null
    mfaCode.value = ''
    await load()
  } catch (e) {
    mfaProblem.value = describeError(e)
  }
}

// ── Sessions ──────────────────────────────────────────────
async function revoke(s: AuthSession): Promise<void> {
  try {
    await auth.revokeSession(s.id)
    await load()
  } catch (e) {
    notify.error(e)
  }
}

async function revokeAll(): Promise<void> {
  try {
    await auth.revokeAllSessions()
    notify.success(t.admin.account.revokedAll)
    await load()
  } catch (e) {
    notify.error(e)
  }
}

const mfaNeedsCode = computed(() => !!me.value?.mfa_enabled || !!setup.value)
</script>

<template>
  <div class="admin-page" data-testid="account-settings">
    <Message v-if="failure" severity="error" class="banner-error" data-testid="admin-failed">{{ failure }}</Message>

    <section v-if="me" class="desk-card admin-card" data-testid="account-profile">
      <h3>{{ t.admin.account.profile }}</h3>
      <div class="admin-row">
        <div><span class="desk-muted">{{ t.admin.account.email }}</span><strong class="admin-value" data-testid="account-email">{{ me.email }}</strong></div>
        <Button :label="t.admin.account.changeEmail" text size="small" severity="secondary" data-testid="account-email-change" @click="mailDialog = true" />
      </div>
      <div v-if="me.has_password !== false" class="admin-row">
        <div>
          <span class="desk-muted">{{ t.admin.account.username }}</span>
          <strong v-if="me.username" class="admin-value" data-testid="account-username">{{ me.username }}</strong>
          <span v-else class="desk-muted admin-value" data-testid="account-username-none">{{ t.admin.account.usernameNone }}</span>
        </div>
        <Button :label="me.username ? t.admin.account.changeUsername : t.admin.account.usernameSet" text size="small" severity="secondary" data-testid="account-username-change" @click="openName" />
      </div>
    </section>

    <section v-if="me && me.has_password === false" class="desk-card admin-card" data-testid="account-set-password">
      <h3>{{ t.admin.account.setPassword }}</h3>
      <p class="desk-muted">{{ t.admin.account.setPasswordHint }}</p>
      <form class="admin-form" @submit.prevent="setPassword">
        <label class="bf-field"><span>{{ t.admin.account.newPassword }}</span><Password v-model="pw.next" :feedback="false" toggle-mask fluid autocomplete="new-password" input-id="pw-next" /></label>
        <label class="bf-field"><span>{{ t.admin.account.confirmPassword }}</span><Password v-model="pw.confirm" :feedback="false" toggle-mask fluid autocomplete="new-password" input-id="pw-confirm" /></label>
        <p class="desk-muted admin-hint">{{ t.admin.account.passwordRules }}</p>
        <Message v-if="pwProblem" severity="error" class="banner-error" data-testid="password-problem">{{ pwProblem }}</Message>
        <div><Button type="submit" :label="t.admin.account.setPassword" size="small" :loading="pwBusy" :disabled="!pw.next" data-testid="password-set" /></div>
      </form>
    </section>

    <section v-else class="desk-card admin-card" data-testid="account-password">
      <h3>{{ t.admin.account.password }}</h3>
      <form class="admin-form" @submit.prevent="changePassword">
        <label class="bf-field"><span>{{ t.admin.account.currentPassword }}</span><Password v-model="pw.current" :feedback="false" toggle-mask fluid autocomplete="current-password" input-id="pw-current" /></label>
        <label class="bf-field"><span>{{ t.admin.account.newPassword }}</span><Password v-model="pw.next" :feedback="false" toggle-mask fluid autocomplete="new-password" input-id="pw-next" /></label>
        <label class="bf-field"><span>{{ t.admin.account.confirmPassword }}</span><Password v-model="pw.confirm" :feedback="false" toggle-mask fluid autocomplete="new-password" input-id="pw-confirm" /></label>
        <label v-if="me?.mfa_enabled" class="bf-field"><span>{{ t.admin.account.otpCode }}</span><InputText v-model="pw.otp" inputmode="numeric" autocomplete="one-time-code" maxlength="10" data-testid="password-otp" /></label>
        <p class="desk-muted admin-hint">{{ t.admin.account.passwordRules }}</p>
        <Message v-if="pwProblem" severity="error" class="banner-error" data-testid="password-problem">{{ pwProblem }}</Message>
        <div><Button type="submit" :label="t.admin.account.change" size="small" :loading="pwBusy" :disabled="!pw.current || !pw.next" data-testid="password-save" /></div>
      </form>
    </section>

    <section v-if="me" class="desk-card admin-card" data-testid="account-mfa">
      <h3>{{ t.admin.account.mfa }}</h3>
      <p class="desk-muted">{{ me.mfa_enabled ? t.admin.account.mfaOn : t.admin.account.mfaOff }}</p>
      <template v-if="setup">
        <label class="bf-field"><span>{{ t.admin.account.mfaSecret }}</span><InputText :model-value="setup.secret" readonly data-testid="mfa-secret" /></label>
        <label class="bf-field"><span>{{ t.admin.account.mfaUri }}</span><InputText :model-value="setup.otpauth_url" readonly /></label>
        <div class="bf-field" data-testid="mfa-backup">
          <span>{{ t.admin.account.mfaBackup }}</span>
          <code class="admin-codes">{{ setup.backup_codes.join('  ') }}</code>
        </div>
      </template>
      <form v-if="mfaNeedsCode" class="admin-form" @submit.prevent="finishMfa">
        <label class="bf-field"><span>{{ t.admin.account.mfaCode }}</span><InputText v-model="mfaCode" inputmode="numeric" autocomplete="one-time-code" maxlength="10" data-testid="mfa-code" /></label>
        <Message v-if="mfaProblem" severity="error" class="banner-error" data-testid="mfa-problem">{{ mfaProblem }}</Message>
        <div><Button type="submit" :label="me.mfa_enabled ? t.admin.account.mfaDisable : t.admin.account.mfaEnable" size="small" :severity="me.mfa_enabled ? 'danger' : 'primary'" :disabled="!mfaCode.trim()" data-testid="mfa-confirm" /></div>
      </form>
      <Button v-if="!me.mfa_enabled && !setup" :label="t.admin.account.mfaEnable" size="small" data-testid="mfa-start" @click="startMfa" />
    </section>

    <section class="desk-card admin-card" data-testid="account-sessions">
      <header class="collab-card-head">
        <h3>{{ t.admin.account.sessions }}</h3>
        <span class="desk-spacer" />
        <Button v-if="sessions.length > 1" :label="t.admin.account.revokeAll" text size="small" severity="danger" data-testid="sessions-revoke-all" @click="revokeAll" />
      </header>
      <ul class="admin-list">
        <li v-for="s in sessions" :key="s.id" class="admin-row" data-testid="session">
          <div>
            <strong>{{ s.ip_address ?? '—' }}</strong>
            <span v-if="s.is_current" class="status-pill ok">{{ t.admin.account.current }}</span>
            <span class="desk-muted admin-sub">{{ t.admin.account.lastSeen(relativeTime(s.last_seen)) }} · {{ t.admin.account.expires(relativeTime(s.expires_at)) }}</span>
          </div>
          <Button v-if="!s.is_current" :label="t.admin.account.revoke" text size="small" severity="secondary" data-testid="session-revoke" @click="revoke(s)" />
        </li>
      </ul>
    </section>

    <section v-if="received.length" class="desk-card admin-card" data-testid="account-invitations">
      <h3>{{ t.admin.account.invitations }}</h3>
      <p class="desk-muted">{{ t.admin.account.invitationsHint }}</p>
      <ul class="admin-list">
        <li v-for="i in received" :key="i.id" class="admin-row" data-testid="received-invite">
          <strong>{{ t.admin.account.invitationFrom(i.tenant_name ?? i.tenant_id) }}</strong>
          <RouterLink :to="{ name: 'invite', query: { token: i.token } }" class="desk-link" data-testid="received-invite-open">{{ t.admin.account.accept }}</RouterLink>
        </li>
      </ul>
    </section>

    <section class="desk-card admin-card" data-testid="account-linked">
      <h3>{{ t.admin.account.linked }}</h3>
      <p v-if="!linked.length" class="desk-muted" data-testid="linked-none">{{ t.admin.account.linkedNone }} {{ t.admin.account.linkedHint }}</p>
      <ul v-else class="admin-list">
        <li v-for="a in linked" :key="a.provider" class="admin-row" data-testid="linked-account">
          <div>
            <strong>{{ a.provider }}</strong>
            <span class="desk-muted admin-sub">{{ a.provider_email ?? a.provider_name ?? '' }} · {{ t.admin.account.linkedAt(relativeTime(a.linked_at)) }}</span>
          </div>
          <Button :label="t.admin.account.unlink" text size="small" severity="secondary" data-testid="linked-unlink" @click="unlink(a)" />
        </li>
      </ul>
    </section>

    <Dialog v-model:visible="nameDialog" modal :header="me?.username ? t.admin.account.changeUsername : t.admin.account.usernameSet" :style="{ width: '420px', maxWidth: '95vw' }" data-testid="username-dialog">
      <form class="admin-form" @submit.prevent="changeUsername">
        <label class="bf-field"><span>{{ t.admin.account.newUsername }}</span><InputText v-model="name.username" maxlength="32" autocapitalize="none" autocorrect="off" spellcheck="false" data-testid="username-new" /></label>
        <p class="desk-muted admin-hint">{{ t.admin.account.usernameRules }}</p>
        <label class="bf-field"><span>{{ t.admin.account.currentPassword }}</span><Password v-model="name.password" :feedback="false" toggle-mask fluid input-id="username-password" /></label>
        <label v-if="me?.mfa_enabled" class="bf-field"><span>{{ t.admin.account.otpCode }}</span><InputText v-model="name.otp" inputmode="numeric" autocomplete="one-time-code" maxlength="10" data-testid="username-otp" /></label>
        <Message v-if="nameProblem" severity="error" class="banner-error" data-testid="username-problem">{{ nameProblem }}</Message>
      </form>
      <template #footer>
        <Button :label="t.admin.cancel" text severity="secondary" @click="nameDialog = false" />
        <Button :label="t.admin.save" :disabled="!nameValid || !name.password" data-testid="username-save" @click="changeUsername" />
      </template>
    </Dialog>

    <Dialog v-model:visible="mailDialog" modal :header="t.admin.account.changeEmail" :style="{ width: '420px', maxWidth: '95vw' }" data-testid="email-dialog">
      <form class="admin-form" @submit.prevent="changeEmail">
        <label class="bf-field"><span>{{ t.admin.account.newEmail }}</span><InputText v-model="mail.email" type="email" data-testid="email-new" /></label>
        <label class="bf-field"><span>{{ t.admin.account.currentPassword }}</span><Password v-model="mail.password" :feedback="false" toggle-mask fluid input-id="email-password" /></label>
        <label v-if="me?.mfa_enabled" class="bf-field"><span>{{ t.admin.account.otpCode }}</span><InputText v-model="mail.otp" inputmode="numeric" autocomplete="one-time-code" maxlength="10" data-testid="email-otp" /></label>
        <Message v-if="mailProblem" severity="error" class="banner-error" data-testid="email-problem">{{ mailProblem }}</Message>
      </form>
      <template #footer>
        <Button :label="t.admin.cancel" text severity="secondary" @click="mailDialog = false" />
        <Button :label="t.admin.save" :disabled="!mail.email || !mail.password" data-testid="email-save" @click="changeEmail" />
      </template>
    </Dialog>
  </div>
</template>
