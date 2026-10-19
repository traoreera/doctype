import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import BlockGrid from '@/components/BlockGrid.vue'
import ChartSvg from '@/components/ChartSvg.vue'
import ChartCard from '@/components/blocks/ChartCard.vue'
import LinksCard from '@/components/blocks/LinksCard.vue'
import NumberCard from '@/components/blocks/NumberCard.vue'
import QuickList from '@/components/blocks/QuickList.vue'
import { ApiError } from '@/lib/api'
import { SERVICES } from '@/lib/services'
import type { AggregateResult, ChartBlock, DocRecord, NumberCardBlock, QuickListBlock, WorkspaceBlock } from '@/lib/types'

const router = () =>
  createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/app/:doctype', name: 'list', component: { render: () => null } },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: { render: () => null } },
    ],
  })

type Api = Record<string, unknown>
function setup<T>(component: T, props: Record<string, unknown>, api: Api = {}) {
  const r = router()
  const w = mount(component as never, {
    props: props as never,
    global: {
      plugins: [PrimeVue, createPinia(), r],
      provide: { [SERVICES as symbol]: { transport: {}, config: { baseUrl: '', loginUrl: null }, api: { meta: async () => ({ title_field: 'item_name' }), ...api } } },
    },
  }) as unknown as VueWrapper
  return { w, router: r }
}

const result = (over: Partial<AggregateResult> = {}): AggregateResult => ({
  group: null,
  metrics: [{ function: 'count', field: null, label: null, decimals: null }],
  rows: [{ key: null, values: [42] }],
  truncated: false,
  ...over,
})
const grouped = (keys: (string | null)[], values: (number | string)[], over: Partial<AggregateResult> = {}): AggregateResult =>
  result({ group: { field: 'purpose', bucket: null, fieldtype: 'Select' }, rows: keys.map((key, i) => ({ key, values: [values[i]!] })), ...over })

const card = (over: Partial<NumberCardBlock> = {}): NumberCardBlock => ({ type: 'number_card', label: 'Brouillons', doctype: 'Stock Entry', function: 'count', ...over })
const chart = (over: Partial<ChartBlock> = {}): ChartBlock => ({ type: 'chart', label: 'Par nature', doctype: 'Stock Entry', kind: 'donut', group_by: 'purpose', function: 'count', limit: 10, ...over })

// ── Le dessin ───────────────────────────────────────────────────────────────────────────────

/** Les lignes de la légende, cellule par cellule : [libellé, valeur, part]. */
const legendRows = (w: { findAll: (s: string) => { findAll: (s: string) => { text: () => string }[] }[] }) =>
  w.findAll('[data-testid="chart-legend-item"]').map((li) => li.findAll('span:not(.chart-swatch)').map((x) => x.text()))

const draw = (props: Record<string, unknown>) =>
  mount(ChartSvg, { props: { title: 'Mouvements', labels: ['A', 'B', 'C'], values: [10, 20, 5], display: ['10', '20', '5'], ...props } as never })

/** Le bout de la donnée d'une barre (son ordonnée) et sa ligne de base, lus dans son tracé : `M x base V … Q x tip …`. */
const geometry = (w: ReturnType<typeof draw>, i: number) => {
  const d = w.find(`[data-testid="chart-bar-${i}"] path`).attributes('d')!
  const base = Number(/^M [\d.-]+ ([\d.-]+)/.exec(d)![1])
  const tip = Number(/Q [\d.-]+ ([\d.-]+)/.exec(d)![1])
  const x = Number(/^M ([\d.-]+)/.exec(d)![1])
  const right = Number(/H ([\d.-]+)/.exec(d)![1])
  return { base, tip, height: Math.abs(base - tip), x, right }
}

