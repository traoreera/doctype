// Sème un module RH de démonstration (photos de profil comprises) dans un tenant à part, puis photographie le Desk pour en juger l'allure.
import { deflateSync, crc32 } from 'node:zlib'
import { api, launch } from './helpers.mjs'
const cfg = { api: process.env.DESK_API, token: process.env.DESK_TOKEN, tenant: process.env.DESK_TENANT }
const out = process.env.SHOTS_DIR
const post = (p, d) => api(cfg, 'POST', p, { data: d })
const log = (r, what) => { if (r.status >= 300) console.log('!', what, r.status, JSON.stringify(r.json).slice(0, 200)); return r.json }

/** Un PNG de 96×96 : un disque de couleur sur fond clair (un « avatar » reconnaissable sans dépendance). */
function png(rgb) {
  const N = 96, raw = Buffer.alloc(N * (N * 3 + 1))
  for (let y = 0; y < N; y++) {
    raw[y * (N * 3 + 1)] = 0
    for (let x = 0; x < N; x++) {
      const inside = (x - 48) ** 2 + (y - 40) ** 2 < 24 ** 2 || (y > 66 && (x - 48) ** 2 / 34 ** 2 + (y - 96) ** 2 / 34 ** 2 < 1)
      const c = inside ? rgb : [236, 238, 242]
      raw.set(c, y * (N * 3 + 1) + 1 + x * 3)
    }
  }
  const chunk = (t, d) => { const b = Buffer.concat([Buffer.from(t), d]); const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const c = Buffer.alloc(4); c.writeUInt32BE(crc32(b)); return Buffer.concat([len, b, c]) }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(N, 0); ihdr.writeUInt32BE(N, 4); ihdr[8] = 8; ihdr[9] = 2
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}
async function upload(doctype, field, buf, name, type) {
  const form = new FormData(); form.append('file', new Blob([buf], { type }), name)
  const r = await fetch(`${cfg.api}/upload/${encodeURIComponent(doctype)}/${field}`, { method: 'POST', headers: { Authorization: `Bearer ${cfg.token}`, 'X-Tenant-ID': cfg.tenant }, body: form })
  return log({ status: r.status, json: await r.json() }, 'upload')
}
const day = (n) => new Date(Date.now() + n * 86400000).toISOString().slice(0, 10)

