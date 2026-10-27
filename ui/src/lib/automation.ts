/**
 * Le modèle d'une automatisation xflow, tel que l'éditeur du Desk la manipule : la DÉFINITION de xflow elle-même
 * (`app/xflows/src/schemas/workflow.py`), jamais une copie à reconvertir. Un nœud est un step, un lien est un champ du step
 * (`on_success`, `if_true`, `cases.x`, `branches[i][0]`, `steps[0]`…), et la position d'une carte voyage dans le step (`_x`, `_y`,
 * que xflow garde tels quels). Tout est pur : chaque fonction rend une NOUVELLE définition.
 *
 * Les nœuds « Document » ne sont pas un type de xflow : ce sont des steps `action` vers `xcore-doctype` (`doc.get`, `doc.patch`…),
 * que l'éditeur reconnaît et présente avec des champs de document. Ils agissent au nom de qui déclenche, ou de qui a déployé quand
 * personne ne déclenche (événement, webhook, planification) ; le moteur juge leurs droits comme ceux de n'importe qui.
 */

export type StepType = 'action' | 'condition' | 'switch' | 'foreach' | 'parallel' | 'wait' | 'transform' | 'template' | 'ai'
export type TriggerType = 'manual' | 'event' | 'webhook' | 'schedule'

export interface FlowTrigger {
  type: TriggerType
  event_name?: string | null
  cron?: string | null
  interval_seconds?: number | null
  initial_payload?: Record<string, unknown>
  _x?: number
  _y?: number
  [key: string]: unknown
}

export interface FlowStep {
  id: string
  type: StepType
  description?: string | null
  _x?: number
  _y?: number
  [key: string]: unknown
}

/** Une étape avant qu'elle ne soit posée : son identifiant n'est qu'une suggestion (`addStep` le rend unique). */
export interface NewStep {
  type: StepType
  id?: string
  [key: string]: unknown
}

export interface FlowDefinition {
  name: string
  version?: string
  description?: string | null
  trigger: FlowTrigger
  steps: FlowStep[]
  entry_step?: string | null
  tags?: string[]
  /** Posé par xflow : qui a déployé (ses droits du moment servent les déclenchements sans humain). Jamais renvoyé par le client. */
  deployed_by?: string | null
  [key: string]: unknown
}

/** Le nœud du déclencheur : il n'est pas un step, son unique sortie choisit le premier step (`entry_step`). */
export const TRIGGER_ID = '__trigger'
export const ENGINE = 'xcore-doctype'
const ID_RE = /^[a-z][a-z0-9_]{0,63}$/
const NAME_RE = /^[a-z][a-z0-9_]{0,99}$/

// ── Sorties d'un nœud ────────────────────────────────────────────────────────────────────────────────────────────────────────────

export type PortTone = 'ok' | 'bad' | 'neutral'
export interface Port {
  id: string
  label: string
  tone: PortTone
}

const NEXT: Port = { id: 'on_success', label: 'ensuite', tone: 'ok' }
const FAIL: Port = { id: 'on_failure', label: 'en cas d’erreur', tone: 'bad' }

/** Les sorties d'un step, dans l'ordre où la carte les montre. Un `parallel` offre une sortie de plus que ses branches (en ajouter une). */
export function portsOf(step: FlowStep): Port[] {
  switch (step.type) {
    case 'action':
    case 'ai':
    case 'wait':
      return [NEXT, FAIL]
    case 'condition':
      return [{ id: 'if_true', label: 'vrai', tone: 'ok' }, { id: 'if_false', label: 'faux', tone: 'bad' }]
    case 'switch':
      return [...Object.keys(cases(step)).map((k) => ({ id: `case:${k}`, label: k, tone: 'neutral' as const })), { id: 'default', label: 'sinon', tone: 'neutral' }]
    case 'parallel': {
      const n = branches(step).length
      return [...Array.from({ length: n + 1 }, (_, i) => ({ id: `branch:${i}`, label: i < n ? `branche ${i + 1}` : '+ branche', tone: 'neutral' as const })), NEXT, FAIL]
    }
    case 'foreach':
      return [{ id: 'body', label: 'pour chaque élément', tone: 'neutral' }, NEXT, FAIL]
    default:
      return [NEXT]
  }
}

