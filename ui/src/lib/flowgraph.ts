/**
 * Une automatisation xflow dessinée comme un GRAPHE (l'éditeur, façon n8n) : le déclencheur, puis une étape par nœud, les flèches
 * étant les clés que le langage d'xflow nomme déjà — `on_success`, `on_failure`, `if_true`/`if_false`, les `cases` d'un switch, les
 * `branches` d'un parallel, le corps d'un foreach. L'éditeur n'invente donc aucune notion : il montre la définition telle qu'elle est
 * exécutée (`platform/xflow/src/runtime/engine.py`).
 *
 * Tout est pur ici, testé à part (`tests/flowgraph.spec.ts`) : la page ne fait que donner ces nœuds et ces flèches à Vue Flow et
 * renvoyer au serveur ce que `writeFlow` rend.
 *
 * **Rien n'est jamais perdu** : chaque étape garde son objet BRUT (`raw`, `id` et `type` compris) et l'éditeur n'y touche que les clés
 * qu'il montre. Une définition écrite à la main (retry, webhooks sortants, `wait_for_event`, étapes IA…) se rouvre, se déplace et se
 * relie sans rien laisser au passage — c'est ce qui remplace l'ancien « ce flux ne s'édite qu'en JSON ». Un type d'étape qu'xflow
 * ajouterait demain se dessine en nœud neutre (`kind: null`) au lieu de fermer l'écran.
 *
 * Les POSITIONS vivent DANS la définition (`_x`/`_y` par étape, et sur le déclencheur) : xflow laisse passer les clés qu'il ne connaît
 * pas (`_StepBase`, `extra="allow"`) et son schéma les nomme déjà « métadonnées d'éditeur ». Le dessin suit donc le flux d'un
 * navigateur et d'un utilisateur à l'autre, au lieu de rester dans le localStorage de celui qui l'a tracé (c'est la différence avec
 * l'éditeur de circuit d'un DocType, où le placement n'est qu'une commodité de lecture).
 */
import { t } from '@/i18n'
import { FLOW_NAME, TRIGGER_KINDS, type TriggerKind } from './automations'
import { actionOf, actionSpec } from './flowactions'
import { readValue } from './flowvalues'

// ── Le vocabulaire d'xflow (`platform/xflow/src/schemas/workflow.py`) ────────────────────────────────────────────

/** Les neuf types d'étape d'xflow. Fermé par son schéma : il refuse au déploiement tout ce qui n'est pas là-dedans. */
export const STEP_KINDS = ['action', 'condition', 'switch', 'parallel', 'foreach', 'wait', 'transform', 'template', 'ai'] as const
export type StepKind = (typeof STEP_KINDS)[number]

/** Les opérateurs d'une condition (`ConditionOperator`). Les deux derniers n'ont pas d'opérande droite. */
export const OPERATORS = ['==', '!=', '>', '>=', '<', '<=', 'in', 'not_in', 'contains', 'starts_with', 'ends_with', 'regex', 'is_null', 'is_not_null'] as const
export type Operator = (typeof OPERATORS)[number]
export const UNARY_OPERATORS: readonly Operator[] = ['is_null', 'is_not_null']

/** Les services d'une étape IA (`AIService`). */
export const AI_SERVICES = ['summarize', 'classify', 'decide', 'extract'] as const

/**
 * Le type MIME du glisser-déposer de la palette : seul l'éditeur reconnaît ce qu'il glisse, donc un fichier lâché sur le canevas ne
 * devient pas une étape.
 */
export const STEP_MIME = 'application/x-xdt-flow-step'

/** L'identifiant d'une étape : ce qu'une flèche cite. Sans point ni `$`, donc jamais confondu avec une clé de sortie. */
export const STEP_ID = /^[A-Za-z0-9_-]{1,60}$/
/** Le nœud du déclencheur. `$` est interdit dans un identifiant d'étape : aucune collision possible. */
export const TRIGGER_NODE = '$trigger'

