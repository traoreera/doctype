import { ApiClient, ApiError, pluginBase, type ClientContext } from './api'
import type { FlowDefinition } from './automation'
import type { Transport } from './transport'

/**
 * Le client de xflow, l'orchestrateur des automatisations (`app/xflows`, un plugin à part) : même transport, même jeton, même
 * renouvellement sur un 401 que le client des documents ; seule la racine change (`<préfixe>/xflow`, déduite de celle de xcore-doctype).
 *
 * xflow répond 200 même quand il refuse (`{"status": "error", "msg", "code"}`) : ce client en fait une `ApiError`, comme toute autre
 * réponse d'erreur du Desk. Ses droits (`xflow:workflows:read|write`, `xflow:runs:read|write`) sont jugés par xflow lui-même : un 403
 * dit « réservé », jamais une panne.
 */

export interface FlowSummary {
  name: string
  version: string
  trigger: string
  description: string | null
  tags: string[]
}

export type RunStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled' | 'paused'
export type StepStatus = 'pending' | 'running' | 'success' | 'failed' | 'skipped' | 'retrying'

export interface StepRun {
  step_id: string
  status: StepStatus
  attempt: number
  started_at: string | null
  finished_at: string | null
  result: unknown
  error: string | null
}

export interface FlowRun {
  run_id: string
  workflow_name: string
  workflow_version: string
  status: RunStatus
  trigger_payload: Record<string, unknown>
  steps: Record<string, StepRun>
  current_step_id: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface CatalogAction {
  qualified_name: string
  plugin: string
  action: string
  description: string
  input_schema: Record<string, unknown>
}

export interface CatalogEvent {
  name: string
  description?: string
  source_plugin?: string
}

export type XflowAvailability = 'yes' | 'reserved' | 'absent'

type Answer = { status?: string; msg?: string; message?: string; code?: string | null } & Record<string, unknown>

export class XflowClient extends ApiClient {
  constructor(transport: Transport, context: () => ClientContext, onUnauthorized?: () => boolean | Promise<boolean> | void) {
    super(transport, context, onUnauthorized)
  }

  /** xflow est-il là, et puis-je lire ses automatisations ? Un 404 sur `/flows` : le plugin n'est pas installé. */
  async availability(): Promise<XflowAvailability> {
    try {
      await this.flows()
      return 'yes'
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) return 'reserved'
      if (e instanceof ApiError && e.status === 404) return 'absent'
      throw e
    }
  }

  async flows(): Promise<FlowSummary[]> {
    return (await this.ask<{ workflows: FlowSummary[] }>('GET', '/flows')).workflows
  }

  async flow(name: string): Promise<FlowDefinition> {
    return (await this.ask<{ workflow: FlowDefinition }>('GET', `/flows/${encodeURIComponent(name)}`)).workflow
  }

  /** Enregistre (déploie) : xflow retient QUI déploie, c'est avec ses droits du moment que tourneront les déclenchements sans humain. */
  async deploy(definition: FlowDefinition): Promise<void> {
    await this.ask('POST', '/flows', { body: definition })
  }

  async remove(name: string): Promise<void> {
    await this.ask('DELETE', `/flows/${encodeURIComponent(name)}`)
  }

  async run(name: string, payload: Record<string, unknown> = {}): Promise<string> {
    return (await this.ask<{ run_id: string }>('POST', `/run/${encodeURIComponent(name)}`, { body: payload })).run_id
  }

  async runs(name?: string, limit = 30): Promise<FlowRun[]> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (name) params.set('workflow_name', name)
    return (await this.ask<{ runs: FlowRun[] }>('GET', '/executions', { params })).runs
  }

  async runDetail(id: string): Promise<FlowRun> {
    return (await this.ask<{ run: FlowRun }>('GET', `/executions/${encodeURIComponent(id)}`)).run
  }

  async cancelRun(id: string): Promise<void> {
    await this.ask('POST', `/executions/${encodeURIComponent(id)}/cancel`)
  }

  /** Les actions IPC que xflow a découvertes chez les autres plugins (le catalogue de la palette « Plugins »). */
  async catalog(): Promise<CatalogAction[]> {
    return (await this.ask<{ actions: CatalogAction[] }>('GET', '/registry')).actions
  }

  async events(): Promise<CatalogEvent[]> {
    return (await this.ask<{ events: CatalogEvent[] }>('GET', '/events')).events
  }

  private async ask<T>(method: 'GET' | 'POST' | 'DELETE', path: string, opts: { params?: URLSearchParams; body?: unknown } = {}): Promise<T> {
    const answer = await this.call<Answer>(method, path, opts)
    if (answer && answer.status === 'error') {
      const code = answer.code || 'xflow_error'
      throw new ApiError(code === 'not_found' ? 404 : 422, code, answer.msg || answer.message || 'Refusé par xflow')
    }
    return answer as T
  }
}

/** La racine de xflow : `<préfixe>/xflow`, à côté de xcore-doctype (`/app/xcore-doctype` → `/app/xflow`). */
export function xflowBase(apiBase: string): string {
  return pluginBase(apiBase, 'xflow')
}

/** Le ton d'un statut (la pastille) : réussi, échoué, en cours, le reste neutre. */
export function statusTone(status: string): 'ok' | 'bad' | 'warn' | 'neutral' {
  return status === 'success' ? 'ok' : status === 'failed' ? 'bad' : ['running', 'pending', 'retrying'].includes(status) ? 'warn' : 'neutral'
}

/**
 * Une date de xflow, lisible par `Date` : xflow écrit en UTC (`datetime.now(timezone.utc)`) mais sa base rend des dates SANS fuseau
 * (`2026-10-27T16:45:37.227984`), que le navigateur lirait comme une heure LOCALE. Sans fuseau, c'est donc de l'UTC.
 */
export function xflowTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  return /(?:Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`
}