function cases(step: FlowStep): Record<string, string> {
  return step.cases && typeof step.cases === 'object' ? (step.cases as Record<string, string>) : {}
}
function branches(step: FlowStep): string[][] {
  return Array.isArray(step.branches) ? (step.branches as string[][]) : []
}

/** Où mène la sortie `port` de `step` (null : nulle part). */
export function targetOf(step: FlowStep, port: string): string | null {
  if (port.startsWith('case:')) return cases(step)[port.slice(5)] ?? null
  if (port.startsWith('branch:')) return branches(step)[Number(port.slice(7))]?.[0] ?? null
  if (port === 'body') return Array.isArray(step.steps) ? ((step.steps as string[])[0] ?? null) : null
  const v = step[port]
  return typeof v === 'string' && v ? v : null
}

function withTarget(step: FlowStep, port: string, target: string | null): FlowStep {
  const out: FlowStep = { ...step }
  if (port.startsWith('case:')) {
    out.cases = { ...cases(step), [port.slice(5)]: target ?? '' }
  } else if (port.startsWith('branch:')) {
    const i = Number(port.slice(7))
    const list = branches(step).map((b) => [...b])
    if (target === null) list.splice(i, 1)
    else if (i >= list.length) list.push([target])
    else list[i] = [target]
    out.branches = list
  } else if (port === 'body') {
    if (target === null) delete out.steps
    else out.steps = [target]
  } else if (target === null) {
    delete out[port]
  } else {
    out[port] = target
  }
  return out
}

function mapStep(def: FlowDefinition, id: string, f: (s: FlowStep) => FlowStep): FlowDefinition {
  return { ...def, steps: def.steps.map((s) => (s.id === id ? f(s) : s)) }
}

/** Relier une sortie à un step. Depuis le déclencheur : choisir le premier step. Un lien vers soi-même est refusé (boucle immédiate). */
export function connect(def: FlowDefinition, source: string, port: string, target: string): FlowDefinition {
  if (source === target || !def.steps.some((s) => s.id === target)) return def
  if (source === TRIGGER_ID) return { ...def, entry_step: target }
  return mapStep(def, source, (s) => withTarget(s, port, target))
}

export function disconnect(def: FlowDefinition, source: string, port: string): FlowDefinition {
  if (source === TRIGGER_ID) return { ...def, entry_step: null }
  return mapStep(def, source, (s) => withTarget(s, port, null))
}

export function firstStep(def: FlowDefinition): string | null {
  if (def.entry_step && def.steps.some((s) => s.id === def.entry_step)) return def.entry_step
  return def.steps[0]?.id ?? null
}

export interface FlowEdge {
  id: string
  source: string
  port: string
  target: string
  label: string
  tone: PortTone
}

/** Tous les liens de la définition, déclencheur compris (vers le premier step). Un lien vers un step disparu n'est pas dessiné. */
export function edgesOf(def: FlowDefinition): FlowEdge[] {
  const ids = new Set(def.steps.map((s) => s.id))
  const out: FlowEdge[] = []
  const start = firstStep(def)
  if (start) out.push({ id: `${TRIGGER_ID}>start`, source: TRIGGER_ID, port: 'start', target: start, label: '', tone: 'neutral' })
  for (const step of def.steps) {
    for (const port of portsOf(step)) {
      const target = targetOf(step, port.id)
      if (target && ids.has(target)) out.push({ id: `${step.id}>${port.id}`, source: step.id, port: port.id, target, label: port.label, tone: port.tone })
    }
  }
  return out
}

// ── Steps ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

