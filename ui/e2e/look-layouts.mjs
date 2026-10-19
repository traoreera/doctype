// Dispositions : la mienne (par défaut) ne touche personne d'autre ; « toute l'organisation » est vue par les autres ; une couleur libre s'applique.
// Deux utilisateurs du même tenant (DESK_TOKEN, DESK_TOKEN2). Tout est remis comme avant à la fin.
import { api, launch } from './helpers.mjs'

const out = process.env.SHOTS_DIR
const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const cfgFor = (token) => ({ api: process.env.DESK_API, token, tenant: process.env.DESK_TENANT })

const { browser, page, problems } = await launch()
const context = page.context()
const other = await context.newPage()
await page.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), process.env.DESK_TOKEN)
await other.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), process.env.DESK_TOKEN2)

const cards = (p) => p.$$eval('.number-card', (els) => els.map((e) => e.textContent.trim()))
const open = async (p) => { await p.goto(process.env.DESK_URL + '#/w/sales'); await p.reload(); await p.waitForSelector('[data-testid="workspace-edit"]'); await p.waitForTimeout(1800) }

await open(page)
await page.click('[data-testid="workspace-edit"]')
await page.waitForSelector('[data-testid="block-grid-edit"]')
await page.click('[data-testid="editor-add"]')
await page.waitForTimeout(500)
await page.click('li[role="menuitem"]:has-text("Chiffre")')
await page.fill('[data-testid="bf-label"]', 'Ma carte orange')
await page.click('[data-testid="bf-doctype"]')
await page.fill('.p-select-overlay input', 'Facture')
await page.click('li[role="option"]:has-text("Facture")')
await page.click('[data-testid="bf-color"]')
await page.click('li[role="option"]:has-text("Autre couleur")')
await page.$eval('[data-testid="bf-color-custom"]', (el) => { el.value = '#d6452a'; el.dispatchEvent(new Event('input', { bubbles: true })) })
await page.screenshot({ path: `${out}/60-color-dialog.png` })
await page.click('[data-testid="bf-validate"]')
await page.waitForTimeout(600)
await page.screenshot({ path: `${out}/61-scope-bar.png` })
await page.click('[data-testid="editor-save"]') // « Pour moi » par défaut
await page.waitForSelector('[data-testid="workspace-edit"]')
await page.waitForTimeout(1500)

;(await cards(page)).some((c) => c.startsWith('Ma carte orange')) ? ok('ma carte est sur ma page') : fail('ma carte absente')
const border = await page.$$eval('.number-card', (els) => els.map((e) => getComputedStyle(e).borderLeftColor))
border.includes('rgb(214, 69, 42)') ? ok('la couleur libre s’applique (rgb(214, 69, 42))') : fail(`couleurs : ${border.join(' | ')}`)

await open(other)
;(await cards(other)).some((c) => c.startsWith('Ma carte orange')) ? fail('un autre utilisateur voit ma carte') : ok('l’autre utilisateur ne la voit pas')

// Pour toute l'organisation.
await page.click('[data-testid="workspace-edit"]')
await page.waitForSelector('[data-testid="editor-layer"]')
;(await page.textContent('[data-testid="editor-layer"]')).includes('Ma disposition') ? ok('le badge dit « Ma disposition »') : fail('badge')
await page.click('[data-testid="editor-scope"] >> text=Toute l’organisation')
await page.click('[data-testid="tile-remove-1"]')
await page.click('[data-testid="editor-save"]')
await page.waitForSelector('[data-testid="workspace-edit"]')
await open(other)
const orgCards = await cards(other)
orgCards.some((c) => c.startsWith('Ma carte orange')) ? ok('l’organisation a reçu la disposition (carte incluse)') : fail(`autre utilisateur : ${orgCards.join(' | ')}`)

// Remise en l'état (par l'API).
const a = cfgFor(process.env.DESK_TOKEN)
for (const scope of ['user', 'org']) {
  const r = await api(a, 'DELETE', `/override/workspace/sales?scope=${scope}`)
  r.status === 204 ? ok(`disposition ${scope} retirée`) : fail(`retrait ${scope} : ${r.status}`)
}
console.log(problems.slice(0, 8).join('\n') || 'aucune erreur navigateur')
await browser.close()
