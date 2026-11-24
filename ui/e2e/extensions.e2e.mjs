/**
 * Les extensions JavaScript des plugins (D28, process/extensions.md), dans un vrai navigateur, face à un vrai serveur : l'import map et
 * la CSP laissent charger l'extension de `sales` (même origine, empreinte de l'import map), qui ajoute à la fiche Client un panneau et
 * une action, une page `#/x/sales/overview` (lien direct compris), un lien de barre latérale et une commande. Aucune violation CSP.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)

console.log('Desk — extensions des plugins')
try {
  const customer = (await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Acme', type: 'Particulier' } })).json

  await r.step('un lien direct vers une page d’extension s’ouvre après la connexion', async () => {
    await page.goto(`${cfg.desk}#/x/sales/overview`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/x\/sales\/overview$/)
    await page.getByRole('heading', { level: 1, name: 'Synthèse des ventes' }).waitFor()
    await page.getByTestId('sales-overview').filter({ hasText: '0 facture(s) au total' }).waitFor()
    await r.shot(page, 'ext-01-overview')
  }, page)

  await r.step('la page se recharge directement (les routes existent avant la première navigation)', async () => {
    await page.reload()
    await page.getByTestId('sales-overview').filter({ hasText: 'facture(s) au total' }).waitFor()
  }, page)

  await r.step('le lien de l’extension se range sous l’espace Ventes', async () => {
    await page.goto(`${cfg.desk}#/`)
    // DANS le bloc de l'espace Ventes de la barre latérale (pas dans le groupe « Extensions »).
    const link = page.getByTestId('ws-sales').getByTestId('nav-ext-sales-Synthèse')
    if (!(await link.isVisible())) await page.getByTestId('ws-toggle-sales').click()
    await link.click()
    await page.waitForURL(/#\/x\/sales\/overview$/)
  }, page)

  await r.step('la fiche Client montre le panneau de l’extension', async () => {
    await page.goto(`${cfg.desk}#/app/Customer/${customer.name}`)
    await page.getByTestId('ext-panel').filter({ hasText: 'Factures' }).waitFor()
    await page.getByTestId('sales-customer-invoices').filter({ hasText: '0 facture(s)' }).waitFor()
    await r.shot(page, 'ext-02-panel')
  }, page)

  await r.step('l’action de l’extension, dans le menu « ⋯ », ouvre une nouvelle facture', async () => {
    await page.getByTestId('more-actions').click()
    await page.getByTestId('ext-action-sales-Nouvelle facture').click()
    await page.waitForURL(/#\/app\/Invoice\/new\?customer=/)
  }, page)

  await r.step('une action de LISTE qui ne demande aucune sélection est un bouton, sans rien cocher', async () => {
    await page.goto(`${cfg.desk}#/app/Invoice`)
    await page.getByTestId('list-title').waitFor()
    // Rien n'est coché — et la barre groupée n'existe donc pas. C'est tout l'objet de `selection: 'none'`.
    assert(!(await page.getByTestId('selection-bar').isVisible()), 'aucune ligne cochée : la barre groupée ne doit pas être là')
    await page.getByTestId('ext-action-sales-Synthèse').click()
    await page.waitForURL(/#\/x\/sales\/overview$/)
  }, page)

  await r.step('une action de LIGNE ouvre le menu de CETTE ligne et reçoit son document', async () => {
    const item = await api(cfg, 'POST', '/doc/Item', { data: { item_name: 'Vis inox', uom: 'pcs' } })
    const client = await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Durand', type: 'Particulier' } })
    const made = await api(cfg, 'POST', '/doc/Invoice', {
      data: { customer: client.json.name, posting_date: '2026-11-24', items: [{ item: item.json.name, qty: 1, rate: 10 }] },
    })
    assert(made.status === 201, `facture : ${made.status} ${JSON.stringify(made.json)}`)

    await page.goto(`${cfg.desk}#/app/Invoice`)
    await page.getByTestId(`row-actions-${made.json.name}`).click()
    await page.getByTestId('ext-row-sales-Ouvrir le client').click()
    await page.waitForURL(new RegExp(`#/app/Customer/${client.json.name}$`))
    await r.shot(page, 'ext-03-row-action')
  }, page)

  await r.step('la barre de commande propose la commande de l’extension', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.keyboard.press('Control+k')
    await page.getByTestId('palette').locator('input').fill('synthese')
    await page.keyboard.press('Enter')
    await page.waitForURL(/#\/x\/sales\/overview$/)
  }, page)
} finally {
  await browser.close()
}

// Une violation de CSP (import map refusée, script d'une autre origine) arrive ici comme une erreur de console.
const bad = problems.filter((p) => !/Failed to load resource.*(401|403|404|409|422)/.test(p))
if (bad.length) {
  console.log('\nProblèmes console/réseau :')
  for (const p of bad) console.log('  -', p)
  r.failures.push(`${bad.length} problème(s) console ou réseau`)
}
console.log(r.failures.length ? `\n${r.failures.length} échec(s)` : '\nTout est passé')
process.exit(r.failures.length ? 1 : 0)
