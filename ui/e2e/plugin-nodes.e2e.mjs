/**
 * Les nœuds et les déclencheurs qu'un PLUGIN apporte à l'éditeur d'automatisations, dans un vrai navigateur face au VRAI xflow, avec
 * le plugin de test `mailer` (tests/support/plugins/mailer : section `xflow:` de son manifeste) :
 *
 * 1. le déclencheur propose l'événement du plugin, « E-mail reçu », et dit ce que ses étapes pourront en reprendre ;
 * 2. la palette a sa rubrique « Des applications » avec le nœud « Envoyer un e-mail » ;
 * 3. une étape « Créer un document » crée un client dont le nom vient de l'événement (« Du déclencheur › Objet ») ;
 * 4. le nœud du plugin, posé d'un clic et relié, répond à l'expéditeur en citant le client créé (« D'une étape › action_1 ») ;
 * 5. un second nœud reprend le RÉSULTAT du premier (« Résultat d'une étape › action_2 › Identifiant du message ») ;
 * 6. « Déployer », puis un e-mail reçu par la route du plugin : le flux tourne, les deux e-mails partent, le client existe.
 *
 * Captures `plug-*.png` dans SHOTS_DIR. `MAILER_OUTBOX` : le fichier où le plugin de test « envoie ».
 */
import { existsSync, readFileSync } from 'node:fs'
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const NAME = 'reponse_mail'
const OUTBOX = process.env.MAILER_OUTBOX ?? ''

async function pick(testid, option) {
  await page.keyboard.press('Escape')
  await page.getByTestId(testid).click()
  await page.getByRole('option', { name: option, exact: true }).first().click()
  await page.locator('.p-select-overlay').waitFor({ state: 'hidden' }).catch(() => undefined)
}

