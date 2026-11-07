/**
 * L'éditeur du circuit d'approbation dans un vrai navigateur (fixture `flux`, type `Demande`) : le graphe se dessine sous la vraie CSP,
 * choisir une transition montre son droit, la fermer puis enregistrer RESSERRE le circuit côté serveur (la transition n'est plus offerte),
 * ajouter un état et la transition qui y mène l'ADAPTE (un document y passe pour de vrai),
 * le canevas tient sur un téléphone, et le thème sombre garde le graphe lisible. Aucune erreur de console.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)

console.log('Desk — éditeur du circuit d’approbation')
try {
  await r.step('connexion, puis « Circuit d’approbation » depuis le menu de la liste', async () => {
    await page.goto(`${cfg.desk}#/app/Demande`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/app\/Demande$/)
    // Ouvert au CLAVIER : un clic simulé fait défiler la zone et le menu surgissant de PrimeVue se referme au défilement.
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('list-workflow').click()
    await page.waitForURL(/#\/workflow\/Demande$/)
    await page.getByRole('heading', { level: 1, name: 'Circuit d’approbation · Demande' }).waitFor()
  }, page)

  await r.step('le graphe : quatre états, quatre transitions, chacune un bouton', async () => {
    for (const s of ['Brouillon', 'À approuver', 'Approuvée', 'Refusée']) await page.getByTestId(`wf-state-${s}`).waitFor()
    for (const a of ['Soumettre', 'Approuver', 'Refuser', 'Reprendre']) await page.getByTestId(`wf-edge-${a}`).waitFor()
    const box = await page.getByTestId('wf-canvas').boundingBox()
    assert(box && box.height >= 380, `canevas trop petit : ${JSON.stringify(box)}`)
    await r.shot(page, 'workflow-01-graphe')
  }, page)

  await r.step('choisir une transition montre le droit qu’elle exige', async () => {
    await page.getByTestId('wf-edge-Refuser').click()
    await page.getByTestId('wf-panel-title').filter({ hasText: 'Refuser' }).waitFor()
    await page.getByTestId('wf-grant').filter({ hasText: 'flux:demande:workflow_refuser' }).waitFor()
    await r.shot(page, 'workflow-02-transition')
  }, page)

  await r.step('fermer « Reprendre » et enregistrer : le serveur ne l’offre plus', async () => {
    // « Reprendre » est la seule sortie offerte à l'AUTEUR (les deux autres sont `not_owner`) : on vérifie qu'elle l'est, puis qu'elle ne l'est plus.
    const doc = (await api(cfg, 'POST', '/doc/Demande', { data: { objet: 'Congés d’été', jours: 5, commentaire: 'à revoir' } })).json
    const moved = await api(cfg, 'POST', `/doc/Demande/${encodeURIComponent(doc.name)}/transition`, { action: 'Soumettre', version: doc.version })
    assert(moved.status < 300, `soumettre : ${moved.status} ${JSON.stringify(moved.json)}`)
    const offered = async () => ((await api(cfg, 'GET', `/transitions/Demande/${encodeURIComponent(doc.name)}`)).json.transitions ?? []).map((t) => t.action)
    assert((await offered()).includes('Reprendre'), `« Reprendre » devrait être offert avant : ${JSON.stringify(await offered())}`)
    await page.getByTestId('wf-edge-Reprendre').click()
    await page.getByTestId('wf-panel-title').filter({ hasText: 'Reprendre' }).waitFor()
    await page.getByTestId('wf-closed').locator('input').check()
    await page.getByTestId('wf-save').click()
    await page.getByText('Circuit d’approbation enregistré.').waitFor()
    const stored = (await api(cfg, 'GET', '/override/workflow/Demande')).json
    assert(stored.config?.transitions?.Reprendre?.closed === true, `habillage enregistré : ${JSON.stringify(stored.config)}`)
    assert(!(await offered()).includes('Reprendre'), `« Reprendre » encore offert : ${JSON.stringify(await offered())}`)
    await page.reload()
    await page.locator('[data-testid="wf-edge-Reprendre"].wf-edge-label-closed').waitFor()
  }, page)

  await r.step('ajouter un état et la transition qui y mène, l’enregistrer : un document y passe pour de vrai', async () => {
    // Depuis « À approuver » : le choisir, puis « Ajouter un état » ; le panneau enchaîne sur la transition qui y mène.
    await page.getByTestId('wf-state-À approuver').click()
    await page.getByTestId('wf-add-state').click()
    await page.getByTestId('wf-new-state-name').fill('Visée')
    await r.shot(page, 'workflow-05-nouvel-etat')
    await page.getByTestId('wf-new-state-ok').click()
    await page.getByTestId('wf-state-Visée').waitFor()
    assert(await page.getByTestId('wf-save').isDisabled(), 'un état que rien n’atteint ne s’enregistre pas')
    await page.getByTestId('wf-new-action').fill('Viser')
    await page.keyboard.press('Escape')
    await page.getByTestId('wf-new-grant').click()
    await page.getByRole('option').filter({ hasText: 'flux:demande:workflow_approuver' }).first().click()
    await r.shot(page, 'workflow-06-nouvelle-transition')
    await page.getByTestId('wf-new-transition-ok').click()
    await page.getByTestId('wf-edge-Viser').waitFor()
    await page.getByTestId('wf-fit').click()
    await page.waitForTimeout(300)
    await r.shot(page, 'workflow-07-circuit-adapte')
    await page.getByTestId('wf-save').click()
    await page.getByText('Circuit d’approbation enregistré.').waitFor()
    const stored = (await api(cfg, 'GET', '/override/workflow/Demande')).json
    assert(stored.config?.added?.[0]?.action === 'Viser' && stored.config.added[0].from === 'À approuver', `ajout enregistré : ${JSON.stringify(stored.config)}`)
    // Le serveur applique l'ajout : un document soumis est « Visé » par la transition ajoutée, et `/meta` annonce l'état.
    const doc = (await api(cfg, 'POST', '/doc/Demande', { data: { objet: 'Formation', jours: 2, commentaire: 'ok' } })).json
    const sent = (await api(cfg, 'POST', `/doc/Demande/${encodeURIComponent(doc.name)}/transition`, { action: 'Soumettre', version: doc.version })).json
    const seen = await api(cfg, 'POST', `/doc/Demande/${encodeURIComponent(doc.name)}/transition`, { action: 'Viser', version: sent.version })
    assert(seen.status < 300 && seen.json.workflow_state === 'Visée', `viser : ${seen.status} ${JSON.stringify(seen.json)}`)
    const meta = (await api(cfg, 'GET', '/meta/Demande')).json
    assert(meta.workflow.states.some((s) => s.name === 'Visée'), 'l’état ajouté est annoncé par /meta')
    // La fiche montre le document dans l'état ajouté.
    await page.goto(`${cfg.desk}#/app/Demande/${encodeURIComponent(doc.name)}`)
    await page.locator('[data-state="Visée"]').first().waitFor()
    await r.shot(page, 'workflow-08-document-vise')
    await page.goto(`${cfg.desk}#/workflow/Demande`)
    await page.getByTestId('wf-state-Visée').waitFor()
  }, page)

  await r.step('téléphone : le panneau passe sous le canevas, rien ne déborde', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload() // un chargement À cette largeur : sinon le menu, ouvert sur ordinateur, est encore en train de se replier
    await page.getByTestId('wf-state-Brouillon').waitFor()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(over <= 1, `la page déborde de ${over} px`)
    const canvas = await page.getByTestId('wf-canvas').boundingBox()
    const panel = await page.getByTestId('wf-panel').boundingBox()
    assert(canvas && panel && panel.y >= canvas.y + canvas.height - 1, 'le panneau devrait suivre le canevas')
    await r.shot(page, 'workflow-03-telephone')
    await page.setViewportSize({ width: 1280, height: 800 })
    // Le canevas a grandi : le graphe est recadré, il ne reste pas tassé dans un coin (Vue Flow ne cadre qu'au premier affichage).
    await page.waitForFunction(() => {
      const canvas = document.querySelector('[data-testid="wf-canvas"]')?.getBoundingClientRect()
      const card = document.querySelector('[data-testid="wf-state-Refusée"]')?.getBoundingClientRect()
      return !!canvas && !!card && card.right > canvas.left + canvas.width * 0.6
    }, null, { timeout: 4000 })
  }, page)

  await r.step('thème sombre : le graphe garde ses cartes et ses étiquettes', async () => {
    await page.evaluate(() => document.documentElement.classList.add('app-dark'))
    const bg = await page.getByTestId('wf-state-Brouillon').evaluate((el) => getComputedStyle(el).backgroundColor)
    const fg = await page.getByTestId('wf-state-Brouillon').evaluate((el) => getComputedStyle(el).color)
    assert(bg !== fg, `carte illisible : fond ${bg}, texte ${fg}`)
    await r.shot(page, 'workflow-04-sombre')
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