// ── Le modèle ────────────────────────────────────────────────────────────────────────────────────────────────────

export interface FlowStep {
  id: string
  /** Le type, quand l'éditeur le connaît ; `null` sinon (il le garde tel quel et le dessine en nœud neutre). */
  kind: StepKind | null
  /** L'étape telle qu'xflow la stocke. Seule source de vérité : les fonctions ci-dessous en rendent des copies modifiées. */
  raw: Record<string, unknown>
}

export interface FlowTrigger {
  kind: TriggerKind
  event: string
  cron: string
  /** Ce que le déclencheur porte et que l'éditeur ne montre pas (`interval_seconds`, `initial_payload`, `webhook_path`…). */
  raw: Record<string, unknown>
}

export interface FlowModel {
  name: string
  description: string
  trigger: FlowTrigger
  steps: FlowStep[]
  /** L'étape de départ (`entry_step`) ; `null` = la première de la liste, comme xflow l'entend. */
  entry: string | null
  /** Ce que le flux porte et que l'éditeur ne montre pas (`webhooks`, `tags`, `version`, `timeout_seconds`…). */
  extra: Record<string, unknown>
}

export type OutletTone = 'ok' | 'fail' | 'yes' | 'no' | 'case' | 'branch' | 'loop'

/** Une sortie d'étape : d'où part une flèche, et vers quoi. `key` est la clé de la définition qu'elle écrit. */
export interface Outlet {
  /** `on_success`, `on_failure`, `if_true`, `if_false`, `default`, `cases.<valeur>`, `branches.<rang>`, `body`. */
  key: string
  tone: OutletTone
  /** Le texte qui vient de la définition (la valeur d'un `cases`, le rang d'une branche) ; `null` : c'est la teinte qui se traduit. */
  text: string | null
  target: string | null
}

const text = (v: unknown): string => (typeof v === 'string' ? v : '')
const object = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
const number = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** Le type d'une étape, ou `null` si l'éditeur ne le connaît pas. Une étape sans `type` est une action (comme xflow la lit). */
export function kindOf(raw: Record<string, unknown>): StepKind | null {
  const declared = raw.type === undefined ? 'action' : raw.type
  return typeof declared === 'string' && (STEP_KINDS as readonly string[]).includes(declared) ? (declared as StepKind) : null
}

// ── Lire et écrire une définition ────────────────────────────────────────────────────────────────────────────────

/**
 * Ce que l'éditeur montre d'un flux ; les clés qu'il ne montre pas restent dans `extra` / `raw`. `deployed_by` n'est NI montré ni
 * gardé : le serveur le réécrit depuis l'appelant, jamais depuis le corps (`xflow/src/main.py`), donc le renvoyer n'aurait aucun effet.
 */
const FLOW_KEYS = new Set(['name', 'description', 'trigger', 'steps', 'entry_step', 'deployed_by'])
const TRIGGER_KEYS = new Set(['type', 'event_name', 'cron'])

/** Le modèle d'une définition. Ne refuse jamais rien : une définition qu'xflow accepte s'ouvre toujours. */
export function readFlow(definition: unknown): FlowModel {
  const d = object(definition)
  const trigger = object(d.trigger)
  const kind = TRIGGER_KINDS.includes(text(trigger.type) as TriggerKind) ? (text(trigger.type) as TriggerKind) : 'manual'
  const steps = list(d.steps)
    .map((s) => object(s))
    .filter((raw) => STEP_ID.test(text(raw.id)))
    .map((raw) => ({ id: text(raw.id), kind: kindOf(raw), raw }))
  return {
    name: text(d.name),
    description: text(d.description),
    trigger: { kind, event: text(trigger.event_name), cron: text(trigger.cron), raw: without(trigger, TRIGGER_KEYS) },
    steps,
    entry: STEP_ID.test(text(d.entry_step)) ? text(d.entry_step) : null,
    extra: without(d, FLOW_KEYS),
  }
}

function without(source: Record<string, unknown>, keys: Set<string>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(source)) if (!keys.has(k)) out[k] = v
  return out
}

