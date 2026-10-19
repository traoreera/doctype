// Collaboration dans un vrai navigateur, deux utilisateurs du même tenant (DESK_TOKEN = alice, DESK_TOKEN2 = bob) : alice commente, assigne bob et
// joint un fichier à un document ; bob voit la tâche (pastille, « Mes tâches »), la termine et répond. Le document est jetable : à la fin on le
// supprime, et son fil (commentaires, assignation, pièce jointe) disparaît avec lui.
import { writeFileSync } from 'node:fs'
import { api, launch } from './helpers.mjs'

const out = process.env.SHOTS_DIR
const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const cfg = { api: process.env.DESK_API, token: process.env.DESK_TOKEN, tenant: process.env.DESK_TENANT }

const made = await api(cfg, 'POST', '/doc/Country', { data: { country_name: 'Pays de collaboration' } })
if (made.status !== 201) { fail(`création du document jetable : ${made.status}`); process.exit(1) }
const name = made.json.name
const url = `${process.env.DESK_URL}#/app/Country/${encodeURIComponent(name)}`

const { browser, page: alice, problems } = await launch()
const bob = await alice.context().newPage()
await alice.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), process.env.DESK_TOKEN)
await bob.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), process.env.DESK_TOKEN2)

await alice.goto(url)
await alice.waitForSelector('[data-testid="collab"]')
await alice.waitForTimeout(1200)

// 1. Un commentaire.
await alice.fill('[data-testid="comment-input"]', 'Merci de vérifier ce pays avant vendredi.')
await alice.click('[data-testid="comment-send"]')
await alice.waitForSelector('[data-testid="comment-text"]')
;(await alice.textContent('[data-testid="comment-text"]')).includes('vendredi') ? ok('le commentaire est dans le fil') : fail('commentaire absent')

// 2. Une assignation à bob, échéance passée.
await alice.click('[data-testid="assign-open"]')
await alice.waitForSelector('[data-testid="assign-dialog"]')
await alice.fill('[data-testid="assign-who"] input', 'bob')
await alice.fill('[data-testid="assign-description"]', 'Relire la fiche')
await alice.screenshot({ path: `${out}/70-assign-dialog.png` })
await alice.click('[data-testid="assign-save"]')
await alice.waitForSelector('[data-testid="assignment-bob"]')
ok('bob est assigné')

// 3. Une pièce jointe.
const file = `${out}/devis.txt`
writeFileSync(file, 'Devis 2026 : 1 200 €\n')
await alice.setInputFiles('[data-testid="attach-input"]', file)
await alice.waitForSelector('[data-testid="attachment-link"]')
;(await alice.textContent('[data-testid="attachment-link"]')).trim() === 'devis.txt' ? ok('la pièce jointe est listée') : fail('jointe absente')
await alice.waitForTimeout(500)
await alice.screenshot({ path: `${out}/71-collab-alice.png`, fullPage: true })

// Le lien signé télécharge bien le fichier.
const href = await alice.getAttribute('[data-testid="attachment-link"]', 'href')
const dl = await alice.evaluate(async (u) => { const r = await fetch(u); return { status: r.status, text: await r.text(), cd: r.headers.get('content-disposition') } }, href)
dl.status === 200 && dl.text.includes('Devis 2026') && dl.cd.startsWith('attachment') ? ok('le lien signé télécharge le fichier (en pièce jointe)') : fail(`téléchargement : ${JSON.stringify(dl)}`)

// 4. Bob : la pastille, « Mes tâches », terminer, répondre.
await bob.goto(process.env.DESK_URL + '#/')
await bob.waitForSelector('[data-testid="tasks-count"]')
;(await bob.textContent('[data-testid="tasks-count"]')).trim() === '1' ? ok('la pastille de bob indique 1 tâche') : fail('pastille')
await bob.click('[data-testid="nav-tasks"]')
await bob.waitForSelector('[data-testid="task"]')
;(await bob.textContent('[data-testid="task"]')).includes('Relire la fiche') ? ok('« Mes tâches » montre la consigne') : fail('tâche')
await bob.screenshot({ path: `${out}/72-tasks-bob.png` })
await bob.click('[data-testid="task-toggle"]')
await bob.waitForSelector('[data-testid="tasks-empty"]')
await bob.waitForTimeout(500)
;(await bob.locator('[data-testid="tasks-count"]').count()) === 0 ? ok('terminée : la pastille disparaît') : fail('pastille restée')
await bob.goto(url)
await bob.waitForSelector('[data-testid="collab"]')
await bob.fill('[data-testid="comment-input"]', 'Fait, tout est bon.')
await bob.click('[data-testid="comment-send"]')
await bob.waitForTimeout(800)
// bob ne peut ni modifier le commentaire d'alice, ni assigner.
;(await bob.locator('[data-testid="comment-edit"]').count()) === 1 ? ok('bob ne peut modifier que SON commentaire') : fail('édition')

await alice.reload()
await alice.waitForSelector('[data-testid="collab"]')
await alice.waitForTimeout(1200)
const feed = (await alice.$$eval('[data-testid="feed-item"]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()))).join(' | ')
feed.includes('Fait, tout est bon.') && feed.includes('terminée') ? ok('alice voit la réponse et la clôture dans le fil') : fail(`fil : ${feed}`)
await alice.screenshot({ path: `${out}/73-collab-after.png`, fullPage: true })

// Nettoyage : supprimer le document supprime son fil.
const del = await api(cfg, 'DELETE', `/doc/Country/${encodeURIComponent(name)}`)
del.status === 204 ? ok('document jetable supprimé (son fil avec lui)') : fail(`suppression : ${del.status}`)
const gone = await api(cfg, 'GET', `/collab/Country/${encodeURIComponent(name)}`)
gone.status === 404 ? ok('plus de fil pour ce document') : fail(`fil resté : ${gone.status}`)
console.log(problems.slice(0, 8).join('\n') || 'aucune erreur navigateur')
await browser.close()
