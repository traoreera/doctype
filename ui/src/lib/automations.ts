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

// ── Ce que l'ÉDITEUR partage avec cet écran ──────────────────────────────────────────────────────────────────────────────
//
// Le tracé d'un flux vit dans `lib/flowgraph.ts` (le modèle du graphe) et dans `views/FlowBuilderView.vue` (la page). Ne restent
// ici que les quelques constantes et lectures que les DEUX écrans emploient.

/** Le nom d'un flux, tel qu'une transition peut le citer (`schema.FLOW_NAME` côté moteur : les deux règles sont les mêmes). */
export const FLOW_NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/

export type TriggerKind = 'manual' | 'event' | 'schedule' | 'webhook'
export const TRIGGER_KINDS: TriggerKind[] = ['manual', 'event', 'schedule', 'webhook']

/** Le texte JSON d'une définition à montrer : sans `deployed_by`, que le serveur réécrit de toute façon. */
export function definitionText(definition: unknown): string {
  const { deployed_by: _, ...rest } = (definition && typeof definition === 'object' ? definition : {}) as Record<string, unknown>
  return JSON.stringify(rest, null, 2)
}
