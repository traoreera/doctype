/**
 * « Personnaliser l'impression » dans un VRAI navigateur (process/impression.md §10, étape 7).
 *
 * Ce que ce scénario prouve, et que les tests de composant ne peuvent pas : qu'on choisit réellement les champs
 * et les colonnes à l'écran, que l'aperçu SUIT (il vient du serveur, sur un document réel), que ce qu'on
 * enregistre est bien ce que l'impression emploie ensuite, et que « Revenir à l'original » rend la main au
 * gabarit de l'application. Plus la règle de droit : un lecteur regarde, il n'enregistre pas.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)

const srcdoc = () => page.getByTestId('pb-frame').getAttribute('srcdoc')

console.log('Desk — Personnaliser l’impression')
try {
  let customer
  await r.step('un document, puis la page par le menu « ⋯ » de la liste', async () => {
    const made = await api(cfg, 'POST', '/doc/Customer', {
      data: { customer_name: 'Société Durand & Fils', type: 'Entreprise', company_id: 'FR-12345' },
    })
    assert(made.status === 201, `création : ${made.status} ${JSON.stringify(made.json)}`)
    customer = made.json.name

    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/app\/Customer$/)
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('list-print').click()
    await page.waitForURL(/#\/print\/Customer$/)
    await page.getByTestId('pb-title').waitFor()
    await page.getByTestId('pc-paper').waitFor()
    await page.getByTestId('pb-frame').waitFor({ state: 'attached' }) // l'aperçu du serveur, derrière la bascule
    await r.shot(page, 'printbuilder-01-ouvert')
  }, page)

  await r.step('DIAGNOSTIC : la page est-elle stable ?', async () => {
    const asked = []
    page.on('request', (q) => { if (/print-preview/.test(q.url())) asked.push(Date.now()) })
    await page.waitForTimeout(3000)
    const moves = await page.evaluate(() => new Promise((res) => {
      let n = 0
      const o = new MutationObserver((muts) => { n += muts.length })
      o.observe(document.body, { childList: true, subtree: true, attributes: true })
      setTimeout(() => { o.disconnect(); res(n) }, 2000)
    }))
    console.log(`       aperçus demandés en 3 s : ${asked.length} ; mutations DOM en 2 s : ${moves}`)
    assert(asked.length <= 2, `BOUCLE : ${asked.length} aperçus demandés sans rien toucher`)
    assert(moves < 500, `BOUCLE DE RENDU : ${moves} mutations DOM sans rien toucher`)
  }, page)

  await r.step('l’aperçu montre un document RÉEL, et le nom du client (pas son identifiant)', async () => {
    const html = await srcdoc()
    assert(html.includes('Société Durand') || html.includes('Durand'), `l'aperçu ne montre pas le client : ${html.slice(0, 300)}`)
    // Le NUMÉRO du document, lui, est à sa place : le bloc titre imprime `{{ name }}`, et une facture porte
    // son numéro. (Une première version de ce test confondait « identifiant d'un lien » et « numéro de pièce ».)
    assert(html.includes(customer), `le numéro du document devrait figurer sur la pièce`)
  }, page)

  await r.step('aucune colonne n’en recouvre une autre', async () => {
    // La palette débordait par-dessus la pile : un clic sur un bloc atteignait un bouton de palette. Un
    // enfant de grille a `min-width: auto`, donc son contenu sort de la piste au lieu de la contraindre.
    const covered = await page.evaluate(() =>
      [...document.querySelectorAll('[data-testid^="pb-block-"], [data-testid^="pb-new-"], [data-testid="pb-save"], [data-testid="pb-mode"]')]
        .map((el) => {
          const r = el.getBoundingClientRect()
          const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
          return top && !el.contains(top) && top !== el ? `${el.dataset.testid} recouvert par ${top.getAttribute('data-testid') ?? top.tagName}` : null
        })
        .filter(Boolean),
    )
    assert(covered.length === 0, `éléments recouverts : ${covered.join(' ; ')}`)
  }, page)

  await r.step('CHOISIR les champs : on en retire un, l’aperçu le perd', async () => {
    await page.getByTestId('pb-block-1').click() // un clic sur le bloc de la FEUILLE le choisit
    await page.getByTestId('pb-panel-title').waitFor()
    const dropped = await page.locator('[data-testid^="pb-drop-"]').first().getAttribute('data-testid')
    const field = dropped.replace('pb-drop-', '')
    const before = await srcdoc()
    await page.getByTestId(dropped).click()
    await page.waitForTimeout(900) // l'aperçu se redemande après un court répit
    const after = await srcdoc()
    assert(before !== after, 'l’aperçu n’a pas suivi le retrait du champ')
    await r.shot(page, 'printbuilder-02-champ-retire')
    // Et on le remet : le bouton d'ajout porte le même nom de champ.
    await page.getByTestId(`pb-add-${field}`).click()
    await page.waitForTimeout(900)
  }, page)

  await r.step('AJOUTER un bloc : il entre dans la pile', async () => {
    const before = await page.locator('[data-testid^="pb-block-"]').count()
    await page.getByTestId('pb-new-page_break').click()
    const after = await page.locator('[data-testid^="pb-block-"]').count()
    assert(after === before + 1, `la pile n'a pas grandi : ${before} -> ${after}`)
  }, page)

  await r.step('ENREGISTRER, puis imprimer : c’est bien le gabarit de l’organisation qui sert', async () => {
    await page.getByTestId('pb-new-title').click()
    await page.getByTestId('pb-text').fill('GABARIT MAISON {{ name }}')
    await page.waitForTimeout(900)
    await page.getByTestId('pb-save').click()
    await page.getByText('Gabarit enregistré').waitFor()

    const printed = await api(cfg, 'GET', `/print/Customer/${encodeURIComponent(customer)}`)
    assert(printed.status === 200, `impression : ${printed.status}`)
    assert(printed.json.html.includes('GABARIT MAISON'), `le gabarit enregistré ne sert pas : ${printed.json.html.slice(0, 300)}`)
    const listed = await api(cfg, 'GET', '/print-templates/Customer')
    assert(listed.json.templates.some((x) => x.source === 'organisation'), `source attendue : ${JSON.stringify(listed.json.templates)}`)
    await r.shot(page, 'printbuilder-03-enregistre')
  }, page)

  await r.step('« Revenir à l’original » rend la main au gabarit de l’application', async () => {
    await page.getByTestId('pb-reset').click()
    await page.getByText('Gabarit retiré').waitFor()
    const listed = await api(cfg, 'GET', '/print-templates/Customer')
    assert(!listed.json.templates.some((x) => x.source === 'organisation'), `habillage restant : ${JSON.stringify(listed.json.templates)}`)
  }, page)

  await r.step('un lecteur regarde sans pouvoir enregistrer', async () => {
    // Un jeton qui LIT seulement : la page s'ouvre, le bouton est grisé, et elle le dit.
    const reader = cfg.readerToken ?? null
    if (!reader) return // l'hôte de test ne fournit qu'un jeton : la règle est vérifiée côté serveur (test_print_service)
    await page.evaluate((tok) => sessionStorage.setItem('xdt.token', tok), reader)
    await page.reload()
    await page.getByTestId('pb-readonly').waitFor()
    assert(await page.getByTestId('pb-save').isDisabled(), 'le bouton Enregistrer doit être grisé pour un lecteur')
  }, page)

  await r.step('une FACTURE se compose à la souris : bloc glissé de la palette, bloc, champ et colonne déplacés, largeur tirée', async () => {
    const item = await api(cfg, 'POST', '/doc/Item', { data: { item_name: 'Vis inox', uom: 'pcs' } })
    const client = await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Durand', type: 'Particulier' } })
    const invoice = await api(cfg, 'POST', '/doc/Invoice', {
      data: { customer: client.json.name, posting_date: '2026-10-29', items: [{ item: item.json.name, qty: 3, rate: 12.5 }] },
    })
    assert(invoice.status === 201, `facture : ${invoice.status} ${JSON.stringify(invoice.json)}`)
    await page.goto(`${cfg.desk}#/print/Invoice`)
    await page.getByTestId('pc-paper').waitFor()
    await page.getByTestId('pc-col-item').waitFor()
    const kinds = () => page.locator('[data-testid^="pb-block-"]').evaluateAll((els) => els.map((e) => e.dataset.kind))
    // Le client par son NOM, pas son identifiant : le titre se lit juste après les valeurs, on l'attend.
    await page.getByTestId('pc-field-customer').getByText('Durand', { exact: true }).waitFor()
    const start = await kinds()
    const table = start.indexOf('table')

    // 1. Un bloc de la palette lâché sur le HAUT du tableau se pose juste avant lui.
    await page.getByTestId('pb-new-text').dragTo(page.getByTestId(`pb-block-${table}`), { targetPosition: { x: 40, y: 4 } })
    let now = await kinds()
    assert(now[table] === 'text' && now[table + 1] === 'table', `bloc glissé mal placé : ${now}`)
    await page.getByTestId('pb-text').fill('MENTION GLISSÉE')

    // 2. Le bloc des totaux remonte tout en haut.
    const totals = now.indexOf('totals')
    await page.getByTestId(`pb-block-${totals}`).dragTo(page.getByTestId('pb-block-0'), { targetPosition: { x: 40, y: 3 } })
    now = await kinds()
    assert(now[0] === 'totals', `les totaux devaient passer en tête : ${now}`)

    // 3. Un champ passe devant un autre.
    await page.getByTestId('pc-field-payment').dragTo(page.getByTestId('pc-field-customer'), { targetPosition: { x: 3, y: 5 } })
    const fields = await page.locator('[data-testid^="pc-field-"]').evaluateAll((e) => e.map((x) => x.dataset.testid))
    assert(fields.indexOf('pc-field-payment') < fields.indexOf('pc-field-customer'), `champ non déplacé : ${fields}`)

    // 4. Une colonne passe en tête du tableau.
    await page.getByTestId('pc-col-amount').dragTo(page.getByTestId('pc-col-item'), { targetPosition: { x: 3, y: 5 } })
    const cols = await page.locator('th[data-testid^="pc-col-"]').evaluateAll((e) => e.map((x) => x.dataset.testid))
    assert(cols[0] === 'pc-col-amount', `colonne non déplacée : ${cols}`)

    // 5. La largeur se TIRE : la poignée est atteignable (pas recouverte par la colonne voisine) et suit la souris.
    const handle = await page.getByTestId('pc-resize-item').boundingBox()
    const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.getAttribute('data-testid'), [handle.x + handle.width / 2, handle.y + handle.height / 2])
    assert(hit === 'pc-resize-item', `poignée recouverte par ${hit}`)
    const before = await page.getByTestId('pc-col-item').evaluate((th) => th.style.width)
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2)
    await page.mouse.down()
    await page.mouse.move(handle.x - 80, handle.y + 5, { steps: 6 })
    await page.mouse.up()
    const after = await page.getByTestId('pc-col-item').evaluate((th) => th.style.width)
    assert(after && after !== before && parseInt(after) < parseInt(before), `largeur inchangée : ${before} -> ${after}`)
    await r.shot(page, 'printbuilder-05-facture')

    // 6. Ce qu'on a composé est ce qui s'IMPRIME.
    await page.waitForTimeout(600)
    await page.getByTestId('pb-save').click()
    await page.getByText('Gabarit enregistré').waitFor()
    const printed = await api(cfg, 'GET', `/print/Invoice/${encodeURIComponent(invoice.json.name)}`)
    const html = printed.json.html
    assert(html.indexOf('MENTION GLISSÉE') > 0 && html.indexOf('MENTION GLISSÉE') < html.indexOf('Article'), 'le bloc glissé doit précéder le tableau à l’impression')
    assert(html.indexOf('Montant') < html.indexOf('Article'), 'la colonne déplacée doit précéder « Article » à l’impression')
    await page.getByText('Aperçu imprimé').click()
    await page.getByTestId('pb-frame').waitFor()
    await page.getByTestId('pb-reset').click()
    await page.getByText('Gabarit retiré').waitFor()
  }, page)

  await r.step('téléphone : tout s’empile, rien ne déborde', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByTestId('pb-title').waitFor()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(over <= 1, `la page déborde de ${over} px`)
    await r.shot(page, 'printbuilder-04-telephone')
    await page.setViewportSize({ width: 1280, height: 800 })
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
