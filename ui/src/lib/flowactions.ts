/**
 * Ce qu'une automatisation peut FAIRE, dit en français, et son déclencheur dit en français aussi.
 *
 * Une étape « action » d'xflow est un appel IPC `{plugin, action, payload}`. Personne ne doit avoir à connaître `doc.save` ni à
 * écrire du JSON : l'écran propose une LISTE fermée de gestes métier (créer un document, le soumettre, assigner une tâche…) et ce
 * module traduit le geste en appel, et l'appel en geste. Les actions sont celles que le moteur expose vraiment (`plugin.py`), avec
 * la charge exacte que chacune attend — c'est ici, et nulle part ailleurs, qu'on en tient la liste.
 *
 * De même pour le déclencheur : un événement se choisit en deux listes (quel type de document, à quel moment) au lieu de taper
 * `doctype.Sales Invoice.after_submit`, et une planification se choisit en « chaque jour à 9 h » au lieu d'une expression cron.
 *
 * Tout est pur et testé à part (`tests/flowactions.spec.ts`).
 */

/** Le plugin du moteur : toutes les actions de ce catalogue sont les siennes. */
export const ENGINE = 'xcore-doctype'

export type ActionId = 'create' | 'update' | 'submit' | 'cancel' | 'transition' | 'delete' | 'assign' | 'comment' | 'read'

/** Ce qu'une action demande à l'écran de remplir, dans l'ordre où le panneau le pose. */
export type Ask = 'doctype' | 'target' | 'data' | 'transition' | 'assignee' | 'description' | 'due_date' | 'content'

export interface ActionSpec {
  id: ActionId
  /** Le nom de l'action IPC du moteur (`plugin.py`). */
  action: string
  asks: readonly Ask[]
  /** L'action travaille sur un document DÉJÀ là (elle a besoin de son nom et de sa version). */
  onExisting: boolean
}

/**
 * Le catalogue. `doc.save` sans `name` crée, avec `name` modifie (`plugin.py` : c'est la même action) — d'où deux gestes pour une
 * seule action, distingués par la présence du nom.
 */
export const ACTIONS: readonly ActionSpec[] = [
  { id: 'create', action: 'doc.save', asks: ['doctype', 'data'], onExisting: false },
  { id: 'update', action: 'doc.save', asks: ['doctype', 'target', 'data'], onExisting: true },
  { id: 'submit', action: 'doc.submit', asks: ['doctype', 'target'], onExisting: true },
  { id: 'cancel', action: 'doc.cancel', asks: ['doctype', 'target'], onExisting: true },
  { id: 'transition', action: 'doc.transition', asks: ['doctype', 'target', 'transition'], onExisting: true },
  { id: 'delete', action: 'doc.delete', asks: ['doctype', 'target'], onExisting: true },
  { id: 'assign', action: 'doc.assign', asks: ['doctype', 'target', 'assignee', 'description', 'due_date'], onExisting: true },
  { id: 'comment', action: 'doc.comment.add', asks: ['doctype', 'target', 'content'], onExisting: true },
  { id: 'read', action: 'doc.get', asks: ['doctype', 'target'], onExisting: true },
]

export const actionSpec = (id: ActionId): ActionSpec => ACTIONS.find((a) => a.id === id)!

const text = (v: unknown): string => (typeof v === 'string' ? v : '')
const object = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {})

/**
 * Le geste d'une étape « action », ou `null` quand elle appelle autre chose que le moteur (un plugin métier, un outil maison) :
 * l'écran la montre alors telle quelle, sans prétendre la régler.
 */
export function actionOf(raw: Record<string, unknown>): ActionId | null {
  if (text(raw.plugin) !== ENGINE) return null
  const call = text(raw.action)
  if (call === 'doc.save') return object(raw.payload).name === undefined ? 'create' : 'update'
  const found = ACTIONS.find((a) => a.action === call && a.id !== 'create' && a.id !== 'update')
  return found ? found.id : null
}

/** La valeur par défaut d'une cible : le document qui a déclenché le flux — le cas courant, et plus rien à saisir. */
export const TRIGGER_DOCTYPE = '{{ trigger.doctype }}'
export const TRIGGER_NAME = '{{ trigger.name }}'
export const TRIGGER_VERSION = '{{ trigger.version }}'

/**
 * Changer le geste d'une étape : l'appel et la charge sont refaits pour le nouveau geste. On GARDE ce qui a encore un sens (le type
 * de document, la cible, les valeurs saisies) et on jette ce que le nouveau geste n'a pas — sinon xflow recevrait des clés qu'il
 * refuse. Les clés hors charge utile (retry, description de l'étape…) ne sont pas touchées.
 */