export function uniqueId(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  const clean = base.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'etape'
  const root = (/^[a-z]/.test(clean) ? clean : `e_${clean}`).slice(0, 56)
  if (!used.has(root)) return root
  for (let i = 2; ; i++) if (!used.has(`${root}_${i}`)) return `${root}_${i}`
}

/** Ce qu'un nouveau step de ce type porte d'office : de quoi être valide pour xflow dès qu'il est posé. */
export function blankStep(type: StepType, id: string): FlowStep {
  switch (type) {
    case 'action':
      return { id, type, plugin: '', action: '', payload: {} }
    case 'condition':
      return { id, type, condition: { left: '', operator: '==', right: '' } }
    case 'switch':
      return { id, type, expression: '', cases: {} }
    case 'foreach':
      return { id, type, items: '' }
    case 'parallel':
      return { id, type, branches: [] }
    case 'wait':
      return { id, type, delay_seconds: 60 }
    case 'transform':
      return { id, type, query: '' }
    case 'template':
      return { id, type, template: '', output_key: 'texte' }
    case 'ai':
      return { id, type, service: 'summarize', prompt: '' }
  }
}

export function addStep(def: FlowDefinition, step: NewStep, at?: { x: number; y: number }): { def: FlowDefinition; id: string } {
  const id = uniqueId(step.id ?? step.type, def.steps.map((s) => s.id))
  const placed: FlowStep = { ...step, id, ...(at ? { _x: Math.round(at.x), _y: Math.round(at.y) } : {}) } as FlowStep
  return { def: { ...def, steps: [...def.steps, placed] }, id }
}

/** Retirer un step : plus rien ne mène à lui (les sorties qui le visaient se vident, le premier step passe au suivant). */
export function removeStep(def: FlowDefinition, id: string): FlowDefinition {
  let next: FlowDefinition = { ...def, steps: def.steps.filter((s) => s.id !== id) }
  for (const step of next.steps) {
    for (const port of portsOf(step)) if (targetOf(step, port.id) === id) next = mapStep(next, step.id, (s) => withTarget(s, port.id, null))
    if (step.type === 'parallel') next = mapStep(next, step.id, (s) => ({ ...s, branches: branches(s).filter((b) => b.length) }))
  }
  if (next.entry_step === id) next = { ...next, entry_step: null }
  return next
}

/**
 * Renommer un step PARTOUT : les sorties qui le visent, le premier step, et les gabarits qui lisent son résultat
 * (`{{ steps.<ancien>.result… }}`). Refusé (null) pour un identifiant invalide ou déjà pris.
 */
export function renameStep(def: FlowDefinition, from: string, to: string): FlowDefinition | null {
  if (from === to || !ID_RE.test(to) || def.steps.some((s) => s.id === to)) return null
  const ref = new RegExp(`(\\{\\{\\s*steps\\.)${from}(?=[.\\s}])`, 'g')
  const rewrite = (v: unknown): unknown =>
    typeof v === 'string' ? v.replace(ref, `$1${to}`)
    : Array.isArray(v) ? v.map(rewrite)
    : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, rewrite(x)]))
    : v
  const steps = def.steps.map((s) => {
    let out = (s.id === from ? { ...s, id: to } : s) as FlowStep
    for (const port of portsOf(out)) if (targetOf(out, port.id) === from) out = withTarget(out, port.id, to)
    return rewrite(out) as FlowStep
  })
  return { ...def, steps, entry_step: def.entry_step === from ? to : def.entry_step }
}

/** Poser des propriétés. `undefined` retire une clé (le JSON ne garde que ce qui compte). L'identifiant passe par `renameStep`. */
export function patchStep(def: FlowDefinition, id: string, patch: Record<string, unknown>): FlowDefinition {
  return mapStep(def, id, (s) => {
    const out: FlowStep = { ...s }
    for (const [k, v] of Object.entries(patch)) {
      if (k === 'id' || k === 'type') continue
      if (v === undefined) delete out[k]
      else out[k] = v
    }
    return out
  })
}

