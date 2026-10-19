<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { t } from '@/i18n'
import { customTone, isHexTone, TONES } from '@/lib/brand'
import { OTHER_COLOR, arcPath, barPath, colorVar, formatTick, labelStep, linePoints, shorten, tickWidth, niceScale, polar, scaleY, slices } from '@/lib/charts'
import type { ChartKind } from '@/lib/types'

/**
 * Un graphique en SVG : barres, courbe, camembert, anneau, répartition. Aucune bibliothèque : les couleurs viennent des variables `--chart-N`
 * du thème (palette à huit teintes validée, clair et sombre), la largeur du conteneur. Il ne reçoit que des libellés et des nombres, déjà lus.
 *
 * Traits de la méthode (dataviz) : barres fines (24 px au plus) à bout de donnée arrondi, carrées sur la ligne de base ; courbe de 2 px ;
 * repères de 8 px cerclés de la couleur du fond ; une infobulle (la valeur d'abord) et, sur une courbe, un repère vertical ; valeurs écrites
 * seulement au bout des plus pertinentes ; jamais de texte dans la couleur d'une série.
 */
const props = withDefaults(
  defineProps<{
    kind: ChartKind
    /** Un libellé par valeur (l'axe, la légende). */
    labels: string[]
    /** Les valeurs à dessiner (des nombres : un dessin n'a pas besoin d'être exact). */
    values: number[]
    /** Les mêmes valeurs, lisibles et EXACTES (« 1 234,500 ») : légende et infobulle. */
    display: string[]
    /** Des graduations entières (un compteur). */
    integer?: boolean
    height?: number
    /** Le texte de remplacement lu par un lecteur d'écran. */
    title: string
    /** Texte au centre d'un anneau (le total). */
    center?: string
    /** La teinte des barres ou de la courbe : un nom de la palette ou `#rrggbb` (une seule série) ; sans elle, la première teinte de la palette. */
    color?: string
    /** La dernière part est le repli « Autres » : un gris neutre, pas une teinte de la palette. */
    other?: boolean
  }>(),
  { integer: false, height: 240, center: undefined, color: undefined, other: false },
)

const box = ref<HTMLElement | null>(null)
const width = ref(560)
const active = ref<number | null>(null)
let observer: ResizeObserver | undefined

onMounted(() => {
  const el = box.value
  if (!el) return
  if (el.clientWidth) width.value = el.clientWidth
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w && Math.abs(w - width.value) > 1) width.value = Math.floor(w)
    })
    observer.observe(el)
  }
})
onBeforeUnmount(() => observer?.disconnect())

// La teinte d'une série unique (barres, courbe) : on redéfinit `--chart-1` sur ce graphique seulement.
const custom = computed(() => (isXyKind.value ? customTone(props.color) : null))
const isXyKind = computed(() => props.kind === 'bar' || props.kind === 'line')
const seriesStyle = computed<Record<string, string> | undefined>(() => {
  if (!isXyKind.value || !props.color) return undefined
  if (custom.value) return custom.value
  return (TONES as readonly string[]).includes(props.color) ? { '--chart-1': `var(--tone-${props.color})` } : undefined
})
const empty = computed(() => !props.values.length || props.values.every((v) => v === 0))
const isXy = computed(() => props.kind === 'bar' || props.kind === 'line')
const colorOf = (i: number): string => (props.other && i === props.labels.length - 1 ? OTHER_COLOR : colorVar(i))

// ── Barres et courbe ────────────────────────────────────────
const M = { top: 22, right: 12, bottom: 30, left: 42 }
const W = computed(() => Math.max(240, width.value))
const scale = computed(() => niceScale(Math.min(0, ...props.values), Math.max(0, ...props.values), 4, props.integer))
const plot = computed(() => ({ left: M.left, right: W.value - M.right, top: M.top, bottom: props.height - M.bottom }))
const band = computed(() => (plot.value.right - plot.value.left) / Math.max(1, props.values.length))
const zeroY = computed(() => scaleY(0, scale.value, plot.value.top, plot.value.bottom))
const bars = computed(() =>
  props.values.map((v, i) => {
    const w = Math.min(24, band.value * 0.64) // fine : jamais la bande entière, le reste est de l'air
    const x = plot.value.left + band.value * i + (band.value - w) / 2
    const tip = scaleY(v, scale.value, plot.value.top, plot.value.bottom)
    return { x, w, tip, d: barPath(x, w, zeroY.value, tip, 4), mid: x + w / 2 }
  }),
)
const points = computed(() => linePoints(props.values, scale.value, plot.value.left, plot.value.right, plot.value.top, plot.value.bottom))
const linePath = computed(() => points.value.map(([x, y], i) => `${i ? 'L' : 'M'} ${x} ${y}`).join(' '))
const areaPath = computed(() => {
  const pts = points.value
  if (!pts.length) return ''
  return `${linePath.value} L ${pts.at(-1)![0]} ${zeroY.value} L ${pts[0]![0]} ${zeroY.value} Z`
})
const every = computed(() => labelStep(props.labels.length, plot.value.right - plot.value.left, tickWidth(props.labels)))

