/**
 * Le plugin d'exemple `examples/biblio` dans un vrai navigateur : son extension JavaScript (D28) doit charger sous la vraie CSP et ajouter
 * sa commande, sa page, son lien de barre latérale, son panneau et ses actions de fiche. Aucune erreur de console.
 */
import { api, config, launch, reporter } from './helpers.mjs'

const cfg = config()
const { browser, page, problems } = await launch()
const r = reporter(cfg.shots)

console.log('Desk — extension du plugin d’exemple biblio')
try {
  const livre = (await api(cfg, 'POST', '/doc/Livre', { data: { titre: 'Les Soleils des indépendances', auteur: 'Kourouma' } })).json
  await api(cfg, 'POST', '/doc/Emprunt', { data: { livre: livre.name, emprunteur: 'Moussa', sortie: '2026-01-02', retour_prevu: '2026-01-20' } })

  await r.step('connexion, puis la page de l’extension par lien direct', async () => {
    await page.goto(`${cfg.desk}#/x/biblio/retards`)
    await page.waitForURL(/#\/login/)
    await page.locator('#login-token').fill(cfg.token)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await page.waitForURL(/#\/x\/biblio\/retards$/)
    await page.getByRole('heading', { level: 1, name: 'Emprunts en retard' }).waitFor()
    await page.getByText('Moussa, prévu le 2026-01-20').waitFor()
    await r.shot(page, 'biblio-01-retards')
  }, page)

  await r.step('le lien de l’extension se range sous l’espace Bibliothèque', async () => {
    await page.goto(`${cfg.desk}#/`)
    const link = page.getByTestId('ws-biblio').getByTestId('nav-ext-biblio-Retards')
    if (!(await link.isVisible())) await page.getByTestId('ws-toggle-biblio').click()
    await link.click()
    await page.waitForURL(/#\/x\/biblio\/retards$/)
  }, page)

  await r.step('la barre de commande propose la commande de l’extension', async () => {
    await page.goto(`${cfg.desk}#/`)
    await page.keyboard.press('Control+k')
    await page.getByTestId('palette').locator('input').fill('retard')
    await page.keyboard.press('Enter')
    await page.waitForURL(/#\/x\/biblio\/retards$/)
  }, page)

  await r.step('la fiche Livre montre le panneau, l’action « Prêter ce livre » ouvre un emprunt prérempli', async () => {
    await page.goto(`${cfg.desk}#/app/Livre/${livre.name}`)
    await page.getByTestId('biblio-livre-emprunts').filter({ hasText: 'Sorti en ce moment · 1 emprunt(s) au total' }).waitFor()
    await r.shot(page, 'biblio-02-livre')
    // Ouvert au CLAVIER : un clic simulé fait d'abord défiler la zone principale pour viser le bouton, et le menu surgissant de
    // PrimeVue se referme au défilement (le test échouait une fois sur deux sans que l'extension y soit pour rien).
    await page.getByTestId('more-actions').focus()
    await page.keyboard.press('Enter')
    const item = page.getByTestId('ext-action-biblio-Prêter ce livre')
    await item.waitFor()
    await item.click()
    await page.waitForURL(/#\/app\/Emprunt\/new\?livre=/)
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
