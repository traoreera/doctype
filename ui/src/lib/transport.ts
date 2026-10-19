/**
 * Couche de transport : la SEULE chose qui touche le réseau.
 *
 * Le Desk tourne aujourd'hui dans un navigateur (`fetch`) et pourra tourner demain dans une application Tauri, où les
 * appels réseau passent par des commandes Rust (voir erp-Orchestra) plutôt que par `fetch`. Tout le reste du code
 * dépend de cette interface, jamais de `fetch` : le portage consistera à fournir un autre `Transport`.
 */

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

export interface HttpRequest {
  method: HttpMethod
  url: string
  headers: Record<string, string>
  body?: unknown
  signal?: AbortSignal
}

export interface HttpResponse {
  status: number
  /** Le corps décodé en JSON, ou `null` s'il est vide ou n'est pas du JSON. */
  json: unknown
}

export interface Transport {
  request(req: HttpRequest): Promise<HttpResponse>
}

/** Le serveur n'a pas répondu (réseau coupé, DNS, CORS…) : distinct d'une réponse d'erreur. */
export class NetworkError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message)
    this.name = 'NetworkError'
  }
}

export class FetchTransport implements Transport {
  constructor(private readonly fetchImpl: typeof fetch = (...a) => fetch(...a)) {}

  async request(req: HttpRequest): Promise<HttpResponse> {
    const init: RequestInit = { method: req.method, headers: req.headers, signal: req.signal ?? null }
    // Un formulaire multipart (téléversement) part tel quel : le navigateur pose lui-même l'en-tête avec sa frontière.
    if (req.body !== undefined) init.body = typeof FormData !== 'undefined' && req.body instanceof FormData ? req.body : JSON.stringify(req.body)
    let res: Response
    try {
      res = await this.fetchImpl(req.url, init)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') throw e
      throw new NetworkError('Le serveur est injoignable', e)
    }
    const text = await res.text()
    let json: unknown = null
    if (text) {
      try {
        json = JSON.parse(text)
      } catch {
        json = null
      }
    }
    return { status: res.status, json }
  }
}
