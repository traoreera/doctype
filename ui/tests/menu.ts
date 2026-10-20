import { flushPromises, type VueWrapper } from '@vue/test-utils'

/**
 * Le menu « ⋯ » d'une page est une fenêtre volante PrimeVue : ses entrées sont envoyées dans `document.body`, hors du composant, et
 * celles des tests précédents y restent. On ouvre donc le menu puis on ne regarde que le DERNIER ouvert.
 */
export async function openMore(w: VueWrapper, trigger = 'more-actions'): Promise<void> {
  await w.find(`[data-testid="${trigger}"]`).trigger('click')
  await flushPromises()
}

/** Le dernier menu volant ouvert, ou null. */
export function lastMenu(): HTMLElement | null {
  const menus = document.body.querySelectorAll<HTMLElement>('.p-menu')
  return menus.length ? menus[menus.length - 1]! : null
}

/** L'entrée `data-testid` du dernier menu ouvert. */
export function menuItem(id: string): HTMLElement | null {
  return lastMenu()?.querySelector<HTMLElement>(`[data-testid="${id}"]`) ?? null
}
