/**
 * Imprimer un document dans un VRAI navigateur (process/impression.md §7, étape 5).
 *
 * Ce scénario existe parce que l'essentiel ne se vérifie pas au vitest : jsdom n'applique pas l'isolation
 * d'origine d'une iframe en bac à sable. C'est un vrai Chromium qui a montré qu'avec `sandbox="allow-modals"`
 * seul, `frame.contentWindow.print` lève un `SecurityError` depuis la page parente — le bouton « Imprimer »
 * aurait échoué sans rien dire. On vérifie donc ICI :
 *
 * - l'aperçu arrive bien dans l'iframe et porte le document (titre, client, lignes) ;
 * - le document n'est JAMAIS analysé en nœuds vivants de la page du Desk (c'est tout l'intérêt de `srcdoc`) ;
 * - `contentWindow.print` est ATTEIGNABLE depuis la page (l'erreur qu'on vient de corriger) ;
 * - rien n'exécute de script dans ce cadre (`allow-scripts` absent, et on le prouve en y mettant un script) ;
 * - le changement de gabarit redemande l'aperçu ;
 * - le PDF se télécharge et commence par `%PDF` quand le serveur a le moteur ; sinon le dialogue le DIT.
 *
 * Et tout cela sous la vraie CSP du Desk, qui n'autorise pas `unsafe-inline`.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)

const frameAttr = (name) => page.getByTestId('print-frame').getAttribute(name)

console.log('Desk — Imprimer')
try {
  let invoice
  await r.step('un document à imprimer, puis « Imprimer » depuis le menu « ⋯ » de la fiche', async () => {
    // `company_id` est obligatoire dès que `type` vaut « Entreprise » (`required` + `depends_on` dans le YAML).
    const made = await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Société Durand & Fils', type: 'Entreprise', company_id: 'FR-12345' } })
    assert(made.status === 201, `création du client : ${made.status} ${JSON.stringify(made.json)}`)
    invoice = made.json.name

    await page.goto(`${cfg.desk}#/app/Customer/${encodeURIComponent(invoice)}`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.getByTestId('form-title').waitFor()
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    await page.getByTestId('print').click()
    await page.getByTestId('print-frame').waitFor()
    await r.shot(page, 'print-01-apercu')
  }, page)

  await r.step('l’aperçu porte le document, et il vit DANS le cadre, pas dans la page du Desk', async () => {
    const srcdoc = await frameAttr('srcdoc')
    assert(srcdoc.includes('Société Durand &amp; Fils') || srcdoc.includes('Société Durand &#x26; Fils'), `le client n'est pas dans l'aperçu : ${srcdoc.slice(0, 300)}`)
    assert(srcdoc.includes(invoice), `le numéro du document manque : ${srcdoc.slice(0, 300)}`)

    // Le document rendu doit être analysé DANS le cadre et nulle part ailleurs. On le mesure par les classes de
    // NOTRE feuille de style d'impression (`table.fields`, `table.lines`, `div.lh`), qui n'existent que là :
    // chercher le nom du client dans la page ne prouverait rien, puisque le titre de la fiche l'affiche aussi —
    // c'est l'erreur que la première version de ce test a commise.
    const where = await page.evaluate(() => {
      const marks = 'table.fields, table.lines, table.totaux, div.lh, div.lf'
      const f = document.querySelector('[data-testid="print-frame"]')
      return {
        inFrame: f.contentDocument ? f.contentDocument.querySelectorAll(marks).length : -1,
        inPage: document.querySelectorAll(marks).length,
      }
    })
    assert(where.inFrame > 0, `le document n'est pas rendu dans le cadre (${where.inFrame})`)
    assert(where.inPage === 0, `le document a fui dans la page du Desk : ${where.inPage} élément(s) d'impression`)
  }, page)

  await r.step('le bac à sable : aucun script, mais `print()` reste atteignable depuis la page', async () => {
    const sandbox = (await frameAttr('sandbox')).split(/\s+/)
    assert(!sandbox.includes('allow-scripts'), `allow-scripts ne doit JAMAIS y être : ${sandbox.join(' ')}`)
    assert(sandbox.includes('allow-same-origin'), `sans allow-same-origin, print() est refusé : ${sandbox.join(' ')}`)
    // LE point du scénario : c'est cet accès qui levait un SecurityError avant la correction.
    const reach = await page.evaluate(() => {
      const f = document.querySelector('[data-testid="print-frame"]')
      try {
        return typeof f.contentWindow.print
      } catch (e) {
        return `REFUSE: ${e.name}`
      }
    })
    assert(reach === 'function', `print() n'est pas atteignable dans le cadre : ${reach}`)
  }, page)

  await r.step('rien ne s’exécute dans le cadre, même si le HTML en contient', async () => {
    const ran = await page.evaluate(async () => {
      const host = document.createElement('div')
      document.body.appendChild(host)
      const f = document.createElement('iframe')
      f.setAttribute('sandbox', 'allow-same-origin allow-modals') // le même bac à sable que l'aperçu
      f.srcdoc = '<html><body><script>window.parent.__script_a_tourne = true<\/script>x</body></html>'
      host.appendChild(f)
      await new Promise((res) => setTimeout(res, 300))
      host.remove()
      return window.__script_a_tourne === true
    })
    assert(ran === false, 'un script a tourné dans le cadre : le bac à sable ne tient pas')
  }, page)

  await r.step('changer de papier à en-tête redemande l’aperçu au serveur', async () => {
    // On mesure la REQUÊTE, pas le HTML : sans papier à en-tête déclaré, les deux rendus sont identiques au
    // caractère près, donc comparer les `srcdoc` ne prouverait rien (et attendre une différence bloquerait).
    const asked = []
    const listen = (req) => {
      if (/\/print\/Customer\//.test(req.url())) asked.push(new URL(req.url()).search)
    }
    page.on('request', listen)
    try {
      await page.getByTestId('print-letterhead').click()
      await page.getByRole('option', { name: 'Aucun' }).click()
      await page.waitForTimeout(800) // le temps d'une relecture : il n'y a pas d'événement à attendre
      assert(
        asked.some((q) => q.includes('letterhead=none')),
        `aucune relecture avec « letterhead=none » : ${JSON.stringify(asked)}`,
      )
    } finally {
      page.off('request', listen)
    }
    const after = await frameAttr('srcdoc')
    assert(after.includes(invoice), `l'aperçu a disparu après le changement : ${after.slice(0, 200)}`)
    await r.shot(page, 'print-02-sans-entete')
  }, page)

  await r.step('le PDF : téléchargé et commençant par %PDF, ou bien son absence est DITE', async () => {
    const hasPdf = (await api(cfg, 'GET', '/print-templates/Customer')).json.pdf
    if (!hasPdf) {
      await page.getByTestId('print-no-engine').waitFor()
      assert((await page.getByTestId('print-pdf').count()) === 0, 'le bouton PDF ne doit pas exister sans le moteur')
      // Et « Imprimer » reste proposé : l'aperçu s'imprime depuis le navigateur.
      await page.getByTestId('print-now').waitFor()
      return
    }
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 20000 }), page.getByTestId('print-pdf').click()])
    const name = download.suggestedFilename()
    assert(name.endsWith('.pdf'), `nom de fichier inattendu : ${name}`)
    const stream = await download.createReadStream()
    const chunks = []
    for await (const chunk of stream) chunks.push(chunk)
    const head = Buffer.concat(chunks).subarray(0, 5).toString('latin1')
    assert(head.startsWith('%PDF'), `le fichier téléchargé n'est pas un PDF : ${JSON.stringify(head)}`)
    await r.shot(page, 'print-03-pdf')
  }, page)

  await r.step('les gabarits LIVRÉS par le plugin sont offerts, et chacun imprime le sien', async () => {
    // Le plugin `sales` livre deux gabarits pour `Invoice` (`tests/support/plugins/sales/print/`) : c'est le
    // chemin de §2 — un plugin connaît la forme de sa facture et ne laisse pas le moteur la deviner.
    const client = await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Durand', type: 'Entreprise', company_id: 'FR-1' } })
    const item = await api(cfg, 'POST', '/doc/Item', { data: { item_name: 'Chaise en hêtre', uom: 'pcs' } })
    const made = await api(cfg, 'POST', '/doc/Invoice', {
      data: {
        customer: client.json.name, posting_date: '2026-10-28', due_date: '2026-11-27', payment: 'Impayée',
        items: [{ item: item.json.name, qty: 2, rate: 250 }, { item: item.json.name, qty: 3, rate: 19.9 }],
      },
    })
    assert(made.status === 201, `facture : ${made.status} ${JSON.stringify(made.json)}`)
    const invoice = made.json.name

    const listed = await api(cfg, 'GET', '/print-templates/Invoice')
    const names = listed.json.templates.map((x) => `${x.name}:${x.source}`).sort()
    assert(JSON.stringify(names) === JSON.stringify(['duplicata:plugin', 'standard:plugin']), `gabarits offerts : ${JSON.stringify(names)}`)
    assert(listed.json.default === 'standard', `défaut attendu « standard » : ${listed.json.default}`)

    const standard = await api(cfg, 'GET', `/print/Invoice/${encodeURIComponent(invoice)}`)
    const duplicata = await api(cfg, 'GET', `/print/Invoice/${encodeURIComponent(invoice)}?template=duplicata`)
    assert(standard.json.html.includes(`Facture ${invoice}`), 'le gabarit standard n’imprime pas le titre attendu')
    assert(duplicata.json.html.includes('DUPLICATA'), 'le duplicata n’imprime pas son propre titre')
    // Le TITRE du client et de l'article, jamais leur identifiant : sans cela le papier est illisible.
    assert(standard.json.html.includes('Durand'), `le nom du client manque : ${standard.json.html.slice(0, 400)}`)
    assert(standard.json.html.includes('Chaise en hêtre'), 'le libellé de l’article manque')
    assert(!standard.json.html.includes(client.json.name), `l'identifiant du client est imprimé : ${client.json.name}`)
    // Le MONTANT de chaque ligne, calculé par le serveur (`compute: qty * rate`), et sans bruit binaire :
    // 3 × 19,9 vaut 59.699999999999996 en machine et n'a pas sa place sur une facture.
    assert(standard.json.html.includes('>500<'), `le montant 500 manque : ${standard.json.html.slice(0, 600)}`)
    assert(standard.json.html.includes('>59.7<'), 'le montant 59,7 manque, ou porte le bruit du flottant')
    assert(!standard.json.html.includes('59.699'), 'du bruit de flottant est imprimé')
  }, page)

  await r.step('téléphone : le dialogue tient dans l’écran', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByTestId('print-frame').waitFor()
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    assert(over <= 1, `la page déborde de ${over} px`)
    await r.shot(page, 'print-04-telephone')
    await page.setViewportSize({ width: 1280, height: 800 })
  }, page)
} finally {
  await browser.close()
}

// L'étape qui prouve le bac à sable fait DÉLIBÉRÉMENT bloquer un script : son message de console est la preuve
// que le cadre tient, pas un défaut. On l'écarte donc du contrôle final, nommément.
const bad = problems.filter(
  (p) => !/Failed to load resource.*(401|403|404|409|422)/.test(p) && !/Blocked script execution in 'about:srcdoc'/.test(p),
)
if (bad.length) {
  console.log('\nProblèmes console/réseau :')
  for (const p of bad) console.log('  -', p)
  r.failures.push(`${bad.length} problème(s) console ou réseau`)
}
console.log(r.failures.length ? `\n${r.failures.length} échec(s)` : '\nTout est passé')
process.exit(r.failures.length ? 1 : 0)
