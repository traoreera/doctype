import { describe, expect, it } from 'vitest'
import { inviteToken, inviteTokenOfNext } from '@/lib/invite'

const T = '31ba29fc-3224-487b-bc8b-9833598ae311'

describe('le jeton d’une invitation', () => {
  it('se lit dans le lien envoyé à l’invitée, dans le chemin, ou nu', () => {
    expect(inviteToken(`https://erp.exemple.bf/app/xcore-doctype/desk/#/invite?token=${T}`)).toBe(T)
    expect(inviteToken(`/invite?token=${T}`)).toBe(T)
    expect(inviteToken(`  ${T} `)).toBe(T)
  })

  it('ne se lit pas ailleurs, ni dans n’importe quoi', () => {
    expect(inviteToken(`https://x.bf/#/reset?token=${T}`)).toBeNull() // un lien de mot de passe n’est pas une invitation
    expect(inviteToken('mon code')).toBeNull()
    expect(inviteToken('')).toBeNull()
    expect(inviteToken(`/invite?token=%E0%A4%A`)).toBeNull()
  })

  it('le `next` d’une connexion ne donne un jeton que s’il mène à une invitation', () => {
    expect(inviteTokenOfNext(`/invite?token=${T}`)).toBe(T)
    expect(inviteTokenOfNext(`/app/Employee?token=${T}`)).toBeNull()
    expect(inviteTokenOfNext(T)).toBeNull() // un jeton nu n’est pas une page où revenir
    expect(inviteTokenOfNext(undefined)).toBeNull()
  })
})
