/**
 * Garde-fou du portage de bureau (traoreera/doctype#73) : l'INVENTAIRE des accès directs au navigateur, tenu par un test.
 *
 * Le Desk ne touche le réseau que par `Transport`, et son hôte que par `DeskHost`. Chaque accès direct restant est NOMMÉ ici, avec le
 * fichier qui a le droit de le faire : en ajouter un ailleurs fait échouer ce test, et la correction est de passer par le contrat — ou
 * d'ajouter une ligne ici en disant pourquoi (le détail est dans `docs/desktop.md`).
 */
// @ts-expect-error -- API de Node, sans ses types dans ce projet (comme tests/pill-contrast.spec.ts)
import { readdirSync, readFileSync, statSync } from 'node:fs'
// @ts-expect-error -- idem
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC: string = resolve('src') // vitest tourne depuis ui/ (happy-dom donne à import.meta.url une adresse http)

function files(dir: string): string[] {
  return (readdirSync(dir) as string[]).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return files(path)
    return /\.(ts|vue)$/.test(name) ? [path] : []
  })
}

/** Le code sans ses commentaires (`//…`, blocs, `<!-- -->`) : un commentaire qui CITE `fetch` n'est pas un appel. */
function code(path: string): string {
  return readFileSync(path, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

const SOURCES = files(SRC).map((path) => ({ file: relative(SRC, path).replaceAll('\\', '/'), text: code(path) }))

function users(pattern: RegExp): string[] {
  return SOURCES.filter((s) => pattern.test(s.text)).map((s) => s.file).sort()
}

describe('les accès directs au navigateur sont inventoriés', () => {
  it.each([
    // Le réseau : `FetchTransport` est l'implémentation web du contrat, la seule à appeler `fetch`.
    ['fetch(', /(?<![\w.])fetch\(/, ['lib/transport.ts']],
    ['EventSource', /\bEventSource\b/, []],
    ['XMLHttpRequest / sendBeacon / WebSocket', /\bXMLHttpRequest\b|\bsendBeacon\b|new WebSocket\b/, []],
    // Les jetons : seul l'hôte web touche `sessionStorage`.
    ['sessionStorage', /\bsessionStorage\b/, ['lib/host.ts']],
    // Le couple partagé entre ONGLETS : l'implémentation web de `TabSync`. Le bureau a son coffre (`desktop/vault.ts`).
    ['BroadcastChannel', /\bBroadcastChannel\b/, ['lib/tabsync.ts']],
    ['navigator.locks', /navigator(?: as [^)]+\))?\)?\.locks|\.locks\b/, ['lib/tabsync.ts']],
    // Sortir de l'application : par `host.leave` (web : navigation de l'onglet).
    ['location.assign / location.href =', /location\.assign\(|location\.href\s*=[^=]/, ['lib/host.ts']],
    // Un nouvel onglet : `host.openExternal` / `host.openWindow` (web).
    ['window.open', /(?:window|globalThis)\.open\(/, ['lib/host.ts']],
    // Enregistrer un fichier : `lib/download.ts` est l'implémentation web de `host.save*`, appelée par `lib/host.ts` seulement.
    ['createObjectURL', /createObjectURL/, ['lib/download.ts']],
    ["import … from '@/lib/download'", /from ['"](?:@\/lib|\.)\/download['"]/, ['lib/host.ts']],
    // `import()` d'une URL connue à l'exécution : le chargement des extensions (D28), que l'hôte peut refuser (`unavailable.extensions`).
    ['import(url) — une adresse, pas un module du build', /\bimport\(\s*[A-Za-z_]/, ['extensions/loader.ts']],
  ])('%s', (_what, pattern, allowed) => {
    expect(users(pattern)).toEqual(allowed)
  })

  it('localStorage ne garde que des COMMODITÉS de l’appareil — et, dans le navigateur, le couple partagé entre onglets', () => {
    expect(users(/\blocalStorage\b/)).toEqual([
      'i18n.ts', // la langue choisie
      'lib/navstate.ts', // sections repliées de la barre latérale
      'lib/recents.ts', // documents récents, par utilisateur et organisation
      'lib/tabsync.ts', // web seulement : le couple de jetons d'une lignée d'onglets (le bureau ne s'en sert pas)
      'lib/theme.ts', // clair / sombre
      'lib/viewprefs.ts', // vue et colonnes choisies par type
      'views/ListView.vue', // panneau latéral de la liste ouvert ou non
      'views/WorkflowBuilderView.vue', // positions des nœuds d'un graphe
    ])
  })

  it('ce qui reste au navigateur de la WebView, en connaissance de cause', () => {
    // Recharger la page (changement d'organisation, de langue, de module ; « Réessayer » du démarrage de bureau) : la session est dans
    // le coffre (bureau) ou l'onglet (web), elle y survit, et la page rechargée est toujours celle de l'application.
    expect(users(/location\.reload\(/)).toEqual(['components/AppShell.vue', 'components/admin/OrganisationSettings.vue', 'desktop/main.ts', 'views/ModulesView.vue'])
    // Imprimer l'aperçu : l'iframe de PrintDialog. En bureau, le PDF (transport + dialogue natif) est la voie sûre (docs/desktop.md).
    expect(users(/\.print\(\)/)).toEqual(['components/PrintDialog.vue'])
    expect(users(/navigator\.clipboard/)).toEqual(['components/admin/ApiSettings.vue', 'views/ApiDocsView.vue'])
  })
})

describe('Tauri reste dans le point d’entrée de bureau', () => {
  it('seuls `desktop/tauri.ts` (les branchements) importe `@tauri-apps/*`', () => {
    expect(users(/from ['"]@tauri-apps\//)).toEqual(['desktop/tauri.ts'])
    expect(users(/__TAURI/)).toEqual([])
  })

  it('rien du Desk web n’importe le dossier `desktop/` : le build web ne peut pas en contenir', () => {
    const outside = SOURCES.filter((s) => !s.file.startsWith('desktop/') && /from ['"](?:@\/desktop\/|\.{1,2}\/desktop\/)/.test(s.text)).map((s) => s.file)
    expect(outside).toEqual([])
  })

  it('le point d’entrée web donne `FetchTransport` ; celui du bureau `NativeTransport`, et jamais `FetchTransport`', () => {
    const web = SOURCES.find((s) => s.file === 'main.ts')!.text
    const desktop = SOURCES.filter((s) => s.file.startsWith('desktop/'))
    expect(web).toContain('new FetchTransport()')
    expect(desktop.find((s) => s.file === 'desktop/main.ts')!.text).toContain('new NativeTransport(')
    expect(desktop.filter((s) => /FetchTransport/.test(s.text)).map((s) => s.file)).toEqual([])
  })
})
