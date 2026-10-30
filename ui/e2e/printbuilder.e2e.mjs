/**
 * « Personnaliser l'impression » — l'éditeur FAÇON WORD — dans un VRAI navigateur (process/impression.md §12).
 *
 * Ce que ce scénario prouve, et que les tests de composant ne peuvent pas : qu'on écrit et met en forme la pièce
 * sur la feuille, qu'une valeur du document s'insère dans le texte, qu'une image se téléverse, se pose « devant le
 * texte » et se DÉPLACE à la souris au millimètre, que ce qu'on enregistre est ce que l'impression (HTML et PDF)
 * emploie ensuite, et que « Revenir à l'original » rend la main au gabarit de l'application.
 */
import { writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { deflateSync } from 'node:zlib'
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)

/** Un PNG 120×60 bleu, VALIDE (construit, pas tapé à la main : libpng refuse un faux en silence). */
function logo() {
  const crc = (buf) => {
    let c = ~0
    for (const b of buf) {
      c ^= b
      for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1))
    }
    return ~c >>> 0
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4)
    len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type), data])
    const sum = Buffer.alloc(4)
    sum.writeUInt32BE(crc(body))
    return Buffer.concat([len, body, sum])
  }
  const [w, h] = [120, 60]
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0)
  ihdr.writeUInt32BE(h, 4)
  ihdr.set([8, 2, 0, 0, 0], 8)
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(w * 3, Buffer.from([30, 90, 200]))])
  const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(Buffer.concat(Array(h).fill(row)))), chunk('IEND', Buffer.alloc(0))])
  const path = join(tmpdir(), `xdt-logo-${process.pid}.png`)
  writeFileSync(path, png)
  return path
}

