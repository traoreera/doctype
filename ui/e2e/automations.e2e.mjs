/**
 * Les automatisations dans un vrai navigateur, face au VRAI xflow (livré avec le moteur) :
 *
 * 1. l'entrée « Automatisations » de la barre latérale ouvre la page, vide (xflow annoncé par `config.json`) ;
 * 2. « Nouvelle automatisation » : la page pleine largeur, palette | graphe | propriétés, avec le seul nœud du déclencheur ;
 * 3. une étape GLISSÉE de la palette sur le canevas se pose, se relie au déclencheur et s'édite dans le panneau ;
 * 4. une deuxième étape, RELIÉE à la première en tirant sa poignée « Réussi » (ce qu'xflow exécute vraiment) ;
 * 5. « Déployer » : le flux est listé ;
 * 6. « Lancer » : l'exécution réussit, et le document que son étape a créé existe, écrit AU NOM d'alice ;
 * 7. réouvrir le flux : le graphe revient tel qu'il a été tracé (positions comprises), le même flux se lit en JSON ;
 * 8. « Supprimer » le retire.
 *
 * Captures `auto-*.png` dans SHOTS_DIR.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const NAME = 'accueil_client'
const CLIENT = 'Client créé par une automatisation'

/**
 * Glisser une étape de la palette sur le canevas. `dragTo` fait le vrai glisser-déposer HTML5 de Chromium (`dataTransfer` compris) ;
 * si le navigateur ne le mène pas jusqu'au bout, on rejoue le MÊME échange d'événements pour que le scénario dise « le dépôt n'a rien
 * posé » plutôt que de mourir sur un détail d'automatisation.
 */
async function dragStep(kind, at) {
  const canvas = page.getByTestId('fl-canvas')
  const before = await page.locator('.vue-flow__node').count()
  await page.getByTestId(`fl-add-${kind}`).dragTo(canvas, { targetPosition: at })
  await page.waitForTimeout(150)
  if ((await page.locator('.vue-flow__node').count()) > before) return
  const box = await canvas.boundingBox()
  await page.evaluate(
    ({ kind, x, y }) => {
      const data = new DataTransfer()
      data.setData('application/x-xdt-flow-step', kind)
      const target = document.querySelector('[data-testid="fl-canvas"]')
      target.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: data }))
      target.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: data, clientX: x, clientY: y }))
    },
    { kind, x: box.x + at.x, y: box.y + at.y },
  )
  await page.waitForTimeout(150)
}

