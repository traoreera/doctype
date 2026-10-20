/**
 * Mentions (`@adresse`) et commandes (`/assigner`) dans la zone de commentaire : trouver ce que l'utilisateur est en train de taper, et le remplacer par son
 * choix. Sans interface : testé seul.
 *
 * Une mention s'écrit `@adresse@exemple.fr` : c'est EXACTEMENT ce que le serveur reconnaît (`collaboration._MENTION`) pour prévenir le membre. Une commande
 * s'écrit `/mot` en début de ligne ou après une espace ; elle n'est jamais envoyée dans le commentaire : choisie, elle disparaît et fait son action.
 */
import { normalize } from './commands'

/** Même motif que le serveur (`_MENTION`) : l'adresse, précédée de `@`, pas collée à un mot. */
export const MENTION_RE = /(?<![\w.+-])@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g

export interface Segment {
  text: string
  mention: boolean
}

/** Le texte d'un commentaire coupé en morceaux : les mentions à part (pour les montrer), le reste tel quel. Jamais de HTML : chaque morceau est un TEXTE. */
export function splitMentions(text: string): Segment[] {
  const out: Segment[] = []
  let last = 0
  for (const m of text.matchAll(MENTION_RE)) {
    const start = m.index ?? 0
    if (start > last) out.push({ text: text.slice(last, start), mention: false })
    out.push({ text: m[0], mention: true })
    last = start + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), mention: false })
  return out
}

export type TriggerKind = '@' | '/'

export interface Trigger {
  kind: TriggerKind
  /** Où commence le signe (`@` ou `/`) dans le texte. */
  start: number
  /** Ce qui a été tapé après le signe, jusqu'au curseur. */
  query: string
}

const MENTION_TRIGGER = /(?:^|[\s(])@([A-Za-z0-9._%+-]*)$/
const COMMAND_TRIGGER = /(?:^|\s)\/(\p{L}*)$/u

/** Ce que l'utilisateur est en train de taper au curseur : une mention (`@ad`), une commande (`/ass`), ou rien. */
export function activeTrigger(text: string, caret: number): Trigger | null {
  const before = text.slice(0, caret)
  const mention = MENTION_TRIGGER.exec(before)
  if (mention) return { kind: '@', start: caret - mention[1]!.length - 1, query: mention[1]! }
  const command = COMMAND_TRIGGER.exec(before)
  if (command) return { kind: '/', start: caret - command[1]!.length - 1, query: command[1]! }
  return null
}

/** Remplace ce qui a été tapé (du signe au curseur) par `insert` ; rend le nouveau texte et la nouvelle position du curseur. */
export function replaceTrigger(text: string, caret: number, trigger: Trigger, insert: string): { text: string; caret: number } {
  const next = text.slice(0, trigger.start) + insert + text.slice(caret)
  return { text: next, caret: trigger.start + insert.length }
}

export type CommandId = 'assign' | 'attach' | 'mention' | 'done'

export interface SlashCommand {
  id: CommandId
  /** Le mot qu'on tape après `/`. */
  word: string
  label: string
  hint: string
  icon: string
}

/** Les commandes dont le mot COMMENCE par ce qui est tapé (sans accent ni casse), sinon celles qui le contiennent. */
export function matchCommands(commands: readonly SlashCommand[], query: string): SlashCommand[] {
  const q = normalize(query)
  if (!q) return [...commands]
  const starts = commands.filter((c) => normalize(c.word).startsWith(q))
  const rest = commands.filter((c) => !starts.includes(c) && normalize(`${c.word} ${c.label}`).includes(q))
  return [...starts, ...rest]
}
