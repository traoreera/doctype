<script setup lang="ts">
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { computed, ref, watch } from 'vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { downloadBytes } from '@/lib/download'
import { useServices } from '@/lib/services'
import type { PrintTemplates, PrintedDocument } from '@/lib/types'

/**
 * Imprimer un document (process/impression.md §7).
 *
 * Trois gestes, et deux chemins de sortie délibérément différents :
 *
 * - **« Imprimer »** passe par le navigateur (`window.print` dans l'iframe). Il marche TOUJOURS, même sans le
 *   moteur PDF du serveur, parce que l'aperçu est du HTML ordinaire — c'est la promesse de §1.
 * - **« Télécharger le PDF »** demande le PDF du SERVEUR. Pas celui du navigateur, qui saurait aussi le faire :
 *   une facture sort de l'entreprise, et deux utilisateurs sur deux navigateurs produiraient deux fichiers
 *   différents. Celui du serveur est la référence. Le bouton n'existe pas si le moteur n'est pas installé, et
 *   le dialogue le DIT plutôt que de laisser un bouton qui échoue.
 *
 * L'aperçu vit dans une `iframe` en BAC À SABLE alimentée par `srcdoc` : le HTML vient du serveur, mais il est
 * construit depuis des données d'organisation, et rien ne justifie de le laisser s'exécuter dans la page du
 * Desk.
 *
 * Le bac à sable est `allow-same-origin allow-modals`, et ce choix est MESURÉ : avec `allow-modals` seul, le
 * cadre reçoit une origine opaque et `frame.contentWindow.print` lève un `SecurityError` depuis la page parente
 * (vérifié dans un vrai navigateur) — le bouton « Imprimer » aurait échoué sans rien dire. Ce qui compte, et
 * qui reste absent, c'est **`allow-scripts`** : rien ne s'exécute dans ce cadre. C'est la COMBINAISON
 * `allow-scripts allow-same-origin` qui permettrait à un document de retirer son propre bac à sable ;
 * `allow-same-origin` sans script n'ouvre rien.
 */
const props = defineProps<{ doctype: string; name: string; label: string }>()
const visible = defineModel<boolean>('visible', { required: true })
const { api } = useServices()
const notify = useNotify()

const templates = ref<PrintTemplates | null>(null)
const chosen = ref<string>('')
const letterhead = ref<'' | 'none'>('')
const rendered = ref<PrintedDocument | null>(null)
const busy = ref(false)
const pdfBusy = ref(false)
const failure = ref('')
const frame = ref<HTMLIFrameElement | null>(null)
/** Un jeton par demande d'aperçu : une réponse qui arrive après un changement de gabarit ne doit pas s'afficher. */
let ticket = 0

const choices = computed(() =>
  (templates.value?.templates ?? []).map((tpl) => ({
    value: tpl.name,
    label: tpl.source === 'plugin' ? tpl.label : `${tpl.label} (${t.print.sources[tpl.source] ?? tpl.source})`,
  })),
)
const letterheads = computed(() => [
  { value: '' as const, label: t.print.letterheadDefault },
  { value: 'none' as const, label: t.print.letterheadNone },
])

// `immediate` : un dialogue monté DÉJÀ ouvert doit charger lui aussi (c'est le cas d'un test, et ce serait
// celui d'un lien direct vers l'impression). Sans cela, seul un passage de fermé à ouvert déclencherait.
watch(
  visible,
  (open) => {
    if (!open) {
      rendered.value = null
      return
    }
    failure.value = ''
    letterhead.value = ''
    void load()
  },
  { immediate: true },
)

watch([chosen, letterhead], () => {
  if (visible.value && chosen.value) void preview()
})

async function load(): Promise<void> {
  busy.value = true
  try {
    const found = await api.printTemplates(props.doctype)
    templates.value = found
    // Changer `chosen` déclenche l'aperçu par le `watch` ; s'il ne change pas (même gabarit qu'avant), on le demande ici.
    const before = chosen.value
    chosen.value = found.default
    if (before === chosen.value) await preview()
  } catch (e) {
    failure.value = describeError(e)
    busy.value = false
  }
}

