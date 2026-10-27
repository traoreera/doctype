/**
 * Les automatisations (xflow) dans un vrai navigateur, sous la vraie CSP : le lien de la barre latérale n'apparaît que parce que xflow est
 * là ; une automatisation se construit au canevas (déclencheur sur un événement du moteur, nœuds Document, condition, liens tirés d'une
 * sortie), s'enregistre, part sur un VRAI événement (une demande qu'on soumet), agit au nom de qui l'a enregistrée (le commentaire est
 * signé alice), et son exécution s'allume étape par étape. Téléphone et thème sombre restent lisibles. Aucune erreur de console.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const NAME = 'longues_demandes'

async function drag(from, to) {
  const a = await page.locator(from).boundingBox()
  const b = await page.locator(to).boundingBox()
  assert(a && b, `poignées introuvables : ${from} → ${to}`)
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + 10, { steps: 12 }) // près de l'entrée de la carte (en haut) : le lien s'y accroche
  await page.mouse.up()
}

console.log('Desk — automatisations (xflow)')
try {
  await r.step('connexion ; « Automatisations » est dans la barre latérale parce que xflow est installé', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.getByTestId('nav-automations').click()
    await page.waitForURL(/#\/automations$/)
    await page.getByTestId('flows-empty').waitFor()
  }, page)

  await r.step('créer : le nom technique est vérifié avant d’ouvrir l’éditeur', async () => {
    await page.getByTestId('flow-create').click()
    await page.getByTestId('flow-new-name').fill('Longues demandes')
    assert(await page.getByTestId('flow-new-ok').isDisabled(), 'un nom invalide ne devrait pas être accepté')
    await page.getByTestId('flow-new-name').fill(NAME)
    await page.getByTestId('flow-new-ok').click()
    await page.waitForURL(new RegExp(`#/automations/${NAME}\\?new=1$`))
    await page.getByTestId(`af-node-__trigger`).waitFor()
    await page.getByTestId('af-issues').filter({ hasText: 'Ajoutez au moins une étape' }).waitFor()
  }, page)

  await r.step('déclencheur : « quand une Demande change d’état »', async () => {
    await page.getByTestId('af-choose-__trigger').click()
    await page.getByTestId('af-trigger-type').click()
    await page.getByRole('option', { name: 'Événement' }).click()
    await page.getByTestId('af-trigger-doctype').click()
    await page.getByRole('option', { name: 'Demande', exact: true }).click()
    await page.getByTestId('af-trigger-event').click()
    await page.getByRole('option', { name: 'change d’état (circuit)' }).click()
    await page.getByTestId('af-node-__trigger').filter({ hasText: 'Quand un document Demande change d’état' }).waitFor()
  }, page)

  await r.step('palette : « Lire un document » s’accroche au déclencheur, sur la Demande de l’événement', async () => {
    await page.getByTestId('af-add-doc.get').click()
    await page.getByTestId('af-node-lire').waitFor()
    await page.locator('[data-testid="af-docname"]').waitFor()
    assert((await page.getByTestId('af-docname').inputValue()) === '{{ trigger.name }}', 'le document lu devrait être celui de l’événement')
  }, page)

  await r.step('une condition reliée À LA MAIN : on tire la sortie « ensuite » de Lire jusqu’à elle', async () => {
    await page.getByTestId('af-tab-properties').click()
    // Rien de choisi (un clic dans le vide du canevas, loin des cartes et des commandes) : la condition est posée seule.
    const pane = await page.getByTestId('af-canvas').boundingBox()
    await page.mouse.click(pane.x + pane.width - 30, pane.y + pane.height - 30)
    await page.getByTestId('af-flow-desc').waitFor()
    await page.getByTestId('af-add-condition').click()
    await page.getByTestId('af-node-condition').waitFor()
    await page.getByTestId('af-label').fill('Plus de 5 jours ?')
    // La valeur à comparer : une variable insérée depuis le menu (les champs de la Demande lue par « lire »).
    await page.getByTestId('af-left-vars').click()
    await page.getByRole('menuitem', { name: 'jours' }).click()
    assert((await page.getByTestId('af-left').inputValue()) === '{{ steps.lire.result.doc.data.jours }}', 'variable mal insérée')
    await page.getByTestId('af-operator').click()
    await page.getByRole('option', { name: 'est supérieur à' }).click()
    await page.getByTestId('af-right').fill('5')
    await drag('[data-testid="af-port-lire-on_success"]', '[data-testid="af-node-condition"]')
    await page.locator('[data-testid="af-node-condition"]').waitFor()
    await page.waitForFunction(() => document.querySelectorAll('.vue-flow__edge').length >= 2)
  }, page)

  await r.step('« Commenter » sur la sortie « vrai », avec le nombre de jours dans le texte', async () => {
    await page.getByTestId('af-choose-condition').click()
    await page.getByTestId('af-add-doc.comment.add').click() // accrochée à la première sortie libre de la condition : « vrai »
    await page.getByTestId('af-node-commenter').waitFor()
    await page.getByTestId('af-comment').fill('Longue demande : {{ steps.lire.result.doc.data.jours }} jours')
    // La nouvelle carte est DANS la vue (le canevas se recadre une fois qu'elle est mesurée), pas coupée au bord.
    await page.waitForFunction(() => {
      const canvas = document.querySelector('[data-testid="af-canvas"]')?.getBoundingClientRect()
      const card = document.querySelector('[data-testid="af-node-commenter"]')?.getBoundingClientRect()
      return !!canvas && !!card && card.right <= canvas.right && card.left >= canvas.left && card.bottom <= canvas.bottom
    }, null, { timeout: 4000 })
    await page.getByTestId('af-choose-condition').click()
    assert((await page.getByTestId('af-link-if_true').textContent())?.includes('commenter'), 'la sortie « vrai » devrait mener à commenter')
    await r.shot(page, 'automations-01-canevas')
  }, page)

  await r.step('enregistrer : xflow retient qui l’a enregistrée', async () => {
    assert(!(await page.getByTestId('af-save').isDisabled()), 'rien ne devrait empêcher d’enregistrer')
    await page.getByTestId('af-save').click()
    await page.getByText('Automatisation enregistrée').waitFor()
    await page.waitForURL(new RegExp(`#/automations/${NAME}$`))
    await page.getByText('Enregistrée par alice').waitFor()
  }, page)

  let demande
  await r.step('un VRAI événement : une demande de 8 jours qu’on soumet déclenche l’automatisation, qui commente au nom d’alice', async () => {
    const made = await api(cfg, 'POST', '/doc/Demande', { data: { objet: 'Tour du monde', jours: 8 } })
    assert(made.status === 201, `création : ${made.status} ${JSON.stringify(made.json)}`)
    demande = made.json
    const moved = await api(cfg, 'POST', `/doc/Demande/${encodeURIComponent(demande.name)}/transition`, { action: 'Soumettre', version: demande.version })
    assert(moved.status < 300, `soumettre : ${moved.status} ${JSON.stringify(moved.json)}`)
    let comments = []
    for (let i = 0; i < 40 && !comments.length; i++) {
      await new Promise((ok) => setTimeout(ok, 250))
      comments = ((await api(cfg, 'GET', `/collab/Demande/${encodeURIComponent(demande.name)}`)).json?.comments ?? [])
    }
    assert(comments.length === 1, `un commentaire attendu : ${JSON.stringify(comments)}`)
    assert(comments[0].content === 'Longue demande : 8 jours', `texte : ${comments[0].content}`)
    assert(comments[0].author === 'alice' || comments[0].owner === 'alice', `signé : ${JSON.stringify(comments[0])}`)
  }, page)

  await r.step('l’exécution : réussie, et ses étapes allumées sur le canevas', async () => {
    await page.getByTestId('af-tab-runs').click()
    await page.getByTestId('af-runs-refresh').click()
    const first = page.locator('[data-testid^="af-run-"]').filter({ hasText: 'Réussie' }).first()
    await first.waitFor()
    await first.click()
    await page.getByTestId('af-run-status').filter({ hasText: 'Réussie' }).waitFor()
    await page.locator('[data-testid="af-node-commenter"][data-tone="ok"]').waitFor()
    // xflow ne trace pas une condition évaluée : pas de statut inventé (« Sautée » serait faux, elle a mené à commenter).
    assert((await page.getByTestId('af-node-condition').getAttribute('data-tone')) === null, 'la condition ne devrait porter aucun statut')
    await page.getByTestId('af-choose-lire').click()
    await page.getByTestId('af-step-result').filter({ hasText: 'Tour du monde' }).waitFor()
    await r.shot(page, 'automations-02-execution')
    await page.getByTestId('af-run-hide').click()
  }, page)

  await r.step('la liste montre la dernière exécution', async () => {
    await page.goto(`${cfg.desk}#/automations`)
    await page.getByTestId(`flow-${NAME}`).filter({ hasText: 'Réussie' }).waitFor()
  }, page)

  await r.step('téléphone : palette, canevas, panneau les uns sous les autres, rien ne déborde', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${cfg.desk}#/automations/${NAME}`)
    await page.getByTestId('af-node-lire').waitFor()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(over <= 1, `la page déborde de ${over} px`)
    await r.shot(page, 'automations-03-telephone')
    await page.setViewportSize({ width: 1280, height: 800 })
  }, page)

  await r.step('thème sombre : les cartes restent lisibles', async () => {
    await page.evaluate(() => document.documentElement.classList.add('app-dark'))
    const bg = await page.getByTestId('af-node-lire').evaluate((el) => getComputedStyle(el).backgroundColor)
    const fg = await page.getByTestId('af-node-lire').evaluate((el) => getComputedStyle(el).color)
    assert(bg !== fg, `carte illisible : fond ${bg}, texte ${fg}`)
    await r.shot(page, 'automations-04-sombre')
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
