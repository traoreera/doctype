#!/usr/bin/env node
/**
 * Régler le SERVEUR d'une construction de l'application de bureau — les deux fichiers qui doivent le nommer, écrits ensemble :
 *
 *   pnpm desktop:configure https://erp.exemple.fr/app/xcore-doctype/desk/ [--extensions] [--no-realtime]
 *
 * - `desk.json` : ce que la page lit au démarrage (`ui/src/desktop/config.ts`, mêmes règles : https, ou http vers la machine elle-même) ;
 * - `capabilities/server.json` : la portée réseau du processus (`http:default`), seule origine que le transport natif peut joindre.
 *
 * Les tenir d'accord à la main est possible ; les écrire d'un geste évite l'application qui démarre sur « serveur injoignable » parce
 * que la portée nomme encore l'ancien. Sans argument : affiche le réglage courant et vérifie qu'ils s'accordent (code 1 sinon).
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const at = (name) => fileURLToPath(new URL(name, import.meta.url))
const LOOPBACK = new Set(['localhost', '127.0.0.1', '[::1]'])
const fail = (message) => {
  console.error(`✗ ${message}`)
  process.exit(1)
}

const args = process.argv.slice(2)
const flags = new Set(args.filter((a) => a.startsWith('--')))
const [address, ...rest] = args.filter((a) => !a.startsWith('--'))
for (const flag of flags) if (!['--extensions', '--no-realtime'].includes(flag)) fail(`option inconnue : ${flag} (attendues : --extensions, --no-realtime)`)
if (rest.length) fail(`une seule adresse attendue, reçu aussi : ${rest.join(' ')}`)

if (!address) {
  const desk = JSON.parse(readFileSync(at('desk.json'), 'utf8'))
  const scope = JSON.parse(readFileSync(at('capabilities/server.json'), 'utf8')).permissions.flatMap((p) => p.allow ?? []).map((a) => a.url)
  const origin = new URL(desk.server).origin
  console.log(`serveur : ${desk.server}\nextensions : ${desk.extensions === true}   temps réel : ${desk.realtime !== false}\nportée réseau : ${scope.join(', ')}`)
  if (!scope.includes(`${origin}/*`)) fail(`la portée réseau ne nomme pas ${origin}/* : l'application ne joindra pas son serveur (relancer avec l'adresse)`)
  process.exit(0)
}

let url
try {
  url = new URL(address)
} catch {
  fail(`« ${address} » n'est pas une adresse absolue`)
}
if (url.protocol !== 'https:' && !(url.protocol === 'http:' && LOOPBACK.has(url.hostname))) fail(`${url.protocol}//${url.host} refusé : https obligatoire (http n'est admis que vers localhost, 127.0.0.1 ou [::1])`)
if (url.username || url.password || url.search || url.hash) fail("l'adresse du Desk ne porte ni identifiants, ni « ? », ni « # »")
if (!url.pathname.endsWith('/')) url.pathname += '/'

writeFileSync(at('desk.json'), `${JSON.stringify({ server: url.toString(), extensions: flags.has('--extensions'), realtime: !flags.has('--no-realtime') }, null, 2)}\n`)
const capability = JSON.parse(readFileSync(at('capabilities/server.json'), 'utf8'))
capability.permissions = [{ identifier: 'http:default', allow: [{ url: `${url.origin}/*` }] }]
writeFileSync(at('capabilities/server.json'), `${JSON.stringify(capability, null, 2)}\n`)
console.log(`✓ desk.json et capabilities/server.json : ${url.toString()} (portée ${url.origin}/*)`)
if (url.protocol === 'http:') console.log('  http : développement seulement — les images du serveur passent par la CSP de tauri.conf.json (img-src http://127.0.0.1:* http://localhost:*)')
if (flags.has('--extensions')) console.log(`  extensions : NON vérifiées en bureau — il faut aussi « ${url.origin} » dans script-src (tauri.conf.json) et que le serveur réponde en CORS pour l'origine de l'application (docs/desktop.md)`)