describe('graphique en barres', () => {
  it('une barre par valeur, la plus haute pour la plus grande', () => {
    const w = draw({ kind: 'bar' })
    const heights = [0, 1, 2].map((i) => geometry(w, i).height)
    expect(heights[1]).toBeGreaterThan(heights[0]!)
    expect(heights[0]).toBeGreaterThan(heights[2]!)
    expect(heights[1]! / heights[0]!).toBeCloseTo(2, 1) // 20 fait deux fois 10
  })

  it('des barres fines : 24 px au plus, même quand la bande est large', () => {
    const w = draw({ kind: 'bar' })
    const g = geometry(w, 0)
    expect(g.right - g.x).toBeLessThanOrEqual(24 + 8) // la largeur du tracé = barre moins deux rayons d'arrondi de chaque côté
    const wide = draw({ kind: 'bar', labels: ['A'], values: [3], display: ['3'] })
    const one = geometry(wide, 0)
    expect(one.right - one.x + 2 * 4).toBeLessThanOrEqual(24 + 8)
  })

  it('les barres sont carrées sur la ligne de base, arrondies au bout de la donnée', () => {
    const d = draw({ kind: 'bar' }).find('[data-testid="chart-bar-1"] path').attributes('d')!
    expect(d).toMatch(/^M [\d.]+ [\d.]+ V [\d.]+ Q [\d.]+ [\d.]+ [\d.]+ [\d.]+ H [\d.]+ Q [\d.]+ [\d.]+ [\d.]+ [\d.]+ V [\d.]+ Z$/)
    expect(d.match(/Q /g)).toHaveLength(2) // deux coins arrondis seulement, ceux du haut
  })

  it('les graduations, les étiquettes, un groupe nommé et chaque barre lisible par un lecteur d’écran', () => {
    const w = draw({ kind: 'bar' })
    expect(w.find('svg').attributes('role')).toBe('group')
    expect(w.find('svg').attributes('aria-label')).toBe('Mouvements')
    const text = w.findAll('text').map((x) => x.text())
    expect(text).toEqual(expect.arrayContaining(['A', 'B', 'C', '0', '10', '20']))
    expect(w.find('[data-testid="chart-bar-1"]').attributes('aria-label')).toBe('B : 20')
    expect(w.find('[data-testid="chart-bar-1"]').attributes('tabindex')).toBe('0') // au clavier aussi
  })

  it('l’infobulle donne la valeur exacte d’abord, le libellé ensuite ; quitter ou perdre le focus l’efface', async () => {
    const w = draw({ kind: 'bar', display: ['10', '1 234,50', '5'] })
    expect(w.find('[data-testid="chart-tip"]').exists()).toBe(false)
    await w.find('[data-testid="chart-bar-1"]').trigger('mouseenter')
    const tip = w.find('[data-testid="chart-tip"]')
    expect(tip.find('.chart-tip-value').text()).toBe('1 234,50')
    expect(tip.find('.chart-tip-label').text()).toBe('B')
    expect(tip.text().indexOf('1 234,50')).toBeLessThan(tip.text().indexOf('B')) // les valeurs d'abord
    await w.find('svg').trigger('mouseleave')
    expect(w.find('[data-testid="chart-tip"]').exists()).toBe(false)
    await w.find('[data-testid="chart-bar-2"]').trigger('focus') // même détail au clavier qu'à la souris
    expect(w.find('.chart-tip-value').text()).toBe('5')
    await w.find('[data-testid="chart-bar-2"]').trigger('blur')
    expect(w.find('[data-testid="chart-tip"]').exists()).toBe(false)
  })

  it('la barre survolée se détache : les autres passent en retrait', async () => {
    const w = draw({ kind: 'bar' })
    await w.find('[data-testid="chart-bar-1"]').trigger('mouseenter')
    expect(w.find('[data-testid="chart-bar-1"]').classes()).not.toContain('dim')
    expect(w.find('[data-testid="chart-bar-0"]').classes()).toContain('dim')
  })

  it('les valeurs négatives descendent sous la ligne de zéro', () => {
    const w = draw({ kind: 'bar', values: [10, -10, 0], display: ['10', '-10', '0'] })
    const up = geometry(w, 0)
    const down = geometry(w, 1)
    expect(up.tip).toBeLessThan(up.base)
    expect(down.tip).toBeGreaterThan(down.base) // vers le bas
    expect(down.base).toBeCloseTo(up.base) // la même ligne de base
  })

  it('une valeur écrite au bout de chaque barre quand elles sont peu nombreuses, sinon seulement la plus grande et la dernière', () => {
    const few = draw({ kind: 'bar' })
    expect(few.findAll('[data-testid="chart-value"]').map((x) => x.text())).toEqual(['10', '20', '5'])
    const labels = Array.from({ length: 8 }, (_, i) => `M${i}`)
    const values = [3, 9, 4, 2, 5, 1, 6, 2]
    const many = draw({ kind: 'bar', labels, values, display: values.map(String) })
    expect(many.findAll('[data-testid="chart-value"]').map((x) => x.text())).toEqual(['9', '2']) // le maximum, puis la dernière
  })

  it('beaucoup de barres : toutes dessinées, mais une étiquette sur n pour qu’aucune ne chevauche l’autre', () => {
    const labels = Array.from({ length: 40 }, (_, i) => `M${i}`)
    const w = draw({ kind: 'bar', labels, values: labels.map((_, i) => i + 1), display: labels })
    expect(w.findAll('[data-testid^="chart-bar-"]')).toHaveLength(40)
    const shown = w.findAll('.chart-axis text')
    expect(shown.length).toBeLessThan(40)
    expect(shown[0]!.text()).toBe('M0') // la première est toujours là
    expect(w.findAll('[data-testid="chart-value"]')).toHaveLength(0) // des barres étroites ne portent pas de chiffre : l'infobulle suffit
  })

  it('un compteur n’a pas de graduation décimale', () => {
    const w = draw({ kind: 'bar', values: [1, 0, 1], display: ['1', '0', '1'], integer: true })
    const ticks = w.findAll('.chart-grid .chart-tick').map((x) => x.text())
    expect(ticks.every((x) => /^\d+$/.test(x))).toBe(true)
  })

  it('une seule série : pas de légende (le titre dit ce qu’on lit)', () => {
    expect(draw({ kind: 'bar' }).find('[data-testid="chart-legend"]').exists()).toBe(false)
  })

  it('rien à montrer : le dit, sans dessiner un graphique vide', () => {
    for (const values of [[], [0, 0]]) {
      const w = draw({ kind: 'bar', labels: values.map(String), values, display: values.map(String) })
      expect(w.find('[data-testid="chart-empty"]').exists()).toBe(true)
      expect(w.find('svg').exists()).toBe(false)
    }
  })
})

