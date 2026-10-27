/**
 * Le Form Builder (« Personnaliser le formulaire ») dans un vrai navigateur, sur le type `Customer` : un VRAI glisser-déposer (Chromium),
 * un champ ajouté par l'organisation, l'enregistrement, puis la fiche qui suit la nouvelle disposition ; téléphone, thème sombre, retour à
 * l'application. Aucune erreur de console.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const order = (names) => page.evaluate((ns) => {
  const all = [...document.querySelectorAll('[data-field]')].map((el) => el.getAttribute('data-field'))
  return ns.map((n) => all.indexOf(n))
}, names)

console.log('Desk — Form Builder')
try {
  await r.step('connexion, puis « Personnaliser le formulaire » depuis le menu de la liste', async () => {
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/app\/Customer$/)
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('list-customize').click()
    await page.waitForURL(/#\/customize\/Customer$/)
    await page.getByRole('heading', { level: 1, name: 'Personnaliser · Client' }).waitFor()
    await page.getByTestId('fb-field-customer_name').waitFor()
    await r.shot(page, 'formbuilder-01-ouvert')
  }, page)

  await r.step('un vrai glisser-déposer : « Client depuis » passe devant « Nom »', async () => {
    await page.getByTestId('fb-field-since').dragTo(page.getByTestId('fb-field-customer_name'))
    const [since, name] = await order(['since', 'customer_name'])
    assert(since >= 0 && since < name, `ordre après le dépôt : since=${since}, customer_name=${name}`)
  }, page)

  await r.step('ajouter un champ « Segment » : il se place, ses propriétés s’ouvrent', async () => {
    await page.getByTestId('fb-new-label').fill('Segment')
    await page.getByTestId('fb-add').click()
    await page.getByTestId('fb-field-custom_segment').waitFor()
    await page.getByTestId('fb-panel-title').filter({ hasText: 'Segment' }).waitFor()
    await r.shot(page, 'formbuilder-02-ajout')
  }, page)

  await r.step('enregistrer : le serveur garde la disposition et le champ', async () => {
    await page.getByTestId('fb-save').click()
    await page.getByText('Habillage enregistré.').waitFor()
    const stored = (await api(cfg, 'GET', '/override/form/Customer')).json.config
    const placedNames = (stored.layout ?? []).flatMap((s) => s.columns.flat())
    assert(placedNames.indexOf('since') < placedNames.indexOf('customer_name'), `disposition : ${JSON.stringify(stored.layout)}`)
    assert(placedNames.includes('custom_segment'), 'le champ ajouté est placé')
    assert((stored.added ?? []).some((a) => a.fieldname === 'custom_segment'), `ajoutés : ${JSON.stringify(stored.added)}`)
  }, page)

  await r.step('la fiche suit : « Client depuis » avant « Nom », et le champ « Segment » s’y saisit', async () => {
    await page.goto(`${cfg.desk}#/app/Customer/new`)
    await page.locator('[data-field="custom_segment"]').waitFor()
    const [since, name] = await order(['since', 'customer_name'])
    assert(since >= 0 && since < name, `ordre sur la fiche : since=${since}, customer_name=${name}`)
    await r.shot(page, 'formbuilder-03-fiche')
  }, page)

  await r.step('téléphone : tout s’empile, rien ne déborde', async () => {
    await page.goto(`${cfg.desk}#/customize/Customer`)
    await page.getByTestId('fb-field-customer_name').waitFor() // chargée : recharger plus tôt annulerait sa lecture en vol
    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload()
    await page.getByTestId('fb-field-customer_name').waitFor()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(over <= 1, `la page déborde de ${over} px`)
    await r.shot(page, 'formbuilder-04-telephone')
    await page.setViewportSize({ width: 1280, height: 800 })
  }, page)

  await r.step('thème sombre : cartes et colonnes lisibles', async () => {
    await page.evaluate(() => document.documentElement.classList.add('app-dark'))
    const bg = await page.getByTestId('fb-field-customer_name').evaluate((el) => getComputedStyle(el).backgroundColor)
    const fg = await page.getByTestId('fb-field-customer_name').evaluate((el) => getComputedStyle(el).color)
    assert(bg !== fg, `carte illisible : fond ${bg}, texte ${fg}`)
    await r.shot(page, 'formbuilder-05-sombre')
    await page.evaluate(() => document.documentElement.classList.remove('app-dark'))
  }, page)

  await r.step('« Revenir à l’application » : l’habillage est retiré', async () => {
    await page.getByTestId('fb-reset').click()
    await page.getByText('Habillage retiré').waitFor()
    const stored = (await api(cfg, 'GET', '/override/form/Customer')).json.config
    assert(!stored.layout && !stored.added, `habillage restant : ${JSON.stringify(stored)}`)
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
