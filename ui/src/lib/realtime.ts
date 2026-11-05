/**
 * Le temps réel : le flux SSE d'xpulse (`<préfixe>/xpulse/stream`), sur deux canaux.
 *
 * - `notification` (personnel : xpulse ne m'envoie que ce qui porte MON `user_id`) : « ta cloche a changé » → on relit la cloche chez xauth.
 * - `tenant-<organisation>` (xpulse ne l'ouvre qu'à ses membres) : `doc.changed` « un document de tel TYPE a changé ». Le signal ne porte
 *   ni nom ni valeur (le serveur, `realtime.py`, n'en met jamais) : la page relit ce qu'elle montre, avec les droits de l'utilisateur.
 *
 * Les AUTRES messages de ces deux canaux (ceux qu'un plugin publie par `xpulse.publish` / `xpulse.broadcast`, avec son propre
 * `event_type`) vont à `onMessage` : c'est ce que les extensions écoutent par `desk.realtime.on(type, …)`.
 *
 * Le flux passe par le `Transport` (jamais `EventSource`, qui ne sait pas envoyer le jeton autrement que dans l'URL). Il se reconnecte
 * seul, avec un délai qui double jusqu'à 30 s ; un 401 tente UN renouvellement de session ; un 404 (xpulse absent) ou un 403 arrête tout
 * jusqu'au prochain `restart` (changement de session). Sans temps réel, le Desk marche comme avant : c'est une commodité.
 */

import { decodeEntities } from './entities'
import type { HttpRequest, Transport } from './transport'

export const DOC_CHANGED = 'doc.changed'
export const BELL_CHANGED = 'bell.changed'

/** Un message d'un plugin, tel qu'une extension le reçoit. Le texte est déséchappé (xpulse l’échappe) : à AFFICHER comme texte. */
export interface RealtimeMessage {
  /** `personal` : le canal `notification`, à moi seul (xpulse filtre par `user_id`) ; `organisation` : `tenant-<id>`, tous ses membres. */
  scope: 'personal' | 'organisation'
  /** `event_type` du message ; `message` quand le plugin n'en donne pas. */
  type: string
  text: string | null
  /** Le reste du message (sans `user_id`, `tenant_id`, `event_type`, `text`). */
  data: Record<string, unknown>
}

type MessageListener = (message: RealtimeMessage) => void
const ENVELOPE = new Set(['user_id', 'tenant_id', 'event_type', 'text', 'channel'])

/** Le message d'un événement SSE, ou `null` (pas du JSON objet). */
export function toMessage(e: SseEvent): RealtimeMessage | null {
  let body: unknown
  try {
    body = JSON.parse(e.data)
  } catch {
    return null
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null
  const raw = body as Record<string, unknown>
  const data: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(raw)) if (!ENVELOPE.has(k)) data[k] = v
  return {
    scope: e.event.startsWith('tenant-') ? 'organisation' : 'personal',
    type: typeof raw.event_type === 'string' && raw.event_type ? raw.event_type : 'message',
    text: typeof raw.text === 'string' ? decodeEntities(raw.text) : null,
    data,
  }
}

export interface SseEvent {
  event: string
  data: string
}

/** Découpe un flux SSE en événements, morceau par morceau (un événement peut arriver coupé n'importe où, même au milieu d'un `\r\n`). */
export class SseParser {
  private buffer = ''
  private event = ''
  private data: string[] = []

  feed(text: string): SseEvent[] {
    this.buffer += text
    const out: SseEvent[] = []
    for (;;) {
      const m = /\r\n|\r|\n/.exec(this.buffer)
      if (!m) break
      // Un `\r` en toute fin peut être la moitié d'un `\r\n` : on attend le morceau suivant.
      if (m[0] === '\r' && m.index === this.buffer.length - 1) break
      const line = this.buffer.slice(0, m.index)
      this.buffer = this.buffer.slice(m.index + m[0].length)
      if (line === '') {
        if (this.data.length) out.push({ event: this.event || 'message', data: this.data.join('\n') })
        this.event = ''
        this.data = []
        continue
      }
      if (line.startsWith(':')) continue // commentaire (battement de cœur)
      const colon = line.indexOf(':')
      const field = colon < 0 ? line : line.slice(0, colon)
      let value = colon < 0 ? '' : line.slice(colon + 1)
      if (value.startsWith(' ')) value = value.slice(1)
      if (field === 'event') this.event = value
      else if (field === 'data') this.data.push(value)
    }
    return out
  }
}

export interface RealtimeContext {
  /** La racine d'xpulse (`/app/xpulse`), ou `null` si on ne sait pas la déduire. */
  baseUrl: string | null
  token: string | null
  tenant: string | null
}

