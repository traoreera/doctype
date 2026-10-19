<script setup lang="ts">
import ConfirmDialog from 'primevue/confirmdialog'
import Toast from 'primevue/toast'
import { useRoute } from 'vue-router'
import AppShell from '@/components/AppShell.vue'

const route = useRoute()
</script>

<template>
  <Toast position="bottom-right" />
  <ConfirmDialog />
  <RouterView v-if="route.meta.public" />
  <AppShell v-else>
    <RouterView />
  </AppShell>
</template>
