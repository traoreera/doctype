<script setup lang="ts">
import FieldInput from '@/components/FieldInput.vue'
import LinkField from '@/components/LinkField.vue'
import { t } from '@/i18n'
import { formatCell } from '@/lib/format'
import type { Row } from '@/lib/form'
import type { FieldMeta } from '@/lib/types'

/**
 * UNE cellule d'une table enfant. Extraite de `TableField` parce que la GRILLE et la FICHE DE LIGNE la rendent toutes les
 * deux : les règles d'une cellule (invisible, document figé, valeur écrite par le serveur, saisie) n'existent qu'ici.
 *
 * Ce composant ne décide RIEN : son état, son unité, sa cible dynamique et ses filtres de lien sont calculés par la table,
 * qui seule connaît la ligne, le parent et les liens déjà résolus.
 */
const props = defineProps<{
  column: FieldMeta
  row: Row
  /** La cellule a-t-elle un sens pour CETTE ligne (`depends_on`) ? */
  visible: boolean
  readOnly: boolean
  inputId: string
  error?: string | undefined
  unit?: string | null
  dynamicTarget?: string | null
  linkFilters?: ReturnType<typeof import('@/lib/form').resolveLinkFilters>
  /** Pour les identifiants de test de la grille (`fetched-0-amount`) : l'index de la ligne. */
  index: number
  /** L'identifiant de test de l'erreur, fourni par la table : il porte la clé de la cellule (`error-items.0.qty`). */
  errorTestid: string
}>()
const emit = defineEmits<{ update: [value: unknown] }>()

const value = () => props.row[props.column.fieldname]
</script>

<template>
  <!-- Une colonne qui existe pour d'autres lignes mais pas pour celle-ci : une cellule vide, pas un champ à remplir. -->
  <span v-if="!visible" class="desk-muted" :data-testid="`na-${index}-${column.fieldname}`">—</span>
  <template v-else-if="readOnly">
    <!-- Un document figé montre le TITRE de l'article, pas son identifiant. -->
    <LinkField v-if="column.fieldtype === 'Link' && typeof column.options === 'string' && value()" :model-value="value()" :target="column.options" readonly />
    <LinkField v-else-if="column.fieldtype === 'DynamicLink' && dynamicTarget && value()" :model-value="value()" :target="dynamicTarget" readonly />
    <template v-else>{{ formatCell(column, value()) || t.form.noValue }}</template>
    <span v-if="unit && value()" class="unit">{{ unit }}</span>
  </template>
  <!-- Une cellule remplie par le SERVEUR — copiée (`fetch_from`) ou calculée (`compute`) — se lit,
       elle ne se saisit pas. Un montant de ligne (`qty * rate`) est de ceux-là. -->
  <span v-else-if="column.fetch_from || column.compute" class="value" :data-testid="`fetched-${index}-${column.fieldname}`">{{ formatCell(column, value()) || t.form.noValue }}</span>
  <template v-else>
    <div class="cell-input">
      <FieldInput
        :field="column"
        :model-value="value()"
        :input-id="inputId"
        :invalid="!!error"
        :dynamic-target="dynamicTarget"
        :link-filters="linkFilters"
        @update:model-value="emit('update', $event)"
      />
      <span v-if="unit" class="unit" :data-testid="`unit-${index}-${column.fieldname}`">{{ unit }}</span>
    </div>
    <span v-if="error" class="error" role="alert" :data-testid="errorTestid">{{ error }}</span>
  </template>
</template>
