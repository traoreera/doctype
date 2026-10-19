// Personnaliser un espace dans un vrai navigateur : déplacer par glisser-déposer, redimensionner, ajouter une carte par la fenêtre, enregistrer,
// recharger, puis revenir à la disposition d'origine. Tourne contre le tenant de démonstration de look.mjs.
import { api, launch } from './helpers.mjs'
const cfg = { api: process.env.DESK_API, token: process.env.DESK_TOKEN, tenant: process.env.DESK_TENANT }
const out = process.env.SHOTS_DIR
const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)

const { browser, page, problems } = await launch()
await page.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), cfg.token)
await page.goto(process.env.DESK_URL + '#/w/sales')
await page.waitForSelector('[data-testid="workspace-edit"]')
const titles = () => page.$$eval('.number-card, .chart-card h3, .block-title', (els) => els.map((e) => e.textContent.trim()))
const before = await titles()

await page.click('[data-testid="workspace-edit"]')
await page.waitForSelector('[data-testid="block-grid-edit"]')
await page.waitForTimeout(1500)
await page.screenshot({ path: `${out}/10-edit.png` })

// Glisser la 2e tuile sur la 1re.
const kindsOf = () => page.$$eval('.tile-kind', (els) => els.map((e) => e.textContent.trim()))
const k0 = await kindsOf()
await page.dragAndDrop('[data-testid="tile-5"]', '[data-testid="tile-1"]')
const k1 = await kindsOf()
if (JSON.stringify(k0) !== JSON.stringify(k1)) ok(`glisser-déposer : ${k0.join(',')} → ${k1.join(',')}`)
else fail('le glisser-déposer n’a rien changé')

// Largeur : la dernière tuile graphique en pleine ligne.
const lastChart = k1.lastIndexOf('Graphique')
await page.click(`[data-testid="tile-size-${lastChart}"]`)
await page.click('li[role="option"]:has-text("1/1")')
const span = await page.getAttribute(`[data-testid="tile-${lastChart}"]`, 'style')
if (span.includes('--span: 12')) ok('largeur « 1/1 » appliquée')
else fail(`largeur: ${span}`)

// Ajouter une carte de chiffres : somme du total des factures.
await page.click('[data-testid="editor-add"]')
await page.waitForTimeout(500) // l'animation d'ouverture du menu
await page.click('li[role="menuitem"]:has-text("Chiffre")')
await page.fill('[data-testid="bf-label"]', 'Total facturé')
await page.click('[data-testid="bf-doctype"]')
await page.fill('.p-select-overlay input', 'Facture')
await page.click('li[role="option"]:has-text("Facture")')
await page.click('[data-testid="bf-function"]')
await page.click('li[role="option"]:has-text("Somme")')
await page.click('[data-testid="bf-field"]')
await page.click('li[role="option"]:has-text("Total")')
await page.screenshot({ path: `${out}/11-edit-dialog.png` })
await page.click('[data-testid="bf-validate"]')
await page.waitForTimeout(800)
const addedAt = (await kindsOf()).length - 1
const txt = await page.textContent(`[data-testid="tile-${addedAt}"]`)
if (/Total facturé/.test(txt) && /\d/.test(txt)) ok(`carte ajoutée, calculée en direct : ${txt.replace(/\s+/g, ' ').trim().slice(0, 60)}`)
else fail(`carte ajoutée : ${txt}`)

await page.screenshot({ path: `${out}/12-edit-before-save.png` })
await page.click('[data-testid="editor-save"]')
await page.waitForSelector('[data-testid="workspace-edit"]')
await page.waitForTimeout(1500)
await page.reload()
await page.waitForSelector('[data-testid="workspace-edit"]')
await page.waitForTimeout(2500)
const after = await titles()
if (after.some((x) => x.startsWith('Total facturé')) && JSON.stringify(after) !== JSON.stringify(before)) ok('la disposition survit au rechargement')
else fail(`après rechargement : ${after.join(' | ')}`)
await page.screenshot({ path: `${out}/13-after-save.png`, fullPage: true })

// Retour à l'origine.
await page.click('[data-testid="workspace-edit"]')
await page.click('[data-testid="editor-reset"]')
await page.click('.p-confirmdialog-accept-button, .p-confirmdialog button:has-text("Revenir")')
await page.waitForSelector('[data-testid="workspace-edit"]')
await page.waitForTimeout(2500)
const restored = await titles()
if (JSON.stringify(restored) === JSON.stringify(before)) ok('« Revenir à la disposition d’origine » rétablit le YAML')
else fail(`restauré : ${restored.join(' | ')} / avant : ${before.join(' | ')}`)

// ── Le tableau de bord d'un type (Facture) : retirer une carte, enregistrer, recharger, revenir au tableau généré.
await page.goto(process.env.DESK_URL + '#/app/Invoice?view=dashboard')
await page.waitForSelector('[data-testid="dashboard-edit"]')
await page.waitForTimeout(1500)
const cardsBefore = await page.$$eval('.number-card', (e) => e.length)
await page.click('[data-testid="dashboard-edit"]')
await page.waitForSelector('[data-testid="block-grid-edit"]')
await page.click('[data-testid="tile-remove-0"]')
await page.screenshot({ path: `${out}/14-dashboard-edit.png` })
await page.click('[data-testid="editor-save"]')
await page.waitForSelector('[data-testid="dashboard-edit"]')
await page.reload()
await page.waitForSelector('[data-testid="dashboard-edit"]')
await page.waitForTimeout(2000)
const cardsAfter = await page.$$eval('.number-card', (e) => e.length)
if (cardsAfter === cardsBefore - 1) ok(`tableau de bord de Facture personnalisé : ${cardsBefore} → ${cardsAfter} cartes, après rechargement`)
else fail(`cartes ${cardsBefore} → ${cardsAfter}`)
await page.click('[data-testid="dashboard-edit"]')
await page.click('[data-testid="editor-reset"]')
await page.click('.p-confirmdialog-accept-button, .p-confirmdialog button:has-text("Revenir")')
await page.waitForSelector('[data-testid="dashboard-edit"]')
await page.waitForTimeout(1500)
const cardsReset = await page.$$eval('.number-card', (e) => e.length)
if (cardsReset === cardsBefore) ok('retour au tableau généré')
else fail(`après retour : ${cardsReset}`)

console.log(problems.slice(0, 8).join('\n') || 'aucune erreur navigateur')
await browser.close()