const exists = (await api(cfg, 'GET', '/list/Employee?limit=1')).json?.total > 0
if (!exists) {
  const dep = {}
  dep.dir = log(await post('/doc/Department', { department_name: 'Direction', color: '#2a78d6' }), 'dep').name
  dep.tech = log(await post('/doc/Department', { department_name: 'Technique', parent_department: dep.dir, color: '#1baf7a' }), 'dep').name
  dep.ops = log(await post('/doc/Department', { department_name: 'Opérations', parent_department: dep.dir, color: '#eb6834' }), 'dep').name
  dep.dev = log(await post('/doc/Department', { department_name: 'Développement', parent_department: dep.tech }), 'dep').name
  dep.log = log(await post('/doc/Department', { department_name: 'Logistique', parent_department: dep.ops }), 'dep').name
  const people = [
    ['Awa Traoré', 'Directrice générale', dep.dir, 'Actif', [42, 120, 214], 5, 'EUR'],
    ['Karim Benali', 'Responsable technique', dep.tech, 'Actif', [27, 175, 122], 4],
    ['Léa Martin', 'Développeuse', dep.dev, 'Actif', [235, 104, 52], 5],
    ['Moussa Diallo', 'Développeur', dep.dev, 'En congé', [98, 80, 214], 3],
    ['Inès Lefèvre', 'Cheffe de projet', dep.tech, 'Actif', [232, 123, 164], 4],
    ['Paul Girard', 'Responsable opérations', dep.ops, 'Actif', [237, 161, 0], 4],
    ['Sara Haddad', 'Logisticienne', dep.log, 'Actif', [227, 73, 72], 3],
    ['Tom Nguyen', 'Magasinier', dep.log, 'Parti', [0, 131, 0], 2],
  ]
  const ids = []
  for (const [i, [name, job, d, status, rgb, rating]] of people.entries()) {
    const photo = (await upload('Employee', 'photo', png(rgb), `${name}.png`, 'image/png'))?.id
    const contract = i < 4 ? (await upload('Employee', 'contract', Buffer.from('%PDF-1.4 contrat'), `contrat-${name.split(' ')[1]}.pdf`, 'application/pdf'))?.id : undefined
    const emp = log(await post('/doc/Employee', {
      full_name: name, job_title: job, department: d, status, photo, ...(contract ? { contract } : {}), rating,
      email: `${name.split(' ')[0].toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')}@exemple.fr`, phone: `+33 6 12 34 56 ${10 + i}`,
      hire_date: day(-i * 55 - 20), salary: String(32000 + i * 4100), team_color: `#${(rgb[0] << 16 | rgb[1] << 8 | rgb[2]).toString(16).padStart(6, '0')}`,
      bio: 'Présentation de la personne.\nDeuxième ligne.', ...(i ? { manager: ids[0] } : {}),
    }), 'emp')
    ids.push(emp.name)
  }
  const types = ['Congé payé', 'Maladie', 'Formation', 'Congé payé', 'Sans solde']
  for (let i = 0; i < 7; i++) {
    const lr = log(await post('/doc/Leave Request', { employee: ids[1 + (i % 6)], leave_type: types[i % 5], from_date: day(i * 4 - 6), to_date: day(i * 4 - 3), reason: 'Demande' }), 'leave')
    // Demande de congé suit un workflow : on passe par ses transitions (soumettre, puis approuver), jamais par `/submit`.
    if (i % 2 === 0) {
      const sent = log(await api(cfg, 'POST', `/doc/Leave Request/${encodeURIComponent(lr.name)}/transition`, { action: 'Soumettre', version: 1 }), 'soumettre')
      log(await api(cfg, 'POST', `/doc/Leave Request/${encodeURIComponent(lr.name)}/transition`, { action: 'Approuver', version: sent.json?.version ?? 2 }), 'approuver')
    }
  }
  const s = (await api(cfg, 'GET', '/doc/HR Settings/HR Settings')).json
  log(await api(cfg, 'PUT', '/doc/HR Settings/HR Settings', { version: s.version, data: { ...s.data, hr_email: 'rh@exemple.fr', annual_leave_days: 27 } }), 'settings')
}
const { browser, page, problems } = await launch()
await page.addInitScript((t) => sessionStorage.setItem('xdt.desk.token', t), cfg.token)
await page.goto(process.env.DESK_URL)
await page.waitForTimeout(1500)
const shot = async (name, hash, wait = 1800) => { await page.goto(process.env.DESK_URL + '#' + hash); await page.waitForTimeout(wait); await page.screenshot({ path: `${out}/${name}.png` }) }
await shot('hr-01-workspace', '/w/hr', 3000)
await shot('hr-02-employees', '/app/Employee?view=list')
await shot('hr-03-kanban', '/app/Employee?view=kanban')
await page.goto(process.env.DESK_URL + '#/app/Employee?view=list'); await page.waitForTimeout(1500)
await page.locator('[data-testid^="open-"]').first().click(); await page.waitForTimeout(2000)
await page.screenshot({ path: `${out}/hr-04-form.png`, fullPage: true })
await shot('hr-05-tree', '/app/Department?view=tree')
await shot('hr-06-settings', '/settings')
await shot('hr-07-hr-settings', '/app/HR Settings/HR Settings')
await shot('hr-08-leaves', '/app/Leave Request?view=calendar')
console.log(problems.slice(0, 8).join('\n') || 'aucune erreur navigateur')
await browser.close()
