/** « sales » -> « Sales », « stock_control » -> « Stock control » : le nom d'un module est un identifiant, pas encore un libellé. */
export function moduleLabel(module: string | null | undefined): string {
  if (!module) return 'Autres'
  const spaced = module.replace(/_/g, ' ').trim()
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : 'Autres'
}
