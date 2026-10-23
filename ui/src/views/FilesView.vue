<script setup lang="ts">
import Button from 'primevue/button'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Paginator from 'primevue/paginator'
import Select from 'primevue/select'
import { useConfirm } from 'primevue/useconfirm'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import FileThumb from '@/components/FileThumb.vue'
import { describeError, useNotify } from '@/composables/useNotify'
import { t } from '@/i18n'
import { ApiError } from '@/lib/api'
import { downloadUrl } from '@/lib/download'
import { FILE_SORTS, type FileSort, citationKey, detachTarget, detailLines, isImage, sortQuery } from '@/lib/filelibrary'
import { formatBytes, formatTimestamp, relativeTime } from '@/lib/format'
import { useServices } from '@/lib/services'
import type { FileLibraryItem, FileLibraryResult } from '@/lib/types'
import { useFiles } from '@/stores/files'
import { useLinks } from '@/stores/links'
import { useUsers } from '@/stores/users'

/**
 * « Fichiers » : les fichiers que citent les documents que je peux lire (un champ Image/Attach ou une pièce jointe), une ligne par document citant.
 * Le serveur choisit ce qui s'affiche (un fichier d'un document illisible n'existe pas) et qui peut détacher ; « Détacher » modifie le document qui
 * le cite (ou retire la pièce jointe) par l'écriture ordinaire : un refus (document soumis, champ obligatoire, contrôle, version périmée) est montré
 * tel que le serveur l'a dit. Le contenu se télécharge par un lien signé de courte durée, redemandé s'il a expiré pendant que la page restait ouverte.
 */
const { api } = useServices()
const confirm = useConfirm()
const notify = useNotify()
const files = useFiles()
const links = useLinks()
const users = useUsers()

const page = ref<FileLibraryResult | null>(null)
const failure = ref<string | null>(null)
const search = ref('')
const doctype = ref<string | null>(null)
const sort = ref<FileSort>('creation:desc')
const sortOptions = FILE_SORTS.map((value) => ({ value, label: t.fileLibrary.sorts[value] }))
const first = ref(0)
const rows = ref(20)
const busy = ref<string | null>(null)
const problem = ref<{ title: string; lines: string[] } | null>(null)
let ticket = 0

async function load(): Promise<void> {
  const mine = ++ticket
  failure.value = null
  try {
    const got = await api.fileLibrary({ doctype: doctype.value, q: search.value.trim() || undefined, ...sortQuery(sort.value), limit: rows.value, offset: first.value })
    if (mine !== ticket) return
    if (!got.items.length && got.total && first.value > 0) {
      // La dernière ligne de la dernière page vient d'être détachée : on revient à la page d'avant au lieu d'afficher une page vide.
      first.value = Math.max(0, Math.floor((got.total - 1) / rows.value) * rows.value)
      return load()
    }
    page.value = got
    const ids = got.items.map((i) => i.file_id)
    if (ids.length) void files.resolve(ids)
    // Le titre que l'utilisateur reconnaît (le nom du salarié, pas un identifiant) : un lot par type. Un réglage se nomme comme son type.
    const byType = new Map<string, string[]>()
    for (const i of got.items) if (i.docname !== i.doctype) byType.set(i.doctype, [...(byType.get(i.doctype) ?? []), i.docname])
    for (const [type, names] of byType) void links.resolve(type, names).catch(() => {})
  } catch (e) {
    if (mine === ticket) failure.value = describeError(e)
  }
}

onMounted(load)

function restart(): void {
  first.value = 0
  void load()
}
watch([doctype, sort], restart)
// La recherche part à la fin de la frappe.
let timer: ReturnType<typeof setTimeout> | undefined
watch(search, () => {
  clearTimeout(timer)
  timer = setTimeout(restart, 300)
})
onBeforeUnmount(() => clearTimeout(timer))

function onPage(e: { first: number; rows: number }): void {
  first.value = e.first
  rows.value = e.rows
  void load()
}

const items = computed(() => page.value?.items ?? [])
const filtered = computed(() => !!search.value.trim() || doctype.value !== null)
/** Les types proposés au filtre : ceux qui citent des fichiers (avec leur nombre) ; le type choisi y reste même quand la recherche le vide. */
const typeOptions = computed(() => {
  const found = (page.value?.doctypes ?? []).map((d) => ({ value: d.name, label: t.fileLibrary.typeOption(d.label, d.count) }))
  if (doctype.value && !found.some((o) => o.value === doctype.value)) found.unshift({ value: doctype.value, label: t.fileLibrary.typeOption(doctype.value, 0) })
  return found
})
/** Les types dont la source ne répond pas, nommés comme l'utilisateur les connaît (leur libellé, pas leur nom d'API). */
const unavailable = computed(() => (page.value?.unavailable ?? []).map((u) => u.label))

