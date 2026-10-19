<script setup lang="ts">
import Button from 'primevue/button'
import Menu from 'primevue/menu'
import SelectButton from 'primevue/selectbutton'
import { useConfirm } from 'primevue/useconfirm'
import { computed, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import BlockDialog from '@/components/BlockDialog.vue'
import BlockGrid from '@/components/BlockGrid.vue'
import OverrideEditor from '@/components/OverrideEditor.vue'
import { useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { blankBlock, declare, MAX_BLOCKS, moveBlock, preview, removeBlock, replaceBlock, resizeBlock, type BlockKind, type DeclaredBlock, type Known } from '@/lib/blocksedit'
import type { LayoutScope, WorkspaceBlock } from '@/lib/types'
import { useMeta } from '@/stores/meta'

/**
 * Des blocs qu'on peut PERSONNALISER sur place : on déplace (glisser ou flèches), redimensionne, modifie ou retire une carte, on en ajoute,
 * puis on enregistre. Rien ne part avant « Enregistrer » ; le serveur valide la description avec les règles du chargement d'un fichier.
 *
 * Sert deux pages : un espace (`blocks` = ses blocs, tous genres) et le tableau de bord d'un type (`doctype` : le type y est implicite, on n'y
 * met que titres, chiffres et graphiques). Le parent enregistre (`save`) et relit sa source ; ce composant garde le brouillon.
 */
const props = defineProps<{
  /** Les blocs tels que le serveur les sert (filtrés par les droits de l'utilisateur). */
  blocks: readonly WorkspaceBlock[]
  /** Tableau de bord d'un type : ce type, implicite dans chaque bloc. */
  doctype?: string
  /** D'où vient la disposition affichée : `user` (la mienne), `org` (celle de l'organisation), absent : celle du plugin. */
  layer?: LayoutScope | null
  /** Je peux enregistrer pour toute l'organisation (je peux écrire ce que la page montre). */
  shareable?: boolean
  /** Enregistre pour `scope` : `user` = la mienne seulement, `org` = toute l'organisation. */
  save: (blocks: DeclaredBlock[], scope: LayoutScope) => Promise<void>
  reset: (scope: LayoutScope) => Promise<void>
  /** Le JSON complet de l'habillage d'un espace (sa forme avancée) ; absent : pas de bouton JSON. */
  json?: { body: (blocks: DeclaredBlock[]) => unknown; save: (config: unknown, scope: LayoutScope) => Promise<void> }
}>()
const editing = defineModel<boolean>('editing', { default: false })

const metaStore = useMeta()
const notify = useNotify()
const confirm = useConfirm()

// Pour qui on enregistre : « moi » par défaut (sans conséquence pour les autres) ; « toute l'organisation » seulement si on peut l'écrire.
const scope = ref<LayoutScope>('user')
const scopes = computed(() => [
  { value: 'user' as const, label: t.editor.scopeMine, disabled: false },
  { value: 'org' as const, label: t.editor.scopeOrg, disabled: !props.shareable },
])
const layerLabel = computed(() => (props.layer === 'user' ? t.editor.layerMine : props.layer === 'org' ? t.editor.layerOrg : null))
const draft = ref<DeclaredBlock[]>([])
let before = '[]'
const saving = ref(false)
const dirty = computed(() => editing.value && JSON.stringify(draft.value) !== before)
const addMenu = ref<InstanceType<typeof Menu> | null>(null)
const dialog = ref<{ index: number | null; block: DeclaredBlock } | null>(null)
const dialogOpen = computed({ get: () => dialog.value !== null, set: (v: boolean) => { if (!v) dialog.value = null } })
const jsonOpen = ref(false)
const jsonInitial = ref<unknown>({})

const known = (doctype: string): Known | undefined => {
  const s = metaStore.summaries?.find((d) => d.name === doctype)
  const m = metaStore.metas[doctype]
  return s || m ? { label: s?.label || m?.label, icon: s?.icon, single: s?.single ?? m?.single, title_field: m?.title_field, submittable: m?.submittable } : undefined
}
const shown = computed(() => (editing.value ? draft.value.map((b) => preview(b, known)) : props.blocks))

// Les libellés, icônes et titres de documents que l'aperçu affiche viennent des définitions : on les charge pour chaque type cité.
watch(
  () => draft.value.map((b) => ('doctype' in b ? b.doctype : '')).join('|'),
  () => {
    if (!editing.value) return
    for (const b of draft.value) if (b.type === 'quick_list' && b.doctype) void metaStore.get(b.doctype).catch(() => {})
  },
)

function start(): void {
  draft.value = props.blocks.map(declare)
  before = JSON.stringify(draft.value)
  scope.value = 'user'
  editing.value = true
  void metaStore.loadSummaries().catch(() => [])
}

function stop(): void {
  editing.value = false
  dialog.value = null
  jsonOpen.value = false
}
defineExpose({ start })

const kinds: BlockKind[] = props.doctype ? ['number_card', 'chart', 'header', 'spacer'] : ['number_card', 'chart', 'quick_list', 'shortcut', 'card', 'header', 'paragraph', 'spacer']
const addItems = kinds.map((kind) => ({ label: t.editor.kinds[kind], command: () => add(kind) }))

function add(kind: BlockKind): void {
  if (draft.value.length >= MAX_BLOCKS) return notify.error(new Error(`${MAX_BLOCKS} blocs au plus`))
  const block = blankBlock(kind, props.doctype ?? '')
  if (kind === 'spacer') draft.value = [...draft.value, block]
  else dialog.value = { index: null, block }
}

function change(index: number): void {
  const block = draft.value[index]
  if (block) dialog.value = { index, block }
}

function validated(block: DeclaredBlock): void {
  const at = dialog.value?.index
  draft.value = at === null || at === undefined ? [...draft.value, block] : replaceBlock(draft.value, at, block)
}

const move = (from: number, to: number): void => void (draft.value = moveBlock(draft.value, from, to))
const resize = (index: number, cols: number): void => void (draft.value = resizeBlock(draft.value, index, cols))
const remove = (index: number): void => void (draft.value = removeBlock(draft.value, index))

/** Ce que le serveur attend : dans le tableau de bord d'un type, le type est implicite (`doctype` y est refusé). */
const outgoing = (): DeclaredBlock[] => (props.doctype ? draft.value.map((b) => { const { doctype: _d, ...rest } = b as DeclaredBlock & { doctype?: string }; return rest as DeclaredBlock }) : draft.value)

async function run(work: () => Promise<void>, ok: string): Promise<void> {
  saving.value = true
  try {
    await work()
    stop()
    notify.success(ok)
  } catch (e) {
    notify.error(e) // le serveur a refusé : on reste en édition, le travail est intact
  } finally {
    saving.value = false
  }
}

const doSave = (): Promise<void> => run(() => props.save(outgoing(), scope.value), scope.value === 'user' ? t.editor.savedMine : t.editor.saved)
const doReset = (): Promise<void> => run(() => props.reset(scope.value), scope.value === 'user' ? t.editor.resetMineDone : t.override.resetToast)
const doJson = (config: unknown): Promise<void> => run(() => props.json!.save(config, scope.value), t.override.savedToast)

function openJson(): void {
  jsonInitial.value = props.json!.body(outgoing())
  jsonOpen.value = true
}

const resetLabel = computed(() => (scope.value === 'user' ? t.editor.resetMine : t.editor.reset))

function askReset(): void {
  confirm.require({
    header: resetLabel.value,
    message: scope.value === 'user' ? t.editor.resetMineAsk : props.doctype ? t.editor.resetDashboardAsk : t.editor.resetAsk,
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: resetLabel.value,
    rejectLabel: t.cancel,
    rejectProps: { severity: 'secondary', text: true },
    accept: () => void doReset(),
  })
}

onBeforeRouteLeave(() => {
  if (!dirty.value || saving.value) return true
  return new Promise<boolean>((resolve) => {
    confirm.require({
      header: t.form.unsavedTitle,
      message: t.form.unsaved,
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: t.form.leave,
      rejectLabel: t.form.stay,
      rejectProps: { severity: 'secondary', text: true },
      accept: () => resolve(true),
      reject: () => resolve(false),
      onHide: () => resolve(false),
    })
  })
})

// Le parent change de type sans nous démonter : on ne lui emporte pas le brouillon de l'autre (un autre espace, lui, nous remonte : `:key`).
watch(() => props.doctype, stop)
</script>

<template>
  <div class="block-editor">
    <div v-if="editing" class="editor-bar" role="toolbar" data-testid="editor-bar">
      <SelectButton v-model="scope" :options="scopes" option-label="label" option-value="value" option-disabled="disabled" :allow-empty="false" size="small" :aria-label="t.editor.scopeLabel" data-testid="editor-scope" />
      <span v-if="layerLabel" class="status-pill" data-testid="editor-layer">{{ layerLabel }}</span>
      <span class="desk-spacer" />
      <span v-if="dirty" class="desk-muted editor-dirty" data-testid="editor-dirty">{{ t.editor.dirty }}</span>
      <Button :label="t.editor.add" icon="pi pi-plus" severity="secondary" size="small" aria-haspopup="true" data-testid="editor-add" @click="addMenu?.toggle($event)" />
      <Menu ref="addMenu" :model="addItems" popup />
      <Button v-if="json" :label="t.editor.json" icon="pi pi-code" severity="secondary" text size="small" data-testid="workspace-json" @click="openJson" />
      <Button :label="resetLabel" icon="pi pi-undo" severity="secondary" text size="small" :loading="saving" data-testid="editor-reset" @click="askReset" />
      <Button :label="t.cancel" severity="secondary" text size="small" data-testid="editor-cancel" @click="stop" />
      <Button :label="t.save" icon="pi pi-check" size="small" :loading="saving" :disabled="!dirty" data-testid="editor-save" @click="doSave" />
    </div>

    <p v-if="editing" class="desk-muted editor-hint" data-testid="editor-hint">{{ t.editor.hint }} {{ scope === 'user' ? t.editor.mineNote : doctype ? t.editor.sharedDashboard : t.editor.sharedNote }}</p>

    <BlockGrid :blocks="shown" :editing="editing" @move="move" @resize="resize" @edit="change" @remove="remove" />

    <BlockDialog v-model:visible="dialogOpen" :block="dialog?.block ?? null" :adding="dialog?.index === null" :doctype="doctype" @save="validated" />
    <OverrideEditor v-if="json" v-model:visible="jsonOpen" :title="t.override.workspaceTitle" :initial="jsonInitial" :saving="saving" @save="doJson" @reset="doReset" />
  </div>
</template>