console.log('Desk — Automatisations (xflow)')
try {
  await r.step('la page des automatisations, depuis la barre latérale', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.getByTestId('nav-automations').click()
    await page.waitForURL(/#\/automations/)
    await page.getByTestId('automations-no-flows').waitFor()
    await r.shot(page, 'auto-01-vide')
  }, page)

  await r.step('« Nouvelle automatisation » : la page pleine largeur et son déclencheur', async () => {
    await page.getByTestId('automations-create').click()
    await page.waitForURL(/#\/automations\/new/)
    await page.getByTestId('fl-palette').waitFor()
    await page.getByTestId('fl-node-trigger').waitFor()
    await page.getByTestId('fl-empty').waitFor()
    await page.getByTestId('fl-name').fill(NAME)
    await page.getByTestId('fl-description').fill('Crée un client de bienvenue')
    await r.shot(page, 'auto-02-editeur')
  }, page)

  await r.step('une étape glissée de la palette se pose, reliée au déclencheur', async () => {
    await dragStep('action', { x: 150, y: 90 })
    await page.getByTestId('fl-node-action_1').waitFor()
    const action = page.getByTestId('fl-step-action')
    assert((await action.inputValue()) === 'xcore-doctype.doc.save', `action proposée : ${await action.inputValue()}`)
    await page.getByTestId('fl-step-payload').fill(`{\n  "doctype": "Customer",\n  "data": {"customer_name": "${CLIENT}", "type": "Particulier"}\n}`)
    // La flèche du déclencheur vers l'étape : c'est elle qui fait qu'xflow l'exécute.
    assert((await page.locator('.vue-flow__edge').count()) >= 1, 'aucune flèche depuis le déclencheur')
    assert((await page.locator('[data-testid="fl-step-panel"] .fe-var', { hasText: 'trigger.name' }).count()) === 1, 'les variables à insérer')
    await r.shot(page, 'auto-03-etape-posee')
  }, page)

  await r.step('une deuxième étape, reliée à la première en tirant sa poignée « Réussi »', async () => {
    await dragStep('template', { x: 150, y: 420 })
    await page.getByTestId('fl-node-template_1').waitFor()
    await page.getByTestId('fl-template').fill('Bonjour {{ trigger.name }}')
    // La poignée « Réussi » de l'action vers l'entrée du texte : un vrai glisser de souris, ce que fait un utilisateur.
    const out = page.getByTestId('fl-out-action_1-on_success')
    const target = page.getByTestId('fl-node-template_1')
    const from = await out.boundingBox()
    const to = await target.boundingBox()
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2)
    await page.mouse.down()
    await page.mouse.move(to.x + 4, to.y + 22, { steps: 12 })
    await page.mouse.up()
    await page.waitForTimeout(150)
    assert((await page.locator('.vue-flow__edge').count()) >= 2, `flèches : ${await page.locator('.vue-flow__edge').count()}`)
    await r.shot(page, 'auto-04-reliee')
  }, page)

  await r.step('« Déployer » : le flux est listé', async () => {
    await page.getByTestId('fl-deploy').click()
    await page.waitForURL(new RegExp(`#/automations/${NAME}`))
    await page.getByTestId('fl-to-list').click()
    await page.getByTestId(`flow-${NAME}`).waitFor()
    await r.shot(page, 'auto-05-deploye')
  }, page)

  await r.step('« Lancer » : l’exécution réussit et son étape a créé le client au nom d’alice', async () => {
    await page.getByTestId(`flow-run-${NAME}`).click()
    const ok = page.locator('[data-testid^="run-"] .status-pill.ok')
    for (let i = 0; i < 40 && !(await ok.count()); i++) {
      await page.waitForTimeout(250)
      await page.getByTestId('automations-refresh').click()
    }
    assert(await ok.count(), `aucune exécution réussie : ${await page.getByTestId('automations-runs').innerText().catch(() => '?')}`)
    await r.shot(page, 'auto-06-execution')
    const list = await api(cfg, 'GET', `/list/Customer?filters=${encodeURIComponent(JSON.stringify({ customer_name: CLIENT }))}`)
    assert(list.status === 200 && list.json.total === 1, `client : ${JSON.stringify(list.json)}`)
    const [doc] = list.json.items
    assert(doc.owner === 'alice', `écrit au nom de ${doc.owner}`)
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByText(CLIENT).first().waitFor()
    await r.shot(page, 'auto-07-client-cree')
  }, page)

  await r.step('réouvrir le flux : le graphe revient tel quel, et le même flux se lit en JSON', async () => {
    await page.goto(`${cfg.desk}#/automations/${NAME}`)
    await page.getByTestId('fl-node-action_1').waitFor()
    await page.getByTestId('fl-node-template_1').waitFor()
    assert(await page.getByTestId('fl-name').isDisabled(), 'le nom ne change plus après le premier déploiement')
    await page.getByTestId('fl-mode').click()
    const json = JSON.parse(await page.getByTestId('fl-json').inputValue())
    assert(json.name === NAME && json.steps[0].plugin === 'xcore-doctype', JSON.stringify(json))
    assert(json.steps[0].on_success === 'template_1', `enchaînement : ${JSON.stringify(json.steps[0])}`)
    assert(typeof json.steps[0]._x === 'number', `la position du nœud voyage avec le flux : ${JSON.stringify(json.steps[0])}`)
    assert(!('deployed_by' in json), 'le serveur réécrit deployed_by : il ne repart pas du navigateur')
    await r.shot(page, 'auto-08-json')
    await page.getByTestId('fl-mode').click()
    await page.getByTestId('fl-description').fill('Crée un client de bienvenue (v2)')
    await page.getByTestId('fl-deploy').click()
    await page.getByTestId('fl-to-list').click()
    await page.getByTestId(`flow-${NAME}`).getByText('(v2)').waitFor()
    await r.shot(page, 'auto-09-modifie')
  }, page)

  await r.step('« Supprimer » le retire', async () => {
    await page.getByTestId(`flow-remove-${NAME}`).click()
    await page.locator('.p-confirmdialog').getByRole('button', { name: 'Supprimer' }).click()
    await page.getByTestId('automations-no-flows').waitFor()
    await r.shot(page, 'auto-10-supprime')
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