const docLabel = (f: FileLibraryItem): string => (f.docname === f.doctype ? f.doctype_label : links.title(f.doctype, f.docname))
const fieldLabel = (f: FileLibraryItem): string => (f.attachment ? t.fileLibrary.attachment : (f.field_label ?? f.field))

// ── Télécharger ─────────────────────────────────────────────
const MARGIN = 30 // secondes : un lien qui expire avant d'avoir servi est redemandé (le magasin en fait autant)
const fresh = (id: string): boolean => (files.info(id)?.expires ?? 0) - Date.now() / 1000 > MARGIN

async function download(e: MouseEvent, f: FileLibraryItem): Promise<void> {
  if (fresh(f.file_id)) return // le lien signé est valable : le navigateur le suit lui-même
  e.preventDefault()
  await files.resolve([f.file_id], { force: true })
  const url = files.url(f.file_id)
  if (url) downloadUrl(url, f.filename)
  else notify.warn(t.fileLibrary.gone)
}

// ── Détacher ────────────────────────────────────────────────
function ask(f: FileLibraryItem): void {
  const doc = `${f.doctype_label} ${docLabel(f)}`
  confirm.require({
    header: t.fileLibrary.detachTitle,
    // « historisé » seulement si le type garde un historique (`track_changes`) : ne pas promettre une trace qui n'existera pas.
    message: f.attachment ? t.fileLibrary.confirmAttachment(f.filename, doc) : t.fileLibrary.confirmField(f.filename, fieldLabel(f), doc, f.track_changes),
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: t.fileLibrary.detach,
    rejectLabel: t.collab.cancel,
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', text: true },
    accept: () => void detach(f),
  })
}

/** Le refus du serveur en phrases : son message, puis le détail de chaque champ (le libellé du champ fichier plutôt que son nom). */
function refusal(e: unknown, f: FileLibraryItem): string[] {
  if (!(e instanceof ApiError)) return [describeError(e)]
  if (e.status === 403) return [t.errors.forbidden]
  if (e.isConflict) return [t.fileLibrary.changed]
  const lines = detailLines(e.details, (field) => (field === f.field ? fieldLabel(f) : field))
  return [e.message, ...lines.filter((l) => l !== e.message)]
}