export function withAction(raw: Record<string, unknown>, id: ActionId): Record<string, unknown> {
  const spec = actionSpec(id)
  const before = object(raw.payload)
  const payload: Record<string, unknown> = {}
  if (spec.asks.includes('doctype')) payload.doctype = before.doctype ?? (id === 'create' ? '' : TRIGGER_DOCTYPE)
  if (spec.onExisting) {
    payload.name = before.name ?? TRIGGER_NAME
    // `doc.save` exige la version du document qu'il modifie (verrou optimiste, D15) ; `submit`/`cancel`/`transition` l'acceptent.
    if (id !== 'delete' && id !== 'assign' && id !== 'comment' && id !== 'read') payload.version = before.version ?? TRIGGER_VERSION
  }
  if (spec.asks.includes('data')) payload.data = object(before.data)
  if (spec.asks.includes('transition')) payload.action = before.action ?? ''
  if (spec.asks.includes('assignee')) payload.assignee = before.assignee ?? ''
  if (spec.asks.includes('description') && before.description !== undefined) payload.description = before.description
  if (spec.asks.includes('due_date') && before.due_date !== undefined) payload.due_date = before.due_date
  if (spec.asks.includes('content')) payload.content = before.content ?? ''
  return { ...raw, plugin: ENGINE, action: spec.action, payload }
}

// ── Le déclencheur ───────────────────────────────────────────────────────────────────────────────────────────────

/** Les moments qu'un document émet (la liste fermée de l'éditeur, comme les gestes ci-dessus). */
export const MOMENTS = ['after_save', 'after_submit', 'after_cancel', 'after_delete', 'after_transition', 'after_rename'] as const
export type Moment = (typeof MOMENTS)[number]

export function writeEvent(doctype: string, moment: Moment): string {
  return `doctype.${doctype}.${moment}`
}

/** Le type et le moment d'un nom d'événement, ou `null` si ce n'est pas un événement de document (un événement d'un autre plugin). */
export function readEvent(name: string): { doctype: string; moment: Moment } | null {
  if (!name.startsWith('doctype.')) return null
  const cut = name.lastIndexOf('.')
  const moment = name.slice(cut + 1)
  const doctype = name.slice('doctype.'.length, cut)
  if (!doctype || !(MOMENTS as readonly string[]).includes(moment)) return null
  return { doctype, moment: moment as Moment }
}

export type Every = 'day' | 'week' | 'month'

export interface Schedule {
  every: Every
  hour: number
  minute: number
  /** 0 = dimanche, comme cron. */
  weekday: number
  /** Le jour du mois (1-28 : au-delà, un mois court sauterait l'exécution). */
  day: number
}

export const DEFAULT_SCHEDULE: Schedule = { every: 'day', hour: 9, minute: 0, weekday: 1, day: 1 }

export function writeCron(s: Schedule): string {
  const m = clamp(s.minute, 0, 59)
  const h = clamp(s.hour, 0, 23)
  if (s.every === 'week') return `${m} ${h} * * ${clamp(s.weekday, 0, 6)}`
  if (s.every === 'month') return `${m} ${h} ${clamp(s.day, 1, 28)} * *`
  return `${m} ${h} * * *`
}

/**
 * La planification d'une expression cron, ou `null` si elle dit plus que les trois cas proposés (plages, listes, pas de minute…) :
 * l'écran la montre alors telle quelle, en lecture seule, plutôt que de la réécrire de travers.
 */
export function readCron(cron: string): Schedule | null {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return null
  const [min, hour, day, month, weekday] = parts as [string, string, string, string, string]
  if (month !== '*') return null
  const m = whole(min)
  const h = whole(hour)
  if (m === null || h === null) return null
  if (day === '*' && weekday === '*') return { ...DEFAULT_SCHEDULE, every: 'day', hour: h, minute: m }
  if (day === '*') {
    const w = whole(weekday)
    return w === null || w > 6 ? null : { ...DEFAULT_SCHEDULE, every: 'week', hour: h, minute: m, weekday: w }
  }
  if (weekday === '*') {
    const d = whole(day)
    return d === null || d < 1 || d > 28 ? null : { ...DEFAULT_SCHEDULE, every: 'month', hour: h, minute: m, day: d }
  }
  return null
}

const whole = (s: string): number | null => (/^\d{1,2}$/.test(s) ? Number(s) : null)
const clamp = (n: number, low: number, high: number): number => Math.min(high, Math.max(low, Math.round(Number.isFinite(n) ? n : low)))
