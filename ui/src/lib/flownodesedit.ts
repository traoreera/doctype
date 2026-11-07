/**
 * Décrire un nœud d'automatisation À L'ÉCRAN (« Nœuds personnalisés », `FlowNodesView`) : ce que l'organisation dit d'une action IPC
 * qu'aucun plugin n'a décrite — son libellé, ses champs, ce que son résultat contient. Le serveur le range pour elle seule
 * (`flow_nodes_service.py`) et l'éditeur d'automatisations le montre ensuite comme un nœud de plugin.
 *
 * Fonctions pures, testées à part (`tests/flownodesedit.spec.ts`). `nodeProblems` reprend les règles du serveur (`flownodes.py`) pour
 * dire la faute AVANT d'enregistrer ; le serveur reste juge.
 */
import type { NodeFieldType, PluginNode, PluginNodeField } from './flowcatalog'

export const FIELD_TYPES: readonly NodeFieldType[] = ['text', 'longtext', 'email', 'number', 'boolean', 'select', 'date']

const NAME = /^[a-z][a-z0-9_]{0,59}$/
const ACTION = /^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,99}$/
const PLUGIN = /^[a-z0-9][a-z0-9_-]{0,63}$/
const PATH = /^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,7}$/
const ICON = /^[a-z0-9-]{1,40}$/

export type DraftNode = Omit<PluginNode, 'description'> & { description: string }

export function emptyNode(): DraftNode {
  return { plugin: '', action: '', label: '', description: '', icon: 'bolt', fields: [], outputs: [] }
}

/** Un nœud lu du serveur, prêt à éditer (la description absente devient vide : un champ de saisie ne prend pas `undefined`). */
export function toDraft(node: PluginNode): DraftNode {
  return {
    ...node,
    description: node.description ?? '',
    fields: node.fields.map((f) => ({ ...f, options: f.options ? [...f.options] : [] })),
    outputs: node.outputs.map((o) => ({ ...o })),
  }
}

/**
 * Le nom technique d'un champ, tiré de son libellé : « Date d'échéance » → `date_d_echeance`. Personne n'a à inventer une clé ; on
 * peut toujours la changer.
 */
export function fieldName(label: string): string {
  const base = label
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60)
  return /^[a-z]/.test(base) ? base : base ? `champ_${base}`.slice(0, 60) : ''
}

export function addField(node: DraftNode, label = ''): DraftNode {
  const taken = new Set(node.fields.map((f) => f.name))
  let name = fieldName(label) || 'champ'
  for (let i = 2; taken.has(name); i++) name = `${fieldName(label) || 'champ'}_${i}`
  return { ...node, fields: [...node.fields, { name, label, type: 'text', required: false, options: [] }] }
}

export function patchField(node: DraftNode, index: number, patch: Partial<PluginNodeField>): DraftNode {
  return { ...node, fields: node.fields.map((f, i) => (i === index ? { ...f, ...patch } : f)) }
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index)
}

/** Ce qui part au serveur : rien de vide, les options d'une liste seulement pour une liste. */
export function toPayload(node: DraftNode): Record<string, unknown> {
  return {
    plugin: node.plugin.trim(),
    action: node.action.trim(),
    label: node.label.trim(),
    ...(node.description.trim() ? { description: node.description.trim() } : {}),
    icon: node.icon.trim() || 'bolt',
    fields: node.fields.map((f) => ({
      name: f.name.trim(),
      label: f.label.trim(),
      type: f.type,
      required: !!f.required,
      ...(f.type === 'select' ? { options: (f.options ?? []).map((o) => o.trim()).filter(Boolean) } : {}),
      ...(f.description?.trim() ? { description: f.description.trim() } : {}),
    })),
    outputs: node.outputs.map((o) => ({ path: o.path.trim(), label: o.label.trim() })),
  }
}

export interface NodeProblemLabels {
  plugin: string
  action: string
  label: string
  icon: string
  fieldName: (n: number) => string
  fieldLabel: (n: number) => string
  options: (label: string) => string
  duplicate: (name: string) => string
  outputPath: (n: number) => string
  outputLabel: (n: number) => string
}

/** Les fautes à corriger avant d'enregistrer, dites en phrases (les mêmes règles que le serveur). */
export function nodeProblems(node: DraftNode, l: NodeProblemLabels): string[] {
  const out: string[] = []
  if (!PLUGIN.test(node.plugin.trim())) out.push(l.plugin)
  if (!ACTION.test(node.action.trim())) out.push(l.action)
  if (!node.label.trim() || node.label.trim().length > 80) out.push(l.label)
  if (node.icon.trim() && !ICON.test(node.icon.trim())) out.push(l.icon)
  const seen = new Set<string>()
  node.fields.forEach((f, i) => {
    const name = f.name.trim()
    if (!NAME.test(name)) out.push(l.fieldName(i + 1))
    else if (seen.has(name)) out.push(l.duplicate(name))
    seen.add(name)
    if (!f.label.trim()) out.push(l.fieldLabel(i + 1))
    if (f.type === 'select' && !(f.options ?? []).some((o) => o.trim())) out.push(l.options(f.label || name))
  })
  const paths = new Set<string>()
  node.outputs.forEach((o, i) => {
    if (!PATH.test(o.path.trim())) out.push(l.outputPath(i + 1))
    else if (paths.has(o.path.trim())) out.push(l.duplicate(o.path.trim()))
    paths.add(o.path.trim())
    if (!o.label.trim()) out.push(l.outputLabel(i + 1))
  })
  return out
}
