/**
 * Les VALEURS d'une automatisation, pour quelqu'un qui n'écrit pas de code.
 *
 * xflow remplace `{{ trigger.name }}` dans les charges utiles de ses étapes. Personne ne doit avoir à taper ces accolades : l'écran
 * propose une LISTE (« une valeur fixe », « du document déclencheur », « du document créé à l'étape X ») et ce module traduit le choix
 * en jeton, et le jeton en choix. Les accolades restent une affaire de machine.
 *
 * Tout est pur et testé à part (`tests/flowvalues.spec.ts`).
 *
 * Une valeur FIXE garde son type JSON (un nombre reste un nombre) : le moteur écrit en types stricts et refuse `"12"` pour un
 * nombre (CLAUDE.md « Writes are strict »). Un jeton, lui, est toujours une chaîne : c'est xflow qui le résout à l'exécution.
 */

/** Ce que la charge d'un déclencheur porte (`flows.py` : le moteur met ces clés et rien d'autre). */
export const TRIGGER_KEYS = ['name', 'doctype', 'user', 'action', 'from', 'to', 'version'] as const
/** Une clé de la charge du déclencheur : celles du moteur, ou celles qu'un événement de PLUGIN déclare (`lib/flowcatalog`). */
export type TriggerKey = string

export type FlowValue =
  /** Ce que l'utilisateur a tapé ou choisi : rendu tel quel, avec son type. */
  | { kind: 'fixed'; value: unknown }
  /** Une information du document (ou de l'événement de plugin) qui a déclenché le flux. */
  | { kind: 'trigger'; key: TriggerKey }
  /** Une valeur du RÉSULTAT d'une étape précédente, à un chemin qu'un nœud de plugin déclare (`message_id`…). */
  | { kind: 'output'; step: string; path: string }
  /** Le document qu'une étape précédente a lu ou créé : son nom, ou la valeur de l'un de ses champs. */
  | { kind: 'step'; step: string; field?: string }
  /**
   * Une expression écrite ailleurs (à la main, par un autre outil) : on la MONTRE et on la garde telle quelle, l'écran n'en fabrique
   * jamais. Sans ça, ouvrir un flux venu d'ailleurs l'abîmerait.
   */
  | { kind: 'raw'; text: string }

const TRIGGER_RE = /^\{\{\s*trigger\.([a-z_][a-z0-9_]*)\s*\}\}$/
const OUTPUT_RE = /^\{\{\s*steps\.([A-Za-z0-9_-]+)\.result\.([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s*\}\}$/
/** Le nom du document d'une étape, ou la valeur d'un de ses champs : les deux formes qu'`xcore-doctype` rend (`{doc: {name, data}}`). */
const STEP_RE = /^\{\{\s*steps\.([A-Za-z0-9_-]+)\.result\.doc\.(?:name|data\.([A-Za-z0-9_]+))\s*\}\}$/

/** Le jeton (ou la valeur) qu'xflow recevra. */
export function writeValue(value: FlowValue): unknown {
  switch (value.kind) {
    case 'fixed':
      return value.value
    case 'trigger':
      return `{{ trigger.${value.key} }}`
    case 'step':
      return `{{ steps.${value.step}.result.doc.${value.field ? `data.${value.field}` : 'name'} }}`
    case 'output':
      return `{{ steps.${value.step}.result.${value.path} }}`
    case 'raw':
      return value.text
  }
}

/** Ce que l'écran doit montrer d'une valeur stockée. Ne refuse jamais : ce qu'on ne reconnaît pas reste une expression. */
export function readValue(raw: unknown): FlowValue {
  if (typeof raw !== 'string') return { kind: 'fixed', value: raw }
  const trigger = TRIGGER_RE.exec(raw)
  if (trigger) return { kind: 'trigger', key: trigger[1]! }
  const step = STEP_RE.exec(raw)
  if (step) return step[2] ? { kind: 'step', step: step[1]!, field: step[2] } : { kind: 'step', step: step[1]! }
  const output = OUTPUT_RE.exec(raw)
  if (output) return { kind: 'output', step: output[1]!, path: output[2]! }
  if (raw.includes('{{')) return { kind: 'raw', text: raw }
  return { kind: 'fixed', value: raw }
}

/** Le texte d'une valeur fixe dans un champ de saisie (une valeur absente se saisit vide, pas « undefined »). */
export function fixedText(value: FlowValue): string {
  if (value.kind !== 'fixed') return ''
  if (value.value === null || value.value === undefined) return ''
  return typeof value.value === 'object' ? JSON.stringify(value.value) : String(value.value)
}

/**
 * Une valeur fixe saisie pour un champ de ce TYPE. Le moteur n'accepte aucune conversion : un nombre doit partir en nombre, un
 * booléen en booléen, un montant exact en chaîne canonique (`decimals.ts`), le reste en texte.
 */
export function fixedValue(text: string, fieldtype?: string): unknown {
  const t = text.trim()
  if (fieldtype === 'Boolean') return t === 'true' || t === 'oui' || t === '1'
  if (fieldtype === 'Number' || fieldtype === 'Int' || fieldtype === 'Percent' || fieldtype === 'Rating') {
    return t === '' ? '' : Number.isFinite(Number(t)) ? Number(t) : text
  }
  return text
}