/** La définition à déployer, ou les fautes à montrer (en français, une par ligne). xflow juge de nouveau : ceci évite l'aller-retour. */
export function writeFlow(model: FlowModel): { definition: Record<string, unknown>; problems: [] } | { definition: null; problems: string[] } {
  const problems = [...flowProblems(model)]
  if (problems.length) return { definition: null, problems }
  const trigger: Record<string, unknown> = { ...model.trigger.raw, type: model.trigger.kind }
  if (model.trigger.kind === 'event') trigger.event_name = model.trigger.event.trim()
  if (model.trigger.kind === 'schedule') trigger.cron = model.trigger.cron.trim()
  const definition: Record<string, unknown> = {
    ...model.extra,
    name: model.name.trim(),
    trigger,
    steps: model.steps.map((s) => ({ ...s.raw, id: s.id, type: s.kind ?? s.raw.type ?? 'action' })),
  }
  if (model.description.trim()) definition.description = model.description.trim()
  else delete definition.description
  // `entry_step` n'est écrit que s'il n'est pas la première étape : la définition reste celle qu'un humain écrirait.
  if (model.entry && model.entry !== model.steps[0]?.id) definition.entry_step = model.entry
  else delete definition.entry_step
  return { definition, problems: [] }
}

/** Tout ce que l'éditeur sait refuser avant d'appeler xflow. */
export function flowProblems(model: FlowModel): string[] {
  const problems: string[] = []
  const f = t.flowBuilder.problems
  if (!FLOW_NAME.test(model.name.trim())) problems.push(f.name)
  if (model.trigger.kind === 'event' && !model.trigger.event.trim()) problems.push(f.event)
  if (model.trigger.kind === 'schedule' && model.trigger.cron.trim().split(/\s+/).filter(Boolean).length !== 5) problems.push(f.cron)
  if (!model.steps.length) problems.push(f.noStep)
  const seen = new Set<string>()
  const ids = new Set(model.steps.map((s) => s.id))
  for (const step of model.steps) {
    if (!STEP_ID.test(step.id)) problems.push(f.stepId(step.id))
    else if (seen.has(step.id)) problems.push(f.duplicate(step.id))
    seen.add(step.id)
    for (const missing of required(step)) problems.push(f.missing(step.id, missing))
    // Une flèche vers une étape disparue : l'exécution s'arrêterait là sans rien dire. Seule la vue JSON peut en laisser une.
    for (const o of outletsOf(step)) if (o.target && !ids.has(o.target)) problems.push(f.dangling(step.id, outletLabel(o), o.target))
  }
  if (model.entry && !ids.has(model.entry)) problems.push(f.entry(model.entry))
  return problems
}

/** Ce qu'un type d'étape exige et qui manque encore, nommé comme une phrase le dirait (« il manque le nom du plugin »). */
function required(step: FlowStep): string[] {
  const raw = step.raw
  const labels = t.flowBuilder.missing
  const out: string[] = []
  const need = (ok: boolean, label: string) => { if (!ok) out.push(label) }
  switch (step.kind) {
    case 'action': {
      const gesture = actionOf(raw)
      if (!gesture) {
        // Une action d'un autre plugin : l'écran ne la règle pas, il exige seulement qu'elle soit nommée.
        need(!!text(raw.plugin).trim(), labels.plugin)
        need(!!text(raw.action).trim(), labels.action)
        break
      }
      // Un geste du catalogue : ce qu'il demande doit être rempli, sinon l'étape échouerait à l'exécution.
      const spec = actionSpec(gesture)
      const payload = raw.payload && typeof raw.payload === 'object' ? (raw.payload as Record<string, unknown>) : {}
      const filled = (key: string) => !!String(payload[key] ?? '').trim()
      if (spec.asks.includes('doctype')) need(filled('doctype'), labels.doctype)
      if (spec.onExisting) need(filled('name'), labels.target)
      if (spec.asks.includes('assignee')) need(filled('assignee'), labels.assignee)
      if (spec.asks.includes('content')) need(filled('content'), labels.content)
      if (spec.asks.includes('transition')) need(filled('action'), labels.transition)
      break
    }
    case 'condition':
      need(!!text(object(raw.condition).left).trim(), labels.left)
      break
    case 'switch':
      need(!!text(raw.expression).trim(), labels.expression)
      break
    case 'foreach':
      need(!!text(raw.items).trim(), labels.items)
      break
    case 'transform':
      need(!!text(raw.query).trim(), labels.query)
      break
    case 'template':
      need(!!text(raw.template).trim(), labels.template)
      break
    case 'ai':
      need(!!text(raw.prompt).trim(), labels.prompt)
      break
    case 'parallel':
      need(list(raw.branches).some((b) => list(b).length > 0), labels.branches)
      break
    default:
      break // `wait` (un délai par défaut suffit) et un type inconnu : rien à exiger.
  }
  return out
}

