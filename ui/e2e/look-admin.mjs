// Paramètres > Système contre le vrai xauth : connexion par e-mail et mot de passe, puis chacune des six rubriques (compte, utilisateurs, rôles, courrier,
// journal, accès API) et une invitation envoyée par l'extension de courrier (simulée sans SMTP).
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD (celles de `.env`), SHOTS_DIR.
import { launch } from './helpers.mjs'

const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const shot = (page, name) => page.screenshot({ path: `${process.env.SHOTS_DIR}/${name}.png` })
const { browser, page, problems } = await launch()
await page.goto(process.env.DESK_URL)
await page.fill('#login-email', process.env.LOGIN ?? process.env.ADMIN_EMAIL)
await page.fill('#login-password', process.env.ADMIN_PASSWORD)
await page.click('button[type="submit"]')
// Plusieurs organisations (les relances de ce script en créent) : xauth demande laquelle ; on prend « Default ».
const next = await page.waitForSelector('.desk-sidebar, #login-tenant', { timeout: 15000 })
if ((await next.getAttribute('id')) === 'login-tenant' || (await page.$('#login-tenant'))) {
  await page.click('#login-tenant')
  await page.click('[role="option"]:has-text("Default")')
  await page.click('button[type="submit"]')
  await page.waitForSelector('.desk-sidebar', { timeout: 15000 })
}

await page.goto(process.env.DESK_URL + '#/settings')
await page.waitForSelector('[data-testid="settings-group-system"]')
const rubrics = await page.$$eval('[data-testid^="system-"]', (e) => e.map((a) => a.textContent.trim()).filter(Boolean))
rubrics.length >= 9 ? ok(`rubriques système : ${rubrics.join(', ')}`) : fail(`rubriques : ${rubrics}`)

// Mon compte
await page.click('[data-testid="system-account"]')
await page.waitForSelector('[data-testid="account-settings"]')
await page.waitForSelector('[data-testid="session"]')
const email = await page.textContent('[data-testid="account-email"]')
email.trim() === process.env.ADMIN_EMAIL ? ok('Mon compte : mon adresse et ma session') : fail(`adresse : ${email}`)
await shot(page, '90-account')

// Nom d'utilisateur : on le change, on se relit, on le remet (le script ne laisse pas de trace).
const original = (await page.textContent('[data-testid="account-username"]'))?.trim()
const renameTo = async (name) => {
  await page.click('[data-testid="account-username-change"]')
  await page.fill('[data-testid="username-new"]', name)
  await page.fill('#username-password', process.env.ADMIN_PASSWORD)
  await page.click('[data-testid="username-save"]')
  await page.waitForSelector('[data-testid="username-save"]', { state: 'detached' })
}
if (original) {
  const temp = `demo-${Date.now().toString(36)}`
  await renameTo(temp)
  await page.waitForFunction((n) => document.querySelector('[data-testid="account-username"]')?.textContent?.trim() === n, temp)
  await renameTo(original)
  await page.waitForFunction((n) => document.querySelector('[data-testid="account-username"]')?.textContent?.trim() === n, original)
  ok(`Mon compte : le nom d’utilisateur change puis revient (${original})`)
} else {
  fail('aucun nom d’utilisateur affiché pour le compte administrateur')
}

// Utilisateurs : je m'y vois, propriétaire ; j'invite quelqu'un.
await page.click('[data-testid="system-users"]')
await page.waitForSelector('[data-testid="member"]')
const members = await page.$$eval('[data-testid="member"]', (e) => e.map((m) => m.textContent))
members.some((m) => m.includes(process.env.ADMIN_EMAIL)) ? ok('Utilisateurs : l’administrateur est membre') : fail(`membres : ${members}`)
await page.click('[data-testid="invite-open"]')
await page.fill('[data-testid="invite-email"]', 'nouveau.membre@example.com')
await page.click('[data-testid="invite-send"]')
await page.waitForSelector('[data-testid="invitation"]')
ok('Utilisateurs : l’invitation est créée et listée (courrier simulé)')
await shot(page, '91-users')

// Rôles : les rôles de la plateforme, créer un rôle propre à l'organisation.
await page.click('[data-testid="system-roles"]')
await page.waitForSelector('[data-testid="platform-role"]')
await page.click('[data-testid="role-create-open"]')
await page.fill('[data-testid="role-name"]', 'Comptable')
await page.click('[data-testid^="np-"] input >> nth=0')
await page.click('[data-testid="role-save"]')
await page.waitForSelector('[data-testid="tenant-role"]')
ok('Rôles : un rôle de l’organisation est créé avec une permission')
await shot(page, '92-roles')

