/**
 * Paramètres › Automatisations : les flux xflow de l'organisation et leurs exécutions, lus par les routes d'xflow
 * (`<préfixe>/xflow/flows`, `/executions`, `/run/<nom>`) avec la session du Desk. xflow juge chaque appel par ses grants
 * (`xflow:workflows:read`, `xflow:runs:read|write`) : un 403 dit « réservé », jamais une panne.
 *
 * Pur et testé à part (`tests/automations.spec.ts`) : la page ne fait qu'afficher ce que ces fonctions rendent. D'une exécution on
 * ne garde que son RÉSUMÉ (nom, état, dates, erreur, ce qui l'a lancée) : xflow renvoie aussi le contexte et le résultat de chaque
 * étape, que l'écran n'affiche pas.
 */

export interface FlowSummary {
  name: string
  version?: string
  /** manual | event | schedule | webhook */
  trigger: string
  description?: string | null
  tags?: string[]
}

export type RunStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled' | 'paused' | 'timeout' | string

export interface RunSummary {
  id: string
  flow: string
  status: RunStatus
  startedAt: string | null
  finishedAt: string | null
  error: string | null
  /** Ce qui l'a lancée : `manual`, `event`, `ipc` (une transition du moteur), `scheduler`, `webhook`. */
  via: string | null
  /** La transition qui l'a lancée (`flows.py` met `doctype`, `name`, `action` dans la charge du déclencheur), s'il y en a une. */
  document: { doctype: string; name: string; action: string | null } | null
}

/** xflow écrit ses dates en UTC SANS fuseau (`2026-11-05T10:57:21.937`) : on le précise, sinon le navigateur les lirait en heure locale. */
export function asUtc(stamp: unknown): string | null {
  if (typeof stamp !== 'string' || !stamp) return null
  return /(?:Z|[+-]\d\d:?\d\d)$/.test(stamp) ? stamp : `${stamp}Z`
}

const text = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)