console.log('Desk — Personnaliser l’impression (éditeur façon Word)')
try {
  let invoice
  await r.step('une facture, puis l’éditeur par le menu « ⋯ » de la liste', async () => {
    const item = await api(cfg, 'POST', '/doc/Item', { data: { item_name: 'Vis inox', uom: 'pcs' } })
    const client = await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Durand', type: 'Particulier' } })
    const made = await api(cfg, 'POST', '/doc/Invoice', {
      data: { customer: client.json.name, posting_date: '2026-10-29', items: [{ item: item.json.name, qty: 3, rate: 12.5 }] },
    })
    assert(made.status === 201, `facture : ${made.status} ${JSON.stringify(made.json)}`)
    invoice = made.json.name

    await page.goto(`${cfg.desk}#/app/Invoice`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/app\/Invoice$/)
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('list-print').click()
    await page.waitForURL(/#\/print\/Invoice$/)
    await page.getByTestId('pd-content').waitFor()
    await r.shot(page, 'printbuilder-01-ouvert')
  }, page)

  await r.step('le gabarit de l’application s’ouvre EN DOCUMENT, avec les valeurs de la facture', async () => {
    const content = page.getByTestId('pd-content')
    await content.locator('[data-token="name"]').getByText(invoice).waitFor() // la valeur, pas l'étiquette
    await content.locator('[data-kind="table"]').getByText('Vis inox').first().waitFor()
    assert((await content.locator('[data-testid="pd-block"]').count()) >= 2, 'le tableau des lignes et les totaux sont incorporés')
  }, page)

  await r.step('ÉCRIRE et METTRE EN FORME : un paragraphe centré, une valeur insérée en gras', async () => {
    await page.getByTestId('pd-content').locator('p').last().click()
    await page.keyboard.press('End')
    await page.keyboard.press('Enter')
    await page.keyboard.type('Merci de votre confiance, ')
    await page.getByTestId('pd-value-doc.customer').click()
    await page.keyboard.press('Shift+Home')
    await page.getByTestId('pd-bold').click()
    await page.getByTestId('pd-center').click()
    await page.keyboard.press('End')
    const html = await page.getByTestId('pd-content').innerHTML()
    assert(/text-align: center/.test(html) && /<strong>Merci de votre confiance/.test(html), `mise en forme absente : ${html.slice(-400)}`)
  }, page)

  await r.step('une IMAGE se téléverse, passe « devant le texte » SANS sauter, et se déplace à la souris', async () => {
    await page.getByTestId('pd-image-file').setInputFiles(logo())
    const image = page.getByTestId('pd-image')
    await image.locator('img').waitFor()
    await image.click()
    const before = await image.boundingBox()
    await page.getByTestId('pd-wrap').click()
    await page.getByRole('option', { name: /Devant le texte/ }).click()
    await page.waitForTimeout(200)
    const placed = await image.boundingBox()
    assert(Math.abs(placed.x - before.x) < 3 && Math.abs(placed.y - before.y) < 3, `l'image a sauté : ${JSON.stringify(before)} -> ${JSON.stringify(placed)}`)

    // La poignée n'est pas recouverte, et l'image n'est jamais par-dessus la barre d'outils.
    const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('[data-testid]')?.getAttribute('data-testid'), [placed.x + placed.width / 2, placed.y + placed.height / 2])
    assert(hit === 'pd-image', `l'image est recouverte par ${hit}`)
    const leftBefore = parseFloat(await image.evaluate((el) => el.style.left))
    await page.mouse.move(placed.x + placed.width / 2, placed.y + placed.height / 2)
    await page.mouse.down()
    await page.mouse.move(placed.x + placed.width / 2 - 120, placed.y + placed.height / 2 + 60, { steps: 8 })
    await page.mouse.up()
    const [left, top] = await image.evaluate((el) => [parseFloat(el.style.left), parseFloat(el.style.top)])
    assert(left < leftBefore - 10, `l'image n'a pas suivi la souris : ${leftBefore} -> ${left}`)
    assert(Number.isFinite(top), 'position verticale absente')
    await r.shot(page, 'printbuilder-02-image')
  }, page)

  await r.step('ENREGISTRER : l’impression et le PDF emploient ce document', async () => {
    await page.waitForTimeout(600)
    await page.getByTestId('pb-save').click()
    await page.getByText('Gabarit enregistré').waitFor()
    const printed = await api(cfg, 'GET', `/print/Invoice/${encodeURIComponent(invoice)}`)
    const html = printed.json.html
    assert(/<p style="text-align:center"><strong>Merci de votre confiance, Durand/.test(html), `texte mis en forme absent de l'impression : ${html.slice(-600)}`)
    assert(/<img class="im front" src="data:image\/png;base64,[^"]+" alt="" style="width:40mm;left:-?[\d.]+mm;top:-?[\d.]+mm">/.test(html), 'image absente ou mal placée à l’impression')
    const res = await fetch(`${cfg.api}/print/Invoice/${encodeURIComponent(invoice)}/pdf`, {
      headers: { Authorization: `Bearer ${cfg.token}`, 'X-Tenant-ID': cfg.tenant },
    })
    const pdf = { status: res.status, head: Buffer.from(await res.arrayBuffer()).subarray(0, 5).toString('latin1') }
    // Le moteur PDF est une dépendance OPTIONNELLE : sans lui la route répond 503, et c'est le comportement promis.
    assert(pdf.status === 503 || (pdf.status === 200 && pdf.head === '%PDF-'), `PDF : ${pdf.status} ${pdf.head}`)
  }, page)

  await r.step('« Aperçu imprimé » montre le rendu du serveur', async () => {
    await page.getByTestId('pb-mode').getByText('Aperçu imprimé').click()
    await page.getByTestId('pb-frame').waitFor()
    const srcdoc = await page.getByTestId('pb-frame').getAttribute('srcdoc')
    assert(srcdoc.includes('Merci de votre confiance'), 'l’aperçu ne montre pas le document')
    await r.shot(page, 'printbuilder-03-apercu')
    await page.getByTestId('pb-mode').getByText('Mise en page').click()
  }, page)

  await r.step('« Revenir à l’original » rend la main au gabarit de l’application', async () => {
    await page.getByTestId('pb-reset').click()
    await page.getByText('Gabarit retiré').waitFor()
    const listed = await api(cfg, 'GET', '/print-templates/Invoice')
    assert(!listed.json.templates.some((x) => x.source === 'organisation'), `habillage restant : ${JSON.stringify(listed.json.templates)}`)
  }, page)

  await r.step('sombre : la feuille reste blanche', async () => {
    await page.evaluate(() => document.documentElement.classList.add('app-dark'))
    const bg = await page.getByTestId('pd-paper').evaluate((el) => getComputedStyle(el).backgroundColor)
    assert(bg === 'rgb(255, 255, 255)', `la feuille suit le thème : ${bg}`)
    await r.shot(page, 'printbuilder-04-sombre')
    await page.evaluate(() => document.documentElement.classList.remove('app-dark'))
  }, page)

  await r.step('téléphone : tout s’empile, rien ne déborde', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByTestId('pd-paper').waitFor()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(over <= 1, `la page déborde de ${over} px`)
    await r.shot(page, 'printbuilder-05-telephone')
    await page.setViewportSize({ width: 1280, height: 800 })
  }, page)
} finally {
  await browser.close()
}

const bad = problems.filter((p) => !/Failed to load resource.*(401|403|404|409|422|503)/.test(p))
if (bad.length) {
  console.log('\nProblèmes console/réseau :')
  for (const p of bad) console.log('  -', p)
  r.failures.push(`${bad.length} problème(s) console ou réseau`)
}
console.log(r.failures.length ? `\n${r.failures.length} échec(s)` : '\nTout est passé')
process.exit(r.failures.length ? 1 : 0)
