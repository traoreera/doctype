<script setup lang="ts">
/**
 * « Mes modules » (`#/modules`) : le catalogue de l'instance et ce que MON organisation en a pris, avec le geste qui va avec.
 *
 * - Un module GRATUIT se prend et se rend d'ici (xlicense l'active tout de suite).
 * - Un module PAYANT s'achète chez le prestataire : on suit l'adresse que le SERVEUR a ouverte (`checkout`), et le retour
 *   (`#/billing`, `BillingReturnView`) le confirme. Rien n'est activé sur la foi du navigateur.
 * - Un module PAYÉ se résilie au portail du prestataire, là où l'argent est pris (le retirer d'ici couperait une période réglée).
 *
 * Seul l'administrateur de l'organisation agit (`can_manage`, rendu par le serveur, qui le rejuge à chaque appel) ; les autres
 * membres lisent. Un module pris ou rendu change les droits : on recharge la page pour que menus et espaces suivent.
 */
import Button from 'primevue/button'
import Message from 'primevue/message'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onMounted, ref } from 'vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { intlLocale, t } from '@/i18n'
import { formatTimestamp } from '@/lib/format'
import { useServices } from '@/lib/services'
import { moduleAction, type MarketModule, type ModuleAction, type MyModules } from '@/lib/xlicense'

const { license } = useServices()
const notify = useNotify()
const confirm = useConfirm()

const data = ref<MyModules | null>(null)
const failure = ref<string | null>(null)
const busy = ref<string | null>(null)

async function load(): Promise<void> {
  if (!license) return void (failure.value = t.billing.unavailable)
  try {
    data.value = await license.modules()
    failure.value = null
  } catch (e) {
    failure.value = describeError(e) || t.billing.loadFailed
  }
}
onMounted(load)

const modules = computed(() => [...(data.value?.modules ?? [])].sort((a, b) => Number(b.taken) - Number(a.taken) || a.label.localeCompare(b.label, intlLocale)))
const action = (m: MarketModule): ModuleAction => moduleAction(m, !!data.value?.can_manage)

function price(m: MarketModule): string {
  if (m.free) return t.billing.free
  const amount = m.currency
    ? new Intl.NumberFormat(intlLocale, { style: 'currency', currency: m.currency }).format(m.price)
    : new Intl.NumberFormat(intlLocale).format(m.price)
  return t.billing.price(amount, m.period)
}

function status(m: MarketModule): { label: string; tone: string } {
  if (m.taken && m.usable) return { label: t.billing.statusUsable, tone: 'ok' }
  if (m.taken) return { label: t.billing.statusTaken, tone: 'warn' }
  if (m.usable) return { label: t.billing.statusOpen, tone: 'warn' }
  return { label: t.billing.statusNone, tone: '' }
}

function source(m: MarketModule): string | null {
  if (!m.taken) return null
  if (m.source === 'paid') return t.billing.sourcePaid
  if (m.source === 'granted') return t.billing.sourceGranted
  return null
}

/** Les droits viennent du jeton et des espaces chargés au démarrage : après un changement de module, on repart d'une page neuve. */
function reloadDesk(): void {
  window.setTimeout(() => window.location.reload(), 1200)
}

async function run(m: MarketModule): Promise<void> {
  if (!license || busy.value) return
  const what = action(m)
  if (what === 'drop') {
    return void confirm.require({
      header: t.billing.dropHeader,
      message: t.billing.dropConfirm(m.label),
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: t.billing.drop,
      rejectLabel: t.cancel,
      acceptProps: { severity: 'danger' },
      rejectProps: { severity: 'secondary', text: true },
      accept: () => void act(m, async () => {
        await license.drop(m.code)
        notify.success(t.billing.dropped(m.label))
        reloadDesk()
      }),
    })
  }
  if (what === 'take') {
    return act(m, async () => {
      await license.take(m.code)
      notify.success(t.billing.taken(m.label))
      reloadDesk()
    })
  }
  if (what === 'subscribe') return act(m, async () => window.location.assign((await license.checkout(m.code)).url))
  if (what === 'portal') return act(m, async () => window.location.assign(await license.portal()))
}

