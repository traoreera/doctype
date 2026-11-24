/**
 * Le transport NATIF de l'application de bureau : le contrat `Transport` (`lib/transport.ts`) servi par le processus Rust de Tauri
 * (`@tauri-apps/plugin-http`), jamais par le `fetch` de la WebView.
 *
 * Pourquoi pas le `fetch` de la WebView : la page d'une application Tauri vit sur `tauri://localhost` (ou `http://tauri.localhost`
 * sous Windows), une AUTRE origine que le serveur. Chaque appel serait soumis à CORS (que le serveur n'ouvre pas pour une origine
 * d'application) et à la CSP de la page. Par le processus Rust, il n'y a ni l'un ni l'autre : ce qui borne le réseau est la liste
 * d'origines ci-dessous ET la portée du plugin (`src-tauri/capabilities/`), deux verrous indépendants.
 *
 * Ce module ne dépend PAS de `@tauri-apps/*` : il reçoit son `NativeFetch` (`desktop/tauri.ts` lui donne celui du plugin, les tests un
 * faux). Ce qu'il doit à un transport natif, et que `FetchTransport` n'a pas à faire :
 *
 * - **refuser ce qui sort du serveur configuré** (`TransportRefusal`, qui nomme l'origine) avant tout appel ;
 * - **traduire l'annulation** : le plugin rejette avec `Error('Request cancelled')`, le Desk attend un `AbortError` (`lib/realtime.ts`,
 *   les recherches annulées) — sans cela une saisie rapide afficherait « serveur injoignable » ;
 * - **nommer une lecture interrompue** : un corps coupé en route est une panne de réseau, pas une exception anonyme.
 *
 * Le reste est la même sémantique que le transport web, vérifiée par les MÊMES cas (`tests/transport-contract.spec.ts`).
 */
import { NetworkError, type HttpRequest, type HttpResponse, type Transport } from '@/lib/transport'

/** Ce que le transport lit d'une réponse : le sous-ensemble de `Response` que le plugin HTTP de Tauri fournit. */
export interface NativeResponse {
  status: number
  headers: { get(name: string): string | null }
  text(): Promise<string>
  arrayBuffer(): Promise<ArrayBuffer>
  body: ReadableStream<Uint8Array> | null
}

export interface NativeInit {
  method: string
  headers: Record<string, string>
  /** Du JSON déjà sérialisé, ou un formulaire multipart que le plugin sérialise lui-même (il pose l'en-tête avec sa frontière). */
  body?: string | FormData
  signal?: AbortSignal
}

export type NativeFetch = (url: string, init: NativeInit) => Promise<NativeResponse>

/** La requête n'est pas partie : son adresse n'est pas celle du serveur configuré. Une `NetworkError`, pour que chaque écran sache déjà la dire. */
export class TransportRefusal extends NetworkError {
  constructor(message: string) {
    super(message)
    this.name = 'TransportRefusal'
  }
}

const abortError = (): DOMException => new DOMException("La requête a été annulée", 'AbortError')

export class NativeTransport implements Transport {
  private readonly origins: ReadonlySet<string>

  /** `origins` : les origines http(s) que l'application a le droit de joindre (son serveur, et celui de la connexion s'il diffère). */
  constructor(
    private readonly native: NativeFetch,
    origins: Iterable<string>,
  ) {
    this.origins = new Set(origins)
  }

  /** Une adresse absolue, http(s), sur une origine configurée — sinon un refus qui dit laquelle. */
  private check(raw: string): string {
    let url: URL
    try {
      url = new URL(raw)
    } catch {
      throw new TransportRefusal(`Adresse refusée : « ${raw} » n'est pas une adresse absolue (l'application de bureau n'a pas d'origine à laquelle la rapporter)`)
    }
    if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new TransportRefusal(`Adresse refusée : le schéma « ${url.protocol} » n'est pas du réseau`)
    if (!this.origins.has(url.origin)) throw new TransportRefusal(`Adresse refusée : ${url.origin} n'est pas le serveur configuré pour cette application`)
    return url.toString()
  }

  /** Une erreur du plugin, dite dans les termes du contrat : annulation demandée → `AbortError` ; sinon le serveur n'a pas répondu. */
  private failure(req: HttpRequest, e: unknown, message: string): Error {
    if (req.signal?.aborted || (e instanceof DOMException && e.name === 'AbortError')) return abortError()
    return new NetworkError(message, e)
  }

  private async send(req: HttpRequest, withBody: boolean): Promise<NativeResponse> {
    const url = this.check(req.url)
    if (req.signal?.aborted) throw abortError()
    const init: NativeInit = { method: req.method, headers: req.headers }
    if (req.signal) init.signal = req.signal
    if (withBody && req.body !== undefined) init.body = typeof FormData !== 'undefined' && req.body instanceof FormData ? req.body : JSON.stringify(req.body)
    try {
      return await this.native(url, init)
    } catch (e) {
      throw this.failure(req, e, 'Le serveur est injoignable')
    }
  }

  async request(req: HttpRequest): Promise<HttpResponse> {
    const res = await this.send(req, true)
    const ok = res.status >= 200 && res.status < 300
    try {
      // Une réponse binaire ATTENDUE et réussie se lit en octets ; un échec revient toujours en JSON (comme le transport web).
      if (req.binary && ok) {
        const buffer = await res.arrayBuffer()
        return {
          status: res.status,
          json: null,
          bytes: new Uint8Array(buffer),
          contentType: res.headers.get('Content-Type') ?? '',
          disposition: res.headers.get('Content-Disposition') ?? '',
        }
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
    } catch (e) {
      throw this.failure(req, e, 'La réponse du serveur a été interrompue')
    }
  }

  async stream(req: HttpRequest, onText: (text: string) => void): Promise<{ status: number }> {
    const res = await this.send(req, false)
    if (res.status < 200 || res.status >= 300 || !res.body) return { status: res.status }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value, { stream: true })
        if (text) onText(text)
      }
    } catch (e) {
      throw this.failure(req, e, 'Le flux du serveur a été interrompu')
    }
    // Une fin « propre » sous un signal levé reste une annulation, pas une fermeture par le serveur : `lib/realtime.ts` se
    // reconnecterait à tort si on lui rendait un statut.
    if (req.signal?.aborted) throw abortError()
    const tail = decoder.decode()
    if (tail) onText(tail)
    return { status: res.status }
  }
}