export interface RealtimeOptions {
  transport: Transport
  context: () => RealtimeContext
  /** Renouveler la session sur un 401 : `true` si un nouveau jeton est là. */
  onUnauthorized?: () => Promise<boolean>
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>
}

export type RealtimeState = 'idle' | 'connecting' | 'open' | 'retrying' | 'off'

type Listener = (doctype: string) => void

const MAX_DELAY = 30_000

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve()
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => (clearTimeout(timer), resolve()), { once: true })
  })
}

export class RealtimeClient {
  state: RealtimeState = 'idle'
  private controller: AbortController | null = null
  private readonly doctypeListeners = new Map<string, Set<Listener>>()
  private readonly bellListeners = new Set<() => void>()
  private readonly messageListeners = new Set<MessageListener>()
  private readonly sleep: (ms: number, signal: AbortSignal) => Promise<void>

  constructor(private readonly opts: RealtimeOptions) {
    this.sleep = opts.sleep ?? abortableSleep
  }

  /** `cb` est appelé quand un document de `doctype` a changé dans mon organisation. Renvoie de quoi se désabonner. */
  onDocChanged(doctype: string, cb: Listener): () => void {
    let set = this.doctypeListeners.get(doctype)
    if (!set) this.doctypeListeners.set(doctype, (set = new Set()))
    set.add(cb)
    return () => {
      set.delete(cb)
      if (!set.size) this.doctypeListeners.delete(doctype)
    }
  }

  onBell(cb: () => void): () => void {
    this.bellListeners.add(cb)
    return () => this.bellListeners.delete(cb)
  }

  /** Tout message d'un plugin sur mes deux canaux (pas les signaux du moteur `doc.changed` / `bell.changed`). */
  onMessage(cb: MessageListener): () => void {
    this.messageListeners.add(cb)
    return () => this.messageListeners.delete(cb)
  }

  /** (Re)démarre le flux avec la session actuelle : au montage du Desk, puis à chaque changement de jeton ou d'organisation. */
  restart(): void {
    this.stop()
    if (!this.opts.transport.stream) return void (this.state = 'off')
    const controller = new AbortController()
    this.controller = controller
    void this.run(controller.signal)
  }

  stop(): void {
    this.controller?.abort()
    this.controller = null
    this.state = 'idle'
  }

  private dispatch(e: SseEvent): void {
    if (e.event === 'notification') {
      this.bellListeners.forEach((cb) => cb())
    } else if (!e.event.startsWith('tenant-')) {
      return
    }
    const message = toMessage(e)
    if (!message) return
    if (message.type === DOC_CHANGED) {
      const doctype = message.data.doctype
      if (message.scope === 'organisation' && typeof doctype === 'string') this.doctypeListeners.get(doctype)?.forEach((cb) => cb(doctype))
      return
    }
    if (message.type === BELL_CHANGED) return
    this.messageListeners.forEach((cb) => {
      try {
        cb(message)
      } catch (err) {
        console.warn('[realtime] un abonné a levé une erreur', err)
      }
    })
  }

  private async run(signal: AbortSignal): Promise<void> {
    let delay = 1000
    let renewed = false
    while (!signal.aborted) {
      const ctx = this.opts.context()
      if (!ctx.baseUrl || !ctx.token || !ctx.tenant) return void (this.state = 'off')
      const params = new URLSearchParams()
      params.append('channels', 'notification')
      params.append('channels', `tenant-${ctx.tenant}`)
      const req: HttpRequest = {
        method: 'GET',
        url: `${ctx.baseUrl}/stream?${params.toString()}`,
        headers: { Accept: 'text/event-stream', Authorization: `Bearer ${ctx.token}`, 'X-Tenant-ID': ctx.tenant },
        signal,
      }
      const parser = new SseParser()
      this.state = 'connecting'
      let status = 0
      try {
        const res = await this.opts.transport.stream!(req, (text) => {
          if (this.state !== 'open') {
            this.state = 'open'
            delay = 1000
            renewed = false
          }
          parser.feed(text).forEach((e) => this.dispatch(e))
        })
        status = res.status
      } catch (e) {
        if (signal.aborted) return
        status = 0 // réseau : on réessaie
        void e
      }
      if (signal.aborted) return
      if (status === 404 || status === 403) return void (this.state = 'off') // xpulse absent, ou canal refusé : on n'insiste pas
      if (status === 401) {
        if (!renewed && this.opts.onUnauthorized && (await this.opts.onUnauthorized())) {
          renewed = true
          continue
        }
        return void (this.state = 'off')
      }
      this.state = 'retrying'
      await this.sleep(delay, signal)
      delay = Math.min(delay * 2, MAX_DELAY)
    }
  }
}
