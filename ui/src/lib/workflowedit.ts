/**
 * L'habillage d'automate d'une organisation, côté Desk (process/programme.md §3.6).
 *
 * Le circuit vient de l'APPLICATION (le YAML, rendu par `GET /override/workflow` sous `definition`). Une organisation peut :
 * - le RESSERRER : fermer une transition, lui ajouter une condition, imposer `not_owner` (`transitions`, des retouches par action) ;
 * - y AJOUTER des états (`states`) et des transitions (`added`), rangés pour elle seule. Une transition ajoutée s'ouvre par un droit
 *   que le type déclare déjà (`grant_choices`) : un droit inventé ici, personne ne l'aurait chez xauth.
 *
 * Tout est pur ici — des fonctions qui rendent un NOUVEAU JSON —, et le serveur reste juge (`overrides.validate_workflow_override`).
 * Ce qui est déjà imposé par le YAML (`locked`) se montre coché et grisé : le serveur refuse qu'on y touche.
 */
import type { AddedTransition, TransitionTweak, WorkflowDef, WorkflowOverride, WorkflowState } from './types'

export type TweakKey = 'closed' | 'condition' | 'not_owner'
export type Docstatus = NonNullable<WorkflowState['docstatus']>

/** Une ligne de l'éditeur : une transition (du YAML ou ajoutée), ce que l'organisation en a fait, et ce qui ne se retouche pas. */
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
  /** Le flux xflow lancé après la transition, ou null. */
  flow: string | null
  /** Ajoutée par l'organisation : elle se modifie (cible, droit, condition…) et se supprime, au lieu de se resserrer. */
  added: boolean
  /** Le droit d'une transition ajoutée (celui d'une transition du YAML vient du serveur, `grants`). */
  grant: string | null
}

const tweaks = (config: WorkflowOverride | null | undefined): Record<string, TransitionTweak> =>
  config && typeof config === 'object' && config.transitions && typeof config.transitions === 'object' ? config.transitions : {}
const addedStates = (config: WorkflowOverride | null | undefined): WorkflowState[] => (Array.isArray(config?.states) ? config!.states! : [])
const addedTransitions = (config: WorkflowOverride | null | undefined): AddedTransition[] => (Array.isArray(config?.added) ? config!.added! : [])

/** Un habillage débarrassé de ses clés vides : le serveur reçoit exactement ce qui compte. */
function tidy(config: WorkflowOverride): WorkflowOverride {
  const out: WorkflowOverride = {}
  if (config.transitions && Object.keys(config.transitions).length) out.transitions = config.transitions
  if (config.states?.length) out.states = config.states
  if (config.added?.length) out.added = config.added
  return out
}

/** Le circuit TEL QUE l'organisation le vit : celui de l'application, suivi de ses états et transitions ajoutés. */
export function merged(definition: WorkflowDef | null | undefined, config: WorkflowOverride | null | undefined): WorkflowDef | null {
  if (!definition) return null
  return { states: [...definition.states, ...addedStates(config)], transitions: [...definition.transitions, ...addedTransitions(config)] }
}

/** Les lignes à montrer : les transitions de l'application dans leur ordre, habillées de ce que l'organisation a enregistré, puis les siennes. */
export function flowRows(definition: WorkflowDef | null | undefined, config: WorkflowOverride | null | undefined): FlowRow[] {
  const stored = tweaks(config)
  const fromApp = (definition?.transitions ?? []).map((t): FlowRow => {
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
      flow: t.flow ?? null,
      added: false,
      grant: null,
    }
  })
  const own = definition
    ? addedTransitions(config).map((t): FlowRow => ({
        action: t.action,
        from: t.from,
        to: t.to,
        yamlCondition: null,
        locked: false,
        closed: false,
        condition: t.condition ?? '',
        notOwner: t.not_owner === true,
        flow: t.flow ?? null,
        added: true,
        grant: t.grant ?? null,
      }))
    : []
  return [...fromApp, ...own]
}

/**
 * Le JSON avec cette retouche posée (ou retirée) sur une transition de l'APPLICATION : `false` et la chaîne vide RETIRENT la clé, car
 * le serveur refuse `closed: false` — pour rendre une transition, on retire la retouche, on ne la nie pas. Les ajouts sont gardés.
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
  return tidy({ ...(config ?? {}), transitions: next })
}

/** Y a-t-il quelque chose à enregistrer ? (un habillage vide se retire, il ne s'enregistre pas). */
export function hasTweaks(config: WorkflowOverride | null | undefined): boolean {
  return Object.keys(tweaks(config)).length > 0 || addedStates(config).length > 0 || addedTransitions(config).length > 0
}

