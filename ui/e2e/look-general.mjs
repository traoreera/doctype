// Paramètres > Général dans un vrai navigateur : nom, logo, couleur et format de date de l'organisation s'appliquent à tout le Desk dès
// l'enregistrement ; puis on remet les réglages à zéro (le tenant de démonstration retrouve son allure).
import { readFileSync, writeFileSync } from 'node:fs'
import { deflateSync } from 'node:zlib'
import { api, launch } from './helpers.mjs'

const out = process.env.SHOTS_DIR
const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)

// Un PNG valide de 64 × 64, fabriqué ici (pas de fichier d'image à ranger dans le dépôt).
function png() {
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0 })
  const crc = (b) => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0 }
  const chunk = (type, data) => { const t = Buffer.from(type); const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const c = Buffer.alloc(4); c.writeUInt32BE(crc(Buffer.concat([t, data]))); return Buffer.concat([len, t, data, c]) }
  const w = 64
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(w, 4); ihdr[8] = 8; ihdr[9] = 6
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: w }, () => [220, 60, 40, 255]).flat())])
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(Buffer.concat(Array(w).fill(row)))), chunk('IEND', Buffer.alloc(0))])
}

const { browser, page, problems } = await launch()
await page.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), process.env.DESK_TOKEN)
await page.goto(process.env.DESK_URL + '#/settings')
const form = page.locator('[data-testid="settings-general-System Settings"]')
await form.waitFor()
await page.waitForTimeout(1000)

const logoFile = `${out}/logo.png`
writeFileSync(logoFile, png())
await form.locator('input[type="text"], input:not([type])').first().fill('Atelier Dupont')
await page.setInputFiles('[data-testid="settings-general-System Settings"] input[type="file"]', logoFile)
await page.waitForTimeout(1000)
await form.locator('.p-select').click()
await page.click('li[role="option"]:has-text("AAAA-MM-JJ")')
await form.locator('input[type="color"]').first().evaluate((el) => { el.value = '#d6452a'; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })) })
await form.locator('[data-testid="save"]').click()
await page.waitForTimeout(1800)

const name = await page.textContent('[data-testid="brand-name"]')
name === 'Atelier Dupont' ? ok('le nom de l’organisation remplace « Desk »') : fail(`nom : ${name}`)
;(await page.locator('[data-testid="brand-logo"]').count()) === 1 ? ok('le logo est affiché en haut') : fail('pas de logo')
;(await page.title()) === 'Atelier Dupont' ? ok('le titre de l’onglet porte le nom') : fail(`titre : ${await page.title()}`)
const primary = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--p-primary-color').trim())
primary === '#d6452a' ? ok('la couleur de l’organisation s’applique aux boutons') : fail(`couleur : ${primary}`)

await page.goto(process.env.DESK_URL + '#/app/Invoice')
await page.waitForTimeout(1800)
const firstDate = await page.locator('tbody tr').first().locator('td').nth(3).textContent().catch(() => '')
;/^\d{4}-\d{2}-\d{2}$/.test((firstDate ?? '').trim()) ? ok(`les dates suivent le format choisi (${firstDate.trim()})`) : fail(`date : ${firstDate}`)
await page.screenshot({ path: `${out}/50-branded.png` })

// Remise à zéro (par l'API : l'enregistrement remplace le document, tout champ absent redevient son défaut).
const cfg = { api: process.env.DESK_API, token: process.env.DESK_TOKEN, tenant: process.env.DESK_TENANT }
const current = (await api(cfg, 'GET', '/doc/System%20Settings/System%20Settings')).json
const reset = await api(cfg, 'PUT', '/doc/System%20Settings/System%20Settings', { version: current.version, data: {} })
reset.status === 200 ? ok('réglages remis à zéro') : fail(`remise à zéro : ${reset.status}`)
console.log(problems.slice(0, 8).join('\n') || 'aucune erreur navigateur')
await browser.close()
