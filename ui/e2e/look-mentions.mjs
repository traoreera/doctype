// La zone de commentaire : `@` propose les membres, `/` propose les commandes du document. Un vrai navigateur contre le vrai xauth.
// Le script crée un membre jetable (inscription + invitation + acceptation, comme `scripts/dev_member.py`) et le retire de l'organisation à la fin.
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD (celles de `.env`), SHOTS_DIR ; LOGIN (facultatif : nom d'utilisateur).
import { launch } from './helpers.mjs'

const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const check = (cond, m, extra = '') => (cond ? ok(m) : fail(`${m} ${extra}`))
const shot = (page, name) => page.screenshot({ path: `${process.env.SHOTS_DIR}/${name}.png` })
const { browser, page, problems } = await launch()
await page.goto(process.env.DESK_URL)
await page.fill('#login-email', process.env.LOGIN ?? process.env.ADMIN_EMAIL)
await page.fill('#login-password', process.env.ADMIN_PASSWORD)
await page.click('button[type="submit"]')
await page.waitForSelector('.desk-sidebar, #login-tenant', { timeout: 15000 })
if (await page.$('#login-tenant')) {
  await page.click('#login-tenant')
  await page.click('[role="option"]:has-text("Default")')
  await page.click('button[type="submit"]')
  await page.waitForSelector('.desk-sidebar', { timeout: 15000 })
}
const token = await page.evaluate(() => sessionStorage.getItem('xdt.desk.token'))
const tenant = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).tenant_id
const raw = (method, url, body, bearer, ten) => page.evaluate(async ([m, u, b, tok, t]) => {
  const r = await fetch(u, { method: m, headers: { 'content-type': 'application/json', ...(tok ? { authorization: `Bearer ${tok}` } : {}), ...(t ? { 'x-tenant-id': t } : {}) }, body: b ? JSON.stringify(b) : undefined })
  const text = await r.text()
  return { status: r.status, body: text ? JSON.parse(text) : null }
}, [method, url, body, bearer, ten])
const api = (method, path, body) => raw(method, `/app/xcore-doctype${path}`, body, token, tenant)

// ── Un membre jetable ───────────────────────────────────────────────────────────────
const stamp = Date.now().toString(36)
const email = `mention.${stamp}@example.com`
const password = 'Mention-Demo-Passw0rd!9'
const base = '/app/auth'
const reg = await raw('POST', `${base}/register`, { email, password })
check(reg.status === 201, 'membre jetable inscrit', JSON.stringify(reg.body))
const first = await raw('POST', `${base}/login`, { email, password })
const inv = await raw('POST', `${base}/invites/`, { tenant_id: tenant, email }, token)
const acc = await raw('POST', `${base}/invites/accept`, { token: inv.body?.token, user_id: first.body?.user_id }, first.body?.access_token)
check(acc.status === 200 || acc.status === 201, 'membre invité et accepté', `${inv.status} ${acc.status}`)

const place = await api('POST', '/doc/Place', { data: { label: `Mention ${stamp}` } })
// L'annuaire des membres est gardé 60 s par le moteur (`directory.py`) : un membre tout neuf n'y est pas tout de suite. On attend qu'il y soit (au plus 75 s).
let listed = false
for (let i = 0; i < 15 && !listed; i++) {
  const found = await api('GET', `/mentionable/Place/${place.body.name}?q=${encodeURIComponent(email)}`)
  listed = (found.body?.items ?? []).some((p) => p.email === email)
  if (!listed) await page.waitForTimeout(5000)
}
check(listed, 'le membre est dans l’annuaire (/mentionable)')
await page.goto(process.env.DESK_URL + `#/app/Place/${place.body.name}`)
const box = '[data-testid="comment-input"]'
await page.waitForSelector(box, { timeout: 8000 }).catch(async (e) => {
  await shot(page, '149-no-composer')
  console.log('page :', page.url(), '\n', (await page.textContent('body')).replace(/\s+/g, ' ').slice(0, 400), '\n', problems.slice(0, 4))
  throw e
})

// ── @ : citer ────────────────────────────────────────────────────────────────────────
await page.click(box)
await page.keyboard.type(`Merci de relire @mention.${stamp}`)
await page.waitForSelector('[data-testid="suggestions"]', { timeout: 6000 })
const menu = await page.textContent('[data-testid="suggestions"]')
check(menu.includes(email), `@ propose le membre : « ${menu.replace(/\s+/g, ' ').trim()} »`)
await shot(page, '140-mention-menu')
await page.keyboard.press('Enter')
await page.waitForFunction(([sel, mail]) => document.querySelector(sel).value.includes(`@${mail} `), [box, email])
ok('Entrée insère l’adresse (sans envoyer)')
await page.keyboard.type('avant demain.')
await page.keyboard.press('Control+Enter')
await page.waitForSelector('[data-testid="comment-text"] .mention', { timeout: 8000 })
const shown = await page.textContent('[data-testid="comment-text"] .mention')
check(shown === `@${email}`, `le commentaire montre la mention en évidence : ${shown}`)
await shot(page, '141-mention-posted')

// ── La cloche du membre ──────────────────────────────────────────────────────────────
const member = await raw('POST', `${base}/login`, { email, password, tenant_id: tenant })
const bell = await raw('GET', `${base}/notifications`, null, member.body?.access_token)
const note = (bell.body ?? []).find((n) => n.type === 'MENTION')
check(!!note && /vous a mentionné dans/.test(note.title), `la cloche du membre : « ${note?.title} »`)
check(!JSON.stringify(note ?? {}).includes('relire'), 'le texte du commentaire ne voyage pas dans la notification')

// ── / : les commandes ────────────────────────────────────────────────────────────────
await page.click(box)
await page.keyboard.type('/')
await page.waitForSelector('[data-testid="suggestions"]')
const cmds = await page.textContent('[data-testid="suggestions"]')
check(/assigner/.test(cmds) && /joindre/.test(cmds) && /mention/.test(cmds), `/ propose les commandes du document : « ${cmds.replace(/\s+/g, ' ').trim()} »`)
await shot(page, '142-slash-menu')
await page.keyboard.type('ass')
await page.keyboard.press('Enter')
await page.waitForSelector('[data-testid="assign-dialog"]')
const left = await page.inputValue(box)
check(left === '', `/assigner disparaît du texte (champ : « ${left} ») et ouvre la boîte d’assignation`)
await shot(page, '143-slash-assign')
await page.click('[data-testid="assign-dialog"] .p-dialog-close-button')
await page.waitForSelector('[data-testid="assign-dialog"]', { state: 'detached' })

// ── Ménage ───────────────────────────────────────────────────────────────────────────
await api('DELETE', `/doc/Place/${place.body.name}`)
// Les membres jetables de ce script (et d'une exécution interrompue avant son ménage) quittent l'organisation.
const members = (await raw('GET', `${base}/tenants/${tenant}/members`, null, token)).body ?? []
let gone = 0
for (const m of members.filter((x) => /^mention\.[a-z0-9]+@example\.com$/.test(x.email ?? ''))) {
  if ((await raw('DELETE', `${base}/tenants/${tenant}/members/${m.user_id}`, null, token)).status === 204) gone++
}
check(gone >= 1, `membres jetables retirés de l’organisation (${gone})`)

const noise = problems.filter((p) => !/401|favicon|ResizeObserver|status of 4/.test(p))
noise.length ? fail(`problèmes dans la console : ${noise.slice(0, 5).join(' | ')}`) : ok('aucune erreur dans la console')
await browser.close()