describe('courbe', () => {
  it('un tracé de 2 px, un repère par valeur, une aire légère sous la courbe', () => {
    const w = draw({ kind: 'line' })
    expect(w.find('[data-testid="chart-line"]').attributes('d')).toMatch(/^M [\d.]+ [\d.]+ L /)
    expect(w.findAll('[data-testid^="chart-point-"]')).toHaveLength(3)
    expect(w.findAll('[data-testid^="chart-point-"] circle')).toHaveLength(3)
    expect(w.find('[data-testid="chart-point-0"] circle').attributes('r')).toBe('4') // 8 px de diamètre au moins
    expect(w.find('.chart-area').exists()).toBe(true)
  })

  it('le plus haut point est le plus haut sur l’écran', () => {
    const w = draw({ kind: 'line' })
    const cy = (i: number) => Number(w.find(`[data-testid="chart-point-${i}"] circle`).attributes('cy'))
    expect(cy(1)).toBeLessThan(cy(0))
    expect(cy(0)).toBeLessThan(cy(2))
  })

  it('un repère vertical suit le point survolé et l’infobulle donne sa valeur', async () => {
    const w = draw({ kind: 'line' })
    expect(w.find('[data-testid="chart-crosshair"]').exists()).toBe(false)
    await w.find('[data-testid="chart-point-1"]').trigger('mouseenter')
    const x = w.find('[data-testid="chart-crosshair"]').attributes('x1')
    expect(x).toBe(w.find('[data-testid="chart-point-1"] circle').attributes('cx')) // le repère passe par le point
    expect(w.find('.chart-tip-value').text()).toBe('20')
    await w.find('svg').trigger('mouseleave')
    expect(w.find('[data-testid="chart-crosshair"]').exists()).toBe(false)
  })

  it('une longue série : pas un repère sur chaque point, seulement le dernier (écrit) et celui qu’on survole', async () => {
    const labels = Array.from({ length: 40 }, (_, i) => `J${i}`)
    const values = labels.map((_, i) => i + 1)
    const w = draw({ kind: 'line', labels, values, display: labels.map((_, i) => `${i + 1}`) })
    expect(w.findAll('[data-testid^="chart-point-"] circle')).toHaveLength(1)
    await w.find('[data-testid="chart-point-5"]').trigger('mouseenter')
    expect(w.findAll('[data-testid^="chart-point-"] circle')).toHaveLength(2)
  })
})

