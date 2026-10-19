import { ConfirmationEventBus } from 'primevue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ShortcutGrid from '@/components/blocks/ShortcutGrid.vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import FormView from '@/views/FormView.vue'
import ListView from '@/views/ListView.vue'
import SettingsView from '@/views/SettingsView.vue'
import { listProps } from '@/router'
import type { DocTypeMeta, DocTypeSummary } from '@/lib/types'
import { TASK, doc, flushPromises, mountWith } from './views-fixtures'

afterEach(() => {
  document.body.innerHTML = ''
})

const S = (name: string, module: string | null, extra: Partial<DocTypeSummary> = {}): DocTypeSummary => ({ name, label: name, module, single: true, ...extra })

const SETTINGS_META = (name: string, over: Partial<DocTypeMeta> = {}): DocTypeMeta => ({
  ...TASK,
  name,
  label: name,
  single: true,
  views: undefined,
  title_field: undefined,
  layout: [{ section: null, collapsible: false, collapsed: false, columns: [['title']] }],
  fields: TASK.fields.filter((f) => f.fieldname === 'title').map((f) => ({ ...f, required: false })),
  ...over,
})

/** Le routeur du Desk, pour la partie qui nous intéresse : `/settings/:doctype?` et les pages vers lesquelles on peut en partir. */
function settingsRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { render: () => null } },
      { path: '/settings/:doctype?', name: 'settings', component: SettingsView, props: true },
      { path: '/app/:doctype', name: 'list', component: ListView, props: listProps },
      { path: '/app/:doctype/new', name: 'new', component: { render: () => null } },
      { path: '/app/:doctype/:name', name: 'form', component: FormView, props: true },
    ],
  })
}

