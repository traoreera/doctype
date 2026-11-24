/**
 * Test de fumée de l'application de BUREAU (traoreera/doctype#73) : la VRAIE application Tauri, pilotée par WebDriver, contre un
 * serveur de contrat lancé ici (pas xcore : ce test prouve le portage — transport natif, coffre, fenêtres, liens — pas le moteur).
 *
 *   pnpm tauri build --debug --no-bundle --config src-tauri/tauri.smoke.conf.json   # l'application, avec la poignée de test
 *   xvfb-run -a dbus-run-session -- pnpm desktop:smoke                              # Linux sans écran ; sinon : pnpm desktop:smoke
 *
 * Ce qu'il vérifie est listé à la fin de l'exécution, une ligne par point ; ce qu'il NE vérifie pas est dit dans docs/desktop.md
 * (le dialogue natif d'enregistrement, l'inscription du schéma auprès du système, macOS et Windows).
 *
 * Linux : il parle à `WebKitWebDriver` (paquet `webkit2gtk-driver`) ; ailleurs, à `tauri-driver` s'il est installé. Sans l'application
 * construite, sans pilote ou sans écran, il est IGNORÉ avec sa raison (code 0) — `SMOKE_STRICT=1` en fait un échec.
 */
import { spawn, spawnSync } from 'node:child_process'
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const BINARY = process.env.DESK_BINARY ?? resolve(HERE, '../src-tauri/target/debug', process.platform === 'win32' ? 'xcore-doctype-desk.exe' : 'xcore-doctype-desk')
const PORT = 8000 // celui de `src-tauri/desk.json` et de la portée réseau `capabilities/server.json`
const WD_PORT = Number(process.env.WD_PORT ?? 4477)
const SERVER = `http://127.0.0.1:${PORT}`

function skip(reason) {
  console.log(`IGNORÉ : ${reason}`)
  process.exit(process.env.SMOKE_STRICT ? 1 : 0)
}
const which = (cmd) => spawnSync(process.platform === 'win32' ? 'where' : 'which', [cmd], { encoding: 'utf8' }).stdout.trim().split('\n')[0] || null

if (!existsSync(BINARY)) skip(`application non construite (${BINARY}) — pnpm tauri build --debug --no-bundle --config src-tauri/tauri.smoke.conf.json`)
if (process.platform === 'darwin') skip("macOS n'a pas de pilote WebDriver pour WKWebView : à vérifier à la main (docs/desktop.md)")
if (process.platform === 'linux' && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) skip('aucun écran — xvfb-run -a dbus-run-session -- pnpm desktop:smoke')
const tauriDriver = which('tauri-driver')
const webkitDriver = process.platform === 'linux' ? which('WebKitWebDriver') : null
if (!tauriDriver && !webkitDriver) skip('aucun pilote WebDriver (Linux : paquet webkit2gtk-driver ; Windows : cargo install tauri-driver + msedgedriver)')

// ── Le serveur de contrat ───────────────────────────────────
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
const seen = [] // chaque requête reçue : { method, path, headers, body }
const refreshUsed = []
const live = new Set()
let issued = 0
/** `ttl` court : le Desk renouvelle une minute avant l'échéance, donc presque aussitôt. */
function issue(ttl = 65) {
  issued += 1
  const refresh = `r${issued}`
  live.add(refresh)
  return { access_token: `${b64({ alg: 'none' })}.${b64({ sub: 'u1', email: 'alice@acme.test', tenant_id: 't1', exp: Math.floor(Date.now() / 1000) + ttl, n: issued })}.sig`, refresh_token: refresh, tenant_id: 't1' }
}
let oauthNonce = null
const PDF = Buffer.concat([Buffer.from('%PDF-1.7\n'), Buffer.from([0, 255, 254, 1, 2, 3]), Buffer.from('\n%%EOF')])

