// Captures pour juger l'allure du Desk (données de démonstration dans un tenant à part).
import { api, launch } from './helpers.mjs'
const cfg = { api: process.env.DESK_API, token: process.env.DESK_TOKEN, tenant: process.env.DESK_TENANT }
const out = process.env.SHOTS_DIR
const post = (p, d) => api(cfg, 'POST', p, { data: d })
const month = (n) => { const d = new Date(); d.setMonth(d.getMonth() - n); return d.toISOString().slice(0, 10) }
const log = (r, what) => { if (r.status >= 300) console.log('!', what, r.status, JSON.stringify(r.json).slice(0, 160)) ; return r.json }

const exists = (await api(cfg, 'GET', '/list/Invoice?limit=1')).json?.total > 0
if (!exists) {
const fr = log(await post('/doc/Country', { country_name: 'France' }), 'country')
const be = log(await post('/doc/Country', { country_name: 'Belgique' }), 'country')
const names = ['Atelier Dupont', 'Boulangerie Martin', 'Cabinet Leroy', 'Distri-Nord', 'Éditions Lumière', 'Ferme du Lac']
const custs = []
for (const [i, n] of names.entries()) custs.push(log(await post('/doc/Customer', { customer_name: n, type: i % 2 ? 'Particulier' : 'Entreprise', ...(i % 2 ? {} : { company_id: `SIREN${i}` }), country: (i % 3 ? fr : be).name }), 'cust').name)
const itemsList = []
for (const [n, u] of [['Vis M6', 'pcs'], ['Écrou M6', 'pcs'], ['Peinture blanche', 'l'], ['Câble 2,5 mm²', 'm']]) itemsList.push(log(await post('/doc/Item', { item_name: n, uom: u }), 'item').name)
const whs = []
for (const n of ['Entrepôt Paris', 'Entrepôt Lyon']) whs.push(log(await post('/doc/Warehouse', { warehouse_name: n }), 'wh').name)
for (const [i, it] of itemsList.entries()) {
  const se = log(await post('/doc/Stock Entry', { purpose: 'Réception', items: [{ item: it, qty: String(20 + i * 15), target_warehouse: whs[i % 2] }] }), 'se')
  log(await api(cfg, 'POST', `/doc/Stock Entry/${encodeURIComponent(se.name)}/submit`, { version: 1 }), 'submit')
}
log(await post('/doc/Stock Entry', { purpose: 'Sortie', items: [{ item: itemsList[0], qty: '5', source_warehouse: whs[0] }] }), 'draft')
const states = ['Impayée', 'Payée', 'Payée', 'En retard', 'Impayée', 'Payée']
for (let i = 0; i < 18; i++) {
  const posting = month(i % 6)
  const due = new Date(Date.parse(posting) + (10 + (i % 5) * 7) * 86400000).toISOString().slice(0, 10)
  log(await post('/doc/Invoice', { customer: custs[i % custs.length], posting_date: posting, due_date: due, payment: states[i % states.length], items: [{ item: itemsList[i % itemsList.length], qty: 1 + (i % 3), rate: 100 + i * 37 }] }), 'invoice')
}

}
const { browser, page, problems } = await launch()
await page.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), cfg.token)
await page.goto(process.env.DESK_URL)
const shot = async (name, hash, wait = 1500) => { await page.goto(process.env.DESK_URL + '#' + hash); await page.waitForTimeout(wait); await page.screenshot({ path: `${out}/${name}.png`, fullPage: false }) }
await page.waitForTimeout(1500)
await shot('01-stock', '/w/stock', 2500)
await shot('02-sales', '/w/sales', 2500)
await shot('03-invoice-list', '/app/Invoice?view=list')
await shot('04-invoice-report', '/app/Invoice?view=report')
await shot('05-invoice-dashboard', '/app/Invoice?view=dashboard', 2500)
await shot('06-invoice-kanban', '/app/Invoice?view=kanban', 2000)
await shot('07-invoice-calendar', '/app/Invoice?view=calendar', 2000)
await shot('08-invoice-gantt', '/app/Invoice?view=gantt', 2000)
console.log(problems.slice(0, 8).join('\n') || 'aucune erreur navigateur')
await browser.close()
