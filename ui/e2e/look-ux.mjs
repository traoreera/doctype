// Comportements d'expérience utilisateur vérifiés dans un vrai navigateur : accès clavier, titre de page, ligne cliquable, sélection, erreurs qui mènent au champ,
// en-tête collant, deux colonnes, cartes sur téléphone, création rapide. Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD, SHOTS_DIR ; LOGIN (facultatif).
import { launch } from './helpers.mjs'

const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
const check = (cond, m, extra = '') => (cond ? ok(m) : fail(`${m} ${extra}`))
const shot = (page, name) => page.screenshot({ path: `${process.env.SHOTS_DIR}/${name}.png` })
const { browser, page, problems } = await launch()
await page.goto(process.env.DESK_URL)
await page.fill('#login-email', process.env.LOGIN ?? process.env.ADMIN_EMAIL)
await page.fill('#login-password', process.env.ADMIN_PASSWORD)
await page.click('button[type="submit"]')
await page.waitForSelector('.desk-sidebar, #login-tenant', { timeout: 15000 })
if (await page.$('#login-tenant')) {
  await page.click('#login-tenant')
  await page.click('[role="option"]:has-text("Default")')
  await page.click('button[type="submit"]')
  await page.waitForSelector('.desk-sidebar', { timeout: 15000 })
}
const token = await page.evaluate(() => sessionStorage.getItem('xdt.desk.token'))
const tenant = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).tenant_id
const api = (method, path, body) => page.evaluate(async ([m, p, b, tok, ten]) => {
  const r = await fetch(`/app/xcore-doctype${p}`, { method: m, headers: { 'content-type': 'application/json', authorization: `Bearer ${tok}`, 'x-tenant-id': ten }, body: b ? JSON.stringify(b) : undefined })
  return { status: r.status, body: r.status === 204 ? null : await r.json() }
}, [method, path, body, token, tenant])
const go = async (hash) => {
  await page.goto(process.env.DESK_URL + hash)
  const leave = await page.waitForSelector('.p-confirmdialog-accept-button', { timeout: 500 }).catch(() => null)
  if (leave) await leave.click()
}
const stamp = Date.now().toString(36)
const made = []
const mk = async (dt, data) => { const r = await api('POST', `/doc/${dt}`, { data }); if (r.status === 201) made.push([dt, r.body.name]); return r.body }
const places = []
for (const [label, country] of [['Siège', 'France'], ['Dépôt', 'Belgique'], ['Agence', 'Suisse']]) places.push(await mk('Place', { label: `${label} ${stamp}`, city: label, country }))
for (let i = 0; i < 4; i++) await mk('Visit', { subject: `Visite ${stamp} n°${i + 1}`, place: places[i % 3].name })

// ── Page : titre de l'onglet, focus sur le titre ─────────────────────────────────────────
await go('#/')
await page.waitForSelector('h1')
await page.waitForFunction(() => /Accueil/.test(document.title), null, { timeout: 5000 })
ok(`titre de l’onglet de l’accueil : « ${await page.title()} »`)
await page.click('[data-testid="space-lab"]')
await page.waitForFunction(() => /Vitrine/.test(document.title) && document.activeElement?.tagName === 'H1', null, { timeout: 6000 })
ok(`changement de page : l’onglet devient « ${await page.title()} » et le focus est sur le titre`)

// ── Accès au contenu au clavier ──────────────────────────────────────────────────────────
await go('#/')
await page.reload() // un chargement neuf : le clavier repart du haut de la page
await page.waitForSelector('h1')
await page.keyboard.press('Tab')
const skip = await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))
check(skip === 'skip-link', 'la première touche Tab tombe sur « Aller au contenu »', String(skip))
await shot(page, '150-skip-link')
await page.keyboard.press('Enter')
check(await page.evaluate(() => document.activeElement?.tagName === 'H1'), 'Entrée y mène : le focus est sur le titre de la page')

// ── Repère de focus visible ─────────────────────────────────────────────────────────────
await go('#/app/Visit')
await page.waitForSelector('tbody tr')
await page.focus('[data-testid="new-doc"]')
await page.keyboard.press('Shift+Tab')
await page.keyboard.press('Tab')
const ring = await page.evaluate(() => {
  const el = document.activeElement; const cs = getComputedStyle(el)
  const parse = (c) => c.match(/\d+(\.\d+)?/g).slice(0, 3).map(Number)
  const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
  const out = cs.outlineColor; const shadow = cs.boxShadow
  const bg = parse(getComputedStyle(document.body).backgroundColor)
  const c = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 ? parse(out) : (shadow && shadow !== 'none' ? parse(shadow) : null)
  if (!c) return { ratio: 0, el: el.tagName }
  const a = lum(c), b = lum(bg)
  return { ratio: Math.round(((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)) * 10) / 10, el: `${el.tagName}.${String(el.className).split(' ')[0]}` }
})
check(ring.ratio >= 3, `le repère de focus se voit (contraste ${ring.ratio}:1 sur ${ring.el})`)

