/**
 * Scénario de la fin de phase 2 : « le DocType Client se gère entièrement depuis l'UI ».
 * Connexion, création d'un pays et d'un client, conditions `depends_on`, champ Link, filtres, conflit de version, suppression.
 * Puis le palier 40 % : factures à lignes, historique, et le module Stock (document soumettable, quantités décimales).
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const field = (name) => page.locator(`[data-field="${name}"]`)
/** PrimeVue InputNumber ne lit que de vraies frappes (comme un utilisateur) : un `fill` ne met le modèle à jour qu'au blur. */
const typeInto = async (input, text) => {
  await input.click()
  await input.press('Control+a')
  await input.pressSequentially(text)
}
const typeNumber = (name, text) => typeInto(field(name).locator('input'), text)
const cell = (row, col) => page.locator(`[data-row="${row}"] [data-col="${col}"]`)
/** Choisit un document dans l'autocomplétion d'un champ Link (on tape, on clique sur la proposition). */
const pickLink = async (input, typed, label) => {
  await input.fill(typed)
  await page.getByRole('option', { name: label }).first().click()
}
const toast = (text) => page.locator('.p-toast-message', { hasText: text }).first().waitFor({ timeout: 5000 })

console.log('Desk — scénario Client')
try {
  await r.step('sans jeton, le Desk renvoie à la connexion', async () => {
    await page.goto(cfg.desk)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').waitFor()
    await r.shot(page, '01-login')
  }, page)

  await r.step('un jeton illisible est refusé avec un message', async () => {
    await page.locator('#login-token').fill('pas-un-jeton')
    await page.getByRole('button', { name: 'Se connecter' }).click()
    assert((await page.getByTestId('login-error').innerText()).includes('illisible'), 'message attendu')
  }, page)

  await r.step('un jeton valide ouvre l’accueil et le menu des types', async () => {
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/$/)
    // La barre latérale liste les ESPACES de travail (Ventes, Stock) ; leurs types se dépliaient dessous.
    await page.getByTestId('ws-sales').waitFor()
    await page.getByTestId('ws-stock').waitFor()
    await page.getByTestId('ws-toggle-sales').click()
    await page.locator('.desk-sidebar').getByRole('link', { name: 'Client', exact: true }).waitFor()
    await page.locator('.desk-sidebar').getByRole('link', { name: 'Pays' }).waitFor()
    await r.shot(page, '02-home')
  }, page)

  await r.step('création d’un pays (Pays > Nouveau)', async () => {
    await page.goto(`${cfg.desk}#/app/Country/new`)
    await field('country_name').locator('input').fill('France')
    await field('code').locator('input').fill('FR')
    await page.getByTestId('save').click()
    await toast('Document créé')
    await page.waitForURL(/#\/app\/Country\/(?!new$)[^/]+$/)
    assert((await page.getByTestId('form-title').innerText()) === 'France', 'le titre est le nom du pays')
    await r.shot(page, '03-country-saved')
  }, page)

  await r.step('liste Clients vide', async () => {
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByTestId('list-empty').waitFor()
    assert((await page.getByTestId('list-title').innerText()) === 'Client', 'titre de la liste')
    await r.shot(page, '04-customers-empty')
  }, page)

  await r.step('nouveau client : champ conditionnel masqué, puis affiché et obligatoire', async () => {
    await page.getByTestId('new-doc').click()
    await page.waitForURL(/#\/app\/Customer\/new/)
    await field('customer_name').locator('input').fill('ACME SAS')
    assert((await field('company_id').count()) === 0, 'company_id masqué tant que type != Entreprise')
    await field('type').locator('.p-select').click()
    await page.getByRole('option', { name: 'Entreprise' }).click()
    await field('company_id').waitFor()
    assert((await field('company_id').locator('.req').count()) === 1, 'astérisque obligatoire')
    await r.shot(page, '05-form-enterprise')
  }, page)

  await r.step('enregistrer sans le champ obligatoire : erreur sous le champ, rien d’envoyé', async () => {
    await page.getByTestId('save').click()
    await page.getByTestId('error-company_id').waitFor()
    assert((await page.getByTestId('error-company_id').innerText()) === 'Champ obligatoire', 'message')
    assert((await page.getByTestId('form-banner').innerText()).includes('Corrigez'), 'bandeau')
    assert((await api(cfg, 'GET', '/list/Customer')).json.total === 0, 'aucun client créé')
    await r.shot(page, '06-form-required-error')
  }, page)

  await r.step('plafond > 10000 : le validateur apparaît et devient obligatoire', async () => {
    await field('company_id').locator('input').fill('552100554')
    await typeNumber('credit_limit', '20000')
    await field('approver').waitFor()
    await page.getByTestId('save').click()
    await page.getByTestId('error-approver').waitFor()
    await typeNumber('credit_limit', '5000')
    await field('approver').locator('input').fill('Marie')
    await typeNumber('credit_limit', '20000')
    assert((await page.getByTestId('error-company_id').count()) === 0, "l'erreur d'un champ corrigé disparaît")
  }, page)

  await r.step('champ Link : autocomplétion, le titre est affiché, le nom est stocké', async () => {
    await field('country').locator('input').fill('fra')
    await page.getByRole('option', { name: /France/ }).click()
    assert((await field('country').locator('input').inputValue()) === 'France', 'le titre est affiché')
    await field('since').locator('input').fill('17/10/2026')
    await field('since').locator('input').press('Escape')
    await r.shot(page, '07-form-filled')
  }, page)

  let docName
  await r.step('enregistrement : création, route du document, valeurs relues', async () => {
    await page.getByTestId('save').click()
    await toast('Document créé')
    await page.waitForURL(/#\/app\/Customer\/(?!new$)[^/]+$/)
    docName = decodeURIComponent(page.url().split('/').pop())
    const doc = (await api(cfg, 'GET', `/doc/Customer/${encodeURIComponent(docName)}`)).json
    assert(doc.data.customer_name === 'ACME SAS' && doc.data.credit_limit === 20000, `contenu: ${JSON.stringify(doc.data)}`)
    assert(doc.data.since === '2026-10-17', `la date locale ne doit pas glisser d'un jour (Auckland UTC+13): ${doc.data.since}`)
    const country = (await api(cfg, 'GET', '/list/Country')).json.items[0].name
    assert(doc.data.country === country, 'le Link stocke le nom du pays, pas son titre')
    assert((await page.getByTestId('status').innerText()) === 'Enregistré', 'statut')
    await r.shot(page, '08-saved')
  }, page)

  await r.step('la liste montre le client avec le titre du pays', async () => {
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByTestId(`open-${docName}`).waitFor()
    await page.locator('td', { hasText: 'France' }).first().waitFor()
    assert((await page.getByTestId('list-count').innerText()).includes('1 sur 1'), 'compteur')
    await r.shot(page, '09-list')
  }, page)

  await r.step('filtres et recherche rapide', async () => {
    await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Dupont', type: 'Particulier', credit_limit: 100 } })
    await page.reload()
    await page.getByTestId('list-count').filter({ hasText: '2 sur 2' }).waitFor()
    await page.getByTestId('quick-search').fill('dup')
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
    assert((await page.locator('tbody tr').count()) === 1, 'une seule ligne')
    await page.getByTestId('quick-search').fill('')
    await page.getByTestId('list-count').filter({ hasText: '2 sur 2' }).waitFor()
    await page.getByTestId('toggle-filters').click()
    await page.getByTestId('add-filter').click()
    const row = page.locator('[data-testid="filter-bar"] .filter-row').first()
    await row.locator('.p-select').nth(0).click()
    await page.getByRole('option', { name: 'Type', exact: true }).click()
    await row.locator('.p-select').nth(2).click()
    await page.getByRole('option', { name: 'Entreprise', exact: true }).click()
    await page.getByTestId('apply-filters').click()
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
    assert((await page.locator('tbody tr').innerText()).includes('ACME SAS'), 'le filtre garde le client Entreprise')
    await r.shot(page, '10-filters-open')
    await page.getByRole('button', { name: 'Effacer les filtres' }).click()
    await page.getByTestId('list-count').filter({ hasText: '2 sur 2' }).waitFor()
  }, page)

  await r.step('conflit de version : proposer de recharger', async () => {
    await page.goto(`${cfg.desk}#/app/Customer/${encodeURIComponent(docName)}`)
    await page.getByTestId('form-title').waitFor()
    const current = (await api(cfg, 'GET', `/doc/Customer/${encodeURIComponent(docName)}`)).json
    const bump = await api(cfg, 'PUT', `/doc/Customer/${encodeURIComponent(docName)}`, { version: current.version, data: { ...current.data, credit_limit: 30000 } })
    assert(bump.status === 200, `préparation: ${bump.status}`)
    await field('approver').locator('input').fill('Paul')
    await page.getByTestId('save').click()
    await page.getByText('Document modifié ailleurs').waitFor()
    await r.shot(page, '11-conflict')
    await page.getByRole('button', { name: 'Recharger' }).click()
    await page.waitForFunction(() => document.querySelector('[data-field="credit_limit"] input')?.value === '30000')
  }, page)

  await r.step('suppression depuis la liste (confirmation)', async () => {
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByTestId(`open-${docName}`).waitFor()
    await page.locator('tbody tr', { hasText: 'ACME SAS' }).locator('.p-checkbox').click()
    await page.getByTestId('bulk-delete').click()
    await page.getByRole('button', { name: 'Supprimer', exact: true }).last().click()
    await toast('supprimé')
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
  }, page)

  await r.step('import CSV (fichier Excel en Windows-1252) : une ligne passe, l’autre est refusée avec sa raison', async () => {
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('import-csv').click()
    const dialog = page.getByTestId('import-dialog')
    const csv = 'Nom;Type;Plafond;Client depuis\r\nSociété Élan;Particulier;1 500,5;31/12/2025\r\nSans numéro;Entreprise;;\r\n'
    await dialog.getByTestId('import-file').setInputFiles({ name: 'clients.csv', mimeType: 'text/csv', buffer: Buffer.from(csv, 'latin1') })
    await dialog.getByTestId('import-run').click()
    await dialog.getByTestId('import-report').filter({ hasText: '1 importé, 1 ligne refusée' }).waitFor()
    await dialog.getByTestId('import-fail-3').filter({ hasText: 'company_id' }).waitFor()
    await r.shot(page, 'import-csv')
    await page.keyboard.press('Escape')
    await page.getByTestId('list-count').filter({ hasText: '2 sur 2' }).waitFor()
    const imported = (await api(cfg, 'GET', `/list/Customer?filters=${encodeURIComponent(JSON.stringify({ customer_name: 'Société Élan' }))}`)).json.items[0]
    if (imported?.data.credit_limit !== 1500.5 || imported?.data.since !== '2025-12-31') throw new Error(`import mal lu : ${JSON.stringify(imported?.data)}`)
  }, page)

  await r.step('articles : création de deux articles', async () => {
    for (const name of ['Vis', 'Écrou']) {
      await page.goto(`${cfg.desk}#/app/Item/new`)
      await field('item_name').locator('input').fill(name)
      await page.getByTestId('save').click()
      // On attend la route du document créé, pas le toast : celui du précédent est encore à l'écran quelques secondes.
      await page.waitForURL(/#\/app\/Item\/(?!new$)[^/]+$/)
    }
  }, page)

  let invoiceName
  await r.step('facture à lignes : saisie des lignes, total calculé par le serveur, numéro de série', async () => {
    await page.goto(`${cfg.desk}#/app/Invoice/new`)
    await pickLink(field('customer').locator('input'), 'dup', /Dupont/)
    await page.getByTestId('add-row-items').click()
    await page.getByTestId('add-row-items').click()
    await pickLink(cell(0, 'item').locator('input'), 'vis', /Vis/)
    await typeInto(cell(0, 'rate').locator('input'), '12')
    await pickLink(cell(1, 'item').locator('input'), 'cr', /Écrou/)
    await typeInto(cell(1, 'qty').locator('input'), '3')
    await typeInto(cell(1, 'rate').locator('input'), '2')
    await r.shot(page, '12-invoice-form')
    await page.getByTestId('save').click()
    await toast('Document créé')
    await page.waitForURL(/#\/app\/Invoice\/FA-\d{4}-00001$/)
    invoiceName = decodeURIComponent(page.url().split('/').pop())
    const doc = (await api(cfg, 'GET', `/doc/Invoice/${encodeURIComponent(invoiceName)}`)).json
    assert(doc.data.items.length === 2 && doc.data.total === 18, `lignes et total: ${JSON.stringify(doc.data)}`)
    assert((await page.getByTestId('form-title').innerText()) === invoiceName, 'le titre est le numéro')
    assert((await page.getByTestId('value-total').innerText()).trim() === '18', 'le total calculé est affiché')
    await r.shot(page, '13-invoice-saved')
  }, page)

  await r.step('facture : une ligne incomplète est signalée sous sa cellule, rien n’est envoyé', async () => {
    await page.goto(`${cfg.desk}#/app/Invoice/new`)
    await pickLink(field('customer').locator('input'), 'dup', /Dupont/)
    await page.getByTestId('add-row-items').click()
    await page.getByTestId('save').click()
    await page.getByTestId('error-items.0.item').waitFor()
    await page.getByTestId('error-items.0.rate').waitFor()
    assert((await api(cfg, 'GET', '/list/Invoice')).json.total === 1, 'aucune facture de plus')
    await r.shot(page, '14-invoice-row-errors')
    // Partir avec une saisie non enregistrée : la garde demande confirmation, « Quitter » laisse partir.
    await page.goto(`${cfg.desk}#/app/Item`)
    await page.getByText('Modifications non enregistrées').waitFor()
    await page.getByRole('button', { name: 'Quitter' }).click()
    await page.getByTestId('list-title').waitFor()
  }, page)

  await r.step('un article cité par une ligne ne peut pas être supprimé', async () => {
    await page.goto(`${cfg.desk}#/app/Item`)
    await page.locator('tbody tr', { hasText: 'Vis' }).locator('.p-checkbox').click()
    await page.getByTestId('bulk-delete').click()
    await page.getByRole('button', { name: 'Supprimer', exact: true }).last().click()
    await toast('est utilisé par Facture')
    assert((await api(cfg, 'GET', '/list/Item')).json.total === 2, 'les deux articles sont toujours là')
  }, page)

  await r.step('la deuxième facture reçoit le numéro suivant', async () => {
    const [{ name: customer }] = (await api(cfg, 'GET', '/list/Customer')).json.items
    const [{ name: vis }] = (await api(cfg, 'GET', '/list/Item', undefined)).json.items.filter((i) => i.data.item_name === 'Vis')
    const second = await api(cfg, 'POST', '/doc/Invoice', { data: { customer, items: [{ item: vis, qty: 1, rate: 1 }] } })
    assert(second.status === 201 && /-00002$/.test(second.json.name), `numéro: ${second.json?.name}`)
  }, page)

  await r.step('historique : la création, puis la modification d’une ligne', async () => {
    await page.goto(`${cfg.desk}#/app/Invoice/${encodeURIComponent(invoiceName)}`)
    await page.getByTestId('form-title').waitFor()
    await page.locator('[data-testid="feed-item"][data-kind="version"]', { hasText: 'a créé ce document' }).waitFor()
    await typeInto(cell(1, 'qty').locator('input'), '4')
    await page.getByTestId('save').click()
    await toast('Document enregistré')
    const update = page.locator('[data-testid="feed-item"][data-kind="version"][data-action="update"]')
    await update.waitFor()
    const text = await update.innerText()
    assert(text.includes('Lignes') && text.includes('1 modifiée'), `résumé des lignes: ${text}`)
    assert(text.includes('Total') && /18\s*→\s*20/.test(text), `le total change aussi: ${text}`)
    assert((await page.getByTestId('value-total').innerText()).trim() === '20', 'le total est recalculé')
    await r.shot(page, '15-history')
  }, page)

  // ── Stock (palier 40 %, phase A) : documents soumettables, quantités décimales, annulation par mouvement inverse ───────
  // La veille en UTC : le navigateur tourne à Auckland (UTC+13), la date du jour y est déjà « demain » pour le serveur, qui refuse le futur.
  const yesterday = new Date(Date.now() - 86_400_000)
  const typedDate = `${String(yesterday.getUTCDate()).padStart(2, '0')}/${String(yesterday.getUTCMonth() + 1).padStart(2, '0')}/${yesterday.getUTCFullYear()}`
  const confirmDialog = async (name) => page.locator('.p-confirmdialog').getByRole('button', { name, exact: true }).click()
  const bins = async () => (await api(cfg, 'GET', '/list/Bin')).json.items.map((b) => b.data.qty)
  const open = async (name) => {
    await page.goto(`${cfg.desk}#/app/Stock%20Entry/${encodeURIComponent(name)}`)
    await page.getByTestId('form-title').waitFor()
  }
  const fillEntry = async (purpose, qty, where) => {
    await page.goto(`${cfg.desk}#/app/Stock%20Entry/new`)
    await field('purpose').locator('.p-select').click()
    await page.getByRole('option', { name: purpose }).click()
    await field('posting_date').locator('input').fill(typedDate)
    await field('posting_date').locator('input').press('Escape')
    await page.getByTestId('add-row-items').click()
    await pickLink(cell(0, 'item').locator('input'), 'vis', /Vis/)
    await typeInto(cell(0, 'qty').locator('input'), qty)
    await pickLink(cell(0, where.col).locator('input'), where.typed, where.label)
  }

  await r.step('entrepôts : création de deux entrepôts', async () => {
    for (const name of ['Paris', 'Lyon']) {
      await page.goto(`${cfg.desk}#/app/Warehouse/new`)
      await field('warehouse_name').locator('input').fill(name)
      await page.getByTestId('save').click()
      await page.waitForURL(/#\/app\/Warehouse\/(?!new$)[^/]+$/)
    }
  }, page)

  let receipt
  await r.step('réception : la quantité se saisit en décimal (virgule française) et reste exacte', async () => {
    await fillEntry('Réception', '12,5', { col: 'target_warehouse', typed: 'par', label: /Paris/ })
    await r.shot(page, '16-entry-form')
    await page.getByTestId('save').click()
    await toast('Document créé')
    await page.waitForURL(/#\/app\/Stock%20Entry\/MS-\d{4}-00001$/)
    receipt = decodeURIComponent(page.url().split('/').pop())
    const doc = (await api(cfg, 'GET', `/doc/Stock%20Entry/${receipt}`)).json
    assert(doc.docstatus === 0 && doc.data.items[0].qty === '12.500', `brouillon, quantité exacte en texte: ${JSON.stringify(doc.data.items)}`)
    assert((await page.getByTestId('status').innerText()) === 'Brouillon', 'le statut est Brouillon')
    assert((await bins()).length === 0, 'un brouillon ne bouge pas le stock')
  }, page)

  await r.step('soumettre : confirmation, puis le document est figé et le stock entre au journal', async () => {
    await page.getByTestId('submit').click()
    await confirmDialog('Soumettre')
    await toast('Document soumis')
    await page.locator('[data-testid="status"][data-docstatus="1"]').waitFor()
    assert((await page.getByTestId('status').innerText()) === 'Soumis', 'statut')
    assert((await page.getByTestId('save').count()) === 0, 'plus de Enregistrer')
    if ((await page.getByTestId('more-actions').count()) > 0) {
      await page.getByTestId('more-actions').click()
      await page.locator('.p-menu').waitFor()
      assert((await page.locator('.p-menu [data-testid="delete"]').count()) === 0, 'plus de Supprimer dans le menu « ⋯ »')
      await page.keyboard.press('Escape')
    }
    assert((await field('purpose').locator('input').count()) === 0, 'plus aucun champ de saisie')
    assert((await page.getByTestId('form-frozen').count()) === 1, 'le message « figé » est affiché')
    assert(JSON.stringify(await bins()) === '["12.500"]', `solde: ${await bins()}`)
    assert((await api(cfg, 'GET', '/list/Stock%20Ledger%20Entry')).json.total === 1, 'une écriture au journal')
    await r.shot(page, '17-entry-submitted')
  }, page)

  let issue
  await r.step('sortie trop grande : refusée avec le message du stock et la ligne en cause', async () => {
    await fillEntry('Sortie', '13', { col: 'source_warehouse', typed: 'par', label: /Paris/ })
    await page.getByTestId('save').click()
    await page.waitForURL(/#\/app\/Stock%20Entry\/MS-\d{4}-00002$/)
    issue = decodeURIComponent(page.url().split('/').pop())
    await page.getByTestId('submit').click()
    await confirmDialog('Soumettre')
    await page.getByTestId('error-items.0.qty').waitFor()
    const banner = await page.getByTestId('form-banner').innerText()
    assert(banner.includes('Stock insuffisant') && banner.includes('12,500'), `bandeau: ${banner}`)
    assert((await page.getByTestId('status').innerText()) === 'Brouillon', 'toujours un brouillon')
    assert(JSON.stringify(await bins()) === '["12.500"]', 'le stock n’a pas bougé')
    await r.shot(page, '18-entry-insufficient')
  }, page)

  await r.step('corriger la quantité puis soumettre : le solde descend', async () => {
    await typeInto(cell(0, 'qty').locator('input'), '2,25')
    await page.getByTestId('save').click()
    await toast('Document enregistré')
    await page.getByTestId('submit').click()
    await confirmDialog('Soumettre')
    await page.locator('[data-testid="status"][data-docstatus="1"]').waitFor()
    assert(JSON.stringify(await bins()) === '["10.250"]', `solde: ${await bins()}`)
  }, page)

  await r.step('annuler : le mouvement inverse remet le stock, le journal garde les deux écritures', async () => {
    await page.getByTestId('cancel-doc').click()
    await confirmDialog('Annuler le document')
    await toast('Document annulé')
    await page.locator('[data-testid="status"][data-docstatus="2"]').waitFor()
    assert(JSON.stringify(await bins()) === '["12.500"]', `solde: ${await bins()}`)
    assert((await api(cfg, 'GET', '/list/Stock%20Ledger%20Entry')).json.total === 3, 'réception, sortie et son inverse')
    assert((await page.getByTestId('amend').count()) === 1, 'Amender est proposé')
  }, page)

  await r.step('amender : une copie en brouillon, rattachée à l’original', async () => {
    await page.getByTestId('amend').click()
    await toast('Amendement créé')
    await page.waitForURL(new RegExp(`${issue}-1$`))
    // La route change avant que le nouveau document soit chargé : on attend sa pastille, on ne la lit pas tout de suite.
    await page.locator('[data-testid="status"][data-docstatus="0"]').waitFor()
    assert((await page.getByTestId('status').innerText()) === 'Brouillon', 'brouillon')
    assert((await page.getByTestId('amended-from').innerText()) === issue, 'la filiation est visible')
    assert((await page.getByTestId('save').count()) === 1, 'on peut le modifier')
    await r.shot(page, '19-entry-amended')
  }, page)

  await r.step('liste : filtrer par statut', async () => {
    await page.goto(`${cfg.desk}#/app/Stock%20Entry`)
    await page.getByTestId('list-count').filter({ hasText: '3 sur 3' }).waitFor()
    await page.getByTestId('status-filter').getByText('Annulé', { exact: true }).click()
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
    await page.getByTestId(`status-${issue}`).waitFor()
    await page.getByTestId('status-filter').getByText('Soumis', { exact: true }).click()
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
    await page.getByTestId('status-filter').getByText('Brouillon', { exact: true }).click()
    await page.getByTestId('list-count').filter({ hasText: '1 sur 1' }).waitFor()
    await r.shot(page, '20-entries-list')
  }, page)

  await r.step('historique : qui a soumis et annulé', async () => {
    await open(issue)
    await page.locator('[data-testid="feed-item"][data-kind="version"][data-action="submit"]').waitFor()
    await page.locator('[data-testid="feed-item"][data-kind="version"][data-action="cancel"]').waitFor()
  }, page)

  await r.step('le journal et les soldes se consultent mais ne se créent pas à la main', async () => {
    for (const doctype of ['Stock%20Ledger%20Entry', 'Bin']) {
      await page.goto(`${cfg.desk}#/app/${doctype}`)
      await page.getByTestId('list-title').waitFor()
      assert((await page.getByTestId('new-doc').count()) === 0, `${doctype}: pas de bouton Nouveau`)
    }
    await page.locator('td', { hasText: '12,500' }).first().waitFor() // le solde, formaté en français
  }, page)

  // ── Reprise du Desk : accueil, formulaire de saisie, thème, téléphone ─────────────────────────────────────────────────
  await r.step('accueil : récents, libellés de module et création rapide', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.getByTestId('recents').waitFor()
    assert((await page.locator('[data-testid="recents"] .recent-row').count()) >= 1, 'au moins un document récent')
    const spaces = await page.locator('.home-space strong').allTextContents()
    assert(spaces.includes('Ventes') && spaces.includes('Stock'), `espaces de l'accueil: ${spaces}`)
    // Les cartes d'espaces ne doublent pas le menu : la barre latérale dépliée les liste déjà (224 px sur 720 pour rien), elles
    // s'effacent donc ici — tout en restant dans la page pour les cas où le menu ne les montre pas.
    await page.getByTestId('space-stock').waitFor({ state: 'hidden' })
    await r.shot(page, '21-home')
    // Barre repliée : le menu ne liste plus rien, les cartes reviennent et redeviennent le chemin vers les espaces.
    await page.getByTestId('nav-toggle').click()
    await page.waitForFunction(() => document.querySelector('.desk-shell').classList.contains('collapsed'))
    // La page d'un espace : raccourcis, chiffres, « + » là où l'on peut créer.
    await page.getByTestId('space-stock').click()
    await page.waitForURL(/#\/w\/stock$/)
    await page.getByTestId('nav-toggle').click() // on redéploie : la suite parle du menu déplié
    await page.waitForFunction(() => !document.querySelector('.desk-shell').classList.contains('collapsed'))
    await page.getByTestId('count-Stock Entry').waitFor()
    assert((await page.getByTestId('count-Stock Entry').innerText()) === '3', 'trois mouvements de stock comptés')
    assert((await page.getByTestId('new-Bin').count()) === 0, 'pas de « + » là où on ne peut pas créer (soldes)')
    assert((await page.locator('[data-testid="ws-stock"] .ws-sub').count()) === 1, 'l’espace courant est déplié dans la barre latérale')
    await r.shot(page, '21b-workspace')
    await page.getByTestId('new-Warehouse').click()
    await page.waitForURL(/#\/app\/Warehouse\/new$/)
  }, page)

  await r.step('listes : le numéro du document est la première colonne, les quantités portent leur unité', async () => {
    await page.goto(`${cfg.desk}#/app/Stock%20Entry`)
    await page.locator('th', { hasText: 'N°' }).waitFor()
    await page.locator('td a', { hasText: /^MS-\d{4}-\d{5}$/ }).first().waitFor()
    await page.goto(`${cfg.desk}#/app/Bin`)
    await page.locator('td.num', { hasText: /12,500 pcs/ }).waitFor()
  }, page)

  await r.step('barre de commande : Ctrl+G, on tape, Entrée ; et la barre latérale se replie, choix gardé', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.getByTestId('user-menu').waitFor()
    await page.keyboard.press('Control+g')
    await page.getByTestId('palette-input').waitFor()
    await page.keyboard.type('mouvem')
    await page.getByTestId('cmd-go:Stock Entry').waitFor()
    await page.keyboard.press('Enter')
    await page.waitForURL(/#\/app\/Stock%20Entry$/)
    await page.getByTestId('palette-input').waitFor({ state: 'detached' })
    // Par la souris aussi, et avec un mot sans accent pour un libellé accentué.
    await page.getByTestId('search').click()
    await page.keyboard.type('entrepot')
    await page.getByTestId('cmd-go:Warehouse').waitFor()
    await page.keyboard.press('Escape')
    await page.getByTestId('palette-input').waitFor({ state: 'detached' })
    // La barre latérale se replie, et le choix survit au rechargement.
    await page.getByTestId('nav-toggle').click()
    await page.waitForFunction(() => document.querySelector('.desk-shell').classList.contains('collapsed'))
    await page.reload()
    await page.getByTestId('user-menu').waitFor()
    assert(await page.evaluate(() => document.querySelector('.desk-shell').classList.contains('collapsed')), 'la barre repliée le reste')
    await page.getByTestId('nav-toggle').click()
    await page.waitForFunction(() => !document.querySelector('.desk-shell').classList.contains('collapsed'))
  }, page)

  await r.step('mouvement : date du jour, colonnes selon la nature, unité de l’article, Tab ajoute une ligne', async () => {
    await page.goto(`${cfg.desk}#/app/Stock%20Entry/new`)
    const utc = new Date().toISOString().slice(0, 10).split('-').reverse().join('/')
    assert((await field('posting_date').locator('input').inputValue()) === utc, 'la date du jour est préremplie')
    await page.getByTestId('add-row-items').click()
    assert((await page.locator('[data-col="source_warehouse"]').count()) === 1, 'nature pas choisie : les deux entrepôts sont là')
    await field('purpose').locator('.p-select').click()
    await page.getByRole('option', { name: 'Réception' }).click()
    await page.locator('th', { hasText: 'Entrepôt source' }).waitFor({ state: 'detached' })
    assert((await page.locator('[data-col="target_warehouse"]').count()) === 1, 'une réception garde l’entrepôt cible')
    await pickLink(cell(0, 'item').locator('input'), 'vis', /Vis/)
    await page.getByTestId('unit-0-qty').waitFor()
    assert((await page.getByTestId('unit-0-qty').innerText()) === 'pcs', 'l’unité de l’article suit la quantité')
    // Tab dans la dernière cellule de la dernière ligne : une nouvelle ligne, sans toucher la souris.
    await cell(0, 'target_warehouse').locator('input').focus()
    await page.keyboard.press('Tab')
    await page.locator('[data-row="1"]').waitFor()
    await r.shot(page, '22-entry-columns')
  }, page)

  await r.step('thème sombre : le choix est gardé après rechargement, « automatique » le rend au système', async () => {
    await page.goto(`${cfg.desk}#/`)
    // Le mouvement de l'étape précédente n'est pas enregistré : la garde demande si l'on quitte.
    await page.getByRole('button', { name: 'Quitter' }).click({ timeout: 3000 }).catch(() => {})
    const dark = () => page.evaluate(() => document.documentElement.classList.contains('app-dark'))
    await page.getByTestId('user-menu').click()
    await page.getByRole('menuitem', { name: 'Sombre' }).click()
    await page.waitForFunction(() => document.documentElement.classList.contains('app-dark'))
    await r.shot(page, '23-dark')
    await page.reload()
    await page.getByTestId('user-menu').waitFor()
    assert(await dark(), 'le thème sombre survit au rechargement')
    await page.getByTestId('user-menu').click()
    await page.getByRole('menuitem', { name: 'Automatique' }).click()
    await page.waitForFunction(() => !document.documentElement.classList.contains('app-dark')) // le navigateur du test préfère le clair
  }, page)

  await r.step('téléphone : le menu s’ouvre en tiroir et rien ne déborde de la page', async () => {
    const phone = await browser.newContext({ locale: 'fr-FR', viewport: { width: 390, height: 844 } })
    const p = await phone.newPage()
    try {
      await p.goto(cfg.desk)
      await p.locator('#login-token').fill(cfg.token)
      await p.getByRole('button', { name: 'Se connecter' }).click()
      await p.waitForURL(/#\/$/)
      // Le menu est escamoté : les cartes d'espaces de l'accueil sont la porte d'entrée du téléphone, elles s'affichent.
      await p.getByTestId('space-stock').waitFor()
      const overflow = () => p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      const left = async () => (await p.locator('#desk-sidebar').boundingBox())?.x ?? 0
      assert((await left()) < 0, 'le menu est rangé hors écran')
      await p.getByTestId('nav-toggle').click()
      await p.waitForFunction(() => document.querySelector('#desk-sidebar').getBoundingClientRect().x >= 0)
      await p.getByTestId('ws-toggle-stock').click()
      await p.locator('#desk-sidebar').getByRole('link', { name: 'Mouvement de stock' }).click()
      await p.waitForURL(/#\/app\/Stock%20Entry$/)
      await p.waitForFunction(() => document.querySelector('#desk-sidebar').getBoundingClientRect().x < 0) // choisir une page referme le menu
      assert((await overflow()) <= 1, `la liste déborde de ${await overflow()} px`)
      await p.getByTestId('new-doc').click()
      await p.waitForURL(/#\/app\/Stock%20Entry\/new$/)
      await p.getByTestId('add-row-items').click()
      assert((await overflow()) <= 1, `le formulaire déborde de ${await overflow()} px`)
      assert((await p.locator('[data-row="0"] td[data-label="Quantité"]').count()) === 1, 'les lignes se lisent en fiches (un libellé par cellule)')
      await r.shot(p, '24-phone-form')
    } finally {
      await phone.close()
    }
  }, page)

  await r.step('déconnexion : retour à la connexion', async () => {
    await page.getByTestId('user-menu').click()
    await page.getByRole('menuitem', { name: 'Se déconnecter' }).click()
    await page.waitForURL(/#\/login/)
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
