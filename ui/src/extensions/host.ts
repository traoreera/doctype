/**
 * L'hôte des extensions (D28) : ce que chacune enregistre dans `setup`, rangé par extension pour pouvoir tout retirer d'un coup
 * quand elle échoue. Le Desk lit ces listes (fiche, liste, barre latérale, palette, blocs) ; rien n'est global : une instance par Desk.
 */
import { defineComponent, h, inject, markRaw, shallowReactive, type Component } from 'vue'
import type { Router } from 'vue-router'
import type { ApiClient } from '@/lib/api'
import ExtensionPage from './ExtensionPage.vue'
import { EXTENSIONS } from './keys'
import {
  EXTENSION_API_VERSION,
  type CommandContribution,
  type DeskHandle,
  type ExtensionContext,
  type FormAction,
  type FormContext,
  type FormPanel,
  type ListAction,
  type ListContext,
  type Notifier,
  type PluginClient,
  type RouteContribution,
  type SidebarLink,
  type Tools,
} from './types'

export type Owned<T> = T & { extension: string }

export interface ExtensionFailure {
  name: string
  reason: string
}

export interface HostDeps {
  api: ApiClient
  router: Router
  notify: Notifier
  plugin(name: string): PluginClient
}

const NAME_RE = /^[a-z0-9_]+$/
const ROUTE_RE = /^[a-z0-9][a-z0-9_:/-]{0,120}$/
const BLOCK_RE = /^[a-z0-9_-]{1,40}$/

function need(ok: unknown, what: string): void {
  if (!ok) throw new TypeError(what)
}
const text = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0 && v.length <= 140
const isComponent = (v: unknown): v is Component => (typeof v === 'object' && v !== null) || typeof v === 'function'
const internal = (to: unknown): to is string => typeof to === 'string' && to.startsWith('/') && !to.startsWith('//') && !to.includes('\\')
const matches = (target: string, doctype: string) => target === '*' || target === doctype

/** `when` ne doit jamais casser la page qui l'appelle : une condition qui lève vaut « caché ». */
function shown<C>(item: { when?(ctx: C): boolean; extension: string }, ctx: C): boolean {
  if (!item.when) return true
  try {
    return item.when(ctx) === true
  } catch (e) {
    console.warn(`[extension ${item.extension}] when() a levé une erreur : contribution cachée`, e)
    return false
  }
}

export class ExtensionHost {
  readonly formActionItems = shallowReactive<Owned<FormAction>[]>([])
  readonly formPanelItems = shallowReactive<Owned<FormPanel>[]>([])
  readonly listActionItems = shallowReactive<Owned<ListAction>[]>([])
  readonly sidebarItems = shallowReactive<Owned<SidebarLink>[]>([])
  readonly commandItems = shallowReactive<Owned<CommandContribution>[]>([])
  readonly routeItems = shallowReactive<Owned<RouteContribution & { fullPath: string }>[]>([])
  readonly blockItems = shallowReactive(new Map<string, { extension: string; component: Component }>())
  readonly loaded = shallowReactive<string[]>([])
  readonly failures = shallowReactive<ExtensionFailure[]>([])
  private readonly removers = new Map<string, (() => void)[]>()
  private readonly closed = new Set<string>()

  constructor(private readonly deps: HostDeps | null = null) {}

  /** Ce qu'une action reçoit pour parler au serveur et à l'utilisateur, au nom de son extension. */
  tools(name: string): Tools {
    const d = this.required()
    return { api: d.api, plugin: d.plugin(name), notify: d.notify, router: d.router }
  }

  /** Ce qu'un composant d'extension reçoit par `useDesk()`. */
  context(name: string): ExtensionContext {
    return Object.freeze({ name, ...this.tools(name) })
  }

