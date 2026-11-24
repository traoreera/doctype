import { describe, expect, it } from 'vitest'
import { describeError } from '@/composables/useNotify'
import { ApiError } from '@/lib/api'

/**
 * `notify.error` DÉCRIT elle-même ce qu'on lui donne (`useNotify`) : on lui passe l'erreur, jamais son texte. Décrire
 * deux fois perd le message — `describeError` ne reconnaît qu'une `ApiError` ou une `NetworkError`, et retombe sur le
 * message générique pour tout le reste, une chaîne comprise. Quatre appels faisaient cela, et l'utilisateur lisait
 * « une erreur est survenue » là où le serveur nommait la cause (le paquet `plutoprint` à installer, par exemple) :
 * un scénario Chromium le montrait, sans que personne voie pourquoi.
 */
const MISSING = new ApiError(503, 'pdf_engine_missing', 'L’impression PDF demande le paquet « plutoprint » (pip install plutoprint)')

describe('le message d’une erreur arrive jusqu’à l’utilisateur', () => {
  it('une raison du serveur est rendue telle quelle', () => {
    expect(describeError(MISSING)).toContain('plutoprint')
  })

  it('la décrire DEUX FOIS la perd : c’est le piège que le garde-fou ci-dessous interdit', () => {
    expect(describeError(describeError(MISSING))).not.toContain('plutoprint')
  })
})

describe('convention des notifications', () => {
  it('aucune source ne décrit une erreur avant de la notifier', () => {
    // Par Vite, pas par `node:fs` : les tests passent au `vue-tsc` du Desk, qui n'a pas les types de Node.
    const files: Record<string, string> = import.meta.glob('../src/**/*.{ts,vue}', { query: '?raw', import: 'default', eager: true })
    const paths = Object.keys(files)
    // Un motif qui ne trouve aucun fichier rendrait la liste vide, et le garde-fou passerait sans rien regarder.
    expect(paths.length).toBeGreaterThan(100)
    expect(paths.filter((p) => /\.error\(\s*describeError\(/.test(files[p] ?? ''))).toEqual([])
  })
})
