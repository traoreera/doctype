// La vitrine (plugin `lab`) : chaque type de champ de la phase H, de la saisie à la relecture, dans un vrai navigateur contre le vrai xauth.
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD (celles de `.env`), SHOTS_DIR ; LOGIN (facultatif : nom d'utilisateur).
import { launch } from './helpers.mjs'

const fail = (m) => { console.log('✗', m); process.exitCode = 1 }
const ok = (m) => console.log('✓', m)
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

// Deux étiquettes pour la sélection multiple.
const tagA = await api('POST', '/doc/Tag', { data: { label: 'Urgent', color: '#ef4444' } })
const tagB = await api('POST', '/doc/Tag', { data: { label: 'À revoir', color: '#3b82f6' } })
tagA.status === 201 && tagB.status === 201 ? ok('deux étiquettes créées') : fail(`étiquettes : ${tagA.status} ${tagB.status}`)

await page.goto(process.env.DESK_URL + '#/app/Specimen/new')
await page.waitForSelector('[data-field="title"] input')
await page.fill('[data-field="title"] input', 'Essai du ' + new Date().toISOString().slice(0, 10))
await page.fill('[data-field="quantity"] input', '42')
await page.fill('[data-field="progress"] input', '37,5')
await page.fill('[data-field="opens_at"] input', '08:30')
await page.fill('[data-field="work_time"] input', '1h30')
await page.fill('[data-field="snippet"] textarea', 'def bonjour():\n    return "salut"\n')
await page.fill('[data-field="config"] textarea', '{"mode": "test", "seuils": [1, 2, 3]}')
await page.click('[data-field="notes"] .rich-content')
await page.keyboard.type('Bonjour ')
await page.click('[data-testid="rich-bold"]')
await page.keyboard.type('en gras')
await page.fill('[data-field="api_secret"] input', 'sk-live-ne-jamais-afficher')
await page.fill('[data-testid="tag-add"] input, [data-field="tags"] input', 'Urgent')
await page.waitForSelector('[role="option"]:has-text("Urgent")')
await page.click('[role="option"]:has-text("Urgent")')
await page.waitForSelector('[data-testid^="tag-"]')
await shot(page, '110-specimen-new')
ok('saisie : entier, pourcentage, heure, durée « 1h30 », code, JSON, texte enrichi, secret, étiquette')
await page.click('[data-testid="save"]')
await page.waitForFunction(() => location.hash.includes('/app/Specimen/') && !location.hash.endsWith('/new'), null, { timeout: 10000 })
await page.waitForSelector('[data-testid="secret-set"]', { timeout: 10000 })
ok('enregistré ; le secret revient masqué (aucun champ de saisie)')
const shown = await page.textContent('[data-field="work_time"] input').catch(() => null)
const duration = await page.inputValue('[data-field="work_time"] input')
duration === '1 h 30 min' ? ok(`durée relue : « ${duration} »`) : fail(`durée : ${duration}`)
const notes = await page.innerHTML('[data-field="notes"] .rich-content')
notes.includes('<strong>en gras</strong>') ? ok('texte enrichi relu avec son gras') : fail(`notes : ${notes}`)
const bodyText = await page.content()
bodyText.includes('sk-live-ne-jamais-afficher') ? fail('LE SECRET EST DANS LA PAGE') : ok('le secret n’apparaît nulle part dans la page')
await shot(page, '111-specimen-saved')

// Le serveur : le secret est chiffré, l'API ne le rend jamais.
const name = decodeURIComponent(page.url().split('/Specimen/')[1])
const got = await api('GET', `/doc/Specimen/${encodeURIComponent(name)}`)
got.body.data.api_secret === '********' && got.body.data.work_time === 5400 && got.body.data.opens_at === '08:30:00' && got.body.data.quantity === 42 && got.body.data.progress === 37.5
  ? ok('API : secret masqué, durée 5400 s, heure 08:30:00, entier 42, pourcentage 37,5') : fail(`API : ${JSON.stringify(got.body.data)}`)
JSON.stringify(got.body.data.config) === '{"mode":"test","seuils":[1,2,3]}' ? ok('JSON relu comme un objet') : fail('JSON')

// Un HTML qui s'exécuterait est refusé par le serveur (422), jamais « nettoyé ».
const evil = await api('PUT', `/doc/Specimen/${encodeURIComponent(name)}`, { version: got.body.version, data: { ...got.body.data, notes: '<p onclick="alert(1)">x</p><script>alert(2)</script>' } })
evil.status === 422 ? ok('un HTML hors liste est refusé (422)') : fail(`HTML : ${evil.status}`)

// La liste.
await page.goto(process.env.DESK_URL + '#/app/Specimen')
await page.waitForSelector('[data-testid^="open-"]', { timeout: 10000 })
const row = await page.textContent('.p-datatable-tbody tr')
;/42/.test(row) && /37,5 %/.test(row) && /08:30/.test(row) && /1 h 30 min/.test(row) ? ok(`liste : ${row.replace(/\s+/g, ' ').trim().slice(0, 100)}`) : fail(`liste : ${row}`)
await shot(page, '112-specimen-list')

// Filtres sur les nouveaux types.
const filtered = await api('GET', `/list/Specimen?filters=${encodeURIComponent(JSON.stringify({ work_time: ['>=', 3600], opens_at: ['<', '09:00:00'], quantity: ['in', [42, 7]] }))}`)
filtered.body.total >= 1 ? ok('filtres : durée, heure et entier') : fail(`filtres : ${filtered.body.total}`)
const bad = await api('GET', `/list/Specimen?filters=${encodeURIComponent(JSON.stringify({ api_secret: 'x' }))}`)
bad.status === 422 ? ok('un secret ne se filtre pas (422)') : fail(`filtre secret : ${bad.status}`)

const stray = problems.filter((p) => !/401|403|404|422|429/.test(p))
stray.length ? fail(`erreurs navigateur :\n${stray.slice(0, 8).join('\n')}`) : ok('aucune erreur navigateur')
await browser.close()