describe('camembert, anneau, répartition', () => {
  it('chaque valeur positive a son secteur, sa légende et son pourcentage', () => {
    const w = draw({ kind: 'pie', values: [1, 1, 2], display: ['1', '1', '2'], labels: ['A', 'B', 'C'] })
    expect(w.findAll('[data-testid^="chart-slice-"]')).toHaveLength(3)
    expect(legendRows(w)).toEqual([['A', '1', '25 %'], ['B', '1', '25 %'], ['C', '2', '50 %']])
  })

  it('un anneau affiche son total au centre', () => {
    const w = draw({ kind: 'donut', center: '35' })
    expect(w.find('.chart-center').text()).toBe('35')
    expect(draw({ kind: 'pie', center: '35' }).find('.chart-center').exists()).toBe(false)
  })

  it('une valeur nulle ou négative n’a pas de secteur, mais le rang des autres est gardé', () => {
    const w = draw({ kind: 'donut', values: [5, 0, 5], display: ['5', '0', '5'] })
    expect(w.find('[data-testid="chart-slice-1"]').exists()).toBe(false)
    expect(w.find('[data-testid="chart-slice-2"]').attributes('style')).toContain('--chart-3') // la couleur suit la valeur, pas sa position
  })

  it('un seul secteur est un cercle entier, jamais un tracé vide', () => {
    const w = draw({ kind: 'pie', values: [7], labels: ['A'], display: ['7'] })
    const d = w.find('[data-testid="chart-slice-0"]').attributes('d')!
    expect(d).not.toContain('NaN')
    expect(d.match(/A /g)!.length).toBe(2)
  })

  it('la répartition est une barre empilée dont la largeur suit la part', () => {
    const w = draw({ kind: 'percentage', values: [1, 3], labels: ['A', 'B'], display: ['1', '3'] })
    expect(w.find('[data-testid="chart-slice-0"]').attributes('style')).toContain('width: 25%')
    expect(w.find('[data-testid="chart-slice-1"]').attributes('style')).toContain('width: 75%')
    expect(w.findAll('[data-testid="chart-legend-item"]')).toHaveLength(2)
  })

  it('chaque secteur est séparé de ses voisins par un trait de la couleur du fond, et se lit au clavier', () => {
    const w = draw({ kind: 'pie' })
    expect(w.find('[data-testid="chart-slice-0"]').classes()).toContain('chart-slice') // le trait de 2 px est la règle CSS de cette classe
    expect(w.find('[data-testid="chart-slice-0"]').attributes('aria-label')).toBe('A : 10')
    expect(w.find('[data-testid="chart-slice-0"]').attributes('tabindex')).toBe('0')
  })

  it('l’infobulle d’un secteur donne la valeur puis le libellé', async () => {
    const w = draw({ kind: 'donut' })
    await w.find('[data-testid="chart-slice-1"]').trigger('mouseenter')
    expect(w.find('.chart-tip-value').text()).toBe('20')
    expect(w.find('.chart-tip-label').text()).toBe('B')
  })

  it('la part « Autres » est un gris neutre, jamais une teinte de la palette', () => {
    const w = draw({ kind: 'pie', other: true })
    expect(w.find('[data-testid="chart-slice-1"]').attributes('style')).toContain('--chart-2')
    expect(w.find('[data-testid="chart-slice-2"]').attributes('style')).toContain('--chart-other')
  })

  it('survoler une ligne de la légende met le reste en retrait', async () => {
    const w = draw({ kind: 'donut' })
    await w.findAll('[data-testid="chart-legend-item"]')[0]!.trigger('mouseenter')
    expect(w.find('[data-testid="chart-slice-0"]').classes()).not.toContain('dim')
    expect(w.find('[data-testid="chart-slice-1"]').classes()).toContain('dim')
  })

  it('les couleurs viennent du thème, jamais d’une valeur écrite', () => {
    const w = draw({ kind: 'donut' })
    for (const el of w.findAll('[data-testid^="chart-slice-"]')) expect(el.attributes('style')).toMatch(/var\(--chart-\d\)/)
    expect(w.html()).not.toMatch(/#[0-9a-fA-F]{3,6}\b|rgb\(/)
  })
})

// ── Carte de nombre ─────────────────────────────────────────────────────────────────────────

describe('carte de nombre', () => {
  it('demande le chiffre au serveur et l’affiche formaté', async () => {
    const aggregate = vi.fn(async () => result({ rows: [{ key: null, values: [1234] }] }))
    const { w } = setup(NumberCard, { block: card({ filters: { docstatus: 0 } }) }, { aggregate })
    expect(w.find('[data-testid="number-card-loading"]').exists()).toBe(true)
    await flushPromises()
    expect(aggregate).toHaveBeenCalledWith('Stock Entry', { metrics: ['count'], filters: { docstatus: 0 } })
    expect(w.find('[data-testid="number-card-value"]').text().replace(/\s/g, ' ')).toBe('1 234')
    expect(w.text()).toContain('Brouillons')
  })

  it('une somme de Decimal reste exacte, une moyenne a deux décimales au plus, aucune valeur est un tiret', async () => {
    const sum = result({ metrics: [{ function: 'sum', field: 'qty', label: null, decimals: 3 }], rows: [{ key: null, values: ['1234.500'] }] })
    const avg = result({ metrics: [{ function: 'avg', field: 'qty', label: null, decimals: null }], rows: [{ key: null, values: [5.33333] }] })
    const none = result({ metrics: [{ function: 'avg', field: 'qty', label: null, decimals: null }], rows: [{ key: null, values: [null] }] })
    const text = async (res: AggregateResult, fn: 'sum' | 'avg') => {
      const { w } = setup(NumberCard, { block: card({ function: fn, field: 'qty' }) }, { aggregate: async () => res })
      await flushPromises()
      return w.find('[data-testid="number-card-value"]').text().replace(/\s/g, ' ')
    }
    expect(await text(sum, 'sum')).toBe('1 234,500')
    expect(await text(avg, 'avg')).toBe('5,33')
    expect(await text(none, 'avg')).toBe('—')
  })

  it('un clic ouvre la liste de ce qu’on a compté, avec les mêmes filtres', async () => {
    const { w } = setup(NumberCard, { block: card({ filters: { docstatus: 0, purpose: 'Sortie' } }) }, { aggregate: async () => result() })
    await flushPromises()
    const href = w.find('a').attributes('href')!
    expect(href.startsWith('/app/Stock%20Entry?filters=')).toBe(true)
    expect(JSON.parse(decodeURIComponent(href.split('filters=')[1]!))).toEqual({ docstatus: 0, purpose: 'Sortie' })
    const bare = setup(NumberCard, { block: card() }, { aggregate: async () => result() })
    await flushPromises()
    expect(bare.w.find('a').attributes('href')).toBe('/app/Stock%20Entry')
  })

  it('une erreur du serveur se lit sur la carte au lieu de casser la page', async () => {
    const { w } = setup(NumberCard, { block: card() }, { aggregate: async () => { throw new ApiError(403, 'permission_denied', 'Accès refusé') } })
    await flushPromises()
    expect(w.find('[data-testid="number-card-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="number-card-value"]').exists()).toBe(false)
  })

  it('la teinte est une classe du thème', async () => {
    const { w } = setup(NumberCard, { block: card({ color: 'orange' }) }, { aggregate: async () => result() })
    expect(w.find('a').classes()).toContain('tone-orange')
    expect(setup(NumberCard, { block: card() }, { aggregate: async () => result() }).w.find('a').classes()).toContain('tone-blue')
  })

  it('une couleur libre #rrggbb donne deux variables (clair, sombre) ; une valeur qui n’en est pas une est ignorée', async () => {
    const free = setup(NumberCard, { block: card({ color: '#d6452a' }) }, { aggregate: async () => result() }).w.find('a')
    expect(free.classes()).toContain('tone-custom')
    expect(free.attributes('style')).toContain('--custom-light: #d6452a')
    expect(free.attributes('style')).toContain('--custom-dark: ')
    for (const bad of ['red;}', '#abc', 'url(x)', '#12345g']) {
      const a = setup(NumberCard, { block: card({ color: bad }) }, { aggregate: async () => result() }).w.find('a')
      expect(a.attributes('style') ?? '', bad).not.toContain('--custom')
      expect(a.classes()).not.toContain('tone-custom')
    }
  })
})

describe('teinte d’un graphique en barres ou en courbe', () => {
  const draw = (props: Record<string, unknown>) => mount(ChartSvg, { props: { title: 'T', labels: ['A', 'B'], values: [1, 2], display: ['1', '2'], ...props } as never }).find('.chart')

  it('une couleur libre ne change que ce graphique, pour les deux thèmes', () => {
    const c = draw({ kind: 'bar', color: '#2a78d6' })
    expect(c.attributes('data-custom')).toBeDefined()
    expect(c.attributes('style')).toContain('--custom-light: #2a78d6')
  })

  it('un nom de la palette redéfinit la première teinte', () => {
    expect(draw({ kind: 'line', color: 'orange' }).attributes('style')).toContain('--chart-1: var(--tone-orange)')
  })

  it('un camembert garde la palette, quoi qu’on lui donne ; une valeur invalide est ignorée', () => {
    expect(draw({ kind: 'donut', color: '#2a78d6' }).attributes('style') ?? '').not.toContain('--custom')
    expect(draw({ kind: 'bar', color: 'red;}' }).attributes('style') ?? '').not.toContain('red')
    expect(draw({ kind: 'bar' }).attributes('style') ?? '').toBe('')
  })
})

// ── Carte de graphique ──────────────────────────────────────────────────────────────────────

describe('carte de graphique', () => {
  it('demande le regroupement, lit les libellés et dessine', async () => {
    const aggregate = vi.fn(async () => grouped(['Réception', 'Sortie'], [7, 3]))
    const { w } = setup(ChartCard, { block: chart() }, { aggregate })
    expect(w.find('[data-testid="chart-loading"]').exists()).toBe(true)
    await flushPromises()
    expect(aggregate).toHaveBeenCalledWith('Stock Entry', { metrics: ['count'], group_by: 'purpose', limit: 10 })
    expect(legendRows(w)).toEqual([['Réception', '7', '70 %'], ['Sortie', '3', '30 %']])
    expect(w.find('.chart-center').text()).toBe('10')
  })

  it('le total d’un anneau de montants est exact', async () => {
    const res = grouped(['A', 'B', 'C'], ['0.10', '0.20', '0.30'], { metrics: [{ function: 'sum', field: 'amount', label: null, decimals: 2 }] })
    const { w } = setup(ChartCard, { block: chart({ function: 'sum', field: 'amount' }) }, { aggregate: async () => res })
    await flushPromises()
    expect(w.find('.chart-center').text()).toBe('0,60')
  })

  it('une moyenne n’a pas de total au centre', async () => {
    const res = grouped(['A', 'B'], [4, 6], { metrics: [{ function: 'avg', field: 'qty', label: null, decimals: null }] })
    const { w } = setup(ChartCard, { block: chart({ function: 'avg', field: 'qty' }) }, { aggregate: async () => res })
    await flushPromises()
    expect(w.find('.chart-center').exists()).toBe(false)
  })

  it('un regroupement par lien montre les TITRES des documents, pas leurs identifiants', async () => {
    const res = result({
      group: { field: 'item', bucket: null, fieldtype: 'Link', options: 'Item' },
      rows: [{ key: 'a1b2', values: [5] }, { key: 'c3d4', values: [2] }, { key: null, values: [1] }],
    })
    const list = vi.fn(async () => ({ items: [{ name: 'a1b2', data: { item_name: 'Vis M6' } }, { name: 'c3d4', data: { item_name: 'Écrou' } }] as unknown as DocRecord[], total: 2, limit: 2, offset: 0 }))
    const { w } = setup(ChartCard, { block: chart({ group_by: 'item', kind: 'bar' }) }, { aggregate: async () => res, list })
    await flushPromises()
    expect(w.text()).toContain('Vis M6')
    expect(w.text()).toContain('Écrou')
    expect(w.text()).not.toContain('a1b2')
    expect(list).toHaveBeenCalledWith('Item', { filters: { name: ['in', ['a1b2', 'c3d4']] }, limit: 2 })
  })

  it('une série de temps se lit en mois et un groupe sans valeur s’appelle « (vide) »', async () => {
    const byMonth = result({ group: { field: 'posting_date', bucket: 'month', fieldtype: 'Date' }, rows: [{ key: '2026-09', values: [0] }, { key: '2026-10', values: [4] }] })
    const { w } = setup(ChartCard, { block: chart({ kind: 'bar', group_by: 'posting_date' }) }, { aggregate: async () => byMonth })
    await flushPromises()
    expect(w.findAll('.chart-axis text').map((x) => x.text()).join(' ')).toMatch(/sept.*26.*oct.*26/)
    const blank = grouped([null, 'Sortie'], [2, 1])
    const second = setup(ChartCard, { block: chart() }, { aggregate: async () => blank })
    await flushPromises()
    expect(second.w.text()).toContain('(vide)')
  })

  it('actualiser relit les données', async () => {
    let n = 1
    const aggregate = vi.fn(async () => grouped(['A'], [n++]))
    const { w } = setup(ChartCard, { block: chart() }, { aggregate })
    await flushPromises()
    expect(w.find('.chart-center').text()).toBe('1')
    await w.find('[data-testid="chart-refresh"]').trigger('click')
    await flushPromises()
    expect(aggregate).toHaveBeenCalledTimes(2)
    expect(w.find('.chart-center').text()).toBe('2')
  })

  it('une erreur est dite sur la carte, et actualiser peut la réparer', async () => {
    let fail = true
    const aggregate = vi.fn(async () => {
      if (fail) throw new ApiError(500, 'internal_error', 'Erreur interne')
      return grouped(['A'], [3])
    })
    const { w } = setup(ChartCard, { block: chart() }, { aggregate })
    await flushPromises()
    expect(w.find('[data-testid="chart-error"]').exists()).toBe(true)
    fail = false
    await w.find('[data-testid="chart-refresh"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="chart-error"]').exists()).toBe(false)
    expect(w.find('[data-testid="chart-legend-item"]').exists()).toBe(true)
  })

  it('une réponse tardive d’une demande périmée n’écrase pas la dernière', async () => {
    const resolvers: ((r: AggregateResult) => void)[] = []
    const aggregate = vi.fn(() => new Promise<AggregateResult>((r) => resolvers.push(r)))
    const { w } = setup(ChartCard, { block: chart() }, { aggregate })
    await flushPromises()
    await w.setProps({ block: chart({ filters: { docstatus: 1 } }) }) // le bloc change pendant que la première demande est en vol
    expect(aggregate).toHaveBeenCalledTimes(2)
    resolvers[1]!(grouped(['récent'], [2]))
    await flushPromises()
    resolvers[0]!(grouped(['périmé'], [9]))
    await flushPromises()
    expect(w.text()).toContain('récent')
    expect(w.text()).not.toContain('périmé')
  })

  it('la vue tableau montre toutes les valeurs exactes, et l’on revient au graphique', async () => {
    const res = grouped(['Réception', 'Sortie'], ['1234.500', '0.250'], { metrics: [{ function: 'sum', field: 'qty', label: 'Quantité', decimals: 3 }] })
    const { w } = setup(ChartCard, { block: chart({ function: 'sum', field: 'qty' }) }, { aggregate: async () => res })
    await flushPromises()
    expect(w.find('[data-testid="chart-table"]').exists()).toBe(false)
    await w.find('[data-testid="chart-table-toggle"]').trigger('click')
    const rows = w.findAll('[data-testid="chart-table"] tbody tr').map((tr) => tr.findAll('td').map((td) => td.text().replace(/\s/g, ' ')))
    expect(rows).toEqual([['Réception', '1 234,500'], ['Sortie', '0,250']])
    expect(w.find('[data-testid="chart-table"] th.num').text()).toBe('Quantité')
    expect(w.find('[data-testid="chart"]').exists()).toBe(false)
    await w.find('[data-testid="chart-table-toggle"]').trigger('click')
    expect(w.find('[data-testid="chart"]').exists()).toBe(true)
  })

  it('plus de six parts : les plus grosses, puis « Autres » ; la vue tableau garde tout', async () => {
    const keys = Array.from({ length: 9 }, (_, i) => `G${i}`)
    const res = grouped(keys, keys.map((_, i) => 90 - i * 10))
    const { w } = setup(ChartCard, { block: chart({ limit: 10 }) }, { aggregate: async () => res })
    await flushPromises()
    expect(legendRows(w).map((r) => r[0])).toEqual(['G0', 'G1', 'G2', 'G3', 'G4', 'Autres'])
    expect(legendRows(w).at(-1)![1]).toBe(String(40 + 30 + 20 + 10)) // G5 à G8 : 40, 30, 20, 10
    expect(w.find('[data-testid="chart-slice-5"]').attributes('style')).toContain('--chart-other')
    await w.find('[data-testid="chart-table-toggle"]').trigger('click')
    expect(w.findAll('[data-testid="chart-table"] tbody tr')).toHaveLength(9)
  })

  it('un repli de montants reste exact', async () => {
    const keys = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    const vals = ['100.00', '90.00', '80.00', '70.00', '60.00', '0.10', '0.20', '0.30']
    const res = grouped(keys, vals, { metrics: [{ function: 'sum', field: 'amount', label: null, decimals: 2 }] })
    const { w } = setup(ChartCard, { block: chart({ function: 'sum', field: 'amount', kind: 'pie' }) }, { aggregate: async () => res })
    await flushPromises()
    expect(legendRows(w).at(-1)).toEqual(['Autres', '0,60', '0,1 %']) // 0,10 + 0,20 + 0,30 : pas 0,6000000000000001
  })

  it('une barre ou une courbe garde toutes ses valeurs, sans repli', async () => {
    const keys = Array.from({ length: 9 }, (_, i) => `G${i}`)
    const { w } = setup(ChartCard, { block: chart({ kind: 'bar' }) }, { aggregate: async () => grouped(keys, keys.map((_, i) => i + 1)) })
    await flushPromises()
    expect(w.findAll('[data-testid^="chart-bar-"]')).toHaveLength(9)
    expect(w.text()).not.toContain('Autres')
  })

  it('pendant un rechargement le dessin précédent reste, estompé : pas de squelette', async () => {
    let release!: (r: AggregateResult) => void
    let first = true
    const aggregate = vi.fn(() => (first ? ((first = false), Promise.resolve(grouped(['A'], [3]))) : new Promise<AggregateResult>((r) => (release = r))))
    const { w } = setup(ChartCard, { block: chart() }, { aggregate })
    await flushPromises()
    expect(w.find('.chart-card').classes()).not.toContain('stale')
    await w.find('[data-testid="chart-refresh"]').trigger('click')
    expect(w.find('.chart-card').classes()).toContain('stale')
    expect(w.find('[data-testid="chart-legend-item"]').exists()).toBe(true) // toujours là
    expect(w.find('[data-testid="chart-loading"]').exists()).toBe(false)
    release(grouped(['B'], [4]))
    await flushPromises()
    expect(w.find('.chart-card').classes()).not.toContain('stale')
    expect(w.text()).toContain('B')
  })

  it('une troncature est signalée', async () => {
    const { w } = setup(ChartCard, { block: chart() }, { aggregate: async () => grouped(['A', 'B'], [2, 1], { truncated: true }) })
    await flushPromises()
    expect(w.text()).toContain('Seuls les 2 premiers groupes')
  })
})

