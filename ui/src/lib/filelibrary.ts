import type { ApiErrorDetail } from './api'
import type { DetachTarget, FileLibraryItem } from './types'

/**
 * L'écran « Fichiers » (`FilesView`) : ce qui se calcule sans l'écran. Le serveur juge tout (qui voit quoi, qui peut détacher) ; le Desk
 * nomme les lignes, compose la demande de détachement et met les refus en phrases.
 */

/** La clé d'une ligne : le fichier ET le document qui le cite (un fichier que deux documents citent fait deux lignes ; jamais le rang). */
export const citationKey = (f: FileLibraryItem): string => [f.file_id, f.doctype, f.docname, f.field].join('|')

/**
 * Ce que le serveur attend pour détacher : la pièce jointe seule, ou le champ du document avec le fichier qu'on y a vu et la VERSION qu'on
 * a lue (le document a changé depuis : 409, rien n'est retiré).
 */
export function detachTarget(f: FileLibraryItem): DetachTarget {
  if (f.attachment) return { attachment: f.attachment }
  return { doctype: f.doctype, name: f.docname, field: f.field, file_id: f.file_id, version: f.version ?? 0 }
}

/** Le détail d'un refus (« Photo : Champ obligatoire »), un par champ refusé, sans doublon ni ligne vide. `label` nomme un champ. */
export function detailLines(details: readonly ApiErrorDetail[], label: (field: string) => string = (f) => f): string[] {
  const out: string[] = []
  for (const d of details) {
    const msg = typeof d.msg === 'string' ? d.msg.trim() : ''
    if (!msg) continue
    const line = typeof d.field === 'string' && d.field ? `${label(d.field)} : ${msg}` : msg
    if (!out.includes(line)) out.push(line)
  }
  return out
}

/** Un fichier que le navigateur ouvrira « en ligne » s'il le peut : seules les vraies images (le serveur sert tout le reste en téléchargement). */
export const isImage = (f: Pick<FileLibraryItem, 'content_type'>): boolean => /^image\/(png|jpeg|gif|webp)$/.test(f.content_type)
