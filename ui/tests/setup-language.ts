/**
 * Les tests parlent la langue de RÉFÉRENCE du Desk, le français : sans cela, happy-dom annonce `navigator.language = "en-US"` et
 * `src/i18n.ts` choisirait l'anglais. Le choix gardé par le navigateur passe avant tout (voir `i18n.ts`) ; `tests/i18n.spec.ts` vérifie
 * l'anglais en rechargeant le module avec un autre choix.
 */
try {
  globalThis.localStorage?.setItem('desk.language', 'fr')
} catch {
  /* sans stockage : rien à fixer */
}
