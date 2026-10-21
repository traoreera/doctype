/**
 * Vue, tel que le Desk l'utilise, sous un nom FIXE (`shared/vue.js` du build) : l'import map d'`index.html` y envoie le `import … from 'vue'`
 * d'une extension (D28). Une seule copie de Vue dans la page : sinon la réactivité et l'injection ne traverseraient pas la frontière.
 */
export * from 'vue'
