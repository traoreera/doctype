/** Petits outils du scénario de bout en bout : un vrai navigateur, un vrai serveur xcore, aucune simulation. */
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'
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
