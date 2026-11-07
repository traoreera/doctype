/**
 * Ce que les PLUGINS ouvrent à l'éditeur d'automatisations (`GET /flow-catalog`, la section `xflow:` de leur manifeste, côté serveur
 * `flownodes.py`) : des nœuds (une action IPC du plugin, ses champs, ce que son résultat contient) et des événements (un déclencheur,
 * avec les clés de sa charge).
 *
 * Un nœud de plugin est, pour xflow, une étape « action » ordinaire (`{plugin, action, payload}`) : rien de neuf à exécuter. Ce module
 * dit seulement à l'écran comment la MONTRER (son libellé, son icône, un formulaire champ par champ) et ce qu'une étape suivante peut
 * reprendre de son résultat. Tout est pur et testé à part (`tests/flowcatalog.spec.ts`).
 */

import type { ComputedRef, InjectionKey } from 'vue'

/**
 * Ce que le panneau d'une étape donne à TOUS ses `ValueField` sans le répéter sur chacun : les clés du déclencheur (celles d'un
 * événement de plugin quand le flux en écoute un) et les résultats des nœuds de plugin placés avant l'étape.
 */
export interface FlowValueContext {
  triggerKeys: ComputedRef<{ value: string; label: string }[] | undefined>
  outputs: ComputedRef<{ step: string; path: string; label: string }[]>
}
export const FLOW_VALUE_CONTEXT: InjectionKey<FlowValueContext> = Symbol('flow-value-context')

export type NodeFieldType = 'text' | 'longtext' | 'email' | 'number' | 'boolean' | 'select' | 'date'

export interface PluginNodeField {
  name: string
  label: string
  type: NodeFieldType
  required?: boolean
  options?: string[]
  description?: string
}

export interface PluginNode {
  plugin: string
  action: string
  label: string
  description?: string
  icon: string
  fields: PluginNodeField[]
  outputs: { path: string; label: string }[]
}

export interface PluginEvent {
  plugin: string
  name: string
  label: string
  description?: string
  fields: { name: string; label: string }[]
}

export interface FlowCatalog {
  nodes: PluginNode[]
  events: PluginEvent[]
}

export const EMPTY_CATALOG: FlowCatalog = { nodes: [], events: [] }

const list = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)) : []
const str = (v: unknown): string => (typeof v === 'string' ? v : '')

/** Le catalogue tel que le serveur le sert ; ce qui n'en a pas la forme est ignoré (un écran ne casse pas sur une réponse bizarre). */
export function readCatalog(body: unknown): FlowCatalog {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  return {
    nodes: list(b.nodes)
      .filter((n) => str(n.plugin) && str(n.action) && str(n.label))
      .map((n) => ({
        plugin: str(n.plugin),
        action: str(n.action),
        label: str(n.label),
        description: str(n.description) || undefined,
        icon: str(n.icon) || 'bolt',
        fields: list(n.fields)
          .filter((f) => str(f.name))
          .map((f) => ({
            name: str(f.name),
            label: str(f.label) || str(f.name),
            type: (str(f.type) || 'text') as NodeFieldType,
            required: f.required === true,
            options: Array.isArray(f.options) ? f.options.filter((o): o is string => typeof o === 'string') : undefined,
            description: str(f.description) || undefined,
          })),
        outputs: list(n.outputs).filter((o) => str(o.path)).map((o) => ({ path: str(o.path), label: str(o.label) || str(o.path) })),
      })),
    events: list(b.events)
      .filter((e) => str(e.name) && str(e.label))
      .map((e) => ({
        plugin: str(e.plugin),
        name: str(e.name),
        label: str(e.label),
        description: str(e.description) || undefined,
        fields: list(e.fields).filter((f) => str(f.name)).map((f) => ({ name: str(f.name), label: str(f.label) || str(f.name) })),
      })),
  }
}

/** Le nœud de plugin qu'une étape appelle, ou `undefined` (une action du moteur, ou d'un plugin qui ne l'a pas décrite). */
export function nodeOf(catalog: FlowCatalog, raw: Record<string, unknown>): PluginNode | undefined {
  return catalog.nodes.find((n) => n.plugin === raw.plugin && n.action === raw.action)
}

export const nodeKey = (node: Pick<PluginNode, 'plugin' | 'action'>): string => `${node.plugin}:${node.action}`

/** L'étape qu'un nœud de plugin devient : une action xflow, charge vide (le formulaire la remplit). */
export function nodeStep(node: PluginNode): Record<string, unknown> {
  return { plugin: node.plugin, action: node.action, payload: {} }
}

/** Le type de champ du moteur que `ValueField` sait saisir, pour un type de champ de nœud. */
export function fieldtypeOf(type: NodeFieldType): string | undefined {
  return ({ number: 'Number', boolean: 'Boolean', date: 'Date' } as Record<string, string>)[type]
}

/** Les champs obligatoires laissés vides : l'étape échouerait à l'exécution, l'écran le dit avant de déployer. */
export function missingFields(node: PluginNode, raw: Record<string, unknown>): string[] {
  const payload = (raw.payload && typeof raw.payload === 'object' ? raw.payload : {}) as Record<string, unknown>
  return node.fields.filter((f) => f.required && (payload[f.name] === undefined || payload[f.name] === '')).map((f) => f.label)
}

/**
 * Ce qu'une étape peut reprendre du RÉSULTAT des étapes avant elle : les sorties déclarées des nœuds de plugin.
 * (Le document lu ou créé par une étape du moteur passe, lui, par la source « étape » de `ValueField`.)
 */
export function outputSources(catalog: FlowCatalog, before: { id: string; raw: Record<string, unknown> }[]): { step: string; path: string; label: string }[] {
  return before.flatMap((s) => (nodeOf(catalog, s.raw)?.outputs ?? []).map((o) => ({ step: s.id, path: o.path, label: o.label })))
}

/** L'événement de plugin d'un nom d'événement, ou `undefined` (un événement du moteur `doctype.…`, ou inconnu). */
export function eventOf(catalog: FlowCatalog, name: string): PluginEvent | undefined {
  return catalog.events.find((e) => e.name === name)
}
