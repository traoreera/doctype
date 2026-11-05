/**
 * Paramètres › Automatisations dans un vrai navigateur, face au VRAI xflow (livré avec le moteur) :
 *
 * 1. la page est là (xflow annoncé par `config.json`), vide ;
 * 2. « Nouvelle automatisation » : le formulaire (nom, déclencheur, une étape « action » proposée par le catalogue, sa charge utile
 *    et les variables à insérer), le même flux vu en JSON ;
 * 3. « Déployer » : le flux est listé ;
 * 4. « Lancer » : l'exécution réussit, et le document que son étape a créé existe, écrit AU NOM d'alice ;
 * 5. « Modifier » redéploie sous le même nom, « Supprimer » le retire.
 *
 * Captures `auto-*.png` dans SHOTS_DIR.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const NAME = 'accueil_client'
const CLIENT = 'Client créé par une automatisation'

console.log('Desk — Paramètres › Automatisations (xflow)')
try {
  await r.step('la page des automatisations, vide', async () => {
    await page.goto(`${cfg.desk}#/settings/~automations`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/settings\/~automations/)
    await page.getByTestId('automation-automations').waitFor()
    await page.getByTestId('automations-no-flows').waitFor()
    await r.shot(page, 'auto-01-vide')
  }, page)

  await r.step('« Nouvelle automatisation » : le formulaire', async () => {
    await page.getByTestId('automations-create').click()
    await page.getByTestId('flow-editor').waitFor()
    await page.getByTestId('flow-name').fill(NAME)
    await page.getByTestId('flow-description').fill('Crée un client de bienvenue')
    const action = page.getByTestId('flow-step-action-0')
    assert((await action.inputValue()) === 'xcore-doctype.doc.save', `action proposée : ${await action.inputValue()}`)
    const payload = page.getByTestId('flow-step-payload-0')
    await payload.fill(`{\n  "doctype": "Customer",\n  "data": {"customer_name": "${CLIENT}", "type": "Particulier"}\n}`)
    assert(await page.locator('[data-testid="flow-step-0"] .fe-var', { hasText: 'trigger.name' }).count() === 1, 'les variables à insérer')
    await r.shot(page, 'auto-02-formulaire')
  }, page)

  await r.step('le même flux en JSON', async () => {
    await page.getByTestId('flow-mode').getByText('JSON').click()
    const json = await page.getByTestId('flow-json').inputValue()
    const def = JSON.parse(json)
    assert(def.name === NAME && def.steps[0].plugin === 'xcore-doctype' && def.steps[0].action === 'doc.save', json)
    assert(def.steps[0].payload.data.customer_name === CLIENT, json)
    await r.shot(page, 'auto-03-json')
    await page.getByTestId('flow-mode').getByText('Formulaire').click()
    await page.getByTestId('flow-name').waitFor()
  }, page)

  await r.step('« Déployer » : le flux est listé', async () => {
    await page.getByTestId('flow-deploy').click()
    await page.getByTestId(`flow-${NAME}`).waitFor()
    await page.getByTestId('flow-editor').waitFor({ state: 'hidden' })
    await r.shot(page, 'auto-04-deploye')
  }, page)

  await r.step('« Lancer » : l’exécution réussit et son étape a créé le client au nom d’alice', async () => {
    await page.getByTestId(`flow-run-${NAME}`).click()
    const ok = page.locator('[data-testid^="run-"] .status-pill.ok')
    for (let i = 0; i < 40 && !(await ok.count()); i++) {
      await page.waitForTimeout(250)
      await page.getByTestId('automations-refresh').click()
    }
    assert(await ok.count(), `aucune exécution réussie : ${await page.getByTestId('automations-runs').innerText().catch(() => '?')}`)
    await r.shot(page, 'auto-05-execution')
    const list = await api(cfg, 'GET', `/list/Customer?filters=${encodeURIComponent(JSON.stringify({ customer_name: CLIENT }))}`)
    assert(list.status === 200 && list.json.total === 1, `client : ${JSON.stringify(list.json)}`)
    const [doc] = list.json.items
    assert(doc.owner === 'alice', `écrit au nom de ${doc.owner}`)
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByText(CLIENT).first().waitFor()
    await r.shot(page, 'auto-06-client-cree')
  }, page)

  await r.step('« Modifier » redéploie sous le même nom', async () => {
    await page.goto(`${cfg.desk}#/settings/~automations`)
    await page.getByTestId(`flow-edit-${NAME}`).click()
    await page.getByTestId('flow-editor').waitFor()
    assert(await page.getByTestId('flow-name').isDisabled(), 'le nom ne change plus après le premier déploiement')
    await page.getByTestId('flow-description').fill('Crée un client de bienvenue (v2)')
    await page.getByTestId('flow-deploy').click()
    await page.getByTestId('flow-editor').waitFor({ state: 'hidden' })
    await page.getByTestId(`flow-${NAME}`).getByText('(v2)').waitFor()
    await r.shot(page, 'auto-07-modifie')
  }, page)

  await r.step('« Supprimer » le retire', async () => {
    await page.getByTestId(`flow-remove-${NAME}`).click()
    await page.locator('.p-confirmdialog').getByRole('button', { name: 'Supprimer' }).click()
    await page.getByTestId('automations-no-flows').waitFor()
    await r.shot(page, 'auto-08-supprime')
  }, page)
} finally {
  await browser.close()
}

const bad = problems.filter((p) => !/Failed to load resource.*(401|403|404|409|422)/.test(p) && !/xpulse\/stream/.test(p))
if (bad.length) {
  console.log('\nProblèmes console/réseau :')
  for (const p of bad) console.log('  -', p)
  r.failures.push(`${bad.length} problème(s) console ou réseau`)
}
console.log(r.failures.length ? `\n${r.failures.length} échec(s)` : '\nTout est passé')
process.exit(r.failures.length ? 1 : 0)