export function moveNode(def: FlowDefinition, id: string, x: number, y: number): FlowDefinition {
  const at = { _x: Math.round(x), _y: Math.round(y) }
  if (id === TRIGGER_ID) return { ...def, trigger: { ...def.trigger, ...at } }
  return mapStep(def, id, (s) => ({ ...s, ...at }))
}

/** Une case de `switch` : ajoutée vide, renommée en gardant sa cible, retirée avec son lien. */
export function setCase(def: FlowDefinition, id: string, from: string | null, to: string | null): FlowDefinition {
  return mapStep(def, id, (s) => {
    const entries = Object.entries(cases(s))
    if (from === null && to !== null && !entries.some(([k]) => k === to)) return { ...s, cases: { ...cases(s), [to]: '' } }
    if (from !== null && to === null) return { ...s, cases: Object.fromEntries(entries.filter(([k]) => k !== from)) }
    if (from !== null && to !== null && from !== to && !entries.some(([k]) => k === to)) {
      return { ...s, cases: Object.fromEntries(entries.map(([k, v]) => [k === from ? to : k, v])) }
    }
    return s
  })
}

// ── Disposition ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

const COL = 280
const ROW = 150

/**
 * La position de chaque carte : celle que la définition porte (`_x`, `_y`), sinon une colonne par distance au déclencheur et une
 * ligne par ordre d'arrivée dans la colonne. Un step injoignable va dans une dernière colonne, pour qu'on le voie et le relie.
 */
export function positions(def: FlowDefinition): Record<string, { x: number; y: number }> {
  const depth = new Map<string, number>()
  const start = firstStep(def)
  const queue: string[] = start ? [start] : []
  if (start) depth.set(start, 1)
  const byId = new Map(def.steps.map((s) => [s.id, s]))
  while (queue.length) {
    const id = queue.shift()!
    const step = byId.get(id)
    if (!step) continue
    for (const port of portsOf(step)) {
      const t = targetOf(step, port.id)
      if (t && byId.has(t) && !depth.has(t)) {
        depth.set(t, depth.get(id)! + 1)
        queue.push(t)
      }
    }
  }
  const last = Math.max(0, ...depth.values()) + 1
  const rows = new Map<number, number>()
  const place = (col: number) => {
    const r = rows.get(col) ?? 0
    rows.set(col, r + 1)
    return { x: col * COL, y: r * ROW }
  }
  const out: Record<string, { x: number; y: number }> = {}
  const t = def.trigger
  out[TRIGGER_ID] = typeof t._x === 'number' && typeof t._y === 'number' ? { x: t._x, y: t._y } : place(0)
  for (const s of def.steps) {
    out[s.id] = typeof s._x === 'number' && typeof s._y === 'number' ? { x: s._x, y: s._y } : place(depth.get(s.id) ?? last)
  }
  return out
}

/** Oublier les positions posées à la main : la disposition automatique reprend. */
export function arrange(def: FlowDefinition): FlowDefinition {
  const strip = <T extends Record<string, unknown>>(o: T): T => {
    const { _x: _a, _y: _b, ...rest } = o
    return rest as T
  }
  return { ...def, trigger: strip(def.trigger), steps: def.steps.map((s) => strip(s)) }
}

// ── Déclencheur ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

/** Les événements que le moteur émet pour un type de document (sans le contenu du document : il se lit par un step `doc.get`). */
export const DOC_EVENTS = ['save', 'submit', 'cancel', 'transition', 'delete', 'rename'] as const
export type DocEvent = (typeof DOC_EVENTS)[number]

export function docEventName(doctype: string, event: DocEvent): string {
  return `doctype.${doctype}.after_${event}`
}

export function parseDocEvent(name: string | null | undefined): { doctype: string; event: DocEvent } | null {
  const m = /^doctype\.(.+)\.after_([a-z]+)$/.exec(name ?? '')
  return m && (DOC_EVENTS as readonly string[]).includes(m[2]!) ? { doctype: m[1]!, event: m[2] as DocEvent } : null
}

