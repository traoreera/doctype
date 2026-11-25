/**
 * Le JETON d'une invitation d'xauth, là où l'utilisateur l'a : dans le `next` d'une connexion qui vient du lien
 * (`/invite?token=…`), ou collé tel quel — le lien entier, ou le jeton seul — dans l'écran « Rejoindre une organisation ».
 *
 * Une invitée qui vient de créer son compte n'a pas encore d'organisation : sa connexion rend `onboarding_required`, et la
 * seule porte qui lui ouvre l'organisation de l'invitation est `POST /setup/join {refresh_token, invite_token}`. Sans ce jeton,
 * le Desk l'envoyait sur « Créer une organisation », le lien perdu, et « Rejoindre » demandait un code qu'elle ne voyait nulle part.
 */

const TOKEN = /^[A-Za-z0-9_-]{8,128}$/

/** Le jeton d'un lien ou d'un chemin d'invitation (`…#/invite?token=X`, `/invite?token=X`), ou d'un jeton nu ; sinon `null`. */
export function inviteToken(text: string | null | undefined): string | null {
  const value = (text ?? '').trim()
  if (!value) return null
  const found = /[?&]token=([^&#\s]+)/.exec(value)
  if (found) {
    let token: string
    try {
      token = decodeURIComponent(found[1]!)
    } catch {
      return null
    }
    return /(^|[/#])invite\?/.test(value) && TOKEN.test(token) ? token : null
  }
  return TOKEN.test(value) ? value : null
}

/** Le jeton du `next` d'une connexion, s'il mène à une invitation — jamais celui d'une autre page. */
export function inviteTokenOfNext(next: unknown): string | null {
  return typeof next === 'string' && /^\/?invite\?/.test(next.replace(/^#/, '')) ? inviteToken(next) : null
}
