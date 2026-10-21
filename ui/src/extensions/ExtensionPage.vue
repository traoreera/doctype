<script setup lang="ts">
import type { Component } from 'vue'
import { useRoute } from 'vue-router'
import ExtensionSlot from './ExtensionSlot.vue'

/** Une page qu'une extension ajoute (`#/x/<plugin>/…`) : le titre `h1` du Desk (toute page en a un), puis le composant, qui reçoit les paramètres de l'adresse. */
defineProps<{ extension: string; title: string | null; component: Component }>()
const route = useRoute()
</script>

<template>
  <div class="desk-page" :data-extension="extension">
    <div v-if="title" class="desk-page-head"><h1>{{ title }}</h1></div>
    <ExtensionSlot :extension="extension" :component="component" :props="{ ...route.params }" />
  </div>
</template>
