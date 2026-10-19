/** Le fil d'activité d'un document : commentaires, assignations, pièces jointes et modifications, du plus récent au plus ancien. Sans interface : testé seul. */
import type { AssignmentItem, AttachmentItem, CommentItem, VersionEntry } from './types'

export type FeedFilter = 'all' | 'comments' | 'changes'

export type FeedItem =
  | { key: string; kind: 'comment'; at: string; user: string; comment: CommentItem }
  | { key: string; kind: 'version'; at: string; user: string; entry: VersionEntry }
  | { key: string; kind: 'assigned'; at: string; user: string; assignment: AssignmentItem }
  | { key: string; kind: 'closed'; at: string; user: string | null; assignment: AssignmentItem }
  | { key: string; kind: 'attached'; at: string; user: string; attachment: AttachmentItem }

/**
 * Tout ce qui s'est passé autour du document, dans l'ordre. Une assignation donne un événement à sa création (« X a assigné à Y ») et, une fois
 * terminée ou annulée, un second à sa dernière mise à jour (on ne sait pas QUI l'a close : le serveur ne le garde pas, `user` est alors nul).
 * Les dates sont des instants UTC de même forme (`…Z` ou `+00:00`) : on les compare comme des instants, pas comme du texte.
 */
export function buildFeed(input: { comments: readonly CommentItem[]; assignments: readonly AssignmentItem[]; attachments: readonly AttachmentItem[]; versions: readonly VersionEntry[] }, filter: FeedFilter = 'all'): FeedItem[] {
  const out: FeedItem[] = []
  if (filter !== 'changes') for (const c of input.comments) out.push({ key: `c:${c.id}`, kind: 'comment', at: c.creation, user: c.owner, comment: c })
  if (filter === 'all') {
    for (const a of input.assignments) {
      out.push({ key: `a:${a.id}`, kind: 'assigned', at: a.creation, user: a.assigned_by, assignment: a })
      if (a.status !== 'open') out.push({ key: `z:${a.id}`, kind: 'closed', at: a.modified, user: null, assignment: a })
    }
    for (const f of input.attachments) out.push({ key: `f:${f.id}`, kind: 'attached', at: f.creation, user: f.owner, attachment: f })
  }
  if (filter !== 'comments') for (const e of input.versions) out.push({ key: `v:${e.id}`, kind: 'version', at: e.creation, user: e.user, entry: e })
  return out.sort((x, y) => Date.parse(y.at) - Date.parse(x.at) || (x.key < y.key ? 1 : -1))
}

/** Une échéance dépassée : une assignation ouverte dont le jour est avant `today` (`AAAA-MM-JJ`). */
export const isOverdue = (a: Pick<AssignmentItem, 'status' | 'due_date'>, today: string): boolean => a.status === 'open' && !!a.due_date && a.due_date < today

/** Les assignations ouvertes d'abord (échéance la plus proche en tête), puis les closes. */
export function sortAssignments(list: readonly AssignmentItem[]): AssignmentItem[] {
  const rank = (a: AssignmentItem) => (a.status === 'open' ? 0 : 1)
  return [...list].sort((a, b) => rank(a) - rank(b) || (a.due_date ?? '9999').localeCompare(b.due_date ?? '9999') || a.creation.localeCompare(b.creation))
}