// ── Les sorties d'une étape ──────────────────────────────────────────────────────────────────────────────────────

/** Où une étape peut envoyer la suite, dans l'ordre où le nœud les dessine. */
export function outletsOf(step: FlowStep): Outlet[] {
  const raw = step.raw
  const plain = (key: string, tone: OutletTone): Outlet => ({ key, tone, text: null, target: text(raw[key]) || null })
  switch (step.kind) {
    case 'condition':
      return [plain('if_true', 'yes'), plain('if_false', 'no')]
    case 'switch': {
      const cases = object(raw.cases)
      return [
        ...Object.keys(cases).map((value): Outlet => ({ key: `cases.${value}`, tone: 'case', text: value, target: text(cases[value]) || null })),
        plain('default', 'case'),
      ]
    }
    case 'parallel':
      return [
        ...list(raw.branches).map((branch, i): Outlet => ({ key: `branches.${i}`, tone: 'branch', text: String(i + 1), target: text(list(branch)[0]) || null })),
        plain('on_success', 'ok'),
        plain('on_failure', 'fail'),
      ]
    case 'foreach':
      return [
        { key: 'body', tone: 'loop', text: null, target: text(list(raw.steps)[0]) || null },
        plain('on_success', 'ok'),
        plain('on_failure', 'fail'),
      ]
    case 'transform':
    case 'template':
      return [plain('on_success', 'ok')]
    case 'action':
    case 'wait':
    case 'ai':
      return [plain('on_success', 'ok'), plain('on_failure', 'fail')]
    default:
      return [] // un type que l'éditeur ne connaît pas : on ne devine pas ses sorties, on garde son objet intact.
  }
}

/** L'étiquette d'une sortie : ce que la définition dit (valeur d'un `cases`), sinon le mot de sa teinte. */
export function outletLabel(outlet: Outlet): string {
  if (outlet.tone === 'branch') return t.flowBuilder.outlets.branch(outlet.text ?? '')
  if (outlet.tone === 'case') return outlet.text ?? t.flowBuilder.outlets.otherwise
  return t.flowBuilder.outlets[outlet.tone]
}

/** Poser (ou retirer, avec `null`) la cible d'une sortie, dans une COPIE de l'étape. */
function withTarget(raw: Record<string, unknown>, key: string, target: string | null): Record<string, unknown> {
  const out = { ...raw }
  const dot = key.indexOf('.')
  const head = dot < 0 ? key : key.slice(0, dot)
  const rest = dot < 0 ? '' : key.slice(dot + 1)
  if (head === 'cases') {
    const cases = { ...object(out.cases) }
    if (target) cases[rest] = target
    else delete cases[rest]
    out.cases = cases
    return out
  }
  if (head === 'branches') {
    const branches = list(out.branches).map((b) => list(b))
    const i = Number(rest)
    if (!Number.isInteger(i) || i < 0 || i >= branches.length) return out
    branches[i] = target ? [target] : []
    out.branches = branches
    return out
  }
  if (head === 'body') {
    out.steps = target ? [target] : []
    return out
  }
  if (target) out[head] = target
  else delete out[head]
  return out
}

