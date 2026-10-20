// Audit d'expérience utilisateur : les écrans principaux en clair / sombre, ordinateur / téléphone, avec des mesures (contraste, cibles tactiles, débordement, focus visible).
// Variables : DESK_URL, ADMIN_EMAIL, ADMIN_PASSWORD, SHOTS_DIR ; LOGIN (facultatif).
import { chromium } from 'playwright-core'

const shots = process.env.SHOTS_DIR
const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {})
const problems = []

async function session(viewport, scheme) {
  const context = await browser.newContext({ locale: 'fr-FR', timezoneId: 'Europe/Paris', viewport, colorScheme: scheme })
  const page = await context.newPage()
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('console', (m) => (m.type() === 'error' ? problems.push(`console.error: ${m.text().slice(0, 160)}`) : null))
  return { context, page }
}

async function login(page) {
  await page.goto(process.env.DESK_URL)
  await page.fill('#login-email', process.env.LOGIN ?? process.env.ADMIN_EMAIL)
  await page.fill('#login-password', process.env.ADMIN_PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForSelector('.desk-sidebar, #login-tenant, .desk-topbar, header', { timeout: 15000 })
  if (await page.$('#login-tenant')) {
    await page.click('#login-tenant')
    await page.click('[role="option"]:has-text("Default")')
    await page.click('button[type="submit"]')
  }
  await page.waitForSelector('[data-testid="search"], .desk-page, .desk-sidebar, main', { timeout: 15000 })
}

// ── Mesures faites DANS la page ─────────────────────────────────────────────────────
const measure = () => {
  const out = { contrast: [], small: [], overflow: false, noName: [], noLabel: [] }
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map((x) => parseFloat(x)); return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 } }
  const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b) }
  const bgOf = (el) => {
    let layers = []
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor)
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break }
    }
    let base = { r: 255, g: 255, b: 255 }
    if (!layers.length || layers[layers.length - 1].a < 1) base = parse(getComputedStyle(document.body).backgroundColor) ?? base
    for (const c of layers.reverse()) base = { r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a) }
    return base
  }
  const seen = new Set()
  for (const el of document.querySelectorAll('body *')) {
    if (!el.childNodes.length) continue
    const own = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(' ')
    if (!own) continue
    const r = el.getBoundingClientRect()
    const cs = getComputedStyle(el)
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) continue
    const fg = parse(cs.color); if (!fg) continue
    const bg = bgOf(el)
    const a = fg.a
    const mixed = { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a) }
    const l1 = lum(mixed), l2 = lum(bg)
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
    const size = parseFloat(cs.fontSize); const bold = parseInt(cs.fontWeight, 10) >= 700
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5
    const key = `${el.tagName}.${el.className}|${own.slice(0, 30)}`
    if (ratio < need && !seen.has(key)) { seen.add(key); out.contrast.push({ text: own.slice(0, 40), ratio: Math.round(ratio * 100) / 100, need, size, cls: String(el.className).slice(0, 40) }) }
  }
  for (const el of document.querySelectorAll('button, a[href], [role="button"], input:not([type="hidden"]), select, [role="checkbox"], .p-checkbox')) {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el)
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden') continue
    if (el.classList.contains('visually-hidden') || el.classList.contains('cell-link')) continue // une case masquée n'est pas une cible ; un lien dans du texte est exempté (WCAG 2.5.8)
    const grow = el.classList.contains('p-checkbox') || el.classList.contains('p-checkbox-input') ? 8 : 0 // la zone sensible d'une case déborde de 4 px de chaque côté (::after)
    if (r.width + grow < 24 || r.height + grow < 24) out.small.push({ el: `${el.tagName.toLowerCase()}${el.className ? '.' + String(el.className).split(' ')[0] : ''}`, w: Math.round(r.width), h: Math.round(r.height), label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24) })
    const named = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.getAttribute('title') || (el.textContent || '').trim() || el.getAttribute('placeholder')
    if (!named && (el.tagName === 'BUTTON' || el.tagName === 'A') ) out.noName.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]}`)
    if (el.tagName === 'INPUT' && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby') && !(el.id && document.querySelector(`label[for="${el.id}"]`)) && !el.closest('label') && !el.getAttribute('placeholder')) out.noLabel.push(`${el.type}#${el.id || el.name || ''}`)
  }
  out.overflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
  return out
}

async function snap(page, name, { wait = 600 } = {}) {
  await page.waitForTimeout(wait)
  await page.screenshot({ path: `${shots}/${name}.png`, fullPage: false })
  const m = await page.evaluate(measure)
  const bad = m.contrast.length + m.small.length + (m.overflow ? 1 : 0) + m.noName.length + m.noLabel.length
  console.log(`${bad ? '△' : '✓'} ${name}${m.overflow ? '  [débordement horizontal]' : ''}`)
  for (const c of m.contrast.slice(0, 6)) console.log(`    contraste ${c.ratio} < ${c.need} : « ${c.text} » (${c.size}px, .${c.cls})`)
  for (const s of m.small.slice(0, 5)) console.log(`    cible ${s.w}×${s.h} : ${s.el} « ${s.label} »`)
  for (const n of m.noName.slice(0, 4)) console.log(`    sans nom accessible : ${n}`)
  for (const n of m.noLabel.slice(0, 4)) console.log(`    champ sans étiquette : ${n}`)
}