// ── Liste rapide, liens, grille ─────────────────────────────────────────────────────────────

const rec = (name: string, title: string, docstatus = 0): DocRecord =>
  ({ doctype: 'Stock Entry', name, owner: 'a', creation: '2026-10-01T00:00:00Z', modified: new Date(Date.now() - 3_600_000).toISOString(), version: 1, docstatus, amended_from: null, data: { remarks: title } })

const quick = (over: Partial<QuickListBlock> = {}): QuickListBlock => ({ type: 'quick_list', label: 'Derniers mouvements', doctype: 'Stock Entry', limit: 5, ...over })

describe('liste rapide', () => {
  it('les derniers documents, le plus récent d’abord, chacun mène à sa fiche', async () => {
    const list = vi.fn(async () => ({ items: [rec('MS-1', 'Arrivage'), rec('MS-2', 'Sortie')], total: 2, limit: 5, offset: 0 }))
    const { w } = setup(QuickList, { block: quick({ title_field: 'remarks', filters: { docstatus: 0 } }) }, { list })
    await flushPromises()
    expect(list).toHaveBeenCalledWith('Stock Entry', { filters: { docstatus: 0 }, limit: 5, order_by: 'modified', order: 'desc' })
    expect(w.find('[data-testid="quick-MS-1"]').text()).toBe('Arrivage')
    expect(w.find('[data-testid="quick-MS-1"]').attributes('href')).toBe('/app/Stock%20Entry/MS-1')
  })

  it('sans titre déclaré, c’est le nom du document ; un type soumettable montre l’état', async () => {
    const list = async () => ({ items: [rec('MS-1', 'x', 1)], total: 1, limit: 5, offset: 0 })
    const { w } = setup(QuickList, { block: quick({ submittable: true }) }, { list })
    await flushPromises()
    expect(w.find('[data-testid="quick-MS-1"]').text()).toBe('MS-1')
    expect(w.find('.status-pill').text()).toBe('Soumis')
  })

  it('aucun document, ou une erreur, se disent', async () => {
    const empty = setup(QuickList, { block: quick() }, { list: async () => ({ items: [], total: 0, limit: 5, offset: 0 }) })
    await flushPromises()
    expect(empty.w.find('[data-testid="quick-list-empty"]').exists()).toBe(true)
    const broken = setup(QuickList, { block: quick() }, { list: async () => { throw new ApiError(500, 'internal_error', 'x') } })
    await flushPromises()
    expect(broken.w.find('.block-error').exists()).toBe(true)
  })
})