async function detach(f: FileLibraryItem): Promise<void> {
  busy.value = citationKey(f)
  problem.value = null
  try {
    await api.detachFile(detachTarget(f))
    notify.success(t.fileLibrary.detached(f.filename))
    await load()
  } catch (e) {
    problem.value = { title: t.fileLibrary.refused(f.filename), lines: refusal(e, f) }
    // Ce que l'écran montre est périmé (document modifié, soumis ou annulé entre-temps, fichier déjà retiré, document disparu) : on relit la liste.
    if (e instanceof ApiError && (e.isConflict || e.isStale || e.status === 404 || e.code === 'file_not_cited')) await load()
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="desk-page" data-testid="files">
    <div class="desk-page-head"><i class="pi pi-paperclip ws-icon" aria-hidden="true" /><h1 data-testid="files-title">{{ t.fileLibrary.title }}</h1></div>
    <p class="desk-muted ws-description">{{ t.fileLibrary.intro }}</p>

    <div class="desk-toolbar files-toolbar">
      <IconField>
        <InputIcon class="pi pi-search" />
        <InputText v-model="search" type="search" size="small" :placeholder="t.fileLibrary.search" :aria-label="t.fileLibrary.search" data-testid="files-search" />
      </IconField>
      <Select
        v-model="doctype"
        :options="typeOptions"
        option-label="label"
        option-value="value"
        :placeholder="t.fileLibrary.allTypes"
        :aria-label="t.fileLibrary.type"
        show-clear
        size="small"
        data-testid="files-doctype"
      />
      <Select v-model="sort" :options="sortOptions" option-label="label" option-value="value" :aria-label="t.fileLibrary.sort" size="small" data-testid="files-sort" />
      <span class="desk-spacer" />
      <span v-if="page" class="desk-muted" data-testid="files-count">{{ t.fileLibrary.count(page.total) }}</span>
    </div>

    <Message v-if="problem" severity="error" class="banner-error" closable data-testid="files-problem" @close="problem = null">
      <strong>{{ problem.title }}</strong>
      <ul class="files-problem">
        <li v-for="line in problem.lines" :key="line">{{ line }}</li>
      </ul>
    </Message>
    <Message v-if="unavailable.length" severity="warn" class="banner-error" data-testid="files-unavailable">{{ t.fileLibrary.unavailable(unavailable.join(', ')) }}</Message>

    <p v-if="failure" class="desk-muted" role="alert" data-testid="files-failed">{{ t.fileLibrary.failed }} {{ failure }}</p>
    <div v-else-if="page === null" class="desk-muted">{{ t.loading }}</div>
    <div v-else-if="!items.length" class="desk-card" style="padding: 2rem; text-align: center" data-testid="files-empty">
      <p class="desk-muted" style="margin: 0">{{ filtered ? t.fileLibrary.emptyFiltered : t.fileLibrary.empty }}</p>
    </div>
    <template v-else>
      <div class="table-wrap desk-card">
        <table class="grid files-table" :aria-label="t.fileLibrary.title">
          <thead>
            <tr>
              <th scope="col">{{ t.fileLibrary.file }}</th>
              <th scope="col">{{ t.fileLibrary.document }}</th>
              <th scope="col">{{ t.fileLibrary.field }}</th>
              <th scope="col" class="num">{{ t.fileLibrary.size }}</th>
              <th scope="col">{{ t.fileLibrary.owner }}</th>
              <th scope="col">{{ t.fileLibrary.date }}</th>
              <th scope="col" class="act"><span class="visually-hidden">{{ t.fileLibrary.actions }}</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in items" :key="citationKey(f)" data-testid="file-row">
              <td :data-label="t.fileLibrary.file">
                <span class="file-cell">
                  <FileThumb v-if="isImage(f)" :id="f.file_id" :size="28" :alt="f.filename" />
                  <span v-else class="thumb thumb-empty" style="width: 28px; height: 28px" aria-hidden="true"><i class="pi pi-file" /></span>
                  <a
                    class="desk-link file-name"
                    :href="files.url(f.file_id) ?? '#'"
                    :download="f.filename"
                    rel="noopener noreferrer"
                    :title="t.fileLibrary.download(f.filename)"
                    data-testid="file-download"
                    @click="download($event, f)"
                    >{{ f.filename }}</a
                  >
                </span>
              </td>
              <td :data-label="t.fileLibrary.document">
                <RouterLink class="desk-link" :to="{ name: 'form', params: { doctype: f.doctype, name: f.docname } }" :title="t.fileLibrary.openDoc" data-testid="file-doc">{{ docLabel(f) }}</RouterLink>
                <span class="desk-muted file-meta" data-testid="file-doctype"
                  >{{ f.doctype_label }}<template v-if="f.docstatus === 1"> · {{ t.fileLibrary.submitted }}</template
                  ><template v-else-if="f.docstatus === 2"> · {{ t.fileLibrary.cancelled }}</template></span
                >
              </td>
              <td :data-label="t.fileLibrary.field" data-testid="file-field">{{ fieldLabel(f) }}</td>
              <td :data-label="t.fileLibrary.size" class="num">{{ formatBytes(f.size) }}</td>
              <td :data-label="t.fileLibrary.owner">{{ users.label(f.owner) }}</td>
              <td :data-label="t.fileLibrary.date"><span :title="formatTimestamp(f.creation)">{{ relativeTime(f.creation) }}</span></td>
              <td class="act">
                <Button
                  v-if="f.can_detach"
                  icon="pi pi-link"
                  :label="t.fileLibrary.detach"
                  text
                  size="small"
                  severity="danger"
                  :aria-label="`${t.fileLibrary.detach} ${f.filename}`"
                  :loading="busy === citationKey(f)"
                  data-testid="file-detach"
                  @click="ask(f)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <Paginator v-if="page.total > rows || first > 0" :rows="rows" :first="first" :total-records="page.total" :rows-per-page-options="[20, 50, 100]" data-testid="files-paginator" @page="onPage" />
    </template>
  </div>
</template>

<style scoped>
.files-toolbar {
  flex-wrap: wrap;
  margin-bottom: 1rem;
}

.file-cell {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.file-name {
  overflow-wrap: anywhere;
}

.file-meta {
  display: block;
  font-size: 0.8rem;
}

.files-problem {
  margin: 0.25rem 0 0;
  padding-left: 1.25rem;
}

.files-table td.act {
  width: auto;
  white-space: nowrap;
}

/* Sur écran étroit une ligne se lit en fiche (desk.css) : « Détacher », un bouton avec son libellé, se range sous les cellules au lieu de
   recouvrir le nom du fichier en haut à droite (la place d'une icône seule dans une table de lignes). */
@media (max-width: 900px) {
  .files-table td.act {
    position: static;
    display: block;
  }
}
</style>
