import { createPinia, setActivePinia } from 'pinia'
import { createApp } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { moduleLabel } from '@/lib/modules'
import { MAX_RECENTS, pushRecent, readRecents, recentsKey, writeRecents, type Recent } from '@/lib/recents'
import { SERVICES } from '@/lib/services'
import { useRecents } from '@/stores/recents'
import { useSession } from '@/stores/session'

const entry = (n: number, over: Partial<Recent> = {}): Recent => ({ doctype: 'Invoice', name: `FA-${n}`, title: `Facture ${n}`, at: n, ...over })

beforeEach(() => localStorage.clear())

describe('derniers documents ouverts', () => {
  it('le plus récent d’abord, sans doublon : rouvrir un document le remonte', () => {
    const list = [entry(3), entry(2), entry(1)]
    expect(pushRecent(list, entry(1, { at: 9 })).map((r) => r.name)).toEqual(['FA-1', 'FA-3', 'FA-2'])
  })

  it('deux types de document peuvent avoir le même nom sans se confondre', () => {
    const out = pushRecent([entry(1)], entry(1, { doctype: 'Quote' }))
    expect(out.map((r) => r.doctype)).toEqual(['Quote', 'Invoice'])
  })

  it('la liste est bornée', () => {
    let list: Recent[] = []
    for (let i = 0; i < MAX_RECENTS + 5; i++) list = pushRecent(list, entry(i))
    expect(list).toHaveLength(MAX_RECENTS)
    expect(list[0]!.name).toBe(`FA-${MAX_RECENTS + 4}`)
  })

  it('chaque utilisateur de chaque organisation a sa liste', () => {
    expect(recentsKey('acme', 'alice')).not.toBe(recentsKey('acme', 'bob'))
    expect(recentsKey('acme', 'alice')).not.toBe(recentsKey('globex', 'alice'))
    writeRecents(recentsKey('acme', 'alice'), [entry(1)])
    expect(readRecents(recentsKey('acme', 'bob'))).toEqual([])
    expect(readRecents(recentsKey('acme', 'alice'))).toHaveLength(1)
  })

  it('un contenu abîmé ou étranger est ignoré, jamais une erreur', () => {
    localStorage.setItem('k', '{pas du json')
    expect(readRecents('k')).toEqual([])
    localStorage.setItem('k', JSON.stringify([entry(1), { doctype: 1 }, null, 'x']))
    expect(readRecents('k')).toEqual([entry(1)])
    localStorage.setItem('k', JSON.stringify({ not: 'a list' }))
    expect(readRecents('k')).toEqual([])
  })

  it('un stockage qui refuse ne casse rien', () => {
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('plein')
    })
    expect(() => writeRecents('k', [entry(1)])).not.toThrow()
    set.mockRestore()
  })
})

describe('store des récents', () => {
  function setup(user: string, tenant: string) {
    const app = createApp({})
    const pinia = createPinia()
    setActivePinia(pinia)
    app.use(pinia)
    app.provide(SERVICES, { transport: {} as never, config: { baseUrl: '', loginUrl: null }, api: {} as never })
    const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '')
    app.runWithContext(() => useSession(pinia)).useToken(`${b64({ sub: user, tenant_id: tenant, exp: Math.floor(Date.now() / 1000) + 3600 })}.sig`)
    return app.runWithContext(() => useRecents(pinia))
  }

  it('retient ce qu’on ouvre, avec son titre, et le retrouve au prochain chargement', () => {
    const a = setup('alice', 'acme')
    a.touch('Invoice', 'FA-1', 'Facture ACME')
    const again = setup('alice', 'acme')
    again.load()
    expect(again.items.map((r) => [r.name, r.title])).toEqual([['FA-1', 'Facture ACME']])
  })

  it('sans titre, retombe sur le nom', () => {
    const a = setup('alice', 'acme')
    a.touch('Invoice', 'FA-1', '')
    expect(a.items[0]!.title).toBe('FA-1')
  })

  it('un autre compte sur le même poste ne voit pas cette liste', () => {
    setup('alice', 'acme').touch('Invoice', 'FA-1', 'x')
    const bob = setup('bob', 'acme')
    bob.load()
    expect(bob.items).toEqual([])
  })
})

describe('libellé de module', () => {
  it('un identifiant devient un libellé lisible', () => {
    expect(moduleLabel('sales')).toBe('Sales')
    expect(moduleLabel('stock_control')).toBe('Stock control')
    expect(moduleLabel(null)).toBe('Autres')
    expect(moduleLabel('')).toBe('Autres')
  })
})
