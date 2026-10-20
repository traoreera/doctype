/**
 * Les icônes PrimeIcons que les plugins nomment dans leurs fichiers YAML (`icon: flask`). PrimeIcons n'en a qu'un nombre limité : un nom qui n'existe pas
 * laisserait un trou à la place de l'icône (un espace qui se lit comme une faute d'alignement). On vérifie dans le navigateur que la classe dessine bien
 * quelque chose, et sinon on met l'icône de repli.
 */
const verdicts = new Map<string, boolean>()

function exists(name: string): boolean {
  if (typeof document === 'undefined' || !document.body) return true // pas de navigateur : on ne sait pas, on fait confiance
  const known = verdicts.get(name)
  if (known !== undefined) return known
  const probe = document.createElement('i')
  probe.className = `pi pi-${name}`
  probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none'
  document.body.appendChild(probe)
  const content = getComputedStyle(probe, '::before').content
  probe.remove()
  const ok = content !== 'none' && content !== 'normal' // une classe inconnue n'a pas de contenu `::before`
  verdicts.set(name, ok)
  return ok
}

/** Les classes d'une icône PrimeIcons : `pi pi-<nom>` si elle existe, sinon l'icône de repli. */
export function piIcon(name: string | null | undefined, fallback = 'file'): string {
  const wanted = name && /^[a-z0-9-]+$/.test(name) ? name : fallback
  return `pi pi-${exists(wanted) ? wanted : fallback}`
}