/** Ce que porte `trigger` selon le déclencheur : les clés du payload de l'événement du moteur (`document.py`, `_emit`). */
export function triggerFields(trigger: FlowTrigger): string[] {
  const doc = parseDocEvent(trigger.event_name)
  if (trigger.type !== 'event' || !doc) return []
  const base = ['doctype', 'name', 'tenant_id', 'user']
  switch (doc.event) {
    case 'save':
      return [...base, 'version', 'created']
    case 'submit':
    case 'cancel':
      return [...base, 'version', 'docstatus']
    case 'transition':
      return [...base, 'version', 'action', 'from', 'to', 'docstatus']
    case 'rename':
      return [...base, 'old_name']
    default:
      return base
  }
}

// ── Nœuds « Document » ───────────────────────────────────────────────────────────────────────────────────────────────────────────

export type DocAction = 'doc.get' | 'doc.list' | 'doc.save' | 'doc.patch' | 'doc.transition' | 'doc.submit' | 'doc.cancel' | 'doc.delete' | 'doc.comment.add' | 'doc.assign'
export const DOC_ACTIONS: DocAction[] = ['doc.get', 'doc.list', 'doc.save', 'doc.patch', 'doc.transition', 'doc.submit', 'doc.cancel', 'doc.comment.add', 'doc.assign', 'doc.delete']

/** Ce qu'un nœud Document envoie d'office : le document de l'événement quand il y en a un (`{{ trigger.name }}`). */
export function docPayload(action: DocAction, doctype: string): Record<string, unknown> {
  const named = { doctype, name: '{{ trigger.name }}' }
  switch (action) {
    case 'doc.list':
      return { doctype, filters: {}, limit: 20 }
    case 'doc.save':
      return { doctype, data: {} }
    case 'doc.patch':
      return { ...named, data: {} }
    case 'doc.transition':
      return { ...named, action: '', version: '{{ trigger.version }}' }
    case 'doc.submit':
    case 'doc.cancel':
      return { ...named, version: '{{ trigger.version }}' }
    case 'doc.comment.add':
      return { ...named, content: '' }
    case 'doc.assign':
      return { ...named, assignee: '', description: '' }
    default:
      return named
  }
}

export function docStep(action: DocAction, doctype: string): NewStep {
  const base = { 'doc.get': 'lire', 'doc.list': 'lister', 'doc.save': 'creer', 'doc.patch': 'modifier', 'doc.transition': 'faire_avancer', 'doc.submit': 'soumettre', 'doc.cancel': 'annuler', 'doc.delete': 'supprimer', 'doc.comment.add': 'commenter', 'doc.assign': 'assigner' }[action]
  return { id: base, type: 'action', plugin: ENGINE, action, payload: docPayload(action, doctype) }
}

/** Le nœud Document que ce step est, ou null (un step `action` vers un autre plugin, ou une autre action du moteur). */
export function docActionOf(step: FlowStep): DocAction | null {
  return step.type === 'action' && step.plugin === ENGINE && (DOC_ACTIONS as string[]).includes(step.action as string) ? (step.action as DocAction) : null
}

export function payloadOf(step: FlowStep): Record<string, unknown> {
  return step.payload && typeof step.payload === 'object' && !Array.isArray(step.payload) ? (step.payload as Record<string, unknown>) : {}
}

/** Changer une clé du payload ; `undefined` la retire. */
export function patchPayload(def: FlowDefinition, id: string, patch: Record<string, unknown>): FlowDefinition {
  return mapStep(def, id, (s) => {
    const payload = { ...payloadOf(s) }
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined) delete payload[k]
      else payload[k] = v
    }
    return { ...s, payload }
  })
}

// ── Variables à insérer ──────────────────────────────────────────────────────────────────────────────────────────────────────────

export interface Variable {
  token: string
  label: string
  group: string
}

