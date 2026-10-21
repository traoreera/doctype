<script setup lang="ts">
import AutoComplete from 'primevue/autocomplete'
import { computed, ref, watch } from 'vue'
import LinkPreview from '@/components/LinkPreview.vue'
import QuickEntryDialog from '@/components/QuickEntryDialog.vue'
import { t } from '@/i18n'
import { quickEntryFields } from '@/lib/form'
import { useServices } from '@/lib/services'
import type { DocRecord, DocTypeMeta } from '@/lib/types'
import { useLinks } from '@/stores/links'
import { useMeta } from '@/stores/meta'

const props = defineProps<{ modelValue: unknown; target: string; inputId?: string; invalid?: boolean; readonly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

interface Option {
  name: string
  label: string
}

const { api } = useServices()
const links = useLinks()
const metaStore = useMeta()

const shown = ref<string | Option | null>(null)
const options = ref<Option[]>([])

const asName = (v: unknown): string => (typeof v === 'string' ? v : '')

// Affiche le titre du document lié, pas son identifiant ; se remet à jour si la valeur change de l'extérieur.
watch(
  () => [props.modelValue, props.target] as const,
  async ([value, target]) => {
    const name = asName(value)
    if (!name) {
      shown.value = null
      return
    }
    await links.resolve(target, [name])
    shown.value = { name, label: links.title(target, name) }
  },
  { immediate: true },
)

// ── Créer le document cherché, sans quitter le formulaire (saisie rapide) ─────────────────
const targetMeta = ref<DocTypeMeta | null>(null)
const typed = ref('')
const creating = ref(false)
const canCreate = computed(() => !!targetMeta.value && targetMeta.value.rights?.create !== undefined && targetMeta.value.rights.create !== 'none' && !targetMeta.value.single && !targetMeta.value.virtual)
const quick = computed(() => !!targetMeta.value && quickEntryFields(targetMeta.value) !== null)
const label = computed(() => targetMeta.value?.label ?? props.target)
/** Combien de boutons dans le champ (ouvrir, créer) : le champ réserve leur place à droite. */
const actionCount = computed(() => (asName(props.modelValue) ? 1 : 0) + (canCreate.value ? 1 : 0))

async function loadTarget(): Promise<void> {
  if (props.readonly || !props.target || targetMeta.value?.name === props.target) return
  try {
    targetMeta.value = await metaStore.get(props.target)
  } catch {
    targetMeta.value = null // illisible : on ne propose pas de créer
  }
}
// La définition de la cible est déjà lue pour afficher les titres des liens : la relire ici ne coûte rien (cache).
watch(() => props.target, () => { targetMeta.value = null; void loadTarget() }, { immediate: true })
/** Ce que l'utilisateur a tapé et pas encore choisi : il devient le titre (ou le nom) du document qu'il crée. Retenu à la frappe (le champ, lui, se vide quand il perd le focus). */
const draft = computed(() => typed.value)

function created(rec: DocRecord): void {
  const title = targetMeta.value?.title_field ? rec.data[targetMeta.value.title_field] : undefined
  links.seed(props.target, rec.name, typeof title === 'string' ? title : null, rec.data)
  emit('update:modelValue', rec.name)
}

async function complete(e: { query: string }): Promise<void> {
  try {
    const found = await api.search(props.target, e.query.trim(), 10)
    options.value = found.map((i) => ({ name: i.name, label: i.title || i.name }))
  } catch {
    options.value = []
  }
}

function select(e: { value: Option }): void {
  typed.value = ''
  emit('update:modelValue', e.value.name)
}

function onInput(v: unknown): void {
  shown.value = v as string | Option | null
  if (typeof v === 'string' && v.trim()) typed.value = v.trim()
  // Champ vidé à la main : la valeur est effacée. Un texte libre n'est jamais envoyé (forceSelection).
  if (v === '' || v === null) emit('update:modelValue', null)
}
</script>

<template>
  <LinkPreview v-if="readonly && asName(modelValue)" :target="target" :name="asName(modelValue)">
    <RouterLink class="value cell-link" :to="{ name: 'form', params: { doctype: target, name: asName(modelValue) } }" data-testid="link-open">
      {{ links.title(target, asName(modelValue)) }}
    </RouterLink>
  </LinkPreview>
  <span v-else-if="readonly" class="value" />
  <div v-else class="link-field" :style="{ '--actions': actionCount }">
    <AutoComplete
      :model-value="shown"
      :suggestions="options"
      option-label="label"
      force-selection
      complete-on-focus
      :invalid="invalid"
      :input-id="inputId"
      fluid
      @update:model-value="onInput"
      @complete="complete"
      @option-select="select"
    >
      <template #option="{ option }">
        <span>{{ option.label }}</span>
        <small v-if="option.label !== option.name" class="desk-muted" style="margin-left: 0.5rem">{{ option.name }}</small>
      </template>
      <template #empty><span class="desk-muted" style="display: block; padding: 0.5rem 0.75rem">{{ t.linkField.none }}</span></template>
    </AutoComplete>
    <!-- Les actions du champ vivent DANS le champ, à droite (ancrées à son bord : rien ne se décale quand le contenu change, et il garde la largeur des autres champs). -->
    <span class="link-actions">
      <!-- Ouvrir la fiche du document lié, avec un aperçu au survol. -->
      <LinkPreview v-if="asName(modelValue)" :target="target" :name="asName(modelValue)">
        <RouterLink class="link-open" :to="{ name: 'form', params: { doctype: target, name: asName(modelValue) } }" :aria-label="links.title(target, asName(modelValue))" :title="links.title(target, asName(modelValue))" data-testid="link-open">
          <i class="pi pi-arrow-up-right" />
        </RouterLink>
      </LinkPreview>
      <!-- Créer le document qu'on ne trouve pas, sans quitter le formulaire (saisie rapide) ; un type qui exige un tableau ou un fichier ouvre sa fiche complète à part. -->
      <template v-if="canCreate">
        <button v-if="quick" type="button" class="link-open link-create" :aria-label="t.quickEntry.new(label)" :title="draft ? t.linkField.createNamed(label, draft) : t.quickEntry.new(label)" data-testid="link-create" @mousedown.prevent @click="creating = true">
          <i class="pi pi-plus" />
        </button>
        <RouterLink v-else :to="{ name: 'new', params: { doctype: target } }" target="_blank" class="link-open link-create" :aria-label="t.quickEntry.full" :title="t.quickEntry.full" data-testid="link-create-full">
          <i class="pi pi-plus" />
        </RouterLink>
      </template>
    </span>
    <QuickEntryDialog v-if="creating" v-model:visible="creating" :doctype="target" :initial="draft" @created="created" />
  </div>
</template>
