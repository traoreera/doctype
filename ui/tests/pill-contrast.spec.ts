/**
 * Le contraste des pastilles d'état (`--pill-*`, `desk.css`) mesuré contre le seuil AA de 4,5:1, en clair et en sombre (C3.2). On relit les
 * définitions de la feuille et la palette réelle (gris du Desk, couleurs d'Aura) : une couleur changée qui passerait sous le seuil échoue ici.
 */
import Aura from '@primeuix/themes/aura'
import { describe, expect, it } from 'vitest'
// @ts-expect-error -- API de Node, sans ses types dans ce projet : vitest vide les imports CSS, même `?raw`, on lit donc le fichier
import { readFileSync } from 'node:fs'
import { gray } from '@/theme/frappe'

type RGB = [number, number, number]
const primitive = (Aura as unknown as { primitive: Record<string, Record<string, string>> }).primitive
const hex = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as RGB
const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
const lum = (c: RGB) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])
const ratio = (a: RGB, b: RGB) => {
  const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m) as [number, number]
  return (x + 0.05) / (y + 0.05)
}

/** `var(--p-orange-800)`, `var(--p-surface-100)` ou `color-mix(in srgb, var(--p-red-400) 22%, transparent)` posé sur `under`. */
function resolve(value: string, under: RGB): RGB {
  const token = (name: string, step: string): RGB => hex(name === 'surface' ? gray[Number(step) as keyof typeof gray] : primitive[name]![step]!)
  const mix = /color-mix\(in srgb, var\(--p-([a-z]+)-(\d+)\) (\d+)%, transparent\)/.exec(value)
  if (mix) {
    const c = token(mix[1]!, mix[2]!)
    const a = Number(mix[3]) / 100
    return c.map((x, i) => x * a + under[i]! * (1 - a)) as RGB
  }
  const v = /var\(--p-([a-z]+)-(\d+)\)/.exec(value)
  if (!v) throw new Error(`valeur non résolue : ${value}`)
  return token(v[1]!, v[2]!)
}

function pills(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--(pill[a-z-]*):\s*([^;]+);/g)].map((m) => [m[1]!, m[2]!.trim()]))
}

const css: string = readFileSync('src/theme/desk.css', 'utf-8') // vitest tourne dans ui/
const light = pills(/:root\s*\{([\s\S]*?)\n\}/.exec(css)![1]!)
const dark = pills(/\.app-dark\s*\{([\s\S]*?)\n\}/.exec(css)![1]!)

describe('contraste des pastilles (AA, 4,5:1)', () => {
  for (const [scheme, vars, page] of [['clair', light, hex(gray[0])], ['sombre', dark, hex(gray[900])]] as const) {
    for (const kind of ['', 'warn-', 'ok-', 'bad-']) {
      it(`${scheme} : ${kind || 'neutre '}`.trim(), () => {
        const bg = resolve(vars[`pill-${kind}bg`]!, page)
        const fg = resolve(vars[`pill-${kind}fg`]!, bg)
        expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5)
      })
    }
  }
})