  /** La poignée donnée au `setup` d'une extension. Chaque enregistrement est vérifié (une forme fausse fait échouer l'extension entière). */
  handle(name: string): DeskHandle {
    need(NAME_RE.test(name), `nom d'extension invalide : ${name}`)
    const d = this.required()
    const add = <T>(list: T[], item: T): void => {
      if (this.closed.has(name)) return // une extension écartée (délai dépassé) ne revient pas par un appel tardif
      list.push(item)
      this.onDrop(name, () => {
        const i = list.indexOf(item)
        if (i >= 0) list.splice(i, 1)
      })
    }
    const host = this
    return Object.freeze({
      name,
      apiVersion: EXTENSION_API_VERSION,
      api: d.api,
      plugin: d.plugin(name),
      router: d.router,
      notify: d.notify,
      routes: {
        add(route: RouteContribution) {
          need(route && typeof route.path === 'string' && ROUTE_RE.test(route.path), `routes.add : chemin invalide ${route?.path}`)
          need(isComponent(route.component), 'routes.add : composant manquant')
          need(route.title === undefined || text(route.title), 'routes.add : titre invalide')
          if (host.closed.has(name)) return
          const fullPath = `/x/${name}/${route.path}`
          const page = markRaw(defineComponent({ name: `Ext_${name}`, setup: () => () => h(ExtensionPage, { extension: name, title: route.title ?? null, component: route.component }) }))
          const remove = d.router.addRoute({ path: fullPath, name: `x:${name}:${route.path}`, component: page, meta: { extension: name } })
          host.onDrop(name, remove)
          add(host.routeItems, { ...route, component: markRaw(route.component), fullPath, extension: name })
        },
      },
      sidebar: {
        add(link: SidebarLink) {
          need(text(link?.label) && internal(link.to), 'sidebar.add : libellé et chemin interne (« /… ») attendus')
          need(link.workspace === undefined || typeof link.workspace === 'string', 'sidebar.add : espace invalide')
          add(host.sidebarItems, { ...link, extension: name })
        },
      },
      form: {
        action(action: FormAction) {
          need(text(action?.label) && typeof action.doctype === 'string' && typeof action.run === 'function', 'form.action : doctype, libellé et run attendus')
          add(host.formActionItems, { ...action, extension: name })
        },
        panel(panel: FormPanel) {
          need(panel && typeof panel.doctype === 'string' && isComponent(panel.component), 'form.panel : doctype et composant attendus')
          add(host.formPanelItems, { ...panel, component: markRaw(panel.component), extension: name })
        },
      },
      list: {
        action(action: ListAction) {
          need(text(action?.label) && typeof action.doctype === 'string' && typeof action.run === 'function', 'list.action : doctype, libellé et run attendus')
          add(host.listActionItems, { ...action, extension: name })
        },
      },
      blocks: {
        add(block: string, component: Component) {
          need(typeof block === 'string' && BLOCK_RE.test(block) && isComponent(component), `blocks.add : nom (${BLOCK_RE}) et composant attendus`)
          const key = `${name}.${block}`
          need(!host.blockItems.has(key), `blocks.add : ${key} déjà enregistré`)
          if (host.closed.has(name)) return
          host.blockItems.set(key, { extension: name, component: markRaw(component) })
          host.onDrop(name, () => host.blockItems.delete(key))
        },
      },
      commands: {
        add(command: CommandContribution) {
          need(text(command?.label) && typeof command.run === 'function', 'commands.add : libellé et run attendus')
          add(host.commandItems, { ...command, extension: name })
        },
      },
    } satisfies DeskHandle)
  }

  /** Retire tout ce qu'une extension a enregistré (elle a échoué) ; ses appels tardifs sont ignorés. */
  drop(name: string): void {
    this.closed.add(name)
    for (const undo of this.removers.get(name) ?? []) undo()
    this.removers.delete(name)
    const i = this.loaded.indexOf(name)
    if (i >= 0) this.loaded.splice(i, 1)
  }

  // ── Ce que le Desk lit ────────────────────────────────────
  formActions(ctx: FormContext): Owned<FormAction>[] {
    return this.formActionItems.filter((a) => matches(a.doctype, ctx.doctype) && shown(a, ctx))
  }

  formPanels(ctx: FormContext): Owned<FormPanel>[] {
    return this.formPanelItems.filter((p) => matches(p.doctype, ctx.doctype) && shown(p, ctx))
  }

  listActions(ctx: ListContext): Owned<ListAction>[] {
    return this.listActionItems.filter((a) => matches(a.doctype, ctx.doctype) && shown(a, ctx))
  }

  /** Un type a-t-il des actions de liste d'extension (pour offrir la sélection même sans droit d'écriture) ? */
  hasListActions(doctype: string): boolean {
    return this.listActionItems.some((a) => matches(a.doctype, doctype))
  }

  /** Les liens d'un espace (`workspace` donné), ou ceux qui n'en nomment aucun connu (`known` : les espaces affichés). */
  sidebarLinks(workspace: string | null, known: readonly string[] = []): Owned<SidebarLink>[] {
    return this.sidebarItems.filter((l) => (workspace === null ? !l.workspace || !known.includes(l.workspace) : l.workspace === workspace))
  }

  block(component: string): { extension: string; component: Component } | undefined {
    return this.blockItems.get(component)
  }

  /** Lancer une action d'extension sans que son erreur remonte dans la page : elle devient une notification. */
  async run(extension: string, fn: (tools: Tools) => unknown): Promise<void> {
    const tools = this.tools(extension)
    try {
      await fn(tools)
    } catch (e) {
      console.error(`[extension ${extension}] action en échec`, e)
      tools.notify.error(e)
    }
  }

  private onDrop(name: string, undo: () => void): void {
    const list = this.removers.get(name) ?? []
    list.push(undo)
    this.removers.set(name, list)
  }

  private required(): HostDeps {
    if (!this.deps) throw new Error('hôte d’extensions sans services (Desk non initialisé)')
    return this.deps
  }
}

const EMPTY = new ExtensionHost()

/** L'hôte du Desk courant ; sans Desk (un composant monté seul dans un test), un hôte vide : aucune contribution. */
export function useExtensions(): ExtensionHost {
  return inject(EXTENSIONS, EMPTY)
}

export { EXTENSIONS, EXTENSION_CONTEXT } from './keys'
