/**
 * Le constructeur de DocTypes (mode développeur) dans un vrai navigateur : la palette de commandes l'ouvre, on bâtit un type (un champ de
 * chaque sorte utile, un lien vers `Customer`, un champ glissé dans une nouvelle section), le serveur le juge, on TÉLÉCHARGE le YAML — que
 * le test Python fait ensuite charger par `xcore-doctype check`, comme un plugin le chargerait. Puis un type existant s'ouvre depuis sa
 * liste et garde ce que le constructeur n'édite pas. Aucune erreur de console.
 */
import { readFile, writeFile } from 'node:fs/promises'
import { assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const out = process.env.DEV_YAML_OUT

console.log('Desk — constructeur de DocTypes (mode développeur)')
try {
  await r.step('connexion, puis « Nouveau type de document » par la palette de commandes', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/$/)
    await page.keyboard.press('Control+k')
    await page.getByTestId('palette').locator('input').fill('nouveau type')
    await page.keyboard.press('Enter')
    await page.waitForURL(/#\/dev\/doctype$/)
    await page.getByRole('heading', { level: 1, name: 'Nouveau type de document' }).waitFor()
  }, page)

  await r.step('le type : nom, libellé, module, numérotation', async () => {
    await page.getByTestId('dev-type-name').fill('Support Ticket')
    await page.getByTestId('dev-type-label').fill('Ticket de support')
    await page.getByTestId('dev-type-module').fill('support')
  }, page)

  await r.step('des champs : une liste de choix, un lien vers les clients, une date', async () => {
    await page.getByTestId('dev-add-Select').click()
    await page.getByTestId('dev-label').fill('Priorité')
    await page.getByTestId('dev-name').fill('priorite')
    await page.getByTestId('dev-name').press('Enter')
    await page.getByTestId('dev-values').fill('Basse\nHaute')
    await page.getByTestId('dev-add-Link').click()
    await page.getByTestId('dev-label').fill('Client')
    await page.getByTestId('dev-name').fill('client')
    await page.getByTestId('dev-name').press('Enter')
    await page.getByTestId('dev-target').click()
    await page.getByRole('option', { name: /Customer/ }).click()
    await page.getByTestId('dev-add-Date').click()
    await page.getByTestId('fb-field-priorite').waitFor()
    await r.shot(page, 'devbuilder-01-champs')
  }, page)

  await r.step('une nouvelle section, et un vrai glisser-déposer du lien dedans', async () => {
    await page.getByTestId('fb-add-section').click()
    await page.getByTestId('fb-field-client').dragTo(page.getByTestId('fb-column-1-0'))
    await page.getByTestId('fb-column-1-0').getByTestId('fb-field-client').waitFor()
  }, page)

  await r.step('vérifier, puis télécharger le YAML jugé par le serveur', async () => {
    await page.getByTestId('dev-check').click()
    await page.getByTestId('dev-ok').waitFor()
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByTestId('dev-download').click()])
    assert(download.suggestedFilename() === 'support_ticket.yaml', `nom du fichier : ${download.suggestedFilename()}`)
    const text = await readFile(await download.path(), 'utf8')
    assert(text.includes('name: Support Ticket') && text.includes('options: Customer') && text.includes('Haute'), `YAML :\n${text}`)
    if (out) await writeFile(out, text)
    await r.shot(page, 'devbuilder-02-yaml')
  }, page)

  await r.step('un refus du serveur se lit ligne par ligne', async () => {
    await page.getByTestId('fb-field-client').click()
    await page.getByTestId('dev-type').click()
    await page.getByRole('option', { name: 'Table', exact: true }).click() // une table vers… rien : refusée
    await page.getByTestId('dev-check').click()
    await page.getByTestId('dev-refused').waitFor()
  }, page)

  await r.step('un type existant s’ouvre depuis sa liste et garde ce que le constructeur n’édite pas', async () => {
    page.once('dialog', (d) => d.accept()) // « quitter sans télécharger ? »
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('list-dev-doctype').click()
    await page.waitForURL(/#\/dev\/doctype\/Customer$/)
    await page.getByRole('heading', { level: 1, name: 'Type de document · Customer' }).waitFor()
    await page.getByTestId('dev-check').click()
    await page.getByTestId('dev-ok').waitFor()
    const yaml = await page.getByTestId('dev-yaml').textContent()
    assert(yaml.includes('permissions:') && yaml.includes('sales:customer:own'), `YAML de Customer :\n${yaml}`)
    await page.evaluate(() => document.documentElement.classList.add('app-dark'))
    await page.waitForTimeout(600) // les transitions de couleur du thème : la capture ne doit pas tomber au milieu
    await r.shot(page, 'devbuilder-03-existant-sombre')
  }, page)
} finally {
  await browser.close()
}

const bad = problems.filter((p) => !/Failed to load resource.*(401|403|404|409|422)/.test(p))
if (bad.length) {
  console.log('\nProblèmes console/réseau :')
  for (const p of bad) console.log('  -', p)
  r.failures.push(`${bad.length} problème(s) console ou réseau`)
}
console.log(r.failures.length ? `\n${r.failures.length} échec(s)` : '\nTout est passé')
process.exit(r.failures.length ? 1 : 0)
