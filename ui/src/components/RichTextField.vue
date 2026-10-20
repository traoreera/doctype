<script setup lang="ts">
import { EditorContent, useEditor } from '@tiptap/vue-3'
import StarterKit from '@tiptap/starter-kit'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import { onBeforeUnmount, ref, watch } from 'vue'
import { t } from '@/i18n'
import { isRichEmpty, sanitizeRich } from '@/lib/richtext'

/**
 * L'éditeur du champ `TextEditor` (TipTap). Il ne produit que le sous-ensemble de HTML que le serveur accepte (gras, italique, souligné, barré, titres, listes, citation,
 * code, lien http(s) ou mailto) ; ce qu'il rend repasse par `sanitizeRich` avant l'envoi, donc jamais un attribut ni une balise hors liste.
 * Chargé À LA DEMANDE (composant asynchrone) : un formulaire sans texte enrichi ne paie pas l'éditeur.
 */
const props = defineProps<{ modelValue: unknown; inputId: string; invalid?: boolean; readonly?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | null] }>()

const asHtml = (v: unknown): string => sanitizeRich(typeof v === 'string' ? v : '')
const linkOpen = ref(false)
const linkUrl = ref('')

const editor = useEditor({
  content: asHtml(props.modelValue),
  editable: !props.readonly,
  extensions: [StarterKit.configure({ heading: { levels: [1, 2, 3, 4] }, link: { openOnClick: false, autolink: false, HTMLAttributes: {} } })],
  editorProps: { attributes: { id: props.inputId, class: 'rich-content', role: 'textbox', 'aria-multiline': 'true' } },
  onUpdate: ({ editor: e }) => {
    const html = sanitizeRich(e.getHTML())
    emit('update:modelValue', isRichEmpty(html) ? null : html)
  },
})

// Une valeur changée de l'extérieur (rechargement, conflit) : on recharge l'éditeur sans rejouer `onUpdate`.
watch(
  () => props.modelValue,
  (v) => {
    const e = editor.value
    if (!e) return
    const now = sanitizeRich(e.getHTML())
    if (asHtml(v) !== now && !(v === null && isRichEmpty(now))) e.commands.setContent(asHtml(v), { emitUpdate: false })
  },
)
watch(() => props.readonly, (r) => editor.value?.setEditable(!r))
onBeforeUnmount(() => editor.value?.destroy())

const can = (name: string, attrs?: Record<string, unknown>) => !!editor.value?.isActive(name, attrs)
const run = (fn: (chain: ReturnType<NonNullable<typeof editor.value>['chain']>) => { run: () => boolean }) => {
  const e = editor.value
  if (e) fn(e.chain().focus()).run()
}
function openLink(): void {
  linkUrl.value = (editor.value?.getAttributes('link').href as string | undefined) ?? ''
  linkOpen.value = true
}
function applyLink(): void {
  const url = linkUrl.value.trim()
  if (!url) run((c) => c.unsetLink())
  else if (/^(https?:\/\/|mailto:)/i.test(url)) run((c) => c.setLink({ href: url }))
  linkOpen.value = false
}
</script>

<template>
  <div class="rich-field" :class="{ invalid }" data-testid="rich-field">
    <!-- mousedown.prevent : cliquer un bouton ne retire pas le focus de l'éditeur (la mise en forme s'applique à la sélection, la frappe continue) -->
    <div v-if="!readonly && editor" class="rich-toolbar" role="toolbar" :aria-label="t.form.richToolbar" @mousedown.prevent>
      <Button text size="small" :severity="can('bold') ? 'primary' : 'secondary'" :aria-label="t.form.richBold" :title="t.form.richBold" data-testid="rich-bold" @click="run((c) => c.toggleBold())"><b>B</b></Button>
      <Button text size="small" :severity="can('italic') ? 'primary' : 'secondary'" :aria-label="t.form.richItalic" :title="t.form.richItalic" @click="run((c) => c.toggleItalic())"><i>I</i></Button>
      <Button text size="small" :severity="can('strike') ? 'primary' : 'secondary'" :aria-label="t.form.richStrike" :title="t.form.richStrike" @click="run((c) => c.toggleStrike())"><s>S</s></Button>
      <Button label="H" text size="small" :severity="can('heading', { level: 2 }) ? 'primary' : 'secondary'" :aria-label="t.form.richHeading" data-testid="rich-heading" @click="run((c) => c.toggleHeading({ level: 2 }))" />
      <Button icon="pi pi-list" text size="small" :severity="can('bulletList') ? 'primary' : 'secondary'" :aria-label="t.form.richBullets" data-testid="rich-bullets" @click="run((c) => c.toggleBulletList())" />
      <Button label="1." text size="small" :severity="can('orderedList') ? 'primary' : 'secondary'" :aria-label="t.form.richNumbers" @click="run((c) => c.toggleOrderedList())" />
      <Button icon="pi pi-comment" text size="small" :severity="can('blockquote') ? 'primary' : 'secondary'" :aria-label="t.form.richQuote" @click="run((c) => c.toggleBlockquote())" />
      <Button icon="pi pi-code" text size="small" :severity="can('code') ? 'primary' : 'secondary'" :aria-label="t.form.richCode" @click="run((c) => c.toggleCode())" />
      <Button icon="pi pi-link" text size="small" :severity="can('link') ? 'primary' : 'secondary'" :aria-label="t.form.richLink" data-testid="rich-link" @click="openLink" />
    </div>
    <div v-if="linkOpen" class="rich-link">
      <InputText v-model="linkUrl" size="small" :placeholder="t.form.richLinkPrompt" data-testid="rich-link-url" @keydown.enter.prevent="applyLink" />
      <Button :label="t.form.richLinkApply" size="small" data-testid="rich-link-apply" @click="applyLink" />
      <Button :label="t.admin.cancel" text size="small" severity="secondary" @click="linkOpen = false" />
    </div>
    <EditorContent :editor="editor" />
  </div>
</template>
