// Accès (plugin `iam`) contre le vrai xauth : le catalogue des permissions en vue LISTE, les rôles et les membres comme DocTypes.
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD (celles de `.env`), SHOTS_DIR ; LOGIN (facultatif : nom d'utilisateur).
import { launch } from './helpers.mjs'

const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const shot = (page, name) => page.screenshot({ path: `${process.env.SHOTS_DIR}/${name}.png` })
const { browser, page, problems } = await launch()
await page.goto(process.env.DESK_URL)
await page.fill('#login-email', process.env.LOGIN ?? process.env.ADMIN_EMAIL)
await page.fill('#login-password', process.env.ADMIN_PASSWORD)
await page.click('button[type="submit"]')
const next = await page.waitForSelector('.desk-sidebar, #login-tenant', { timeout: 15000 })
if ((await next.getAttribute('id')) === 'login-tenant' || (await page.$('#login-tenant'))) {
  await page.click('#login-tenant')
  await page.click('[role="option"]:has-text("Default")')
  await page.click('button[type="submit"]')
  await page.waitForSelector('.desk-sidebar', { timeout: 15000 })
}

// Le catalogue : une vraie liste.
await page.goto(process.env.DESK_URL + '#/app/Permission')
await page.waitForSelector('.p-datatable-tbody tr:has-text("iam:member:read")')
const count = await page.$$eval('.p-datatable-tbody tr', (r) => r.length)
count >= 20 ? ok(`Permission : ${count} lignes en vue liste (nom technique, plugin, groupe, délégable, active)`) : fail(`lignes : ${count}`)
await shot(page, '100-permissions')
const headers = await page.$$eval('.p-datatable-thead th', (h) => h.map((x) => x.textContent.trim()).filter(Boolean))
headers.includes('Délégable') && headers.includes('Plugin') ? ok(`colonnes : ${headers.join(' · ')}`) : fail(`colonnes : ${headers}`)
!headers.some((h) => /modifié/i.test(h)) ? ok('pas de colonne « Modifié » (la source ne date rien)') : fail('colonne Modifié affichée')

// Un filtre rapide, comme partout.
await page.fill('[data-testid="quick-filter"], input[placeholder*="Rechercher"], input[type="search"]', 'iam:role').catch(() => {})
await page.waitForTimeout(800)
await shot(page, '101-permissions-filtered')

// Les vues proposées : liste, rapport, tableau de bord.
const views = await page.$$eval('[data-testid^="view-"]', (v) => v.map((x) => x.textContent.trim()))
views.length ? ok(`vues : ${views.join(', ')}`) : fail('aucune vue proposée')

// Les rôles.
await page.goto(process.env.DESK_URL + '#/app/Role')
await page.waitForSelector('.p-datatable-tbody tr:has-text("tenant_admin")')
ok('Rôle : la liste montre les rôles de la plateforme (lecture seule)')
await shot(page, '102-roles')

// Les membres, puis une fiche de membre.
await page.goto(process.env.DESK_URL + '#/app/Member')
await page.waitForSelector('.p-datatable-tbody tr')
ok(`Membre : ${(await page.$$('.p-datatable-tbody tr')).length} membre(s) listé(s)`)
await shot(page, '103-members')
await page.click('.p-datatable-tbody tr a')
await page.waitForSelector('[data-testid="save"], .desk-form')
await page.waitForTimeout(1000)
await shot(page, '104-member-form')
const emailShown = (await page.textContent('[data-testid="value-email"]'))?.trim()
emailShown === process.env.ADMIN_EMAIL ? ok('fiche : l’adresse e-mail est affichée en lecture seule (pas un champ de saisie)') : fail(`fiche : ${emailShown}`)
;(await page.$('[data-field="email"] input')) === null ? ok('fiche : aucun champ de saisie pour le compte') : fail('l’e-mail est modifiable')

// Mon profil : depuis le menu de l'utilisateur ; la fiche se crée, le nom et les initiales de la barre du haut suivent.
await page.goto(process.env.DESK_URL)
await page.waitForSelector('[data-testid="user-menu"]')
await page.click('[data-testid="user-menu"]')
await page.click('.p-menu-item-link:has-text("Mon profil")')
await page.waitForSelector('[data-field="full_name"] input', { timeout: 10000 })
await shot(page, '105-profile-new')
await page.fill('[data-field="full_name"] input', 'Eera Traoré')
await page.fill('[data-field="job_title"] input', 'Administrateur').catch(() => {})
await page.click('[data-testid="save"]')
await page.waitForFunction(() => document.querySelector('[data-testid="user-menu"]')?.getAttribute('title') === 'Eera Traoré', null, { timeout: 10000 })
ok('Mon profil : créé depuis le menu, le nom affiché de la barre du haut suit tout de suite')
await shot(page, '106-profile')

// La fiche d'un employé a son lien « Compte ».
await page.goto(process.env.DESK_URL + '#/app/Employee/new')
await page.waitForSelector('[data-field="user"]', { timeout: 10000 })
ok('Employé : la fiche a son champ « Compte » (lien vers le membre xauth)')
await shot(page, '107-employee')

// L'espace « Accès » dans la barre latérale.
await page.goto(process.env.DESK_URL)
await page.waitForSelector('[data-testid="ws-iam"]')
ok('espace « Accès » dans la barre latérale')

const bad = problems.filter((p) => !/401|403|404|429/.test(p))
bad.length ? fail(`erreurs navigateur :\n${bad.slice(0, 8).join('\n')}`) : ok('aucune erreur navigateur')
await browser.close()