describe('carte de liens', () => {
  it('chaque lien mène à la liste de son type', () => {
    const { w } = setup(LinksCard, { block: { type: 'card', title: 'Rapports', links: [{ doctype: 'Bin', label: 'Soldes', icon: 'box' }, { doctype: 'Stock Ledger Entry', label: 'Journal' }] } })
    expect(w.text()).toContain('Rapports')
    expect(w.find('[data-testid="link-Bin"]').attributes('href')).toBe('/app/Bin')
    expect(w.find('[data-testid="link-Bin"] i').classes()).toContain('pi-box')
    expect(w.find('[data-testid="link-Stock Ledger Entry"] i').classes()).toContain('pi-file') // sans icône : celle d’un document
  })
})

describe('grille de blocs', () => {
  const blocks: WorkspaceBlock[] = [
    { type: 'header', text: 'Suivi' },
    { type: 'paragraph', text: 'Chiffres du jour' },
    card(),
    card({ label: 'Heures', cols: 6 }),
    chart(),
    { type: 'spacer' },
    { type: 'header', text: 'Accès' },
    { type: 'shortcut', doctype: 'Bin', label: 'Solde', create: false },
    { type: 'shortcut', doctype: 'Item', label: 'Article', create: true },
    quick(),
    { type: 'card', title: 'Liens', links: [{ doctype: 'Bin', label: 'Soldes' }] },
  ]
  const api = { aggregate: async () => result(), list: async () => ({ items: [], total: 3, limit: 1, offset: 0 }) }

  it('chaque bloc à sa place, avec sa largeur sur 12 colonnes', async () => {
    const { w } = setup(BlockGrid, { blocks }, api)
    await flushPromises()
    expect(w.findAll('.home-title').map((x) => x.text())).toEqual(['Suivi', 'Accès'])
    expect(w.find('[data-testid="block-text"]').text()).toBe('Chiffres du jour')
    const spans = w.findAll('.block-item').map((x) => x.attributes('style')?.match(/--span: (\d+)/)?.[1])
    expect(spans).toEqual(['3', '6', '6', '6', '4']) // carte (3), carte à 6 colonnes, graphique (6), liste rapide (6), liens (4)
    expect(w.findAll('.is-number')).toHaveLength(2)
  })

  it('des raccourcis qui se suivent forment une seule rangée', async () => {
    const { w } = setup(BlockGrid, { blocks }, api)
    await flushPromises()
    expect(w.findAll('.home-grid')).toHaveLength(1)
    expect(w.findAll('.home-card')).toHaveLength(2)
    expect(w.find('[data-testid="count-Bin"]').text()).toBe('3')
  })

  it('un espace de raccourcis seuls se présente comme avant', async () => {
    const { w } = setup(BlockGrid, { blocks: [{ type: 'shortcut', doctype: 'Bin', label: 'Solde', create: false }] }, api)
    await flushPromises()
    expect(w.findAll('.block-item')).toHaveLength(0)
    expect(w.find('[data-testid="shortcut-Bin"]').exists()).toBe(true)
  })
})
