/** Petits outils du scénario de bout en bout : un vrai navigateur, un vrai serveur xcore, aucune simulation. */
import { chromium } from 'playwright-core'
import { mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

export function config() {
  const need = (k) => {
    const v = process.env[k]
    if (!v) throw new Error(`variable d'environnement ${k} requise (voir ui/e2e/README.md)`)
    return v
  }
  return {
    desk: need('DESK_URL'), // ex. http://127.0.0.1:8765/app/xcore-doctype/desk/
    api: need('DESK_API'), // ex. http://127.0.0.1:8765/app/xcore-doctype
    token: need('DESK_TOKEN'),
    tenant: need('DESK_TENANT'),
    shots: process.env.SHOTS_DIR ?? '',
  }
}

export async function launch() {
  const browser = await chromium.launch(process.env.CHROME ? { executablePath: process.env.CHROME } : {})
  const context = await browser.newContext({ locale: 'fr-FR', timezoneId: 'Pacific/Auckland', viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()
  // 30 s par attente (défaut de Playwright) : une étape en échec en coûte autant. `E2E_TIMEOUT=8000` (ms) fait remonter les échecs vite.
  if (process.env.E2E_TIMEOUT) page.setDefaultTimeout(Number(process.env.E2E_TIMEOUT))
  const problems = []
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') problems.push(`console.${m.type()}: ${m.text()}`)
  })
  if (process.env.E2E_DEBUG) {
    // Diagnostic : chaque réponse d'erreur de l'API, avec son corps (les 4xx du scénario sont attendus, on ne les compte pas).
    page.on('response', async (r) => {
      if (r.status() >= 400) console.log(`    [http ${r.status()}] ${r.request().method()} ${r.url()} ${(await r.text().catch(() => '')).slice(0, 200)}`)
    })
  }
  page.on('requestfailed', (r) => problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`))
  return { browser, page, problems }
}

export function reporter(shotsDir) {
  if (shotsDir) mkdirSync(shotsDir, { recursive: true })
  let n = 0
  const failures = []
  return {
    async step(name, fn, page) {
      try {
        await fn()
        console.log(`  ok   ${name}`)
      } catch (e) {
        failures.push(`${name}: ${e.message.split('\n')[0]}`)
        console.log(`  FAIL ${name}\n       ${e.message.split('\n').slice(0, 4).join('\n       ')}`)
        if (page && shotsDir) await page.screenshot({ path: join(shotsDir, `FAIL-${String(++n).padStart(2, '0')}.png`) }).catch(() => {})
      }
    },
    async shot(page, name) {
      if (shotsDir) await page.screenshot({ path: join(shotsDir, `${name}.png`) })
    },
    failures,
  }
}

export function assert(cond, msg) {
  if (!cond) throw new Error(msg)
}

/** Appel direct à l'API (hors interface), pour préparer ou perturber les données. */
export async function api(cfg, method, path, body) {
  const res = await fetch(`${cfg.api}${path}`, {
    method,
    headers: { Authorization: `Bearer ${cfg.token}`, 'X-Tenant-ID': cfg.tenant, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const text = await res.text()
  return { status: res.status, json: text ? JSON.parse(text) : null }
}

/** Un PDF d'UNE page A4, VALIDE (table des renvois exacte : pypdf le relit pour le poser en fond). */
export function pdfBytes() {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>',
  ]
  const stream = '0.18 0.24 0.31 rg 0 772 595 70 re f'
  objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`)
  let out = '%PDF-1.4\n'
  const offsets = []
  objects.forEach((body, i) => {
    offsets.push(out.length)
    out += `${i + 1} 0 obj\n${body}\nendobj\n`
  })
  const xref = out.length
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(out, 'latin1')
}

/** Ce PDF écrit dans le dossier temporaire, pour un `<input type=file>`. */
export function writePdf(name) {
  const path = join(tmpdir(), name)
  writeFileSync(path, pdfBytes())
  return path
}