const mails = () => (OUTBOX && existsSync(OUTBOX) ? readFileSync(OUTBOX, 'utf-8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [])

console.log('Desk — nœuds et déclencheurs de plugin (xflow)')
try {
  await r.step('le déclencheur propose l’événement du plugin « E-mail reçu »', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.getByTestId('nav-automations').waitFor()
    await page.goto(`${cfg.desk}#/automations/new`)
    await page.getByTestId('fl-palette').waitFor()
    await page.getByTestId('fl-name').fill(NAME)
    await page.getByTestId('fl-description').fill('Répond à un e-mail reçu et crée la fiche du client')
    await pick('fl-trigger', 'Événement')
    await pick('fl-event-origin', 'E-mail reçu')
    await page.getByTestId('fl-event-fields').waitFor()
    assert((await page.getByTestId('fl-event-fields').innerText()).includes('Expéditeur'), 'les clés de l’événement sont dites')
    assert((await page.getByTestId('fl-node-trigger').innerText()).includes('E-mail reçu'), 'la carte du déclencheur nomme l’événement')
    // La palette : la rubrique des plugins, avec le nœud du plugin.
    await page.getByTestId('fl-add-node-mailer:mail.send').waitFor()
    await r.shot(page, 'plug-01-declencheur-et-palette')
  }, page)

  await r.step('une étape crée un client dont le nom vient de l’événement', async () => {
    await page.getByTestId('fl-add-action').click()
    await page.getByTestId('fl-node-action_1').waitFor()
    const other = page.getByRole('button', { name: 'En choisir un autre' })
    if (await other.isVisible().catch(() => false)) await other.click()
    await pick('fl-doctype', 'Client')
    await pick('fl-new-field', 'Nom')
    await page.getByTestId('fl-add-field').click()
    await pick('fl-data-customer_name-source', 'Du déclencheur')
    await pick('fl-data-customer_name', 'Objet')
    await page.getByTestId('wf-fit').click() // tout le flux dans le cadre
    await page.waitForTimeout(300)
    await r.shot(page, 'plug-02-creer-le-client')
  }, page)

  await r.step('le nœud du plugin, posé d’un clic, répond à l’expéditeur en citant le client créé', async () => {
    await page.getByTestId('fl-add-node-mailer:mail.send').click()
    await page.getByTestId('fl-node-action_2').waitFor()
    assert((await page.getByTestId('fl-node-action_2').innerText()).includes('Envoyer un e-mail'), 'la carte dit le nœud du plugin')
    assert((await page.getByTestId('fl-node-title').innerText()) === 'Envoyer un e-mail', 'le panneau montre son formulaire')
    await page.getByTestId('fl-incomplete-action_2').waitFor() // destinataire et objet obligatoires, encore vides
    await pick('fl-node-field-to-source', 'Du déclencheur')
    await pick('fl-node-field-to', 'Expéditeur')
    await page.getByTestId('fl-node-field-subject').fill('Votre fiche client est ouverte')
    await pick('fl-node-field-body-source', 'D’une étape')
    await pick('fl-node-field-body', 'action_1')
    await page.getByTestId('fl-incomplete-action_2').waitFor({ state: 'detached' })
    assert((await page.getByTestId('fl-step-panel').innerText()).includes('{{') === false, 'aucune accolade dans le panneau')
    await page.getByTestId('wf-fit').click() // tout le flux dans le cadre
    await page.waitForTimeout(300)
    await r.shot(page, 'plug-03-noeud-du-plugin')
  }, page)

  await r.step('un second nœud reprend le résultat du premier', async () => {
    await page.getByTestId('fl-add-node-mailer:mail.send').click()
    await page.getByTestId('fl-node-action_3').waitFor()
    await page.getByTestId('fl-node-field-to').fill('suivi@example.com')
    await page.getByTestId('fl-node-field-subject').fill('Réponse envoyée')
    await pick('fl-node-field-body-source', 'Résultat d’une étape')
    await pick('fl-node-field-body', 'action_2 › Identifiant du message')
    assert((await page.locator('.vue-flow__edge').count()) >= 3, `flèches : ${await page.locator('.vue-flow__edge').count()}`)
    await page.getByTestId('wf-fit').click() // tout le flux dans le cadre
    await page.waitForTimeout(300)
    await r.shot(page, 'plug-04-resultat-repris')
  }, page)

  await r.step('« Déployer », puis un e-mail reçu : le flux tourne, les deux e-mails partent, le client existe', async () => {
    await page.getByTestId('fl-deploy').click()
    await page.waitForURL(new RegExp(`#/automations/${NAME}`))
    const received = await fetch(`${cfg.api.replace(/\/xcore-doctype$/, '/mailer')}/receive`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${cfg.token}`, 'X-Tenant-ID': cfg.tenant, 'Content-Type': 'application/json' },
      body: JSON.stringify({ sender: 'kone@example.com', subject: 'Koné SARL' }),
    })
    assert(received.ok, `réception : ${received.status}`)
    for (let i = 0; i < 60 && mails().length < 2; i++) await page.waitForTimeout(250)
    const sent = mails()
    assert(sent.length === 2, `e-mails envoyés : ${JSON.stringify(sent)}`)
    const list = await api(cfg, 'GET', `/list/Customer?filters=${encodeURIComponent(JSON.stringify({ customer_name: 'Koné SARL' }))}`)
    assert(list.status === 200 && list.json.total === 1, `client : ${JSON.stringify(list.json)}`)
    const customer = list.json.items[0]
    assert(sent[0].to === 'kone@example.com' && sent[0].body === customer.name, `réponse : ${JSON.stringify(sent[0])}`)
    assert(sent[1].body === sent[0].message_id, `le second e-mail cite le premier : ${JSON.stringify(sent[1])}`)

    await page.getByTestId('fl-to-list').click()
    const ok = page.locator('[data-testid^="run-"] .status-pill.ok')
    for (let i = 0; i < 40 && !(await ok.count()); i++) {
      await page.waitForTimeout(250)
      await page.getByTestId('automations-refresh').click()
    }
    assert(await ok.count(), `aucune exécution réussie : ${await page.getByTestId('automations-runs').innerText().catch(() => '?')}`)
    await r.shot(page, 'plug-05-execution')
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByText('Koné SARL').first().waitFor()
    await r.shot(page, 'plug-06-client-cree')
  }, page)

  await r.step('rouvert, le flux se relit tel qu’il a été tracé', async () => {
    await page.goto(`${cfg.desk}#/automations/${NAME}`)
    await page.getByTestId('fl-node-action_3').waitFor()
    await page.getByTestId('fl-node-action_3').click()
    await page.getByTestId('fl-node-title').waitFor()
    assert((await page.getByTestId('fl-node-field-body').innerText()).includes('Identifiant du message'), 'le résultat repris se relit')
    await page.getByTestId('wf-fit').click() // tout le flux dans le cadre
    await page.waitForTimeout(300)
    await r.shot(page, 'plug-07-rouvert')
  }, page)

  await r.step('« Nœuds personnalisés » : l’organisation décrit une action que le plugin ne décrit pas', async () => {
    await page.goto(`${cfg.desk}#/automations`)
    await page.getByTestId('automations-nodes').click()
    await page.waitForURL(/#\/automations\/nodes/)
    await page.getByTestId('fn-empty').waitFor()
    await page.getByTestId('fn-create').click()
    await page.getByTestId('fn-label').fill('Noter dans le journal')
    await page.getByTestId('fn-icon').fill('book')
    await page.getByTestId('fn-description').fill('Garde une trace dans le journal du courrier.')
    await page.getByTestId('fn-plugin').fill('mailer')
    await page.getByTestId('fn-action').fill('mail.log')
    await page.getByTestId('fn-add-field').click()
    await page.getByTestId('fn-field-label-0').fill('Note')
    await page.getByTestId('fn-field-required-0').click()
    await page.getByTestId('fn-add-output').click()
    await page.getByTestId('fn-output-label-0').fill('Numéro de la note')
    await page.getByTestId('fn-output-path-0').fill('entry')
    assert((await page.getByTestId('fn-field-name-0').inputValue()) === 'note', 'la clé suit le libellé')
    await r.shot(page, 'plug-08-decrire-un-noeud')
    await page.getByTestId('fn-save').click()
    await page.getByTestId('fn-node-mailer:mail.log').waitFor()
    await r.shot(page, 'plug-09-noeud-enregistre')
  }, page)

  await r.step('le nœud décrit se pose dans un flux, se règle par son formulaire et s’exécute', async () => {
    await page.goto(`${cfg.desk}#/automations/new`)
    await page.getByTestId('fl-palette').waitFor()
    await page.getByTestId('fl-name').fill('journal')
    await page.getByTestId('fl-add-node-mailer:mail.log').click()
    await page.getByTestId('fl-node-action_1').waitFor()
    assert((await page.getByTestId('fl-node-title').innerText()) === 'Noter dans le journal', 'le panneau montre le nœud décrit')
    await page.getByTestId('fl-incomplete-action_1').waitFor() // la note est obligatoire
    await page.getByTestId('fl-node-field-note').fill('Bonjour du Desk')
    await page.getByTestId('fl-incomplete-action_1').waitFor({ state: 'detached' })
    await page.getByTestId('wf-fit').click()
    await page.waitForTimeout(300)
    await r.shot(page, 'plug-10-noeud-dans-un-flux')
    await page.getByTestId('fl-deploy').click()
    await page.waitForURL(/#\/automations\/journal/)
    await page.getByTestId('fl-to-list').click()
    const before = mails().length
    await page.getByTestId('flow-run-journal').click()
    for (let i = 0; i < 60 && mails().length === before; i++) await page.waitForTimeout(250)
    const last = mails().at(-1)
    assert(last?.note === 'Bonjour du Desk', `journal : ${JSON.stringify(mails())}`)
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
