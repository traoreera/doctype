<script setup lang="ts">
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Menu from 'primevue/menu'
import Textarea from 'primevue/textarea'
import { computed, nextTick, ref } from 'vue'
import { t } from '@/i18n'
import type { Variable } from '@/lib/automation'

/**
 * Un champ de texte qui accepte des gabarits xflow (`{{ trigger.name }}`, `{{ steps.lire.result.doc.data.jours }}`), avec un bouton
 * qui les propose : ce que le déclencheur porte, ce que les autres étapes ont rendu. La variable s'insère là où est le curseur.
 */
const props = defineProps<{ id: string; modelValue: string; variables: Variable[]; multiline?: boolean; placeholder?: string; disabled?: boolean; testid?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

const menu = ref<InstanceType<typeof Menu> | null>(null)
const field = ref<{ $el: HTMLInputElement | HTMLTextAreaElement } | null>(null)
let caret: [number, number] | null = null

const items = computed(() => {
  const groups = new Map<string, Variable[]>()
  for (const v of props.variables) groups.set(v.group, [...(groups.get(v.group) ?? []), v])
  return [...groups].map(([group, vars]) => ({
    label: group === 'trigger' ? t.automations.triggerNode : group === 'loop' ? 'loop_item' : group,
    items: vars.map((v) => ({ label: v.label, title: v.token, command: () => insert(v.token) })),
  }))
})

function element(): HTMLInputElement | HTMLTextAreaElement | null {
  const el = field.value?.$el ?? null
  return el && 'selectionStart' in el ? el : null
}

function open(e: Event): void {
  const el = element()
  caret = el ? [el.selectionStart ?? props.modelValue.length, el.selectionEnd ?? props.modelValue.length] : null
  menu.value?.toggle(e)
}

async function insert(token: string): Promise<void> {
  const [from, to] = caret ?? [props.modelValue.length, props.modelValue.length]
  emit('update:modelValue', props.modelValue.slice(0, from) + token + props.modelValue.slice(to))
  await nextTick()
  const el = element()
  if (el) {
    el.focus()
    el.setSelectionRange(from + token.length, from + token.length)
  }
}
</script>

<template>
  <div class="af-var-input">
    <Textarea
      v-if="multiline"
      :id="id"
      ref="field"
      :model-value="modelValue"
      rows="3"
      auto-resize
      :placeholder="placeholder"
      :disabled="disabled"
      :data-testid="testid"
      @update:model-value="emit('update:modelValue', $event ?? '')"
    />
    <InputText
      v-else
      :id="id"
      ref="field"
      :model-value="modelValue"
      size="small"
      :placeholder="placeholder"
      :disabled="disabled"
      :data-testid="testid"
      @update:model-value="emit('update:modelValue', $event ?? '')"
    />
    <Button
      icon="pi pi-code"
      text
      rounded
      severity="secondary"
      size="small"
      :disabled="disabled || !variables.length"
      :aria-label="t.automations.insertVariable"
      :title="t.automations.insertVariable"
      aria-haspopup="true"
      :data-testid="testid ? `${testid}-vars` : undefined"
      @click="open"
    />
    <Menu ref="menu" :model="items" popup class="af-var-menu" />
  </div>
</template>
