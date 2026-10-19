<script setup lang="ts">
import Button from 'primevue/button'
</script>

<template>
  <div class="desk-page">
    <div class="desk-card" style="padding: 2rem; text-align: center">
      <h1>Page introuvable</h1>
      <p class="desk-muted">Cette adresse ne correspond à rien dans le Desk.</p>
      <RouterLink :to="{ name: 'home' }"><Button label="Retour à l’accueil" /></RouterLink>
    </div>
  </div>
</template>