/** Relier une sortie à une étape. Une sortie ne tient qu'UNE cible : relier de nouveau remplace. */
export function connect(model: FlowModel, source: string, key: string, target: string | null): FlowModel {
  if (source === TRIGGER_NODE) return { ...model, entry: target }
  if (target === source) return model // une étape ne s'enchaîne pas à elle-même : l'exécution tournerait en rond sur place
  return patchRaw(model, source, (raw) => withTarget(raw, key, target))
}

/** Détacher une flèche (la cible disparaît de la définition : xflow termine le flux là). */
export function disconnect(model: FlowModel, source: string, key: string): FlowModel {
  return connect(model, source, key, null)
}

// ── Modifier le graphe ───────────────────────────────────────────────────────────────────────────────────────────

function patchRaw(model: FlowModel, id: string, change: (raw: Record<string, unknown>) => Record<string, unknown>): FlowModel {
  return { ...model, steps: model.steps.map((s) => (s.id === id ? { ...s, raw: change(s.raw) } : s)) }
}

/** Changer ce que le panneau montre d'une étape. Une clé à `undefined` est RETIRÉE de la définition. */
export function patchStep(model: FlowModel, id: string, patch: Record<string, unknown>): FlowModel {
  return patchRaw(model, id, (raw) => {
    const out = { ...raw }
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete out[k]
      else out[k] = v
    }
    return out
  })
}

