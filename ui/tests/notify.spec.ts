import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { describeError, useNotify, UserMessage } from '@/composables/useNotify'
import { ApiError } from '@/lib/api'
import { t } from '@/i18n'
import { captureToasts, mountWith } from './views-fixtures'

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

/**
 * Un texte écrit par l'AUTEUR pour l'utilisateur (traoreera/doctype#86). Six sites le passaient à `notify.error` sous la
 * forme d'une `Error` ordinaire ou d'une chaîne, et `describeError` — qui ne rend que ce qu'elle sait écrit pour être lu —
 * affichait « Une erreur est survenue » à la place de « Fichier trop volumineux ». Le porteur est maintenant explicite :
 * `notify.fail(texte)`, ou une `UserMessage` quand le texte doit traverser un `throw`.
 */
describe('un texte d’auteur arrive à l’écran, une erreur inattendue reste générique', () => {
  const toasts = captureToasts()
  let notify!: ReturnType<typeof useNotify>
  const Probe = defineComponent({
    setup() {
      notify = useNotify()
      return () => h('div')
    },
  })

  it('`notify.fail` affiche le texte tel quel', async () => {
    mountWith(Probe, {})
    notify.fail('Fichier trop volumineux (maximum 10 Mo)')
    expect(toasts.summaries()).toEqual(['Fichier trop volumineux (maximum 10 Mo)'])
  })

  it('`notify.fail` sans texte retombe sur le message générique, jamais sur une notification vide', async () => {
    mountWith(Probe, {})
    notify.fail('   ')
    notify.fail(undefined)
    expect(toasts.summaries()).toEqual([t.errors.generic, t.errors.generic])
  })

  it('une `UserMessage` levée puis attrapée garde son texte', async () => {
    mountWith(Probe, {})
    try {
      throw new UserMessage('100 blocs au plus')
    } catch (e) {
      notify.error(e)
    }
    expect(toasts.summaries()).toEqual(['100 blocs au plus'])
    expect(describeError(new UserMessage(''))).toBe(t.errors.generic)
  })

  it('une `Error` inattendue reste générique : un message interne ne fuit pas vers l’utilisateur', async () => {
    mountWith(Probe, {})
    notify.error(new TypeError('x is undefined'))
    notify.error(new Error('SELECT * FROM xdt_document'))
    notify.error('une chaîne passée par erreur')
    expect(toasts.summaries()).toEqual([t.errors.generic, t.errors.generic, t.errors.generic])
  })
})

describe('convention des notifications : le porteur d’un texte d’auteur', () => {
  const files: Record<string, string> = import.meta.glob('../src/**/*.{ts,vue}', { query: '?raw', import: 'default', eager: true })
  const offenders = (pattern: RegExp) => Object.keys(files).filter((p) => pattern.test(files[p] ?? ''))

  it('aucune source ne fabrique une erreur pour faire passer un texte', () => {
    expect(Object.keys(files).length).toBeGreaterThan(100)
    // `notify.error(new Error('…'))` : le texte était avalé ; `new ApiError(…)` fabriquée sur place : le détour que chacun trouvait.
    expect(offenders(/\.error\(\s*new\s+(Error|ApiError)\(/)).toEqual([])
  })

  it('aucune source ne passe un littéral ou un champ `message` à `notify.error`', () => {
    expect(offenders(/notify\.error\(\s*(['"`]|[\w.]+\.message\s*\))/)).toEqual([])
  })
})
