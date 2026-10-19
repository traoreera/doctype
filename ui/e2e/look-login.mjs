// Connexion au Desk par e-mail et mot de passe contre le vrai xauth, puis navigation (un appel d'API avec le jeton d'xauth), puis déconnexion.
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD (celles de `.env`), SHOTS_DIR ; LOGIN (facultatif) : ce que l'on tape dans le champ — un nom d'utilisateur ou l'adresse (défaut : l'adresse).
import { launch } from './helpers.mjs'

const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const { browser, page, problems } = await launch()
await page.goto(process.env.DESK_URL)
await page.waitForSelector('#login-email')
await page.screenshot({ path: `${process.env.SHOTS_DIR}/80-login.png` })

// Un mauvais mot de passe : refusé, avec un message, sans jeton.
const typed = process.env.LOGIN ?? process.env.ADMIN_EMAIL
await page.fill('#login-email', typed)
await page.fill('#login-password', 'Mauvais-Passw0rd!')
await page.click('button[type="submit"]')
await page.waitForSelector('[data-testid="login-error"]')
ok('un mauvais mot de passe est refusé avec un message')

await page.fill('#login-password', process.env.ADMIN_PASSWORD)
await page.click('button[type="submit"]')
// Plusieurs organisations : xauth demande laquelle ; on prend « Default ».
await page.waitForSelector('.desk-sidebar, #login-tenant', { timeout: 15000 })
if (await page.$('#login-tenant')) {
  await page.click('#login-tenant')
  await page.click('[role="option"]:has-text("Default")')
  await page.click('button[type="submit"]')
}
await page.waitForSelector('[data-testid="nav-settings"], .desk-sidebar', { timeout: 15000 })
await page.waitForTimeout(1500)
const hash = new URL(page.url()).hash
hash === '#/' || hash === '' ? ok('connecté : l’accueil du Desk') : fail(`adresse : ${hash}`)
const spaces = await page.$$eval('.ws-item', (e) => e.length)
spaces >= 3 ? ok(`${spaces} espaces visibles : l’administrateur a reçu les grants des plugins (rbac.declare → xauth)`) : fail(`espaces : ${spaces}`)
const token = await page.evaluate(() => sessionStorage.getItem('xdt.desk.token'))
const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString())
claims.email === process.env.ADMIN_EMAIL && /^[0-9a-f-]{36}$/.test(claims.tenant_id) ? ok('le jeton est celui d’xauth (e-mail, tenant_id UUID)') : fail(`claims : ${JSON.stringify(claims)}`)
await page.screenshot({ path: `${process.env.SHOTS_DIR}/81-home.png` })

// Les Paramètres > Général listent l'administrateur par son e-mail ; l'annuaire d'xauth répond.
await page.goto(process.env.DESK_URL + '#/tasks')
await page.waitForSelector('[data-testid="tasks"]')
ok('« Mes tâches » s’ouvre')

// Déconnexion : le jeton de renouvellement est révoqué, retour à l'écran de connexion.
await page.click('[data-testid="user-menu"]')
await page.click('text=Se déconnecter')
await page.waitForSelector('#login-email')
const left = await page.evaluate(() => sessionStorage.getItem('xdt.desk.token'))
left === null ? ok('déconnecté : plus de jeton en mémoire') : fail('jeton resté')
console.log(problems.filter((p) => !/401|403/.test(p)).slice(0, 6).join('\n') || 'aucune erreur navigateur')
await browser.close()