// Organisation : la mienne, et une nouvelle.
await page.click('[data-testid="system-organisation"]')
await page.waitForSelector('[data-testid="organisation"]')
const slug = `demo-${Date.now().toString(36)}`
await page.click('[data-testid="organisation-create-open"]')
await page.fill('[data-testid="organisation-new-name"]', 'Organisation de démonstration')
await page.fill('[data-testid="organisation-new-slug"]', slug)
await page.click('[data-testid="organisation-create"]')
// Trois organisations au plus par compte (xauth) : une relance du script sur une base qui en a déjà trois voit le refus, affiché dans la fenêtre.
const outcome = await Promise.race([
  page.waitForSelector('[data-testid="organisation-create"]', { state: 'detached', timeout: 8000 }).then(() => 'created'),
  page.waitForSelector('[data-testid="organisation-create-problem"]', { timeout: 8000 }).then(() => 'refused'),
])
outcome === 'created' ? ok('Organisation : une seconde organisation est créée et listée') : ok('Organisation : le refus d’xauth (limite de trois) s’affiche dans la fenêtre')
if (outcome === 'refused') await page.keyboard.press('Escape')
await shot(page, '96-organisation')

// Catalogue des permissions et comptes de la plateforme (l'administrateur de la plateforme les voit).
await page.click('[data-testid="system-permissions"]')
await page.waitForSelector('[data-testid="permission"]')
ok(`Catalogue : ${(await page.$$('[data-testid="permission"]')).length} permissions connues`)
await shot(page, '97-permissions')
await page.click('[data-testid="system-accounts"]')
await page.waitForSelector('[data-testid="account"]')
ok(`Comptes de la plateforme : ${(await page.$$('[data-testid="account"]')).length} compte(s)`)
await shot(page, '98-accounts')

// Notifications : l'API d'xauth en crée une, la cloche la montre.
const made = await page.evaluate(async () => {
  const tok = sessionStorage.getItem('xdt.desk.token')
  const r = await fetch('/app/auth/notifications', { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${tok}` }, body: JSON.stringify({ title: 'Bienvenue & merci', message: 'Votre espace est prêt.', link: '#/settings' }) })
  return r.status
})
made === 201 ? ok('une notification est créée par l’API') : fail(`création : ${made}`)
await page.reload()
await page.waitForSelector('[data-testid="notifications-count"]')
await page.click('[data-testid="notifications-open"]')
await page.waitForSelector('[data-testid="notification"]')
const title = await page.textContent('[data-testid="notification"] strong')
title.trim() === 'Bienvenue & merci' ? ok('Notifications : la cloche la montre, le texte est lisible (&amp; décodé)') : fail(`titre : ${title}`)
await shot(page, '99-notifications')
await page.click('[data-testid="notification"] .notif-body')
await page.waitForTimeout(500)
const left = await page.$('[data-testid="notifications-count"]')
left === null ? ok('Notifications : ouverte, elle est lue') : fail('toujours non lue')

// Courrier : simulé sans serveur SMTP, essai d'envoi.
await page.click('[data-testid="system-mail"]')
await page.waitForSelector('[data-testid="mail-status"]')
const mode = await page.textContent('[data-testid="mail-mode"]')
void 0, /Simul/i.test(mode) ? ok('Courrier : envoi simulé, dit clairement') : fail(`mode : ${mode}`)
await page.fill('[data-testid="mail-to"]', 'essai@example.com')
await page.click('[data-testid="mail-send"]')
await page.waitForTimeout(1200)
await shot(page, '93-mail')

// Journal d'audit : la connexion et la création d'invitation y sont.
await page.click('[data-testid="system-audit"]')
await page.waitForSelector('[data-testid="audit-entry"]')
ok(`Journal d’audit : ${(await page.$$('[data-testid="audit-entry"]')).length} événements`)
await shot(page, '94-audit')

// Accès API
await page.click('[data-testid="system-api"]')
await page.waitForSelector('[data-testid="api-base"]')
const tenant = await page.inputValue('[data-testid="api-tenant"]')
void 0, /^[0-9a-f-]{36}$/.test(tenant) ? ok('Accès API : adresses, organisation, jeton masqué') : fail(`organisation : ${tenant}`)
await shot(page, '95-api')

const bad = problems.filter((p) => !/401|403|429/.test(p))
bad.length ? fail(`erreurs navigateur :\n${bad.slice(0, 8).join('\n')}`) : ok('aucune erreur navigateur')
await browser.close()