export function readFlows(body: unknown): FlowSummary[] {
  const list = (body as { workflows?: unknown } | null)?.workflows
  if (!Array.isArray(list)) return []
  return list
    .filter((f): f is Record<string, unknown> => !!f && typeof f === 'object' && typeof (f as { name?: unknown }).name === 'string')
    .map((f) => ({
      name: f.name as string,
      version: text(f.version) ?? undefined,
      trigger: text(f.trigger) ?? 'manual',
      description: text(f.description),
      tags: Array.isArray(f.tags) ? f.tags.filter((x): x is string => typeof x === 'string') : [],
    }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

export function readRuns(body: unknown): RunSummary[] {
  const list = (body as { runs?: unknown } | null)?.runs
  if (!Array.isArray(list)) return []
  return list
    .filter((r): r is Record<string, unknown> => !!r && typeof r === 'object' && typeof (r as { run_id?: unknown }).run_id === 'string')
    .map((r) => {
      const trigger = (r.trigger_payload && typeof r.trigger_payload === 'object' ? r.trigger_payload : {}) as Record<string, unknown>
      const context = (r.context && typeof r.context === 'object' ? r.context : {}) as Record<string, unknown>
      const doctype = text(trigger.doctype)
      const name = text(trigger.name)
      return {
        id: r.run_id as string,
        flow: text(r.workflow_name) ?? '?',
        status: text(r.status) ?? 'pending',
        startedAt: asUtc(r.started_at),
        finishedAt: asUtc(r.finished_at),
        error: text(r.error),
        via: text(context._triggered_by),
        document: doctype && name ? { doctype, name, action: text(trigger.action) } : null,
      }
    })
}

/** La classe de la pastille d'état (`.status-pill` de `desk.css` : `cancelled` est la rouge). */
export function statusTone(status: RunStatus): 'ok' | 'warn' | 'cancelled' | '' {
  if (status === 'success') return 'ok'
  if (status === 'failed' || status === 'timeout') return 'cancelled'
  if (status === 'pending' || status === 'running') return 'warn'
  return ''
}

// ── L'éditeur : un brouillon de formulaire ↔ la définition qu'xflow déploie ───────────────────────────────────────────────
//
// Le formulaire couvre le cas courant : un déclencheur et une SUITE d'étapes « action » exécutées dans l'ordre (chacune a pour
// `on_success` la suivante — sans cela xflow s'arrête après la première). Tout le reste du langage d'xflow (conditions, boucles,
// parallèle, attente, IA…) s'écrit dans l'onglet JSON : une définition que le formulaire ne sait pas montrer y est ouverte telle quelle,
// jamais tronquée. xflow juge la définition au déploiement ; ces vérifications-ci évitent seulement l'aller-retour pour une faute évidente.

/** Le nom d'un flux, tel qu'une transition peut le citer (`schema.FLOW_NAME` côté moteur : les deux règles sont les mêmes). */
export const FLOW_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/
const STEP_ID = /^[A-Za-z0-9_-]{1,60}$/

export type TriggerKind = 'manual' | 'event' | 'schedule' | 'webhook'
export const TRIGGER_KINDS: TriggerKind[] = ['manual', 'event', 'schedule', 'webhook']

export interface StepDraft {
  id: string
  /** `plugin.action` (le premier point sépare le plugin : un nom de plugin n'en contient pas). */
  target: string
  /** La charge utile en JSON (un objet), avec ses `{{ variables }}`. */
  payload: string
  /** Ce que le formulaire ne montre pas (timeout, retry, description…) : rendu tel quel. */
  extra: Record<string, unknown>
}

export interface FlowDraft {
  name: string
  description: string
  trigger: TriggerKind
  event: string
  cron: string
  steps: StepDraft[]
  /** Ce que le formulaire ne montre pas au niveau du flux (webhooks sortants, tags, timeout…) : rendu tel quel. */
  extra: Record<string, unknown>
}

/** Ce qu'une étape peut citer : la charge du déclencheur (une transition y met ces clés, `flows.py`) et les résultats des étapes. */
export const VARIABLES = ['trigger.doctype', 'trigger.name', 'trigger.action', 'trigger.from', 'trigger.to', 'trigger.user', 'trigger.version']

export function emptyStep(index: number): StepDraft {
  return { id: `etape_${index + 1}`, target: 'xcore-doctype.doc.save', payload: '{\n  "doctype": "",\n  "data": {}\n}', extra: {} }
}

export function emptyDraft(): FlowDraft {
  return { name: '', description: '', trigger: 'manual', event: '', cron: '', steps: [emptyStep(0)], extra: {} }
}

const STEP_KEYS = new Set(['id', 'type', 'plugin', 'action', 'payload', 'on_success'])
const FLOW_KEYS = new Set(['name', 'description', 'trigger', 'steps', 'entry_step', 'deployed_by', 'version'])

/** Le brouillon d'une définition, ou `null` (avec la raison) si le formulaire ne sait pas la montrer : on l'ouvre alors en JSON. */
export function draftOf(definition: unknown): { draft: FlowDraft; reason: null } | { draft: null; reason: string } {
  const d = (definition && typeof definition === 'object' ? definition : {}) as Record<string, unknown>
  const trigger = (d.trigger && typeof d.trigger === 'object' ? d.trigger : {}) as Record<string, unknown>
  const kind = (typeof trigger.type === 'string' ? trigger.type : 'manual') as TriggerKind
  if (!TRIGGER_KINDS.includes(kind)) return { draft: null, reason: `déclencheur « ${String(trigger.type)} »` }
  if (trigger.interval_seconds) return { draft: null, reason: 'déclencheur périodique (interval_seconds)' }
  const steps = Array.isArray(d.steps) ? (d.steps as Record<string, unknown>[]) : []
  if (!steps.length) return { draft: null, reason: 'aucune étape' }
  if (d.entry_step && d.entry_step !== steps[0]!.id) return { draft: null, reason: 'une étape de départ autre que la première' }
  const out: StepDraft[] = []
  for (const [i, s] of steps.entries()) {
    if ((s.type ?? 'action') !== 'action') return { draft: null, reason: `étape « ${String(s.id)} » de type ${String(s.type)}` }
    if (s.on_failure) return { draft: null, reason: `étape « ${String(s.id)} » avec une branche d'échec` }
    const next = steps[i + 1]?.id ?? null
    if ((s.on_success ?? null) !== next) return { draft: null, reason: 'des étapes qui ne s’enchaînent pas dans l’ordre' }
    const extra: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(s)) if (!STEP_KEYS.has(k) && k !== 'on_failure') extra[k] = v
    out.push({ id: String(s.id ?? ''), target: `${String(s.plugin ?? '')}.${String(s.action ?? '')}`, payload: JSON.stringify(s.payload ?? {}, null, 2), extra })
  }
  const extra: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(d)) if (!FLOW_KEYS.has(k)) extra[k] = v
  return {
    draft: {
      name: String(d.name ?? ''), description: typeof d.description === 'string' ? d.description : '', trigger: kind,
      event: typeof trigger.event_name === 'string' ? trigger.event_name : '', cron: typeof trigger.cron === 'string' ? trigger.cron : '',
      steps: out, extra,
    },
    reason: null,
  }
}

/** La définition à déployer, ou la liste des fautes (en français, une par ligne du formulaire). */
export function definitionOf(draft: FlowDraft): { definition: Record<string, unknown>; problems: [] } | { definition: null; problems: string[] } {
  const problems: string[] = []
  const name = draft.name.trim()
  if (!FLOW_NAME.test(name)) problems.push('Nom : lettres, chiffres, « _ », « . » ou « - », sans espace, 100 caractères au plus.')
  if (draft.trigger === 'event' && !draft.event.trim()) problems.push('Déclencheur : nommez l’événement (ex. doctype.Sales Invoice.after_submit).')
  if (draft.trigger === 'schedule' && draft.cron.trim().split(/\s+/).length !== 5) problems.push('Déclencheur : une expression cron à 5 champs (ex. 0 9 * * 1-5).')
  if (!draft.steps.length) problems.push('Ajoutez au moins une étape.')
  const ids = new Set<string>()
  const steps = draft.steps.map((s, i) => {
    const label = `Étape ${i + 1}`
    if (!STEP_ID.test(s.id)) problems.push(`${label} : identifiant en lettres, chiffres, « _ » ou « - ».`)
    else if (ids.has(s.id)) problems.push(`${label} : l’identifiant « ${s.id} » est déjà pris.`)
    ids.add(s.id)
    const dot = s.target.indexOf('.')
    const plugin = dot > 0 ? s.target.slice(0, dot) : ''
    const action = dot > 0 ? s.target.slice(dot + 1) : ''
    if (!plugin || !action) problems.push(`${label} : choisissez une action « plugin.action » (ex. xcore-doctype.doc.save).`)
    let payload: unknown = {}
    try {
      payload = s.payload.trim() ? JSON.parse(s.payload) : {}
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('objet')
    } catch {
      problems.push(`${label} : la charge utile doit être un objet JSON.`)
    }
    return { ...s.extra, id: s.id, type: 'action', plugin, action, payload, on_success: draft.steps[i + 1]?.id ?? null }
  })
  if (problems.length) return { definition: null, problems }
  const trigger: Record<string, unknown> = { type: draft.trigger }
  if (draft.trigger === 'event') trigger.event_name = draft.event.trim()
  if (draft.trigger === 'schedule') trigger.cron = draft.cron.trim()
  const definition: Record<string, unknown> = { ...draft.extra, name, trigger, steps }
  if (draft.description.trim()) definition.description = draft.description.trim()
  return { definition, problems: [] }
}

/** Ce qu'un champ « événement » propose : les événements que le moteur émet pour chaque type, puis ceux qu'xflow catalogue. */
export const DOC_EVENTS = ['after_save', 'after_submit', 'after_cancel', 'after_delete', 'after_transition', 'after_rename'] as const
export function eventSuggestions(doctypes: readonly string[], catalogued: readonly string[] = []): string[] {
  const fromDocs = doctypes.flatMap((dt) => DOC_EVENTS.map((e) => `doctype.${dt}.${e}`))
  return [...new Set([...fromDocs, ...catalogued])]
}

/** Le catalogue des actions (`GET /registry`) : leurs noms `plugin.action`, triés. */
export function readActions(body: unknown): { name: string; description: string }[] {
  const list = (body as { actions?: unknown } | null)?.actions
  if (!Array.isArray(list)) return []
  return list
    .filter((a): a is Record<string, unknown> => !!a && typeof a === 'object' && typeof (a as { qualified_name?: unknown }).qualified_name === 'string')
    .map((a) => ({ name: a.qualified_name as string, description: typeof a.description === 'string' ? a.description : '' }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

/** Le texte JSON d'une définition à montrer : sans `deployed_by`, que le serveur réécrit de toute façon. */
export function definitionText(definition: unknown): string {
  const { deployed_by: _, ...rest } = (definition && typeof definition === 'object' ? definition : {}) as Record<string, unknown>
  return JSON.stringify(rest, null, 2)
}
