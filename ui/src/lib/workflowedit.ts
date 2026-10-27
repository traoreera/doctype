/**
 * L'habillage d'automate d'une organisation, côté Desk (process/programme.md §3.6).
 *
 * Le Desk n'invente aucune règle : il montre l'automate du YAML (`definition`, rendu par `GET /override/workflow`) et écrit dans le
 * JSON que le serveur validera. Tout est pur ici — une ligne par transition, et des fonctions qui rendent un NOUVEAU JSON — pour que
 * la fenêtre n'ait plus qu'à afficher.
 *
 * On ne fait que RESSERRER : fermer une transition, lui ajouter une condition, imposer `not_owner`. Ce qui est déjà imposé par le
 * YAML (`locked`) se montre coché et grisé — le serveur refuse qu'on y touche, et une case qu'on peut cocher sans effet mentirait.
 */
import type { TransitionTweak, WorkflowDef, WorkflowOverride } from './types'

export type TweakKey = 'closed' | 'condition' | 'not_owner'

/** Une ligne de l'éditeur : la transition du YAML, ce que l'organisation en a fait, et ce qui ne se retouche pas. */
export interface FlowRow {
  action: string
  from: string
  to: string
  /** La condition du YAML, qui s'ajoute (ET) à celle de l'organisation : montrée pour qu'on sache ce qui s'applique déjà. */
  yamlCondition: string | null
  /** `not_owner` imposé par le YAML : la case reste cochée et grisée. */
  locked: boolean
  closed: boolean
  condition: string
  notOwner: boolean
}

const tweaks = (config: WorkflowOverride | null | undefined): Record<string, TransitionTweak> =>
  config && typeof config === 'object' && config.transitions && typeof config.transitions === 'object' ? config.transitions : {}

/** Les lignes à montrer : l'automate du YAML, dans son ordre, habillé de ce que l'organisation a enregistré. */
export function flowRows(definition: WorkflowDef | null | undefined, config: WorkflowOverride | null | undefined): FlowRow[] {
  const stored = tweaks(config)
  return (definition?.transitions ?? []).map((t) => {
    const mine = stored[t.action] ?? {}
    return {
      action: t.action,
      from: t.from,
      to: t.to,
      yamlCondition: t.condition ?? null,
      locked: t.not_owner === true,
      closed: mine.closed === true,
      condition: typeof mine.condition === 'string' ? mine.condition : '',
      notOwner: t.not_owner === true || mine.not_owner === true,
    }
  })
}

/**
 * Le JSON avec cette retouche posée (ou retirée) : `false` et la chaîne vide RETIRENT la clé, car le serveur refuse
 * `closed: false` — pour rendre une transition, on retire la retouche, on ne la nie pas.
 */
export function withTweak(config: WorkflowOverride | null | undefined, action: string, key: TweakKey, value: boolean | string): WorkflowOverride {
  const stored = tweaks(config)
  const mine: TransitionTweak = { ...(stored[action] ?? {}) }
  const empty = value === false || (typeof value === 'string' && !value.trim())
  if (empty) delete mine[key]
  else if (key === 'condition') mine.condition = String(value).trim()
  else mine[key] = true
  const next = { ...stored }
  if (Object.keys(mine).length) next[action] = mine
  else delete next[action] // une transition sans retouche ne figure pas dans l'habillage
  return Object.keys(next).length ? { transitions: next } : {}
}

/** Y a-t-il quelque chose à enregistrer ? (un habillage vide se retire, il ne s'enregistre pas). */
export function hasTweaks(config: WorkflowOverride | null | undefined): boolean {
  return Object.keys(tweaks(config)).length > 0
}

/**
 * Les états qui n'auraient plus aucune sortie si on enregistrait ceci — la même règle que le serveur (`overrides._dead_ends`),
 * évaluée ICI pour le dire AVANT d'enregistrer plutôt que de laisser l'utilisateur buter sur un refus.
 */
export function deadEnds(definition: WorkflowDef | null | undefined, config: WorkflowOverride | null | undefined): string[] {
  const stored = tweaks(config)
  const closed = (action: string) => stored[action]?.closed === true
  const transitions = definition?.transitions ?? []
  const states = [...new Set(transitions.map((t) => t.from))]
  return states.filter((state) => transitions.filter((t) => t.from === state).every((t) => closed(t.action))).sort()
}