// Les valeurs ne s'écrivent pas sur chaque point : toutes si elles sont peu nombreuses, sinon le plus grand et le dernier. L'infobulle porte le reste.
const labelled = computed(() => {
  const n = props.values.length
  if (!n || band.value < 36) return new Set<number>()
  if (n <= 6) return new Set(props.values.map((_, i) => i))
  const max = props.values.indexOf(Math.max(...props.values))
  return new Set([max, n - 1])
})
const markers = computed(() => props.values.length <= 24)

// ── Camembert, anneau, répartition ──────────────────────────
// Même taille d'un anneau à l'autre (la largeur de la carte ne la change pas), sauf dans une carte très étroite.
const pieSize = computed(() => Math.min(160, Math.max(112, W.value - 48)))
const parts = computed(() => slices(props.values))
const radius = computed(() => pieSize.value / 2 - 2)
const inner = computed(() => (props.kind === 'donut' ? radius.value * 0.62 : 0))
const legend = computed(() =>
  parts.value.map((p) => ({ ...p, label: props.labels[p.index] ?? '', shown: props.display[p.index] ?? '', percent: `${Math.round(p.fraction * 1000) / 10}`.replace('.', ',') })),
)

// ── Infobulle : la valeur d'abord, le libellé ensuite ───────
const tip = computed(() => {
  const i = active.value
  if (i === null || empty.value) return null
  let x = 0
  let y = 0
  let color = colorVar(0)
  if (props.kind === 'bar') {
    const b = bars.value[i]
    if (!b) return null
    x = b.mid
    y = Math.min(b.tip, zeroY.value)
  } else if (props.kind === 'line') {
    const p = points.value[i]
    if (!p) return null
    ;[x, y] = p
  } else {
    const p = parts.value.find((s) => s.index === i)
    if (!p) return null
    const [px, py] = polar(pieSize.value / 2, pieSize.value / 2, (radius.value + inner.value) / 2, (p.start + p.end) / 2)
    x = (W.value - pieSize.value) / 2 + px
    y = py + 4
    color = colorOf(i)
  }
  const below = y < 46
  return { x: Math.min(Math.max(x, 70), W.value - 70), y: below ? y + 14 : y - 10, below, label: props.labels[i] ?? '', value: props.display[i] ?? '', color }
})
</script>

