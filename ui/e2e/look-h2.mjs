// Palier 70 %, phases H2/I dans un vrai navigateur contre le vrai xauth : propriétés de champ (fetch_from, unique, set_only_once, no_copy, allow_on_submit),
// nommage (saisi, tiré d'un champ), renommer, dupliquer, lien dynamique, connexions, barre latérale, actions en lot, saisie rapide, aperçu de lien, documentation de l'API.
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD (celles de `.env`), SHOTS_DIR ; LOGIN (facultatif : nom d'utilisateur).
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
const next = await page.waitForSelector('.desk-sidebar, #login-tenant', { timeout: 15000 })
if ((await next.getAttribute('id')) === 'login-tenant' || (await page.$('#login-tenant'))) {
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
const go = async (hash, wait) => {
  await page.goto(process.env.DESK_URL + hash)
  // Un formulaire modifié demande confirmation avant de partir (la garde des modifications non enregistrées) : ici, on part.
  const leave = await page.waitForSelector('.p-confirmdialog-accept-button', { timeout: 700 }).catch(() => null)
  if (leave) await leave.click()
  if (wait) await page.waitForSelector(wait)
}
const stamp = Date.now().toString(36)
const made = []

// ── Les lieux : de quoi lire, renommer ──────────────────────────────────────────────
const paris = await api('POST', '/doc/Place', { data: { label: `Paris ${stamp}`, city: 'Paris', country: 'France' } })
const lyon = await api('POST', '/doc/Place', { data: { label: `Lyon ${stamp}`, city: 'Lyon', country: 'France' } })
const geneva = await api('POST', '/doc/Place', { data: { label: `Genève ${stamp}`, city: 'Genève', country: 'Suisse' } })
paris.status === 201 && lyon.status === 201 && geneva.status === 201 ? ok('trois lieux créés') : fail(`lieux : ${paris.status} ${lyon.status} ${geneva.status}`)
made.push(['Place', paris.body.name], ['Place', lyon.body.name], ['Place', geneva.body.name])

// ── fetch_from : l'aperçu avant l'enregistrement, puis la valeur enregistrée ─────────
await go('#/app/Visit/new', '[data-field="subject"] input')
await page.fill('[data-field="subject"] input', `Visite ${stamp}`)
await page.fill('[data-field="place"] input', `Paris ${stamp}`)
await page.click(`[role="option"]:has-text("Paris ${stamp}")`)
await page.waitForFunction(() => document.querySelector('[data-testid="value-place_city"]')?.textContent.trim() === 'Paris', null, { timeout: 8000 })
ok('fetch_from : la ville du lieu apparaît dès qu’on le choisit, avant d’enregistrer')
const roCity = await page.$('[data-field="place_city"] input')
check(!roCity, 'le champ copié se lit, il ne se saisit pas')
await page.fill('[data-field="contact"] input', `visite-${stamp}@exemple.fr`)
await page.fill('[data-field="badge_no"] input', 'B-100')
await page.fill('[data-field="internal_note"] input', 'à ne pas recopier')
await shot(page, '120-visit-new')
await page.click('[data-testid="save"]')
await page.waitForFunction(() => location.hash.includes('/app/Visit/') && !location.hash.endsWith('/new'), null, { timeout: 10000 })
await page.waitForSelector('[data-testid="status"]:has-text("Enregistré")')
const visitName = decodeURIComponent(page.url().split('/app/Visit/')[1])
made.push(['Visit', visitName])
const saved = await api('GET', `/doc/Visit/${encodeURIComponent(visitName)}`)
check(saved.body.data.place_city === 'Paris' && saved.body.data.place_country === 'France', 'le serveur a enregistré les champs copiés du lieu', JSON.stringify(saved.body.data))

// ── set_only_once : le badge ne bouge plus ───────────────────────────────────────────
check(!(await page.$('[data-field="badge_no"] input')), 'le n° de badge, posé, est figé (lecture seule)')

// ── unique : la même adresse sur une seconde visite ─────────────────────────────────
await go('#/app/Visit/new', '[data-field="subject"] input')
await page.fill('[data-field="subject"] input', `Doublon ${stamp}`)
await page.fill('[data-field="contact"] input', `visite-${stamp}@exemple.fr`)
await page.click('[data-testid="save"]')
await page.waitForSelector('[data-testid="error-contact"]', { timeout: 8000 })
const dupMsg = await page.textContent('[data-testid="error-contact"]')
check(/déjà utilisée/.test(dupMsg), `unique : « ${dupMsg.trim()} » sous le champ`)
await shot(page, '121-visit-unique')

// ── Connexions, aperçu de lien, renommer ─────────────────────────────────────────────
await go(`#/app/Place/${encodeURIComponent(paris.body.name)}`, '[data-testid="connections"]')
const conn = await page.textContent('[data-testid="connections"]')
check(/Visite\s*1/.test(conn), `connexions : « ${conn.replace(/\s+/g, ' ').trim()} »`)
check(!!(await page.$('[data-testid="activity"]')), 'les connexions et l’activité (commentaires, historique) sont toutes deux là')
await shot(page, '122-place-connections')
await page.click('[data-testid="more-actions"]')
await page.click('[data-testid="rename"]')
await page.waitForSelector('[data-testid="rename-input"]')
const newPlaceName = `Paris centre ${stamp}`
await page.fill('[data-testid="rename-input"]', newPlaceName)
await shot(page, '123-rename-dialog')
await page.click('[data-testid="rename-confirm"]')
await page.waitForFunction((n) => decodeURIComponent(location.hash).includes(n), newPlaceName, { timeout: 10000 })
made.splice(made.findIndex((m) => m[1] === paris.body.name), 1, ['Place', newPlaceName])
const afterRename = await api('GET', `/doc/Visit/${encodeURIComponent(visitName)}`)
check(afterRename.body.data.place === newPlaceName, `renommer : la visite cite désormais « ${afterRename.body.data.place} »`)
const hist = await api('GET', `/history/Place/${encodeURIComponent(newPlaceName)}`)
check(hist.body.items[0].action === 'rename', 'renommer : une entrée d’historique garde l’ancien nom')

// ── Dupliquer ───────────────────────────────────────────────────────────────────────
await go(`#/app/Visit/${encodeURIComponent(visitName)}`, '[data-testid="more-actions"]')
await page.waitForSelector('[data-testid="link-open"]')
await page.hover('[data-field="place"] [data-testid="link-open"]')
await page.waitForSelector('[data-testid="link-preview"]', { timeout: 6000 })
await shot(page, '124-link-preview')
ok('aperçu du lieu au survol de la flèche')
await page.click('[data-testid="more-actions"]')
await page.click('[data-testid="duplicate"]')
await page.waitForSelector('[data-testid="form-copy"]')
const copyContact = await page.inputValue('[data-field="contact"] input')
const copyNote = await page.inputValue('[data-field="internal_note"] input')
const copyBadge = await page.inputValue('[data-field="badge_no"] input')
check(copyContact === '' && copyNote === '' && copyBadge === 'B-100', 'dupliquer : ni l’adresse unique ni la note no_copy, mais le reste', `contact=${copyContact} note=${copyNote} badge=${copyBadge}`)
await shot(page, '125-duplicate')

// ── Saisie rapide : créer le lieu qu'on ne trouve pas ───────────────────────────────
await page.fill('[data-field="subject"] input', `Copie ${stamp}`)
await page.fill('[data-field="place"] input', `Marseille ${stamp}`)
await page.click('[data-field="place"] [data-testid="link-create"]')
await page.waitForSelector('[data-testid="quick-entry"]')
const quickTitle = await page.inputValue('[data-testid="quick-entry"] [data-field="label"] input')
check(quickTitle === `Marseille ${stamp}`, 'saisie rapide : ce qui était tapé devient le libellé', quickTitle)
await shot(page, '126-quick-entry')
await page.click('[data-testid="quick-save"]')
await page.waitForSelector('[data-testid="quick-entry"]', { state: 'detached', timeout: 8000 })
const chosen = await page.inputValue('[data-field="place"] input')
check(chosen === `Marseille ${stamp}`, 'le lieu créé est aussitôt le choix du champ', chosen)
const marseille = await api('GET', `/list/Place?filters=${encodeURIComponent(JSON.stringify({ label: `Marseille ${stamp}` }))}`)
if (marseille.body.items[0]) made.push(['Place', marseille.body.items[0].name])
await page.fill('[data-field="contact"] input', `copie-${stamp}@exemple.fr`)
await page.click('[data-testid="save"]')
await page.waitForFunction(() => location.hash.includes('/app/Visit/') && !location.hash.endsWith('/new') && !location.hash.includes('copy='), null, { timeout: 10000 })
made.push(['Visit', decodeURIComponent(page.url().split('/app/Visit/')[1])])

// ── Nommage : saisi (Memo) et tiré d'un champ (Badge) ───────────────────────────────
await go('#/app/Memo/new', '[data-testid="new-name"]')
await page.click('[data-testid="save"]')
await page.waitForSelector('[data-testid="error-name"]')
ok('nom saisi : refusé tant qu’il est vide')
await page.fill('[data-testid="new-name"]', `memo-${stamp}`)
await page.fill('[data-field="body"] textarea', 'Un mémo nommé par son auteur')
await page.click('[data-testid="save"]')
await page.waitForFunction((n) => decodeURIComponent(location.hash).endsWith(`/app/Memo/${n}`), `memo-${stamp}`, { timeout: 10000 })
made.push(['Memo', `memo-${stamp}`])
ok('le mémo porte le nom choisi')
await go('#/app/Badge/new', '[data-field="code"] input')
await page.fill('[data-field="code"] input', `BDG-${stamp}`)
await page.click('[data-testid="save"]')
await page.waitForFunction((n) => decodeURIComponent(location.hash).endsWith(`/app/Badge/${n}`), `BDG-${stamp}`, { timeout: 10000 })
made.push(['Badge', `BDG-${stamp}`])
check(!(await page.$('[data-field="code"] input')), 'nommage par champ : le badge s’appelle comme son code, qui ne change plus')

// ── Soumis : seul le suivi reste modifiable ─────────────────────────────────────────
await go('#/app/Voucher/new', '[data-field="title"] input')
await page.fill('[data-field="title"] input', `Bon ${stamp}`)
await page.click('[data-testid="save"]')
await page.waitForSelector('[data-testid="submit"]:not([disabled])', { timeout: 10000 })
made.push(['Voucher', decodeURIComponent(page.url().split('/app/Voucher/')[1])])
await page.click('[data-testid="submit"]')
await page.click('.p-confirmdialog-accept-button')
await page.waitForSelector('[data-testid="form-frozen"]', { timeout: 10000 })
const frozenText = await page.textContent('[data-testid="form-frozen"]')
check(/champs prévus/.test(frozenText), `document soumis : « ${frozenText.trim()} »`)
check(!(await page.$('[data-field="title"] input')) && !!(await page.$('[data-field="follow_up"] input')), 'le titre est figé, le suivi reste saisissable')
await page.fill('[data-field="follow_up"] input', 'rappeler lundi')
await page.click('[data-testid="save"]')
await page.waitForSelector('[data-testid="status"]:has-text("Soumis")', { timeout: 10000 })
const voucher = await api('GET', `/doc/Voucher/${encodeURIComponent(made.at(-1)[1])}`)
check(voucher.body.docstatus === 1 && voucher.body.data.follow_up === 'rappeler lundi', 'le suivi est enregistré sur le bon soumis')
await shot(page, '127-voucher-submitted')

// ── Liste : barre latérale et actions en lot ─────────────────────────────────────────
await go('#/app/Place', '[data-testid="list-sidebar"]')
await page.waitForSelector('[data-testid^="sidebar-country-"]', { timeout: 8000 })
const sideText = await page.textContent('[data-testid="list-sidebar"]')
check(/France/.test(sideText) && /Suisse/.test(sideText), `barre latérale : ${sideText.replace(/\s+/g, ' ').trim().slice(0, 100)}`)
await shot(page, '128-list-sidebar')
await page.click('[data-testid="sidebar-country-Suisse"]')
await page.waitForFunction(() => document.querySelector('[data-testid="list-count"]')?.textContent.match(/\b1 sur 1\b|^\s*\d+ sur \d+/), null, { timeout: 8000 })
const filtered = await page.textContent('[data-testid="list-count"]')
ok(`un clic sur « Suisse » filtre la liste (${filtered.trim()})`)
await page.click('[data-testid="sidebar-country-Suisse"]') // un second clic retire le filtre
await page.waitForTimeout(600)
// Deux lieux cochés, puis « Modifier un champ ».
await page.fill('[data-testid="quick-search"]', stamp)
await page.waitForTimeout(900)
const rows = page.locator('tbody tr')
const n = await rows.count()
check(n >= 3, `la recherche garde les lieux de l’essai (${n})`)
await rows.nth(0).locator('.p-checkbox').click()
await rows.nth(1).locator('.p-checkbox').click()
await page.click('[data-testid="bulk-actions"]')
await page.click('.p-menu-item:has-text("Modifier un champ")')
await page.waitForSelector('[data-testid="bulk-edit-dialog"]')
await shot(page, '129-bulk-edit')
await page.click('[data-testid="bulk-field"]')
await page.click('[role="option"]:has-text("Ville")')
await page.fill('#bulk-value', 'Marne-la-Vallée')
await page.click('[data-testid="bulk-edit-apply"]')
await page.waitForSelector('[data-testid="bulk-edit-dialog"]', { state: 'detached', timeout: 8000 })
const cities = await api('GET', `/list/Place?filters=${encodeURIComponent(JSON.stringify({ city: 'Marne-la-Vallée' }))}`)
check(cities.body.total === 2, `action en lot : deux lieux modifiés (${cities.body.total})`)

// ── Documentation de l'API ───────────────────────────────────────────────────────────
await go('#/api/Visit', '[data-testid="route-create"]')
const docText = await page.textContent('[data-testid="route-create"]')
check(/POST/.test(docText) && /\/doc\/Visit/.test(docText), 'documentation : la route de création de Visit')
const fieldRow = await page.textContent('[data-testid="field-contact"]')
check(/unique/.test(fieldRow) && /email|Text/.test(fieldRow), 'documentation : le champ « contact » est dit unique')
await shot(page, '130-api-docs')

// ── Ménage ───────────────────────────────────────────────────────────────────────────
for (const [dt, name] of made.reverse()) {
  const doc = await api('GET', `/doc/${encodeURIComponent(dt)}/${encodeURIComponent(name)}`)
  if (doc.status !== 200) continue
  if (doc.body.docstatus === 1) {
    await api('POST', `/doc/${encodeURIComponent(dt)}/${encodeURIComponent(name)}/cancel`, { version: doc.body.version })
    continue // un document annulé reste (il est figé) ; seul un brouillon se supprime
  }
  await api('DELETE', `/doc/${encodeURIComponent(dt)}/${encodeURIComponent(name)}`)
}

const noise = problems.filter((p) => !/401|favicon|ResizeObserver|status of 4/.test(p))
noise.length ? fail(`problèmes dans la console : ${noise.slice(0, 5).join(' | ')}`) : ok('aucune erreur dans la console')
await browser.close()