// ── Liste : la ligne s'ouvre, la case ne l'ouvre pas, Échap vide la sélection ──────────────
await go('#/app/Visit')
await page.waitForSelector('tbody tr')
const row = page.locator('tbody tr').filter({ hasText: `Visite ${stamp} n°4` })
await row.locator('.p-checkbox').click()
check(await page.getByTestId('selection-bar').isVisible(), 'cocher une ligne fait apparaître la barre de sélection (et n’ouvre pas le document)')
check(/#\/app\/Visit$/.test(page.url()), 'on est toujours dans la liste')
await shot(page, '151-selection-bar')
await page.keyboard.press('Escape')
await page.waitForFunction(() => !document.querySelector('[data-testid="selection-bar"]'), null, { timeout: 3000 })
ok('Échap vide la sélection')
await row.locator('td').nth(3).click()
await page.waitForFunction(() => /#\/app\/Visit\/[^/]+$/.test(location.hash) && !location.hash.endsWith('/new'), null, { timeout: 5000 })
ok('un clic n’importe où sur la ligne ouvre le document')

// ── Liste : état vide avec création ─────────────────────────────────────────────────────
await go('#/app/Memo')
await page.waitForSelector('[data-testid="list-empty"], tbody tr', { timeout: 8000 })
if (await page.$('[data-testid="create-first"]')) {
  ok('liste vide : un bouton propose de créer le premier document')
  await shot(page, '152-empty-state')
} else console.log('· (la liste des mémos n’est pas vide : état vide non vérifié)')

// ── Formulaire : en-tête collant, deux colonnes, erreur qui mène au champ ─────────────────
await page.setViewportSize({ width: 1280, height: 420 })
await go('#/app/Visit/new')
await page.waitForSelector('[data-field="subject"]')
const cols = await page.evaluate(() => {
  const a = document.querySelector('[data-field="subject"]').getBoundingClientRect(); const b = document.querySelector('[data-field="place"]').getBoundingClientRect()
  return { sameRow: Math.abs(a.top - b.top) < 4, left: Math.round(a.left), right: Math.round(b.left) }
})
check(cols.sameRow && cols.right > cols.left + 200, `un formulaire sans disposition se range en deux colonnes (${cols.left} | ${cols.right})`)
await page.evaluate(() => (document.querySelector('.desk-main').scrollTop = 400))
await page.waitForTimeout(200)
const head = await page.evaluate(() => { const r = document.querySelector('.desk-page-head').getBoundingClientRect(); return { top: Math.round(r.top), save: !!document.querySelector('[data-testid="save"]') && document.querySelector('[data-testid="save"]').getBoundingClientRect().top < 120 } })
check(head.top < 60 && head.save, `l’en-tête reste visible quand on défile (haut ${head.top}px, « Enregistrer » à portée)`)
await page.evaluate(() => (document.querySelector('.desk-main').scrollTop = 0))
await page.click('[data-testid="save"]')
await page.waitForSelector('[data-testid="error-list"]', { timeout: 5000 })
const focused = await page.evaluate(() => document.activeElement?.closest('[data-field]')?.getAttribute('data-field'))
check(focused === 'subject', `l’enregistrement refusé met le focus sur le premier champ en erreur : ${focused}`)
const list = await page.textContent('[data-testid="error-list"]')
check(/Objet/.test(list) && /obligatoire/i.test(list), `le bandeau liste les erreurs : « ${list.replace(/\s+/g, ' ').trim()} »`)
await shot(page, '153-form-errors')
await page.setViewportSize({ width: 1280, height: 800 })

// ── Champ lien : la liste s'ouvre au focus, sans taper ────────────────────────────────────
await go('#/app/Visit/new')
await page.waitForSelector('[data-field="place"] input')
await page.click('[data-field="place"] input')
await page.waitForSelector('[role="option"]', { timeout: 5000 })
check((await page.locator('[role="option"]').count()) >= 3, 'un champ lien montre ses premiers choix dès qu’on y entre')
const width = await page.evaluate(() => { const a = document.querySelector('[data-field="subject"] input').getBoundingClientRect(); const b = document.querySelector('[data-field="place"] input').getBoundingClientRect(); return [Math.round(a.width), Math.round(b.width)] })
check(Math.abs(width[0] - width[1]) <= 2, `le champ lien a la largeur des autres champs (${width[0]} / ${width[1]})`)
await page.keyboard.press('Escape')

// ── Accueil : salutation et création rapide ───────────────────────────────────────────────
await go('#/')
await page.waitForSelector('[data-testid="greeting"]')
check(/^Bon(jour|soir)/.test(await page.textContent('[data-testid="greeting"]')), `salutation : « ${(await page.textContent('[data-testid="greeting"]')).trim()} »`)
check((await page.locator('[data-testid="home-create"] a').count()) >= 3, 'l’accueil propose de créer les documents qu’on utilise')
await shot(page, '154-home')

// ── Téléphone : la liste devient des cartes ────────────────────────────────────────────────
await page.setViewportSize({ width: 390, height: 844 })
await go('#/app/Visit')
await page.waitForSelector('[data-testid="record-cards"]', { timeout: 8000 })
check((await page.locator('.record-card').count()) >= 4, 'sur téléphone, chaque document est une carte')
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)
check(!overflow, 'aucun débordement horizontal')
await shot(page, '155-mobile-cards')
await page.locator('.record-card').first().click({ position: { x: 200, y: 40 } })
await page.waitForFunction(() => /#\/app\/Visit\/[^/]+$/.test(location.hash), null, { timeout: 5000 })
ok('toucher une carte l’ouvre')
await page.setViewportSize({ width: 1280, height: 800 })

// ── Ménage ───────────────────────────────────────────────────────────────────────────────
for (const [dt, name] of made.reverse()) await api('DELETE', `/doc/${dt}/${encodeURIComponent(name)}`)
const noise = problems.filter((p) => !/401|favicon|ResizeObserver|status of 4/.test(p))
noise.length ? fail(`problèmes dans la console : ${noise.slice(0, 5).join(' | ')}`) : ok('aucune erreur dans la console')
await browser.close()