async function preview(): Promise<void> {
  const mine = ++ticket
  busy.value = true
  failure.value = ''
  try {
    const out = await api.print(props.doctype, props.name, { template: chosen.value, letterhead: letterhead.value || undefined })
    if (mine === ticket) rendered.value = out
  } catch (e) {
    if (mine === ticket) {
      failure.value = describeError(e)
      rendered.value = null
    }
  } finally {
    if (mine === ticket) busy.value = false
  }
}

function printNow(): void {
  // L'impression se demande à l'iframe elle-même : c'est son document qu'on imprime, pas la page du Desk.
  try {
    const window_ = frame.value?.contentWindow
    if (!window_) return
    window_.focus()
    window_.print()
  } catch (e) {
    // Un navigateur qui refuserait l'accès au cadre ne doit pas laisser l'utilisateur devant un bouton muet :
    // on le dit, et le PDF reste disponible quand le serveur a le moteur.
    failure.value = describeError(e)
  }
}

async function pdf(): Promise<void> {
  pdfBusy.value = true
  failure.value = ''
  try {
    const file = await api.printPdf(props.doctype, props.name, { template: chosen.value, letterhead: letterhead.value || undefined })
    downloadBytes(file.filename, file.bytes, 'application/pdf')
    notify.success(t.print.pdfDone)
  } catch (e) {
    failure.value = describeError(e)
  } finally {
    pdfBusy.value = false
  }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal :header="t.print.title(label)" :style="{ width: '62rem', maxWidth: '96vw' }" data-testid="print-dialog">
    <div class="print-body">
      <div class="print-controls">
        <label v-if="choices.length > 1" class="print-field">
          <span>{{ t.print.template }}</span>
          <Select v-model="chosen" :options="choices" option-label="label" option-value="value" size="small" data-testid="print-template" />
        </label>
        <label class="print-field">
          <span>{{ t.print.letterhead }}</span>
          <Select v-model="letterhead" :options="letterheads" option-label="label" option-value="value" size="small" data-testid="print-letterhead" />
        </label>
      </div>
      <Message v-if="templates && !templates.pdf" severity="info" size="small" data-testid="print-no-engine">{{ t.print.noEngine }}</Message>
      <Message v-if="rendered?.background" severity="info" size="small" data-testid="print-background">{{ t.print.background }}</Message>
      <Message v-if="failure" severity="error" data-testid="print-error">{{ failure }}</Message>
      <p v-if="busy && !rendered" class="desk-muted" data-testid="print-loading">{{ t.print.loading }}</p>
      <iframe
        v-if="rendered"
        ref="frame"
        class="print-frame"
        sandbox="allow-same-origin allow-modals"
        :title="t.print.preview"
        :srcdoc="rendered.html"
        data-testid="print-frame"
      />
    </div>
    <template #footer>
      <Button :label="t.close" text severity="secondary" @click="visible = false" />
      <Button
        v-if="templates?.pdf"
        :label="t.print.pdf"
        icon="pi pi-file-pdf"
        severity="secondary"
        outlined
        :loading="pdfBusy"
        :disabled="!rendered || pdfBusy"
        data-testid="print-pdf"
        @click="pdf"
      />
      <Button :label="t.print.printNow" icon="pi pi-print" :disabled="!rendered || busy" data-testid="print-now" @click="printNow" />
    </template>
  </Dialog>
</template>

<style scoped>
.print-body {
  display: grid;
  gap: 0.875rem;
}

.print-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.875rem;
}

.print-field {
  display: grid;
  gap: 0.3rem;
  font-size: 0.85rem;
  min-width: 12rem;
}

.print-field > span {
  color: var(--p-text-muted-color);
}

/* L'aperçu est une page de papier : fond blanc quel que soit le thème du Desk, comme elle sortira. */
.print-frame {
  width: 100%;
  height: min(60vh, 40rem);
  border: 1px solid var(--p-content-border-color);
  border-radius: var(--p-border-radius-md, 6px);
  background: #fff;
}
</style>