async function act(m: MarketModule, work: () => Promise<void>): Promise<void> {
  busy.value = m.code
  try {
    await work()
    await load()
  } catch (e) {
    notify.error(e)
  } finally {
    busy.value = null
  }
}

const LABELS: Record<Exclude<ModuleAction, null>, { label: () => string; icon: string; outlined: boolean; severity?: string }> = {
  take: { label: () => t.billing.take, icon: 'pi pi-plus', outlined: false },
  subscribe: { label: () => t.billing.subscribe, icon: 'pi pi-credit-card', outlined: false },
  drop: { label: () => t.billing.drop, icon: 'pi pi-minus-circle', outlined: true, severity: 'secondary' },
  portal: { label: () => t.billing.manage, icon: 'pi pi-external-link', outlined: true, severity: 'secondary' },
}
</script>

<template>
  <div class="desk-page" data-testid="modules">
    <div class="desk-page-head"><i class="pi pi-th-large ws-icon" /><h1>{{ t.billing.myModules }}</h1></div>
    <p class="desk-muted ws-description">{{ t.billing.modulesIntro }}</p>

    <Message v-if="failure" severity="error" :closable="false" data-testid="modules-failed">{{ failure }}</Message>
    <p v-else-if="!data" class="desk-muted">{{ t.loading }}</p>
    <template v-else>
      <Message v-if="!data.valid" severity="error" :closable="false" data-testid="modules-blocked">{{ t.billing.licenseBlocked }}</Message>
      <Message v-else-if="data.trial" severity="info" :closable="false" data-testid="modules-trial">{{ t.billing.trial }}</Message>
      <p v-if="!data.can_manage" class="desk-muted" data-testid="modules-readonly">{{ t.billing.notManager }}</p>
      <p v-if="!modules.length" class="desk-muted" data-testid="modules-empty">{{ t.billing.empty }}</p>

      <ul class="modules-grid">
        <li v-for="m in modules" :key="m.code" class="module-card" :data-module="m.code" :data-taken="m.taken || undefined">
          <div class="module-head">
            <strong class="module-label">{{ m.label }}</strong>
            <span class="status-pill" :class="status(m).tone" data-testid="module-status">{{ status(m).label }}</span>
          </div>
          <p v-if="m.description" class="desk-muted module-desc">{{ m.description }}</p>
          <p class="module-price" data-testid="module-price">{{ price(m) }}</p>
          <p v-if="source(m) || m.expires_at" class="desk-muted module-meta">
            <span v-if="source(m)">{{ source(m) }}</span>
            <span v-if="m.expires_at"> · {{ t.billing.until(formatTimestamp(m.expires_at)) }}</span>
          </p>
          <div class="module-actions">
            <Button
              v-if="action(m)"
              :label="LABELS[action(m)!].label()"
              :icon="LABELS[action(m)!].icon"
              :outlined="LABELS[action(m)!].outlined"
              :severity="LABELS[action(m)!].severity"
              size="small"
              :loading="busy === m.code"
              :disabled="!!busy && busy !== m.code"
              :data-testid="`module-${action(m)}`"
              @click="run(m)"
            />
            <span v-else-if="data.can_manage && !m.taken && !m.free && !m.sellable" class="desk-muted">{{ t.billing.unsellable }}</span>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.modules-grid {
  list-style: none;
  margin: 1rem 0 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 16rem), 1fr));
  gap: 0.75rem;
}
.module-card {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 0;
  padding: 0.9rem 1rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-content-border-radius);
  background: var(--p-content-background);
}
.module-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.module-label {
  overflow-wrap: anywhere;
}
.module-desc,
.module-meta,
.module-price {
  margin: 0;
}
.module-price {
  font-weight: 600;
}
.module-actions {
  margin-top: auto;
  padding-top: 0.5rem;
}
</style>