/** Un identifiant libre pour un nouveau nœud : `action_1`, `action_2`… */
export function freeId(model: FlowModel, kind: StepKind | null): string {
  const base = kind ?? 'etape'
  const taken = new Set(model.steps.map((s) => s.id))
  for (let i = 1; ; i++) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`
}

/** Le brouillon d'une étape neuve : ce qu'xflow attend de ce type, vide, prêt à remplir dans le panneau. */
export function newStep(kind: StepKind, id: string, position?: { x: number; y: number }): FlowStep {
  const raw: Record<string, unknown> = { id, type: kind }
  switch (kind) {
    case 'action':
      Object.assign(raw, { plugin: 'xcore-doctype', action: 'doc.save', payload: {} })
      break
    case 'condition':
      Object.assign(raw, { condition: { left: '', operator: '==', right: '' } })
      break
    case 'switch':
      Object.assign(raw, { expression: '', cases: {} })
      break
    case 'parallel':
      Object.assign(raw, { branches: [[], []], wait_all: true })
      break
    case 'foreach':
      Object.assign(raw, { items: '', steps: [], parallel: false })
      break
    case 'wait':
      Object.assign(raw, { delay_seconds: 60 })
      break
    case 'transform':
      Object.assign(raw, { query: '' })
      break
    case 'template':
      Object.assign(raw, { template: '', output_key: 'rendered' })
      break
    case 'ai':
      Object.assign(raw, { service: 'summarize', prompt: '' })
      break
  }
  if (position) Object.assign(raw, { _x: Math.round(position.x), _y: Math.round(position.y) })
  return { id, kind, raw }
}

/**
 * Ajouter une étape. Elle est RELIÉE à ce qui l'attend : la première sortie libre de l'étape choisie, ou le déclencheur quand le flux
 * est vide — sans cela xflow s'arrêterait avant elle, et c'est l'oubli que l'ancien éditeur faisait faire à tout le monde.
 */
export function addStep(model: FlowModel, kind: StepKind, position?: { x: number; y: number }, after?: string | null): { model: FlowModel; id: string } {
  const id = freeId(model, kind)
  const step = newStep(kind, id, position)
  let next: FlowModel = { ...model, steps: [...model.steps, step] }
  if (!model.steps.length) return { model: { ...next, entry: id }, id }
  const from = after && model.steps.some((s) => s.id === after) ? model.steps.find((s) => s.id === after)! : null
  const free = from ? outletsOf(from).find((o) => !o.target) : null
  if (from && free) next = connect(next, from.id, free.key, id)
  return { model: next, id }
}

/** Retirer une étape, et avec elle toute flèche qui la visait (sinon l'exécution s'arrêterait sur une cible absente). */
export function removeStep(model: FlowModel, id: string): FlowModel {
  const kept = model.steps.filter((s) => s.id !== id)
  let next: FlowModel = { ...model, steps: kept, entry: model.entry === id ? kept[0]?.id ?? null : model.entry }
  for (const step of kept) for (const o of outletsOf(step)) if (o.target === id) next = disconnect(next, step.id, o.key)
  return next
}

/** Renommer une étape : son identifiant ET toutes les flèches qui la citent (`entry_step` compris). */
export function renameStep(model: FlowModel, from: string, to: string): FlowModel {
  if (from === to) return model
  const renamed = model.steps.map((s) => (s.id === from ? { ...s, id: to, raw: { ...s.raw, id: to } } : s))
  let next: FlowModel = { ...model, steps: renamed, entry: model.entry === from ? to : model.entry }
  for (const step of renamed) for (const o of outletsOf(step)) if (o.target === from) next = connect(next, step.id, o.key, to)
  return next
}

/** Garder la position d'un nœud DANS la définition (`_x`/`_y`), déclencheur compris. */
export function moveNode(model: FlowModel, id: string, position: { x: number; y: number }): FlowModel {
  const _x = Math.round(position.x)
  const _y = Math.round(position.y)
  if (id === TRIGGER_NODE) return { ...model, trigger: { ...model.trigger, raw: { ...model.trigger.raw, _x, _y } } }
  return patchRaw(model, id, (raw) => ({ ...raw, _x, _y }))
}

/** Oublier toutes les positions : le graphe se replace tout seul (« Réorganiser »). */
export function forgetPositions(model: FlowModel): FlowModel {
  const drop = (raw: Record<string, unknown>) => without(raw, new Set(['_x', '_y']))
  return {
    ...model,
    trigger: { ...model.trigger, raw: drop(model.trigger.raw) },
    steps: model.steps.map((s) => ({ ...s, raw: drop(s.raw) })),
  }
}

/** Les branches d'un parallel : en ajouter une, en retirer une (le panneau). */
export function addBranch(model: FlowModel, id: string): FlowModel {
  return patchRaw(model, id, (raw) => ({ ...raw, branches: [...list(raw.branches).map((b) => list(b)), []] }))
}
export function removeBranch(model: FlowModel, id: string, index: number): FlowModel {
  return patchRaw(model, id, (raw) => ({ ...raw, branches: list(raw.branches).map((b) => list(b)).filter((_, i) => i !== index) }))
}

/** Les cas d'un switch : en nommer un, le renommer, le retirer (le panneau). */
export function addCase(model: FlowModel, id: string, value: string): FlowModel {
  return patchRaw(model, id, (raw) => (value && !(value in object(raw.cases)) ? { ...raw, cases: { ...object(raw.cases), [value]: '' } } : raw))
}
export function renameCase(model: FlowModel, id: string, from: string, to: string): FlowModel {
  return patchRaw(model, id, (raw) => {
    const cases = object(raw.cases)
    if (from === to || !(from in cases) || !to || to in cases) return raw
    // L'ordre des cas est celui où ils ont été nommés : on le garde (un objet renommé en place les mélangerait).
    const next: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(cases)) next[k === from ? to : k] = v
    return { ...raw, cases: next }
  })
}
export function removeCase(model: FlowModel, id: string, value: string): FlowModel {
  return patchRaw(model, id, (raw) => ({ ...raw, cases: without(object(raw.cases), new Set([value])) }))
}

// ── Le graphe : ce que Vue Flow reçoit ───────────────────────────────────────────────────────────────────────────

export const COLUMN_GAP = 300
export const ROW_GAP = 170

export interface TriggerNodeData {
  kind: TriggerKind
  /** L'événement, l'expression cron ou rien : ce que la carte dit sous le nom du déclencheur. */
  detail: string
}

export interface StepNodeData {
  step: FlowStep
  outlets: Outlet[]
  /** Aucune flèche n'y arrive et ce n'est pas le départ : l'étape ne s'exécutera jamais. */
  orphan: boolean
  /** Ce que le type d'étape exige et qui manque encore. */
  missing: string[]
}

export interface GraphNode {
  id: string
  type: 'trigger' | 'step'
  position: { x: number; y: number }
  data: TriggerNodeData | StepNodeData
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  sourceHandle: string
  targetHandle: 'in'
  data: { outlet: Outlet }
}

/** La colonne de chaque nœud : sa distance au départ. Ce qu'on n'atteint pas se range après le reste. */
export function ranks(model: FlowModel): Map<string, number> {
  const out = new Map<string, number>([[TRIGGER_NODE, 0]])
  const entry = model.entry ?? model.steps[0]?.id
  const byId = new Map(model.steps.map((s) => [s.id, s]))
  const queue: string[] = []
  if (entry && byId.has(entry)) {
    out.set(entry, 1)
    queue.push(entry)
  }
  while (queue.length) {
    const here = queue.shift()!
    for (const o of outletsOf(byId.get(here)!)) {
      if (!o.target || out.has(o.target) || !byId.has(o.target)) continue
      out.set(o.target, out.get(here)! + 1)
      queue.push(o.target)
    }
  }
  const last = Math.max(1, ...out.values()) + 1
  for (const s of model.steps) if (!out.has(s.id)) out.set(s.id, last)
  return out
}

/** Les nœuds et les flèches, prêts pour Vue Flow. Une position gardée dans la définition (`_x`/`_y`) gagne sur le placement calculé. */
export function graphOf(model: FlowModel): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const column = ranks(model)
  const entry = model.entry ?? model.steps[0]?.id ?? null
  const byColumn = new Map<number, string[]>()
  for (const id of [TRIGGER_NODE, ...model.steps.map((s) => s.id)]) {
    const col = column.get(id)!
    byColumn.set(col, [...(byColumn.get(col) ?? []), id])
  }
  const placed = (id: string, raw: Record<string, unknown>): { x: number; y: number } => {
    const x = number(raw._x)
    const y = number(raw._y)
    if (x !== null && y !== null) return { x, y }
    const col = column.get(id)!
    const siblings = byColumn.get(col)!
    return { x: col * COLUMN_GAP, y: (siblings.indexOf(id) - (siblings.length - 1) / 2) * ROW_GAP }
  }

  const reached = new Set<string>([entry ?? ''])
  for (const s of model.steps) for (const o of outletsOf(s)) if (o.target) reached.add(o.target)

  const nodes: GraphNode[] = [
    {
      id: TRIGGER_NODE,
      type: 'trigger',
      position: placed(TRIGGER_NODE, model.trigger.raw),
      data: { kind: model.trigger.kind, detail: model.trigger.kind === 'event' ? model.trigger.event : model.trigger.kind === 'schedule' ? model.trigger.cron : '' },
    },
    ...model.steps.map((step): GraphNode => ({
      id: step.id,
      type: 'step',
      position: placed(step.id, step.raw),
      data: { step, outlets: outletsOf(step), orphan: !reached.has(step.id), missing: required(step) },
    })),
  ]

  const known = new Set(model.steps.map((s) => s.id))
  const edges: GraphEdge[] = []
  if (entry && known.has(entry)) {
    edges.push({
      id: `${TRIGGER_NODE}::start`,
      source: TRIGGER_NODE,
      target: entry,
      sourceHandle: 'start',
      targetHandle: 'in',
      data: { outlet: { key: 'start', tone: 'ok', text: null, target: entry } },
    })
  }
  for (const step of model.steps) {
    for (const outlet of outletsOf(step)) {
      if (!outlet.target || !known.has(outlet.target)) continue
      edges.push({
        id: `${step.id}::${outlet.key}`,
        source: step.id,
        target: outlet.target,
        sourceHandle: outlet.key,
        targetHandle: 'in',
        data: { outlet },
      })
    }
  }
  return { nodes, edges }
}

/** La flèche d'une identité de Vue Flow (`<étape>::<sortie>`) : ce que le panneau détache. */
export function edgeParts(id: string): { source: string; key: string } | null {
  const cut = id.indexOf('::')
  return cut < 0 ? null : { source: id.slice(0, cut), key: id.slice(cut + 2) }
}

// ── Ce que le nœud écrit sous son titre ──────────────────────────────────────────────────────────────────────────

/** Une valeur telle qu'on la MONTRE : un jeton connu devient son libellé (« Nouvel état »), le reste demeure ce qu'il est. */
function shownValue(raw: unknown): string {
  const value = readValue(raw)
  if (value.kind === 'trigger') return t.flowBuilder.values.triggerKeys[value.key]
  // Le nœud n'a pas les libellés du type visé (le panneau, lui, les a) : il nomme l'étape et le champ repris.
  if (value.kind === 'step') return value.field ? `${value.step} › ${value.field}` : value.step
  if (value.kind === 'raw') return value.text
  if (value.value === null || value.value === undefined) return ''
  return typeof value.value === 'object' ? JSON.stringify(value.value) : String(value.value)
}

/** Une ligne qui dit ce que l'étape FAIT : l'action appelée, la condition évaluée, le délai attendu… */
export function stepSummary(step: FlowStep): string {
  const raw = step.raw
  const labels = t.flowBuilder.summary
  switch (step.kind) {
    case 'action': {
      // Le nœud dit le GESTE, pas l'appel : « Soumettre un document · Facture ». L'appel ne paraît que pour une action étrangère,
      // qu'on ne sait pas nommer autrement.
      const gesture = actionOf(raw)
      if (!gesture) {
        const name = `${text(raw.plugin)}.${text(raw.action)}`
        return name === '.' ? labels.noAction : name
      }
      const payload = object(raw.payload)
      const doctype = readValue(payload.doctype).kind === 'fixed' ? text(payload.doctype) : labels.fromTrigger
      return doctype ? `${t.flowBuilder.gestures[gesture].label} · ${doctype}` : t.flowBuilder.gestures[gesture].label
    }
    case 'condition': {
      const c = object(raw.condition)
      const op = text(c.operator) || '=='
      const said = t.flowBuilder.operators[op] ?? op
      return [shownValue(c.left), said, UNARY_OPERATORS.includes(op as Operator) ? '' : shownValue(c.right)].filter(Boolean).join(' ') || labels.noCondition
    }
    case 'switch':
      return shownValue(raw.expression) || labels.noExpression
    case 'parallel':
      return labels.branches(list(raw.branches).length)
    case 'foreach':
      return shownValue(raw.items) || labels.noItems
    case 'wait':
      return number(raw.delay_seconds) !== null ? labels.delay(number(raw.delay_seconds)!) : text(raw.wait_for_event) || labels.noDelay
    case 'transform':
      return text(raw.query) || labels.noQuery
    case 'template':
      return text(raw.template) || labels.noTemplate
    case 'ai':
      return `${text(raw.service)} — ${text(raw.prompt)}`
    default:
      return text(raw.type) || labels.unknown
  }
}

/** L'icône PrimeIcons d'un type d'étape (le carré de couleur du nœud, comme n8n). */
export const STEP_ICONS: Record<StepKind, string> = {
  action: 'pi-bolt',
  condition: 'pi-filter',
  switch: 'pi-sitemap',
  parallel: 'pi-share-alt',
  foreach: 'pi-replay',
  wait: 'pi-clock',
  transform: 'pi-code',
  template: 'pi-align-left',
  ai: 'pi-sparkles',
}

export const TRIGGER_ICONS: Record<TriggerKind, string> = {
  manual: 'pi-play',
  event: 'pi-bell',
  schedule: 'pi-calendar-clock',
  webhook: 'pi-link',
}