const server = createServer((req, res) => {
  const chunks = []
  req.on('data', (c) => chunks.push(c))
  req.on('end', () => {
    const body = Buffer.concat(chunks)
    const url = new URL(req.url, SERVER)
    seen.push({ method: req.method, path: url.pathname, query: url.searchParams, headers: req.headers, body })
    // AUCUN en-tête CORS, exprès : un `fetch` de la WebView (origine tauri://localhost) échouerait sur chacune de ces réponses.
    const json = (status, value) => {
      res.writeHead(status, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify(value))
    }
    const parsed = () => {
      try {
        return JSON.parse(body.toString('utf8') || '{}')
      } catch {
        return {}
      }
    }
    if (url.pathname === '/app/xcore-doctype/desk/config.json') return json(200, { api_base: '/app/xcore-doctype', login_url: '/app/auth/login', extensions: [], realtime: true, developer: false, automations: false })
    if (url.pathname === '/app/auth/login' && req.method === 'POST') {
      if (parsed().password !== 'bon') return json(401, { detail: 'bad credentials' })
      // Comme xauth (`vendor/auth/src/routes/auth.py`) : le jeton d'accès est AUSSI posé en cookie. L'application ne doit ni le garder ni le renvoyer.
      const tokens = issue()
      res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': `access_token=${tokens.access_token}; HttpOnly; SameSite=Strict; Max-Age=1800; Path=/` })
      return res.end(JSON.stringify(tokens))
    }
    if (url.pathname === '/app/auth/refresh') {
      const used = parsed().refresh_token
      refreshUsed.push(used)
      return live.delete(used) ? json(200, issue(3600)) : json(401, { detail: 'refresh token already used' })
    }
    if (url.pathname === '/app/auth/logout') return json(200, {})
    if (url.pathname === '/app/auth/oauth/providers') return json(200, { providers: ['github'] })
    if (url.pathname === '/app/auth/oauth/github/authorize') {
      oauthNonce = url.searchParams.get('app_state')
      return json(200, { auth_url: `https://idp.exemple.test/authorize?state=srv&redirect=${encodeURIComponent(url.searchParams.get('redirect') ?? '')}` })
    }
    if (url.pathname === '/smoke/echo') return json(200, { method: req.method, authorization: req.headers.authorization ?? null, tenant: req.headers['x-tenant-id'] ?? null, origin: req.headers.origin ?? null, cookie: req.headers.cookie ?? null, body: parsed() })
    if (url.pathname === '/smoke/refused') return json(409, { detail: { code: 'document_locked', message: 'Document figé' } })
    if (url.pathname === '/smoke/doc.pdf') {
      res.writeHead(200, { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="FA-2026-00001.pdf"' })
      return res.end(PDF)
    }
    if (url.pathname === '/smoke/upload') {
      const type = req.headers['content-type'] ?? ''
      const boundary = /boundary=(.+)$/.exec(type)?.[1]
      const text = body.toString('latin1')
      return json(201, { multipart: type.startsWith('multipart/form-data'), boundaryInBody: !!boundary && text.includes(`--${boundary}`), filename: /filename="([^"]+)"/.exec(text)?.[1] ?? null, hasContent: text.includes('contenu du fichier'), size: body.length })
    }
    if (url.pathname === '/smoke/stream' || url.pathname === '/app/xpulse/stream') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
      let n = 0
      const tick = setInterval(() => {
        n += 1
        res.write(`event: tick\ndata: {"n":${n}}\n\n`)
        if (n === 3 && url.pathname === '/smoke/stream') {
          clearInterval(tick)
          res.end()
        }
      }, 300)
      res.on('close', () => clearInterval(tick)) // `res`, pas `req` : la requête est « close » dès qu'elle est lue
      return
    }
    if (url.pathname === '/smoke/slow') return void setTimeout(() => json(200, {}), 5000)
    return json(404, { detail: { code: 'not_found', message: 'inconnu du serveur de fumée' } })
  })
})
await new Promise((ok, ko) => server.once('error', ko).listen(PORT, '127.0.0.1', ok)).catch((e) => skip(`le port ${PORT} est pris (${e.code}) : c'est celui de desk.json et de la portée réseau de l'application`))

// ── Le système autour de l'application : un `xdg-open` qui NOTE ce qu'on lui demande d'ouvrir ──
const scratch = mkdtempSync(join(tmpdir(), 'desk-smoke-'))
const opened = join(scratch, 'opened.log')
writeFileSync(opened, '')
if (process.platform !== 'win32') {
  writeFileSync(join(scratch, 'xdg-open'), `#!/bin/sh\necho "$1" >> "${opened}"\n`)
  chmodSync(join(scratch, 'xdg-open'), 0o755)
}
const env = {
  ...process.env,
  PATH: `${scratch}${process.platform === 'win32' ? ';' : ':'}${process.env.PATH}`,
  XDG_DATA_HOME: join(scratch, 'data'),
  XDG_CONFIG_HOME: join(scratch, 'config'),
  XDG_CACHE_HOME: join(scratch, 'cache'),
  TAURI_AUTOMATION: 'true',
  TAURI_WEBVIEW_AUTOMATION: 'true',
}
delete env.XDESK_CONFIG

// ── WebDriver ───────────────────────────────────────────────
const driver = tauriDriver ? spawn(tauriDriver, ['--port', String(WD_PORT)], { env, stdio: 'pipe' }) : spawn(webkitDriver, [`--port=${WD_PORT}`, '--host=127.0.0.1'], { env, stdio: 'pipe' })
let driverLog = ''
driver.stdout.on('data', (d) => (driverLog += d))
driver.stderr.on('data', (d) => (driverLog += d))

async function wd(method, path, body) {
  const res = await fetch(`http://127.0.0.1:${WD_PORT}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const value = (await res.json()).value
  if (value && typeof value === 'object' && value.error) throw new Error(`WebDriver ${method} ${path} : ${value.error} — ${value.message}`)
  return value
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function until(what, probe, ms = 15000) {
  const end = Date.now() + ms
  let last
  for (;;) {
    try {
      last = await probe()
      if (last) return last
    } catch (e) {
      last = e
    }
    if (Date.now() > end) throw new Error(`délai dépassé : ${what} (dernier état : ${last instanceof Error ? last.message : JSON.stringify(last)})`)
    await sleep(150)
  }
}

const results = []
let failed = false
async function check(name, fn) {
  try {
    const note = await fn()
    results.push(`  ✓ ${name}${note ? ` — ${note}` : ''}`)
  } catch (e) {
    failed = true
    results.push(`  ✗ ${name}\n      ${e.message}`)
  }
}
const assert = (ok, message) => {
  if (!ok) throw new Error(message)
}

let sid = null
try {
  await until('le pilote WebDriver répond', () => fetch(`http://127.0.0.1:${WD_PORT}/status`).then((r) => r.ok), 10000)
  const capabilities = tauriDriver ? { 'tauri:options': { application: BINARY } } : { 'webkitgtk:browserOptions': { binary: BINARY, args: [] } }
  sid = (await wd('POST', '/session', { capabilities: { alwaysMatch: capabilities } })).sessionId
  const S = `/session/${sid}`
  await wd('POST', `${S}/timeouts`, { script: 20000 })
  /** Exécute une fonction ASYNCHRONE dans la page ; une exception revient avec son nom (`AbortError`, `TransportRefusal`…). */
  const page = async (fn, ...args) => {
    const out = await wd('POST', `${S}/execute/async`, {
      script: `const done = arguments[arguments.length - 1]; Promise.resolve().then(() => (${fn})(...Array.from(arguments).slice(0, -1))).then((v) => done({ ok: v === undefined ? null : v }), (e) => done({ error: (e && e.name) + ': ' + (e && e.message || e) }))`,
      args,
    })
    if (out.error) throw new Error(out.error)
    return out.ok
  }
  const find = async (css) => Object.values(await wd('POST', `${S}/element`, { using: 'css selector', value: css }))[0]
  const type = async (css, text) => wd('POST', `${S}/element/${await find(css)}/value`, { text })
  const click = async (css) => wd('POST', `${S}/element/${await find(css)}/click`, {})
  const hash = () => page(() => location.hash)

  await check("l'application démarre le Desk depuis son propre paquet, avec la configuration LUE chez le serveur", async () => {
    await until("l'écran de connexion", async () => (await hash()).startsWith('#/login'))
    const href = await page(() => location.href)
    assert(/^(tauri:\/\/localhost|https?:\/\/tauri\.localhost)(\/|#|$)/.test(href), `la page n'est pas celle de l'application : ${href}`)
    assert(seen.some((r) => r.path === '/app/xcore-doctype/desk/config.json'), "config.json n'a pas été demandé au serveur")
    await find('#login-email')
    return href.replace(/#.*/, '')
  })

  await check('le transport est NATIF : le serveur répond sans CORS, et la page a bien le transport de bureau', async () => {
    const out = await page(async (url) => {
      const { transport } = window.__DESK_SMOKE__
      const viaDesk = await transport.request({ method: 'POST', url, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t', 'X-Tenant-ID': 'acme' }, body: { a: 1 } })
      const viaWebView = await fetch(url, { method: 'POST', body: '{}' }).then(() => 'réponse lue', (e) => `refusé (${e.name})`)
      return { viaDesk, viaWebView }
    }, `${SERVER}/smoke/echo`)
    assert(out.viaDesk.status === 200 && out.viaDesk.json.body.a === 1, `réponse inattendue : ${JSON.stringify(out.viaDesk)}`)
    assert(out.viaDesk.json.authorization === 'Bearer t' && out.viaDesk.json.tenant === 'acme', "les en-têtes n'arrivent pas au serveur")
    assert(out.viaWebView.startsWith('refusé'), `le fetch de la WebView aurait dû être refusé (CSP/CORS) : ${out.viaWebView}`)
    return `fetch de la WebView : ${out.viaWebView}`
  })

  await check('JSON : une réponse d’erreur revient avec son statut et son corps', async () => {
    const out = await page((url) => window.__DESK_SMOKE__.transport.request({ method: 'DELETE', url, headers: {} }), `${SERVER}/smoke/refused`)
    assert(out.status === 409 && out.json.detail.code === 'document_locked', JSON.stringify(out))
  })

  await check('PDF : les octets, le type et le nom proposé arrivent intacts', async () => {
    const out = await page(async (url) => {
      const res = await window.__DESK_SMOKE__.transport.request({ method: 'GET', url, headers: {}, binary: true })
      return { status: res.status, bytes: Array.from(res.bytes ?? []), type: res.contentType, disposition: res.disposition }
    }, `${SERVER}/smoke/doc.pdf`)
    assert(out.status === 200 && Buffer.from(out.bytes).equals(PDF), `octets altérés (${out.bytes.length} reçus, ${PDF.length} attendus)`)
    assert(out.type === 'application/pdf' && out.disposition.includes('FA-2026-00001.pdf'), JSON.stringify(out))
    return `${out.bytes.length} octets`
  })

  await check('téléversement : un formulaire multipart arrive avec sa frontière et son fichier', async () => {
    const out = await page(async (url) => {
      const form = new FormData()
      form.append('file', new Blob(['contenu du fichier'], { type: 'text/plain' }), 'note.txt')
      return window.__DESK_SMOKE__.transport.request({ method: 'POST', url, headers: { Authorization: 'Bearer t' }, body: form })
    }, `${SERVER}/smoke/upload`)
    assert(out.status === 201 && out.json.multipart && out.json.boundaryInBody && out.json.filename === 'note.txt' && out.json.hasContent, JSON.stringify(out))
  })

  await check('SSE : les événements arrivent UN PAR UN pendant que le flux dure, puis le statut', async () => {
    const out = await page(async (url) => {
      const at = []
      const t0 = performance.now()
      const res = await window.__DESK_SMOKE__.transport.stream({ method: 'GET', url, headers: { Accept: 'text/event-stream' } }, (text) => at.push([Math.round(performance.now() - t0), text]))
      return { status: res.status, at, total: Math.round(performance.now() - t0) }
    }, `${SERVER}/smoke/stream`)
    const text = out.at.map((x) => x[1]).join('')
    assert(out.status === 200 && (text.match(/event: tick/g) ?? []).length === 3, JSON.stringify(out))
    assert(out.at.length >= 2 && out.at[0][0] < out.total - 250, `le flux est arrivé d'un bloc, pas au fil de l'eau : ${JSON.stringify(out.at.map((x) => x[0]))} / ${out.total} ms`)
    return `morceaux à ${out.at.map((x) => x[0]).join(', ')} ms sur ${out.total} ms`
  })

  await check('annulation : une requête interrompue rejette sur AbortError, un flux aussi', async () => {
    const out = await page(async (slow, stream) => {
      const { transport } = window.__DESK_SMOKE__
      const a = new AbortController()
      setTimeout(() => a.abort(), 200)
      const request = await transport.request({ method: 'GET', url: slow, headers: {}, signal: a.signal }).then(() => 'réponse', (e) => e.name)
      const b = new AbortController()
      let got = 0
      const streamed = await transport.stream({ method: 'GET', url: stream, headers: {}, signal: b.signal }, () => (++got === 1 ? b.abort() : null)).then(() => 'fin', (e) => e.name)
      return { request, streamed, got }
    }, `${SERVER}/smoke/slow`, `${SERVER}/app/xpulse/stream`)
    assert(out.request === 'AbortError' && out.streamed === 'AbortError', JSON.stringify(out))
  })

  await check('une adresse hors du serveur configuré est refusée avant de partir, serveur injoignable = NetworkError', async () => {
    const out = await page(async () => {
      const { transport } = window.__DESK_SMOKE__
      const name = (p) => p.then(() => 'réponse', (e) => `${e.name}: ${e.message}`)
      return { ailleurs: await name(transport.request({ method: 'GET', url: 'https://example.com/', headers: {} })), ferme: await name(transport.request({ method: 'GET', url: 'http://127.0.0.1:8000/', headers: {}, signal: AbortSignal.abort() })) }
    })
    assert(out.ailleurs.startsWith('TransportRefusal') && out.ailleurs.includes('https://example.com'), out.ailleurs)
    return out.ailleurs
  })

  await check('connexion par le formulaire : le jeton va dans le coffre du PROCESSUS, rien dans le stockage de la WebView', async () => {
    await type('#login-email', 'alice@acme.test')
    await type('#login-password', 'bon')
    await click('form button[type="submit"]')
    await until('la sortie de l’écran de connexion', async () => !(await hash()).startsWith('#/login'))
    const login = seen.find((r) => r.path === '/app/auth/login')
    assert(JSON.parse(login.body.toString()).email === 'alice@acme.test', 'le formulaire n’est pas arrivé en JSON')
    const out = await page(async () => ({
      authenticated: window.__DESK_SMOKE__.session.authenticated,
      vault: Object.keys(await window.__TAURI_INTERNALS__.invoke('vault_all')).sort(),
      webview: [sessionStorage.length, Object.keys(localStorage).filter((k) => k.startsWith('xdt.desk.session') || k.includes('token'))],
    }))
    assert(out.authenticated, 'la session n’est pas ouverte')
    assert(out.vault.includes('xdt.desk.token') && out.vault.includes('xdt.desk.refresh'), `coffre : ${out.vault}`)
    assert(out.webview[0] === 0 && out.webview[1].length === 0, `des jetons dans la WebView : ${JSON.stringify(out.webview)}`)
    return `coffre : ${out.vault.join(', ')}`
  })

  await check('le cookie de session posé par le serveur n’est ni gardé ni renvoyé : seule l’en-tête Authorization authentifie', async () => {
    const out = await page((url) => window.__DESK_SMOKE__.transport.request({ method: 'POST', url, headers: { 'Content-Type': 'application/json' }, body: {} }), `${SERVER}/smoke/echo`)
    assert(out.json.cookie === null, `le processus a renvoyé un cookie : ${String(out.json.cookie).slice(0, 40)}…`)
    assert(out.json.authorization === null, 'une autorisation est partie sans avoir été demandée')
    const withCookie = seen.filter((r) => r.headers.cookie).map((r) => r.path)
    assert(withCookie.length === 0, `des requêtes ont porté un cookie : ${withCookie}`)
    return `Origin vu par le serveur : ${out.json.origin}`
  })

  await check('renouvellement : le jeton proche de son échéance est échangé UNE fois, et le suivant sert aux appels', async () => {
    await until('un appel à /refresh', () => refreshUsed.length > 0, 20000)
    await sleep(500)
    assert(refreshUsed.length === 1 && refreshUsed[0] === 'r1', `échanges : ${refreshUsed}`)
    const out = await page(async () => ({ refresh: window.__DESK_SMOKE__.session.refreshToken, vault: (await window.__TAURI_INTERNALS__.invoke('vault_all'))['xdt.desk.refresh'] }))
    assert(out.refresh === 'r2' && out.vault === 'r2', JSON.stringify(out))
  })

  await check('temps réel du Desk : le flux d’xpulse est demandé avec le jeton de la session', async () => {
    const stream = await until('la demande du flux', () => seen.find((r) => r.path === '/app/xpulse/stream' && r.headers.authorization))
    assert(stream.headers.authorization.startsWith('Bearer '), 'sans jeton')
  })

  await check('un lien externe ne navigue pas la WebView : le SYSTÈME l’ouvre', async () => {
    const before = await page(() => location.href)
    await page(() => {
      const a = document.createElement('a')
      a.href = 'https://docs.exemple.test/guide?x=1'
      a.target = '_blank'
      a.id = 'smoke-external'
      a.textContent = 'doc'
      document.body.appendChild(a)
      a.click()
    })
    await until('la demande au système', () => readFileSync(opened, 'utf8').includes('https://docs.exemple.test/guide?x=1'), 8000)
    assert((await page(() => location.href)) === before, 'la WebView a navigué')
    return 'xdg-open https://docs.exemple.test/guide?x=1'
  })

  await check('le processus refuse d’ouvrir autre chose que http(s)/mailto/tel, même si la page le demande directement', async () => {
    const out = await page(() => window.__TAURI_INTERNALS__.invoke('open_external', { url: 'file:///etc/passwd' }).then(() => 'ouvert', (e) => String(e)))
    assert(out.includes('refusé'), out)
    assert(!readFileSync(opened, 'utf8').includes('passwd'), 'le système a été sollicité')
    return out
  })

  await check('une navigation forcée hors de l’application est refusée par le processus', async () => {
    const before = await page(() => location.href)
    await page((url) => void (location.href = url), `${SERVER}/smoke/echo`).catch(() => {})
    await sleep(800)
    const after = await page(() => location.href)
    assert(after === before, `la WebView est partie sur ${after}`)
    // C'est bien le verrou du processus qui a refusé (il l'écrit), pas un hasard de la WebView.
    assert(driverLog.includes(`navigation refusée : ${SERVER}/smoke/echo`), 'le processus n’a pas journalisé de refus')
  })

  await check('la page n’a AUCUN accès natif générique (fichiers, shell, fenêtres arbitraires)', async () => {
    const out = await page(async () => {
      const tryIt = (cmd, args) => window.__TAURI_INTERNALS__.invoke(cmd, args).then(() => 'ACCORDÉ', (e) => String(e))
      return {
        fs: await tryIt('plugin:fs|read_text_file', { path: '/etc/passwd' }),
        opener: await tryIt('plugin:opener|open_path', { path: '/etc/passwd' }),
        window: await tryIt('plugin:webview|create_webview_window', { options: { label: 'x', url: 'https://example.com' } }),
        dialog: await tryIt('plugin:dialog|open', { options: {} }),
        vaultKey: await tryIt('vault_set', { key: 'autre.cle', value: 'x' }),
      }
    })
    for (const [what, answer] of Object.entries(out)) assert(answer !== 'ACCORDÉ', `${what} est accordé à la page`)
    return Object.entries(out).map(([k, v]) => `${k} : ${v.slice(0, 60)}`).join(' | ')
  })

  let handles = []
  await check('une SECONDE fenêtre hérite de la session (même coffre), sans se reconnecter', async () => {
    await page(() => window.__DESK_SMOKE__.host.openWindow('/tasks'))
    handles = await until('la seconde fenêtre', async () => {
      const all = await wd('GET', `${S}/window/handles`)
      return all.length >= 2 ? all : null
    })
    const first = await wd('GET', `${S}/window`)
    await wd('POST', `${S}/window`, { handle: handles.find((h) => h !== first) })
    await until('le Desk dans la seconde fenêtre', () => page(() => !!window.__DESK_SMOKE__))
    const out = await page(() => ({ href: location.href, authenticated: window.__DESK_SMOKE__.session.authenticated, refresh: window.__DESK_SMOKE__.session.refreshToken, label: window.__TAURI_INTERNALS__.metadata.currentWindow.label }))
    assert(out.authenticated && out.refresh === 'r2', JSON.stringify(out))
    assert(out.href.includes('#/tasks'), `route inattendue : ${out.href}`)
    assert(seen.filter((r) => r.path === '/app/auth/login').length === 1, 'la seconde fenêtre s’est reconnectée')
    await wd('POST', `${S}/window`, { handle: first })
    return `fenêtre ${out.label}`
  })

  await check('deux fenêtres qui renouvellent ensemble : UN seul échange du jeton à usage unique', async () => {
    if (handles.length < 2) throw new Error('pas de seconde fenêtre')
    const first = await wd('GET', `${S}/window`)
    const second = handles.find((h) => h !== first)
    const before = refreshUsed.length
    // Les deux demandes partent sans s'attendre : chacune rend la main aussitôt, le résultat est relu ensuite.
    const fire = () => page(() => void (window.__smokeRefresh = window.__DESK_SMOKE__.session.refresh().then((v) => (window.__smokeRefreshed = v))))
    await fire()
    await wd('POST', `${S}/window`, { handle: second })
    await fire()
    const b = await until('le renouvellement de la seconde', () => page(() => window.__smokeRefreshed ?? null))
    await wd('POST', `${S}/window`, { handle: first })
    const a = await until('le renouvellement de la première', () => page(() => window.__smokeRefreshed ?? null))
    await sleep(300)
    const pair = await page(() => window.__DESK_SMOKE__.session.refreshToken)
    assert(a === 'ok' && b === 'ok', `résultats : ${a}, ${b}`)
    assert(refreshUsed.length === before + 1, `${refreshUsed.length - before} échanges (${refreshUsed.slice(before)})`)
    return `échanges vus par le serveur : ${refreshUsed.join(', ')} ; les deux fenêtres tiennent ${pair}`
  })

  await check('se déconnecter dans une fenêtre déconnecte l’autre', async () => {
    if (handles.length < 2) throw new Error('pas de seconde fenêtre')
    const first = await wd('GET', `${S}/window`)
    await page(() => window.__DESK_SMOKE__.session.logout())
    await wd('POST', `${S}/window`, { handle: handles.find((h) => h !== first) })
    await until('la déconnexion de la seconde fenêtre', () => page(() => !window.__DESK_SMOKE__.session.authenticated && location.hash.startsWith('#/login')))
    await wd('DELETE', `${S}/window`) // on ferme la seconde : la suite se joue dans la première
    await wd('POST', `${S}/window`, { handle: first })
    const vault = await page(async () => Object.keys(await window.__TAURI_INTERNALS__.invoke('vault_all')))
    assert(vault.length === 0, `le coffre garde ${vault}`)
  })

  /** Le système remet un lien `erp://` en relançant l'application avec le lien en argument : l'instance unique le transmet à celle qui tourne. */
  const deepLink = (url) => spawnSync(BINARY, [url], { env, timeout: 15000 })

  await check('OAuth : le fournisseur s’ouvre dans le navigateur du système, avec un retour par lien profond et un nonce', async () => {
    await until('l’écran de connexion', async () => (await hash()).startsWith('#/login'))
    const button = await until('le bouton du fournisseur', () => page(() => !![...document.querySelectorAll('.login-oauth button')].length))
    assert(button, 'aucun bouton OAuth')
    await click('.login-oauth button')
    await until('la demande au système', () => readFileSync(opened, 'utf8').includes('https://idp.exemple.test/authorize'), 8000)
    const asked = seen.findLast((r) => r.path === '/app/auth/oauth/github/authorize')
    assert(asked.query.get('redirect') === 'erp://oauth-callback', `redirect = ${asked.query.get('redirect')}`)
    assert(/^[0-9a-f]{32}$/.test(oauthNonce ?? ''), `nonce = ${oauthNonce}`)
    assert((await hash()).startsWith('#/login'), 'la WebView a quitté l’écran de connexion')
  })

  await check('un lien profond FORGÉ (mauvais nonce) n’ouvre pas de session', async () => {
    const forged = issue(3600)
    deepLink(`erp://oauth-callback?access_token=${forged.access_token}&refresh_token=${forged.refresh_token}&app_state=${'0'.repeat(32)}`)
    await until('l’écran du retour OAuth', async () => (await hash()).startsWith('#/oauth'), 10000)
    await sleep(300)
    const out = await page(async () => ({ authenticated: window.__DESK_SMOKE__.session.authenticated, error: document.querySelector('[data-testid="oauth-error"]')?.textContent ?? null, vault: Object.keys(await window.__TAURI_INTERNALS__.invoke('vault_all')) }))
    assert(!out.authenticated && out.vault.length === 0, JSON.stringify(out))
    assert(out.error, 'le refus n’est pas affiché')
    return `affiché : « ${out.error.trim()} »`
  })

  await check('un lien profond sans connexion en cours est ignoré (le nonce a servi)', async () => {
    const forged = issue(3600)
    deepLink(`erp://oauth-callback?access_token=${forged.access_token}&refresh_token=${forged.refresh_token}&app_state=${oauthNonce}`)
    await sleep(1500)
    assert(!(await page(() => window.__DESK_SMOKE__.session.authenticated)), 'session ouverte par un lien rejoué')
  })

  await check('le retour OAuth au BON nonce ouvre la session dans l’application', async () => {
    await page(() => void (location.hash = '#/login'))
    await until('le bouton du fournisseur', () => page(() => !![...document.querySelectorAll('.login-oauth button')].length))
    await click('.login-oauth button')
    const previous = oauthNonce
    await until('un nouveau nonce', () => oauthNonce !== previous || seen.filter((r) => r.path === '/app/auth/oauth/github/authorize').length >= 2)
    const good = issue(3600)
    deepLink(`erp://oauth-callback?access_token=${good.access_token}&refresh_token=${good.refresh_token}&user_id=u1&app_state=${oauthNonce}`)
    await until('la session ouverte', () => page(() => window.__DESK_SMOKE__.session.authenticated), 10000)
    // L'écriture au coffre suit la session d'un aller-retour avec le processus : on l'attend, on ne la suppose pas.
    await until('le jeton dans le coffre', () => page(async (r) => (await window.__TAURI_INTERNALS__.invoke('vault_all'))['xdt.desk.refresh'] === r, good.refresh_token))
    const out = await page(() => ({ refresh: window.__DESK_SMOKE__.session.refreshToken, hash: location.hash }))
    assert(out.refresh === good.refresh_token, JSON.stringify(out))
    assert(!out.hash.includes('access_token'), `les jetons sont restés dans l'adresse : ${out.hash}`)
    return `route ${out.hash}`
  })

  await check('un lien profond inconnu ne fait rien', async () => {
    const before = await hash()
    deepLink('erp://app/Facture/FA-1?x=1')
    await sleep(1000)
    assert((await hash()) === before, 'la route a changé')
  })
} catch (e) {
  failed = true
  results.push(`  ✗ le pilotage s'est arrêté : ${e.message}`)
  if (driverLog.trim()) results.push(`      pilote : ${driverLog.trim().split('\n').slice(-5).join(' / ')}`)
} finally {
  if (sid) await wd('DELETE', `/session/${sid}`).catch(() => {})
  driver.kill()
  server.closeAllConnections?.()
  server.close()
  try {
    rmSync(scratch, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  } catch {
    // l'application écrit encore dans son cache en s'arrêtant : un dossier temporaire de plus, rien d'autre
  }
}

console.log(`Application de bureau — ${process.platform}, ${tauriDriver ? 'tauri-driver' : 'WebKitWebDriver'}\n${results.join('\n')}`)
console.log(failed ? '\nÉCHEC' : `\n${results.length} points vérifiés`)
process.exit(failed ? 1 : 0)