const first = await session({ width: 1280, height: 800 }, 'light')
await login(first.page)
const token = await first.page.evaluate(() => sessionStorage.getItem('xdt.desk.token'))
const tenant = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).tenant_id
const api = (method, path, body) => first.page.evaluate(async ([m, p, b, tok, ten]) => {
  const r = await fetch(`/app/xcore-doctype${p}`, { method: m, headers: { 'content-type': 'application/json', authorization: `Bearer ${tok}`, 'x-tenant-id': ten }, body: b ? JSON.stringify(b) : undefined })
  return { status: r.status, body: r.status === 204 ? null : await r.json() }
}, [method, path, body, token, tenant])

// Des données pour que les écrans ne soient pas vides.
const stamp = Date.now().toString(36)
const made = []
const mk = async (dt, data, name) => { const r = await api('POST', `/doc/${dt}`, name ? { data, name } : { data }); if (r.status === 201) made.push([dt, r.body.name]); return r.body }
const places = []
for (const [label, city, country] of [['Siège', 'Paris', 'France'], ['Entrepôt Nord', 'Lille', 'France'], ['Agence', 'Genève', 'Suisse'], ['Dépôt', 'Bruxelles', 'Belgique'], ['Atelier', 'Lyon', 'France']]) places.push(await mk('Place', { label: `${label} ${stamp}`, city, country }))
for (let i = 0; i < 6; i++) await mk('Visit', { subject: `Visite de contrôle n°${i + 1}`, place: places[i % places.length].name, contact: `v${i}-${stamp}@exemple.fr` })
const voucher = await mk('Voucher', { title: `Bon d'achat ${stamp}`, amount: '1250.50' })

const pages = (target) => ({
  login: null,
  home: '#/',
  workspace: '#/w/lab',
  list: '#/app/Visit',
  form: `#/app/Place/${encodeURIComponent(places[0].name)}`,
  newform: '#/app/Visit/new',
  voucher: `#/app/Voucher/${encodeURIComponent(voucher.name)}`,
  tasks: '#/tasks',
  settings: '#/settings',
  api: '#/api/Visit',
})

async function tour(page, tag) {
  const urls = pages()
  for (const [name, hash] of Object.entries(urls)) {
    if (!hash) continue
    await page.goto(process.env.DESK_URL + hash)
    const leave = await page.waitForSelector('.p-confirmdialog-accept-button', { timeout: 500 }).catch(() => null)
    if (leave) await leave.click()
    await snap(page, `${tag}-${name}`, { wait: 1200 })
  }
}

console.log('── Ordinateur, clair')
await tour(first.page, 'd-clair')
await first.page.goto(process.env.DESK_URL + '#/')
await first.page.keyboard.press('Control+g')
await snap(first.page, 'd-clair-palette')
await first.page.keyboard.press('Escape')
await first.page.goto(process.env.DESK_URL + '#/app/Visit')
await first.page.waitForSelector('tbody tr')
await first.page.locator('tbody tr').nth(0).locator('.p-checkbox').click()
await first.page.locator('tbody tr').nth(1).locator('.p-checkbox').click()
await snap(first.page, 'd-clair-list-selection')
await first.page.click('[data-testid="toggle-filters"]').catch(() => {})
await snap(first.page, 'd-clair-list-filtres')

console.log('── Ordinateur, sombre')
await first.page.emulateMedia({ colorScheme: 'dark' })
await first.page.evaluate(() => localStorage.setItem('xdt.desk.theme', 'dark')).catch(() => {})
await first.page.reload()
await first.page.waitForTimeout(800)
const darkPages = pages()
for (const name of ['home', 'list', 'form', 'newform', 'voucher']) {
  await first.page.goto(process.env.DESK_URL + darkPages[name])
  const leave = await first.page.waitForSelector('.p-confirmdialog-accept-button', { timeout: 500 }).catch(() => null)
  if (leave) await leave.click()
  await snap(first.page, `d-sombre-${name}`, { wait: 1200 })
}
await first.page.evaluate(() => localStorage.removeItem('xdt.desk.theme')).catch(() => {})

console.log('── Téléphone, clair')
const phone = await session({ width: 390, height: 844 }, 'light')
await login(phone.page)
const mobile = pages()
for (const name of ['home', 'list', 'form', 'newform', 'voucher', 'settings']) {
  await phone.page.goto(process.env.DESK_URL + mobile[name])
  const leave = await phone.page.waitForSelector('.p-confirmdialog-accept-button', { timeout: 500 }).catch(() => null)
  if (leave) await leave.click()
  await snap(phone.page, `t-clair-${name}`, { wait: 1200 })
}
await phone.page.goto(process.env.DESK_URL + '#/')
await phone.page.click('[aria-label*="enu"], .desk-menu-button, button[aria-label]').catch(() => {})
await snap(phone.page, 't-clair-tiroir')

// ── Login (déconnecté) ────────────────────────────────────────────────────────────
const anon = await session({ width: 1280, height: 800 }, 'light')
await anon.page.goto(process.env.DESK_URL)
await anon.page.waitForSelector('#login-email')
console.log('── Connexion')
await snap(anon.page, 'd-clair-login')

// ── Ménage ───────────────────────────────────────────────────────────────────────
for (const [dt, name] of made.reverse()) {
  const d = await api('GET', `/doc/${encodeURIComponent(dt)}/${encodeURIComponent(name)}`)
  if (d.status !== 200) continue
  if (d.body.docstatus === 1) { await api('POST', `/doc/${encodeURIComponent(dt)}/${encodeURIComponent(name)}/cancel`, { version: d.body.version }); continue }
  await api('DELETE', `/doc/${encodeURIComponent(dt)}/${encodeURIComponent(name)}`)
}
console.log(problems.length ? `console : ${[...new Set(problems)].slice(0, 6).join(' | ')}` : '✓ console propre')
await browser.close()