describe('page Paramètres : un seul endroit pour tous les réglages', () => {
  const SUMMARIES = [S('Stock Settings', 'stock', { label: 'Paramètres du stock' }), S('Sales Settings', 'sales'), S('Item', 'stock', { single: false }), S('HR Settings', 'hr', { icon: 'users' })]
  const open = async (path: string, summaries: DocTypeSummary[], api: Record<string, unknown> = {}, workspaces: { name: string; label: string }[] = []) => {
    const r = settingsRouter()
    await r.push(path)
    const { w } = mountWith(
      { template: '<RouterView />' },
      {},
      {
        doctypes: async () => summaries,
        workspaces: async () => workspaces.map((x) => ({ ...x, blocks: [] })),
        meta: async (name: string) => SETTINGS_META(name),
        get: async (dt: string) => doc(dt, { title: 'x' }, { doctype: dt, name: dt, version: 0 }),
        ...api,
      },
      r,
    )
    await flushPromises()
    return { w, r }
  }

  it('la colonne des rubriques : Général, puis les réglages de chaque application, chacun menant à sa rubrique', async () => {
    const { w } = await open('/settings', SUMMARIES)
    expect(w.findAll('.settings-module').map((h) => h.text())).toEqual(['Hr', 'Sales', 'Stock']) // un groupe par application, par ordre alphabétique
    expect(w.find('[data-testid="settings-nav-general"]').attributes('href')).toBe('/settings')
    expect(w.find('[data-testid="setting-Stock Settings"]').attributes('href')).toBe('/settings/Stock%20Settings')
    expect(w.find('[data-testid="setting-Stock Settings"]').text()).toBe('Paramètres du stock')
    expect(w.find('[data-testid="setting-HR Settings"] i').classes()).toContain('pi-users')
    expect(w.find('[data-testid="setting-Item"]').exists()).toBe(false) // un type ordinaire n'est pas un réglage
  })

  it('le libellé d’une application est celui de son espace de travail (RH, pas hr)', async () => {
    const { w } = await open('/settings', SUMMARIES, {}, [{ name: 'hr', label: 'RH' }])
    expect(w.find('[data-testid="settings-group-hr"] .settings-module').text()).toBe('RH')
  })

  it('aucun réglage lisible : le dit, sans casser', async () => {
    const { w } = await open('/settings', [S('Item', 'stock', { single: false })])
    expect(w.find('[data-testid="settings-empty"]').exists()).toBe(true)
  })

  describe('« Général » : les réglages globaux de l’application', () => {
    const GLOBALS = [S('System Settings', 'system', { label: 'Paramètres généraux', settings_group: 'general' }), S('Branding Extra', 'shop', { label: 'Charte', settings_group: 'general' }), ...SUMMARIES]
    const preferences = vi.fn(async () => ({}))

    it('montre le formulaire de chaque réglage global (System Settings d’abord), avant le thème et le compte', async () => {
      const { w } = await open('/settings', [GLOBALS[1]!, GLOBALS[0]!, ...SUMMARIES])
      const forms = w.findAll('[data-testid^="settings-general-"]').map((f) => f.attributes('data-testid'))
      expect(forms).toEqual(['settings-general-System Settings', 'settings-general-Branding Extra'])
      expect(w.find('[data-testid="theme-dark"]').exists()).toBe(true)
      expect(w.find('[data-testid="settings-user"]').exists()).toBe(true)
    })

    it('un réglage global n’a pas de rubrique à lui dans la colonne', async () => {
      const { w } = await open('/settings', GLOBALS)
      expect(w.find('[data-testid="setting-System Settings"]').exists()).toBe(false)
      expect(w.find('[data-testid="settings-group-system"]').exists()).toBe(false)
      expect(w.find('[data-testid="setting-Stock Settings"]').exists()).toBe(true) // ceux des applications restent par application
    })

    it('l’ancienne adresse d’un réglage global ramène à « Général », sans « inconnu »', async () => {
      const { r } = await open('/settings/System%20Settings', GLOBALS)
      await flushPromises()
      expect(r.currentRoute.value.params.doctype).toBeUndefined()
      expect(r.currentRoute.value.name).toBe('settings')
    })

    it('enregistrer System Settings applique tout de suite les préférences (nom, couleur, format)', async () => {
      const update = vi.fn(async (_d: string, name: string, _v: number, data: Record<string, unknown>) => doc(name, data, { doctype: name, name, version: 1 }))
      preferences.mockClear()
      const { w } = await open('/settings', GLOBALS, { update, preferences })
      const before = preferences.mock.calls.length
      const form = w.find('[data-testid="settings-general-System Settings"]')
      await form.find('input').setValue('ACME')
      await form.find('[data-testid="save"]').trigger('click')
      await flushPromises()
      expect(update).toHaveBeenCalledWith('System Settings', 'System Settings', 0, expect.anything())
      expect(preferences.mock.calls.length).toBe(before + 1)
      // Un autre réglage global enregistré ne recharge rien : seul System Settings porte les préférences.
      const other = w.find('[data-testid="settings-general-Branding Extra"]')
      await other.find('input').setValue('x')
      await other.find('[data-testid="save"]').trigger('click')
      await flushPromises()
      expect(preferences.mock.calls.length).toBe(before + 1)
    })

    it('quitter « Général » avec une modification non enregistrée demande confirmation', async () => {
      const { w, r } = await open('/settings', GLOBALS)
      await w.find('[data-testid="settings-general-System Settings"] input').setValue('ACME')
      const seen = vi.fn()
      const on = (o: { reject?: () => void }) => {
        seen()
        o.reject?.()
      }
      ConfirmationEventBus.on('confirm', on as never)
      await r.push('/settings/Stock%20Settings').catch(() => {})
      await flushPromises()
      ConfirmationEventBus.off('confirm', on as never)
      expect(seen).toHaveBeenCalledTimes(1)
      expect(r.currentRoute.value.params.doctype).toBeUndefined()
    })

    it('sans droit sur les réglages globaux, « Général » ne montre que le thème et le compte', async () => {
      const { w } = await open('/settings', SUMMARIES)
      expect(w.findAll('[data-testid^="settings-general-"]').length).toBe(0)
      expect(w.find('[data-testid="theme-dark"]').exists()).toBe(true)
    })
  })

  it('« Général » : l’affichage et le compte ; le thème se choisit ici', async () => {
    const { w } = await open('/settings', [])
    expect(w.find('[data-testid="settings-user"]').exists()).toBe(true)
    await w.find('[data-testid="theme-dark"]').trigger('click')
    expect(w.find('[data-testid="theme-dark"]').attributes('aria-checked')).toBe('true')
    expect(w.find('[data-testid="theme-light"]').attributes('aria-checked')).toBe('false')
  })

  it('un réglage s’édite SUR PLACE, avec le formulaire habituel, sans flèche de retour ni suppression', async () => {
    const update = vi.fn(async (_d: string, name: string, _v: number, data: Record<string, unknown>) => doc(name, data, { doctype: 'Stock Settings', name, version: 1 }))
    const { w, r } = await open('/settings/Stock%20Settings', SUMMARIES, { update })
    expect(r.currentRoute.value.name).toBe('settings') // on ne quitte pas la page
    expect(w.find('[data-testid="settings-form"]').exists()).toBe(true)
    expect(w.find('[data-testid="form-title"]').text()).toBe('Stock Settings')
    expect(w.find('[data-testid="delete"]').exists()).toBe(false)
    expect(w.find('[data-testid="settings"] .desk-page-head .pi-arrow-left').exists()).toBe(false)
    expect(w.find('[data-testid="theme-dark"]').exists()).toBe(false) // la rubrique « Général » n'est pas affichée en même temps
    const input = w.find('input[type="text"], input:not([type])')
    await input.setValue('nouveau')
    await w.find('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('Stock Settings', 'Stock Settings', 0, expect.objectContaining({ title: 'nouveau' }))
  })

  it('un réglage ouvert ne s’inscrit pas dans les récents (ce n’est pas un document qu’on retrouve)', async () => {
    await open('/settings/Stock%20Settings', SUMMARIES)
    expect(localStorage.getItem('xdt.desk.recents') ?? '').not.toContain('Stock Settings')
  })

  it('une rubrique inconnue ou illisible le dit, sans afficher de formulaire', async () => {
    const { w } = await open('/settings/Ghost', SUMMARIES)
    expect(w.find('[data-testid="settings-unknown"]').exists()).toBe(true)
    expect(w.find('[data-testid="settings-form"]').exists()).toBe(false)
    const other = await open('/settings/Item', SUMMARIES) // un type ordinaire n'est pas un réglage
    expect(other.w.find('[data-testid="settings-unknown"]').exists()).toBe(true)
  })

  describe('changer de rubrique avec des modifications non enregistrées', () => {
    const answer = (accept: boolean) => {
      const seen = vi.fn()
      const on = (o: { accept?: () => void; reject?: () => void }) => {
        seen()
        ;(accept ? o.accept : o.reject)?.()
      }
      ConfirmationEventBus.on('confirm', on as never)
      return { seen, off: () => ConfirmationEventBus.off('confirm', on as never) }
    }

    it('demande confirmation, et reste où l’on est si l’on refuse', async () => {
      const { w, r } = await open('/settings/Stock%20Settings', SUMMARIES)
      await w.find('input').setValue('modifié')
      const a = answer(false)
      await r.push('/settings/Sales%20Settings').catch(() => {})
      await flushPromises()
      a.off()
      expect(a.seen).toHaveBeenCalledTimes(1)
      expect(r.currentRoute.value.params.doctype).toBe('Stock Settings')
    })

    it('change de rubrique si l’on accepte', async () => {
      const { w, r } = await open('/settings/Stock%20Settings', SUMMARIES)
      await w.find('input').setValue('modifié')
      const a = answer(true)
      await r.push('/settings/Sales%20Settings')
      await flushPromises()
      a.off()
      expect(r.currentRoute.value.params.doctype).toBe('Sales Settings')
      expect(w.find('[data-testid="form-title"]').text()).toBe('Sales Settings')
    })

    it('ne demande rien quand rien n’a changé', async () => {
      const { r } = await open('/settings/Stock%20Settings', SUMMARIES)
      const a = answer(false)
      await r.push('/settings/Sales%20Settings')
      await flushPromises()
      a.off()
      expect(a.seen).not.toHaveBeenCalled()
      expect(r.currentRoute.value.params.doctype).toBe('Sales Settings')
    })
  })
})

describe('un réglage n’a pas d’autre adresse que les Paramètres', () => {
  it('un raccourci y mène, sans compteur (il n’y en a qu’un)', async () => {
    const list = vi.fn(async () => ({ items: [], total: 4, limit: 1, offset: 0 }))
    const r = settingsRouter()
    const { w } = mountWith(ShortcutGrid, { shortcuts: [{ type: 'shortcut', doctype: 'Stock Settings', label: 'Réglages', create: false, single: true }, { type: 'shortcut', doctype: 'Item', label: 'Article', create: false }] }, { list }, r)
    await flushPromises()
    expect(w.find('[data-testid="shortcut-Stock Settings"]').attributes('href')).toBe('/settings/Stock%20Settings')
    expect(w.find('[data-testid="count-Stock Settings"]').exists()).toBe(false)
    expect(list).toHaveBeenCalledTimes(1) // seulement Item
    expect(w.find('[data-testid="shortcut-Item"]').attributes('href')).toBe('/app/Item')
  })

  it('ouvrir sa « liste » ou sa fiche ouvre la rubrique des Paramètres', async () => {
    for (const path of ['/app/Stock%20Settings', '/app/Stock%20Settings/Stock%20Settings']) {
      const r = settingsRouter()
      await r.push(path)
      const list = vi.fn(async () => ({ items: [], total: 0, limit: 20, offset: 0 }))
      mountWith({ template: '<RouterView />' }, {}, { meta: async () => SETTINGS_META('Stock Settings'), list, get: async () => doc('Stock Settings', {}, { version: 0 }), doctypes: async () => [S('Stock Settings', 'stock')], workspaces: async () => [] }, r)
      await flushPromises()
      expect(r.currentRoute.value.name, path).toBe('settings')
      expect(r.currentRoute.value.params).toMatchObject({ doctype: 'Stock Settings' })
      expect(list).not.toHaveBeenCalled() // aucune liste demandée pour un réglage
    }
  })

  it('une liste ordinaire reste une liste', async () => {
    const r = settingsRouter()
    await r.push('/app/Task')
    mountWith({ template: '<RouterView />' }, {}, { meta: async () => TASK, list: async () => ({ items: [], total: 0, limit: 20, offset: 0 }) }, r)
    await flushPromises()
    expect(r.currentRoute.value.name).toBe('list')
  })
})
