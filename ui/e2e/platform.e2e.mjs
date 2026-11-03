/**
 * La plateforme dans un vrai navigateur, face au VRAI xpulse (flux SSE sur `ext.pubsub`) et au VRAI xlicense :
 *
 * 1. le flux temps réel s'ouvre (Transport.stream, jeton dans un en-tête, jamais dans l'URL) ;
 * 2. une liste suit un document créé AILLEURS (par l'API), sans recharger la page ;
 * 3. une fiche intacte suit une modification faite ailleurs ; une fiche en cours de saisie garde la saisie et le dit, « Relire » relit ;
 * 4. « Mes modules » : le catalogue, prendre un module gratuit, et la caisse d'un module payant qui, SANS Stripe (pas de clés ici),
 *    refuse proprement : un message lisible, aucune navigation vers une adresse de paiement.
 *
 * Aucune erreur de console hors les refus attendus.
 */
import { api, assert, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)
const license = cfg.api.replace(/\/xcore-doctype$/, '/xlicense')
const streams = []
page.on('request', (q) => {
  if (q.url().includes('/xpulse/stream')) streams.push({ url: q.url(), auth: q.headers().authorization ?? '' })
})

console.log('Desk — plateforme : temps réel (xpulse) et « Mes modules » (xlicense)')
try {
  await r.step('connexion : le flux temps réel s’ouvre, jeton dans un en-tête', async () => {
    await page.goto(`${cfg.desk}#/app/Customer`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    // L'attente est posée AVANT de se connecter : le flux s'ouvre aussitôt la session établie.
    const opened = page.waitForResponse((res) => res.url().includes('/xpulse/stream'), { timeout: 15_000 })
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/app\/Customer$/)
    await page.getByTestId('list-title').waitFor()
    const res = await opened
    assert(res.status() === 200, `flux xpulse : ${res.status()}`)
    const s = streams.at(-1)
    assert(s, 'aucun flux /xpulse/stream demandé')
    assert(s.url.includes('channels=notification') && s.url.includes(`channels=tenant-${cfg.tenant}`), `canaux : ${s.url}`)
    assert(!s.url.includes(cfg.token), 'le jeton ne doit jamais être dans l’URL')
    assert(s.auth === `Bearer ${cfg.token}`, 'le jeton part dans l’en-tête Authorization')
  }, page)

  let customer
  await r.step('une liste suit un document créé ailleurs, sans recharger la page', async () => {
    await page.evaluate(() => (window.__sameDocument = true))
    const res = await api(cfg, 'POST', '/doc/Customer', { data: { customer_name: 'Créé ailleurs SA', type: 'Particulier' } })
    assert(res.status === 201, `création : ${res.status} ${JSON.stringify(res.json)}`)
    customer = res.json
    await page.getByText('Créé ailleurs SA').first().waitFor({ timeout: 10_000 })
    assert(await page.evaluate(() => window.__sameDocument === true), 'la page a été rechargée')
    await r.shot(page, 'platform-01-liste')
  }, page)

  const nameInput = () => page.locator('[data-field="customer_name"] input')
  await r.step('une fiche intacte suit une modification faite ailleurs', async () => {
    await page.goto(`${cfg.desk}#/app/Customer/${encodeURIComponent(customer.name)}`)
    await nameInput().waitFor()
    assert((await nameInput().inputValue()) === 'Créé ailleurs SA', 'valeur initiale')
    const res = await api(cfg, 'PUT', `/doc/Customer/${encodeURIComponent(customer.name)}`, { data: { ...customer.data, customer_name: 'Renommé ailleurs' }, version: customer.version })
    assert(res.status === 200, `mise à jour : ${res.status} ${JSON.stringify(res.json)}`)
    customer = res.json
    await page.waitForFunction(() => document.querySelector('[data-field="customer_name"] input')?.value === 'Renommé ailleurs', null, { timeout: 10_000 })
  }, page)

  await r.step('une fiche en cours de saisie garde la saisie et le dit ; « Relire » relit', async () => {
    await nameInput().fill('Ma saisie en cours')
    const res = await api(cfg, 'PUT', `/doc/Customer/${encodeURIComponent(customer.name)}`, { data: { ...customer.data, customer_name: 'Changé pendant la saisie' }, version: customer.version })
    assert(res.status === 200, `mise à jour : ${res.status}`)
    await page.getByTestId('form-changed-elsewhere').waitFor({ timeout: 10_000 })
    assert((await nameInput().inputValue()) === 'Ma saisie en cours', 'la saisie a été écrasée')
    await r.shot(page, 'platform-02-fiche-modifiee-ailleurs')
    await page.getByTestId('form-reload-changed').click()
    await page.waitForFunction(() => document.querySelector('[data-field="customer_name"] input')?.value === 'Changé pendant la saisie', null, { timeout: 10_000 })
    await page.getByTestId('form-changed-elsewhere').waitFor({ state: 'detached' })
  }, page)

  await r.step('« Mes modules » : mettre deux modules en rayon (administrateur de la plateforme)', async () => {
    for (const body of [
      { code: 'crm', label: 'CRM', description: 'Prospects et opportunités', free: true },
      { code: 'caisse', label: 'Caisse', description: 'La vente au comptoir', free: false, price: 15000, currency: 'XOF', period: 'month', stripe_price_id: 'price_e2e_absent' },
    ]) {
      const res = await fetch(`${license}/modules/${body.code}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${cfg.token}`, 'X-Tenant-ID': cfg.tenant, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      assert(res.status === 200, `mise en rayon ${body.code} : ${res.status} ${await res.text()}`)
    }
  }, page)

  await r.step('« Mes modules » depuis le menu du compte : le catalogue et les prix', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.getByRole('heading', { level: 1 }).first().waitFor()
    await page.goto(`${cfg.desk}#/modules`)
    await page.getByTestId('modules').waitFor()
    await page.locator('[data-module="crm"]').waitFor()
    const price = await page.locator('[data-module="caisse"] [data-testid="module-price"]').innerText()
    assert(/15\s?000/.test(price) && /mois/.test(price), `prix de la caisse : ${price}`)
    assert((await page.locator('[data-module="crm"] [data-testid="module-status"]').innerText()) === 'Non pris', 'CRM non pris au départ')
    // Cette organisation n'a pas de licence active : la page doit le DIRE, lisiblement (mesuré : la boîte était rendue vide).
    const blocked = page.getByTestId('modules-blocked')
    await blocked.waitFor()
    // La boîte de PrimeVue entre par une animation de hauteur : on mesure une fois arrivée (une capture prise avant la montre écrasée).
    await page.waitForFunction(() => (document.querySelector('[data-testid="modules-blocked"]')?.getBoundingClientRect().height ?? 0) >= 24, null, { timeout: 5_000 })
    const box = await blocked.boundingBox()
    const text = await blocked.innerText()
    assert(/licence/i.test(text) && box && box.height >= 24, `message de licence illisible : ${JSON.stringify({ text, box })}`)
    await r.shot(page, 'platform-03-mes-modules')
  }, page)

  await r.step('prendre un module gratuit : la page se recharge et le dit pris', async () => {
    await page.locator('[data-module="crm"] [data-testid="module-take"]').click()
    await page.waitForFunction(() => {
      const s = document.querySelector('[data-module="crm"] [data-testid="module-status"]')
      return s && /Actif|Pris/.test(s.textContent ?? '')
    }, null, { timeout: 10_000 })
    await page.locator('[data-module="crm"] [data-testid="module-drop"]').waitFor({ timeout: 10_000 })
    // La page se recharge d'elle-même (droits et espaces changent) : on attend ce rechargement avant la suite.
    await page.waitForEvent('load', { timeout: 10_000 })
    await page.locator('[data-module="caisse"] [data-testid="module-subscribe"]').waitFor()
    await r.shot(page, 'platform-04-crm-pris')
  }, page)

  await r.step('s’abonner à un module payant sans Stripe : refus lisible, aucune navigation', async () => {
    const before = page.url()
    const answer = page.waitForResponse((res) => res.url().includes('/me/modules/caisse/checkout'))
    await page.locator('[data-module="caisse"] [data-testid="module-subscribe"]').click()
    const res = await answer
    assert(res.status() >= 500, `la caisse sans prestataire devait échouer côté serveur : ${res.status()}`)
    const toast = page.locator('.p-toast-message').filter({ hasText: /paiement|prestataire/i }).first()
    await toast.waitFor({ timeout: 10_000 })
    const text = await toast.innerText()
    assert(/paiement|prestataire/i.test(text), `message lisible attendu, reçu : ${text}`)
    assert(page.url() === before, `navigation inattendue vers ${page.url()}`)
    await r.shot(page, 'platform-05-caisse-sans-stripe')
  }, page)

} finally {
  await browser.close()
}

// Attendus : la caisse sans prestataire (5xx sur /checkout) et le flux temps réel coupé à la fermeture du navigateur.
const bad = problems.filter((p) => !/Failed to load resource.*(401|403|404|409|422|50\d)/.test(p) && !/xpulse\/stream/.test(p))
if (bad.length) {
  console.log('\nProblèmes console/réseau :')
  for (const p of bad) console.log('  -', p)
  r.failures.push(`${bad.length} problème(s) console ou réseau`)
}
console.log(r.failures.length ? `\n${r.failures.length} échec(s)` : '\nTout est passé')
process.exit(r.failures.length ? 1 : 0)