/**
 * Ce qu'un step peut lire : les clés du déclencheur, et ce qu'ont rendu les AUTRES steps. Pour un nœud Document qui rend un
 * document, ses champs (`result.doc.data.<champ>`), lus dans `/meta` (`fieldsOf`) ; pour les autres, leur résultat entier.
 */
export function variablesFor(def: FlowDefinition, stepId: string | null, fieldsOf: (doctype: string) => string[] | null): Variable[] {
  const out: Variable[] = triggerFields(def.trigger).map((k) => ({ token: `{{ trigger.${k} }}`, label: k, group: 'trigger' }))
  if (!out.length) out.push({ token: '{{ trigger }}', label: 'trigger', group: 'trigger' })
  for (const s of def.steps) {
    if (s.id === stepId) continue
    const action = docActionOf(s)
    const doctype = payloadOf(s).doctype
    if (action && ['doc.get', 'doc.save', 'doc.patch', 'doc.transition', 'doc.submit', 'doc.cancel'].includes(action) && typeof doctype === 'string') {
      const base = `steps.${s.id}.result.doc`
      out.push({ token: `{{ ${base}.name }}`, label: 'name', group: s.id }, { token: `{{ ${base}.version }}`, label: 'version', group: s.id })
      for (const f of fieldsOf(doctype) ?? []) out.push({ token: `{{ ${base}.data.${f} }}`, label: f, group: s.id })
    } else if (action === 'doc.list') {
      out.push({ token: `{{ steps.${s.id}.result.items }}`, label: 'items', group: s.id })
    } else if (s.type === 'template') {
      out.push({ token: `{{ steps.${s.id}.result.${String(s.output_key || 'rendered')} }}`, label: String(s.output_key || 'rendered'), group: s.id })
    } else {
      out.push({ token: `{{ steps.${s.id}.result }}`, label: 'result', group: s.id })
    }
  }
  if (def.steps.some((s) => s.type === 'foreach')) out.push({ token: '{{ loop_item }}', label: 'loop_item', group: 'loop' })
  return out
}

// ── Ce qui empêche d'enregistrer ─────────────────────────────────────────────────────────────────────────────────────────────────

export type Problem =
  | { code: 'name' }
  | { code: 'no_steps' }
  | { code: 'event' }
  | { code: 'schedule' }
  | { code: 'step_id'; step: string }
  | { code: 'missing'; step: string; field: string }
  | { code: 'unreachable'; step: string }

/** Ce que xflow refuserait, ou qui ne tournerait jamais, dit AVANT d'enregistrer (le serveur reste juge). */
export function problems(def: FlowDefinition): Problem[] {
  const out: Problem[] = []
  if (!NAME_RE.test(def.name)) out.push({ code: 'name' })
  if (!def.steps.length) out.push({ code: 'no_steps' })
  if (def.trigger.type === 'event' && !def.trigger.event_name) out.push({ code: 'event' })
  if (def.trigger.type === 'schedule' && !def.trigger.cron && !def.trigger.interval_seconds) out.push({ code: 'schedule' })
  const need = (s: FlowStep, field: string, ok: boolean) => ok || out.push({ code: 'missing', step: s.id, field })
  for (const s of def.steps) {
    if (!ID_RE.test(s.id)) out.push({ code: 'step_id', step: s.id })
    const str = (k: string) => typeof s[k] === 'string' && (s[k] as string).trim() !== ''
    if (s.type === 'action') {
      need(s, 'plugin', str('plugin'))
      need(s, 'action', str('action'))
      const doc = docActionOf(s)
      const p = payloadOf(s)
      if (doc) need(s, 'doctype', typeof p.doctype === 'string' && p.doctype !== '')
      if (doc === 'doc.transition') need(s, 'transition', typeof p.action === 'string' && p.action !== '')
    }
    if (s.type === 'condition') need(s, 'left', typeof (s.condition as { left?: string } | undefined)?.left === 'string' && !!(s.condition as { left: string }).left.trim())
    if (s.type === 'switch') need(s, 'expression', str('expression'))
    if (s.type === 'foreach') need(s, 'items', str('items'))
    if (s.type === 'transform') need(s, 'query', str('query'))
    if (s.type === 'template') need(s, 'template', str('template'))
    if (s.type === 'ai') need(s, 'prompt', str('prompt'))
  }
  const reached = new Set(edgesOf(def).map((e) => e.target))
  const start = firstStep(def)
  for (const s of def.steps) if (s.id !== start && !reached.has(s.id)) out.push({ code: 'unreachable', step: s.id })
  return out
}