<template>
  <div ref="box" class="chart" :data-kind="kind" :data-custom="custom ? '' : undefined" :style="seriesStyle" data-testid="chart">
    <div v-if="empty" class="chart-empty desk-muted" data-testid="chart-empty">{{ t.charts.noData }}</div>

    <template v-else>
      <div class="chart-plot" :class="{ 'is-pie': !isXy && kind !== 'percentage' }">
        <svg
          v-if="isXy"
          class="chart-svg"
          :viewBox="`0 0 ${W} ${height}`"
          :width="W"
          :height="height"
          role="group"
          :aria-label="title"
          data-testid="chart-svg"
          @mouseleave="active = null"
        >
          <g class="chart-grid">
            <template v-for="tick in scale.ticks" :key="tick">
              <line :x1="plot.left" :x2="plot.right" :y1="scaleY(tick, scale, plot.top, plot.bottom)" :y2="scaleY(tick, scale, plot.top, plot.bottom)" :class="tick === 0 ? 'chart-zero' : 'chart-line'" />
              <text :x="plot.left - 6" :y="scaleY(tick, scale, plot.top, plot.bottom)" class="chart-tick" text-anchor="end" dominant-baseline="middle">{{ formatTick(tick) }}</text>
            </template>
          </g>

          <template v-if="kind === 'bar'">
            <g v-for="(b, i) in bars" :key="i" :class="['chart-bar', { dim: active !== null && active !== i }]" :data-testid="`chart-bar-${i}`" role="img" :aria-label="`${labels[i]} : ${display[i]}`" tabindex="0" @mouseenter="active = i" @focus="active = i" @blur="active = null">
              <path :d="b.d" :style="{ fill: colorVar(0) }" />
              <rect :x="plot.left + band * i" :y="plot.top" :width="band" :height="plot.bottom - plot.top" class="chart-hit" />
            </g>
            <template v-for="(b, i) in bars" :key="`v${i}`">
              <text v-if="labelled.has(i)" :x="b.mid" :y="(values[i] ?? 0) >= 0 ? b.tip - 5 : b.tip + 13" class="chart-value" text-anchor="middle" data-testid="chart-value">{{ display[i] }}</text>
            </template>
          </template>

          <template v-else>
            <path :d="areaPath" class="chart-area" :style="{ fill: colorVar(0) }" />
            <line v-if="active !== null && points[active]" :x1="points[active]![0]" :x2="points[active]![0]" :y1="plot.top" :y2="plot.bottom" class="chart-crosshair" data-testid="chart-crosshair" />
            <path :d="linePath" class="chart-path" :style="{ stroke: colorVar(0) }" fill="none" data-testid="chart-line" />
            <g v-for="(p, i) in points" :key="i" :data-testid="`chart-point-${i}`" role="img" :aria-label="`${labels[i]} : ${display[i]}`" tabindex="0" @mouseenter="active = i" @focus="active = i" @blur="active = null">
              <circle v-if="markers || active === i || i === points.length - 1" :cx="p[0]" :cy="p[1]" r="4" class="chart-dot" :style="{ fill: colorVar(0) }" />
              <rect :x="plot.left + band * i" :y="plot.top" :width="band" :height="plot.bottom - plot.top" class="chart-hit" />
            </g>
            <text v-if="points.length && !markers" :x="points.at(-1)![0]" :y="points.at(-1)![1] - 9" class="chart-value" text-anchor="end">{{ display.at(-1) }}</text>
          </template>

          <g class="chart-axis">
            <template v-for="(label, i) in labels" :key="i">
              <text v-if="i % every === 0" :x="plot.left + band * (i + 0.5)" :y="plot.bottom + 17" class="chart-tick" text-anchor="middle">{{ shorten(label) }}</text>
            </template>
          </g>
        </svg>

        <div v-else-if="kind === 'percentage'" class="chart-percent" data-testid="chart-percentage">
          <div class="chart-stack" role="img" :aria-label="title">
            <span
              v-for="p in parts"
              :key="p.index"
              :class="['chart-seg', { dim: active !== null && active !== p.index }]"
              :style="{ width: `${p.fraction * 100}%`, background: colorOf(p.index) }"
              :data-testid="`chart-slice-${p.index}`"
              @mouseenter="active = p.index"
              @mouseleave="active = null"
            />
          </div>
        </div>

        <svg v-else class="chart-svg" :viewBox="`0 0 ${pieSize} ${pieSize}`" :width="pieSize" :height="pieSize" role="group" :aria-label="title" data-testid="chart-svg" @mouseleave="active = null">
          <path
            v-for="p in parts"
            :key="p.index"
            :d="arcPath(pieSize / 2, pieSize / 2, radius, inner, p.start, p.end)"
            fill-rule="evenodd"
            :class="['chart-slice', { dim: active !== null && active !== p.index }]"
            :style="{ fill: colorOf(p.index) }"
            :data-testid="`chart-slice-${p.index}`"
            role="img"
            :aria-label="`${labels[p.index]} : ${display[p.index]}`"
            tabindex="0"
            @mouseenter="active = p.index"
            @focus="active = p.index"
            @blur="active = null"
          />
          <text v-if="kind === 'donut' && center" :x="pieSize / 2" :y="pieSize / 2" class="chart-center" text-anchor="middle" dominant-baseline="middle">{{ center }}</text>
        </svg>

        <div v-if="tip" class="chart-tip" :class="{ below: tip.below }" :style="{ left: `${tip.x}px`, top: `${tip.y}px` }" role="status" data-testid="chart-tip">
          <span class="chart-tip-key" :style="{ background: tip.color }" />
          <strong class="chart-tip-value">{{ tip.value }}</strong>
          <span class="chart-tip-label">{{ tip.label }}</span>
        </div>
      </div>

      <ul v-if="!isXy" class="chart-legend" data-testid="chart-legend">
        <li v-for="item in legend" :key="item.index" :class="{ dim: active !== null && active !== item.index }" data-testid="chart-legend-item" @mouseenter="active = item.index" @mouseleave="active = null">
          <span class="chart-swatch" :style="{ background: colorOf(item.index) }" />
          <span class="chart-legend-label">{{ item.label }}</span>
          <span class="chart-legend-value">{{ item.shown }}</span>
          <span class="desk-muted chart-legend-percent">{{ item.percent }} %</span>
        </li>
      </ul>
    </template>
  </div>
</template>
