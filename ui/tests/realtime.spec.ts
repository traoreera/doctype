import { describe, expect, it, vi } from 'vitest'
import { RealtimeClient, SseParser, type RealtimeContext } from '@/lib/realtime'
import type { HttpRequest, HttpResponse, Transport } from '@/lib/transport'

describe('SseParser', () => {
  it('reassembles events cut anywhere, CRLF included, and skips comments', () => {
    const p = new SseParser()
    const raw = 'event: tenant-acme\r\ndata: {"a":1}\r\n\r\n: ping\n\nevent: notification\ndata: x\ndata: y\n\n'
    const out = []
    for (const ch of raw) out.push(...p.feed(ch)) // un caractère à la fois : le pire découpage
    expect(out).toEqual([
      { event: 'tenant-acme', data: '{"a":1}' },
      { event: 'notification', data: 'x\ny' },
    ])
  })
})

type Step = { status: number; chunks?: string[]; hang?: boolean } | 'network'

/** Un transport qui rejoue des connexions écrites d'avance ; `hang` garde la dernière ouverte jusqu'à l'abandon. */
function fake(steps: Step[]) {
  const requests: HttpRequest[] = []
  const transport: Transport = {
    request: async (): Promise<HttpResponse> => ({ status: 200, json: null }),
    async stream(req, onText) {
      requests.push(req)
      const step = steps.shift() ?? { status: 200, hang: true }
      if (step === 'network') throw new Error('offline')
      step.chunks?.forEach(onText)
      if (step.hang) {
        await new Promise<void>((resolve) => req.signal?.addEventListener('abort', () => resolve()))
        throw new DOMException('aborted', 'AbortError')
      }
      return { status: step.status }
    },
  }
  return { transport, requests }
}

const ctx = (over: Partial<RealtimeContext> = {}): (() => RealtimeContext) => () => ({ baseUrl: '/app/xpulse', token: 'tok', tenant: 'acme', ...over })
const flush = () => new Promise((r) => setTimeout(r, 0))

describe('RealtimeClient', () => {
  it('listens to my bell and my organisation, with the token in a HEADER, never in the URL', async () => {
    const { transport, requests } = fake([{ status: 200, hang: true }])
    const rt = new RealtimeClient({ transport, context: ctx() })
    rt.restart()
    await flush()
    expect(requests[0]!.url).toBe('/app/xpulse/stream?channels=notification&channels=tenant-acme')
    expect(requests[0]!.headers.Authorization).toBe('Bearer tok')
    expect(requests[0]!.url).not.toContain('tok')
    rt.stop()
  })

  it('calls the listeners of the changed TYPE only, and rings the bell on a notification', async () => {
    const frames = [
      'event: tenant-acme\ndata: {"event_type":"doc.changed","tenant_id":"acme","doctype":"Invoice"}\n\n',
      'event: tenant-acme\ndata: pas du json\n\n',
      'event: notification\ndata: {"event_type":"bell.changed"}\n\n',
    ]
    const { transport } = fake([{ status: 200, chunks: frames, hang: true }])
    const rt = new RealtimeClient({ transport, context: ctx() })
    const invoice = vi.fn(), customer = vi.fn(), bell = vi.fn()
    rt.onDocChanged('Invoice', invoice)
    const off = rt.onDocChanged('Customer', customer)
    rt.onBell(bell)
    off()
    rt.restart()
    await flush()
    expect(invoice).toHaveBeenCalledWith('Invoice')
    expect(customer).not.toHaveBeenCalled()
    expect(bell).toHaveBeenCalledTimes(1)
    expect(rt.state).toBe('open')
    rt.stop()
  })

  it('gives up on 404 (xpulse absent) without retrying', async () => {
    const sleep = vi.fn(async () => {})
    const { transport, requests } = fake([{ status: 404 }])
    const rt = new RealtimeClient({ transport, context: ctx(), sleep })
    rt.restart()
    await flush()
    expect(requests).toHaveLength(1)
    expect(sleep).not.toHaveBeenCalled()
    expect(rt.state).toBe('off')
  })

  it('renews the session ONCE on a 401, then stops', async () => {
    const renew = vi.fn(async () => true)
    const { transport, requests } = fake([{ status: 401 }, { status: 401 }])
    const rt = new RealtimeClient({ transport, context: ctx(), onUnauthorized: renew })
    rt.restart()
    await flush()
    await flush()
    expect(renew).toHaveBeenCalledTimes(1)
    expect(requests).toHaveLength(2)
    expect(rt.state).toBe('off')
  })

  it('reconnects after a network failure or a closed stream, waiting longer each time (capped)', async () => {
    const delays: number[] = []
    const { transport, requests } = fake(['network', 'network', { status: 200 }, { status: 200, hang: true }])
    const rt = new RealtimeClient({ transport, context: ctx(), sleep: async (ms) => void delays.push(ms) })
    rt.restart()
    for (let i = 0; i < 6; i++) await flush()
    expect(requests).toHaveLength(4)
    expect(delays).toEqual([1000, 2000, 4000])
    rt.stop()
  })

  it('does nothing without a session or a transport able to stream', async () => {
    const { transport, requests } = fake([])
    const rt = new RealtimeClient({ transport, context: ctx({ token: null }) })
    rt.restart()
    await flush()
    expect(requests).toHaveLength(0)
    const plain = new RealtimeClient({ transport: { request: transport.request }, context: ctx() })
    plain.restart()
    expect(plain.state).toBe('off')
  })
})
