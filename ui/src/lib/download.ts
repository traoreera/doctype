/** Faire suivre au navigateur un lien de téléchargement (un lien signé qu'on vient de redemander), comme un clic sur `<a download>`. */
export function downloadUrl(url: string, filename: string): void {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener noreferrer'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/** Faire télécharger un texte au navigateur (un export CSV) : un fichier en mémoire, jamais envoyé nulle part. */
export function downloadText(filename: string, text: string, mime = 'text/csv;charset=utf-8'): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