/** L'état est-il un ajout de l'organisation ? (il se modifie et se supprime ; ceux de l'application, non). */
export function isAddedState(config: WorkflowOverride | null | undefined, name: string): boolean {
  return addedStates(config).some((s) => s.name === name)
}

/** Ajouter un état. Il n'est pas encore joignable : la page propose aussitôt la transition qui y mène. */
export function addState(config: WorkflowOverride | null | undefined, name: string, docstatus: Docstatus = 'draft'): WorkflowOverride {
  return tidy({ ...(config ?? {}), states: [...addedStates(config), { name: name.trim(), docstatus }] })
}

/** Changer l'état de document qu'un état ajouté impose. */
export function setStateDocstatus(config: WorkflowOverride | null | undefined, name: string, docstatus: Docstatus): WorkflowOverride {
  return tidy({ ...(config ?? {}), states: addedStates(config).map((s) => (s.name === name ? { ...s, docstatus } : s)) })
}

/** Retirer un état ajouté, et les transitions ajoutées qui en partent ou y mènent (elles viseraient un état inconnu). */
export function removeState(config: WorkflowOverride | null | undefined, name: string): WorkflowOverride {
  return tidy({
    ...(config ?? {}),
    states: addedStates(config).filter((s) => s.name !== name),
    added: addedTransitions(config).filter((t) => t.from !== name && t.to !== name),
  })
}

/** Ajouter une transition (son droit est obligatoire : sans lui, personne ne pourrait la faire). */
export function addTransition(config: WorkflowOverride | null | undefined, transition: AddedTransition): WorkflowOverride {
  const t: AddedTransition = { action: transition.action.trim(), from: transition.from, to: transition.to, grant: transition.grant }
  if (transition.not_owner) t.not_owner = true
  if (transition.condition?.trim()) t.condition = transition.condition.trim()
  return tidy({ ...(config ?? {}), added: [...addedTransitions(config), t] })
}

/** Modifier une transition ajoutée. Une condition vide ou `not_owner: false` RETIRENT la clé (le serveur ne garde que l'utile). */
export function patchAdded(config: WorkflowOverride | null | undefined, action: string, patch: Partial<AddedTransition>): WorkflowOverride {
  const added = addedTransitions(config).map((t) => {
    if (t.action !== action) return t
    const next: AddedTransition = { ...t, ...patch }
    if (!next.condition?.trim()) delete next.condition
    else next.condition = next.condition.trim()
    if (!next.not_owner) delete next.not_owner
    if (!next.description?.trim()) delete next.description
    return next
  })
  return tidy({ ...(config ?? {}), added })
}

export function removeAdded(config: WorkflowOverride | null | undefined, action: string): WorkflowOverride {
  return tidy({ ...(config ?? {}), added: addedTransitions(config).filter((t) => t.action !== action) })
}

/**
 * Les états qui n'auraient plus aucune sortie si on enregistrait ceci — la même règle que le serveur (`overrides._dead_ends`),
 * sur le circuit de l'organisation (ses ajouts compris), évaluée ICI pour le dire AVANT d'enregistrer.
 */
export function deadEnds(definition: WorkflowDef | null | undefined, config: WorkflowOverride | null | undefined): string[] {
  const stored = tweaks(config)
  const closed = (action: string) => stored[action]?.closed === true
  const transitions = merged(definition, config)?.transitions ?? []
  const states = [...new Set(transitions.map((t) => t.from))]
  return states.filter((state) => transitions.filter((t) => t.from === state).every((t) => closed(t.action))).sort()
}

export interface AdditionLabels {
  duplicateState: (name: string) => string
  duplicateAction: (name: string) => string
  unreachable: (name: string) => string
  noGrant: (action: string) => string
}

/** Ce qui empêcherait d'enregistrer les ajouts, dit avant le serveur (qui rejuge tout) : noms pris, état que rien n'atteint, droit absent. */
export function additionProblems(definition: WorkflowDef | null | undefined, config: WorkflowOverride | null | undefined, l: AdditionLabels): string[] {
  const all = merged(definition, config)
  if (!all) return []
  const out: string[] = []
  const seenStates = new Set<string>()
  for (const s of all.states) {
    if (seenStates.has(s.name)) out.push(l.duplicateState(s.name))
    seenStates.add(s.name)
  }
  const seenActions = new Set<string>()
  for (const t of all.transitions) {
    if (seenActions.has(t.action)) out.push(l.duplicateAction(t.action))
    seenActions.add(t.action)
  }
  const reached = new Set([all.states[0]?.name, ...all.transitions.map((t) => t.to)])
  for (const s of addedStates(config)) if (!reached.has(s.name)) out.push(l.unreachable(s.name))
  for (const t of addedTransitions(config)) if (!t.grant) out.push(l.noGrant(t.action))
  return out
}