/** Une automatisation neuve : manuelle, une étape vide n'existe pas (xflow exige au moins un step) — la page en pose une à la création. */
export function emptyFlow(name = ''): FlowDefinition {
  return { name, version: '1.0.0', description: '', trigger: { type: 'manual' }, steps: [] }
}

/** Ce que xflow rend et qu'on ne renvoie pas : `deployed_by` est posé par le serveur (qui déploie signe), jamais par le client. */
export function forDeploy(def: FlowDefinition): FlowDefinition {
  const { deployed_by: _ignored, ...rest } = def
  return rest as FlowDefinition
}

// ── Présentation (icônes PrimeIcons, vérifiées dans primeicons 7) ────────────────────────────────────────────────────────────────

const DOC_ICONS: Record<DocAction, string> = {
  'doc.get': 'pi-file', 'doc.list': 'pi-search', 'doc.save': 'pi-plus', 'doc.patch': 'pi-pencil', 'doc.transition': 'pi-forward',
  'doc.submit': 'pi-send', 'doc.cancel': 'pi-ban', 'doc.delete': 'pi-trash', 'doc.comment.add': 'pi-comment', 'doc.assign': 'pi-user-plus',
}
const TYPE_ICONS: Record<StepType, string> = {
  action: 'pi-bolt', condition: 'pi-question-circle', switch: 'pi-directions', foreach: 'pi-replay', parallel: 'pi-share-alt', wait: 'pi-clock',
  transform: 'pi-sliders-h', template: 'pi-align-left', ai: 'pi-sparkles',
}
const TRIGGER_ICONS: Record<TriggerType, string> = { manual: 'pi-play', event: 'pi-bolt', webhook: 'pi-globe', schedule: 'pi-calendar' }

export function stepIcon(step: FlowStep): string {
  const doc = docActionOf(step)
  return `pi ${doc ? DOC_ICONS[doc] : TYPE_ICONS[step.type] ?? 'pi-bolt'}`
}

export function triggerIcon(trigger: FlowTrigger): string {
  return `pi ${TRIGGER_ICONS[trigger.type] ?? 'pi-play'}`
}

// ── Valeurs tapées dans un nœud Document ─────────────────────────────────────────────────────────────────────────────────────────

const WHOLE_TEMPLATE = /^\{\{\s*[\w.]+\s*\}\}$/

/**
 * Le texte tapé pour un champ devient la valeur que le moteur attend (il refuse `"3"` pour un entier, `"true"` pour un booléen). Un
 * gabarit ENTIER (`{{ steps.lire.result.doc.data.jours }}`) reste tel quel : xflow le remplace par la valeur elle-même, avec son type.
 * Un Decimal reste une chaîne exacte ; ce qui ne se lit pas comme le type voulu reste du texte, le moteur dira pourquoi.
 */
export function typedValue(fieldtype: string, text: string): unknown {
  const v = text.trim()
  if (WHOLE_TEMPLATE.test(v)) return v
  if (['Int', 'Rating', 'Duration'].includes(fieldtype) && /^-?[0-9]+$/.test(v)) return Number(v)
  if (['Number', 'Percent'].includes(fieldtype) && /^-?([0-9]+\.?[0-9]*|\.[0-9]+)$/.test(v)) return Number(v)
  if (fieldtype === 'Boolean' && (v === 'true' || v === 'false')) return v === 'true'
  return text
}

/** L'inverse, pour réafficher une valeur dans son champ de saisie. */
export function shownValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}
