import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ViewSwitcher from '@/components/ViewSwitcher.vue'
import type { ViewName } from '@/lib/types'

const mk = (modelValue: ViewName, views: ViewName[] = ['list', 'report', 'dashboard', 'kanban']) => mount(ViewSwitcher, { props: { views, modelValue } })

describe('choix de la vue', () => {
  it('un bouton par vue, dans l’ordre, avec son icône et son nom ; la vue courante est cochée', () => {
    const w = mk('report')
    expect(w.findAll('[role="radio"]').map((b) => b.text())).toEqual(['Liste', 'Rapport', 'Tableau de bord', 'Kanban'])
    expect(w.findAll('[role="radio"]').map((b) => b.attributes('aria-checked'))).toEqual(['false', 'true', 'false', 'false'])
    expect(w.find('[data-testid="view-kanban"] i').classes()).toContain('pi-objects-column')
    expect(w.find('[role="radiogroup"]').attributes('aria-label')).toBe('Vue')
  })

  it('un clic choisit la vue', async () => {
    const w = mk('list')
    await w.find('[data-testid="view-dashboard"]').trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([['dashboard']])
  })

  it('seule la vue courante est dans l’ordre de tabulation, les flèches font le tour', async () => {
    const w = mk('list')
    expect(w.findAll('[role="radio"]').map((b) => b.attributes('tabindex'))).toEqual(['0', '-1', '-1', '-1'])
    await w.find('[data-testid="view-list"]').trigger('keydown', { key: 'ArrowRight' })
    await w.find('[data-testid="view-list"]').trigger('keydown', { key: 'ArrowLeft' }) // depuis la première : retour à la dernière
    expect(w.emitted('update:modelValue')).toEqual([['report'], ['kanban']])
  })
})
