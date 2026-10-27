import { createRouter, createWebHashHistory, type RouteLocationNormalized, type Router } from 'vue-router'
import type { Pinia } from 'pinia'
import { useSession } from '@/stores/session'

/** Ce que la page d'un type reçoit de l'adresse : le type, la vue demandée (`?view=`) et les filtres de départ (`?filters=`). */
export function listProps(r: RouteLocationNormalized): { doctype: string; view: string | null; filters: string | null } {
  return {
    doctype: String(r.params.doctype),
    view: typeof r.query.view === 'string' ? r.query.view : null,
    filters: typeof r.query.filters === 'string' ? r.query.filters : null,
  }
}

/**
 * Routage par « hash » : le Desk est servi sous un préfixe que l'opérateur choisit (…/desk/) et, plus tard, sous un
 * protocole d'application (Tauri). Un `#/app/Customer` ne demande aucune règle de réécriture côté serveur.
 */
export function createDeskRouter(pinia: Pinia): Router {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
      // Les écrans d'authentification publics : le lien d'un e-mail atterrit ici (hash), jamais dans un deep-link.
      { path: '/register', name: 'register', component: () => import('@/views/RegisterView.vue'), meta: { public: true } },
      { path: '/forgot', name: 'forgot', component: () => import('@/views/ForgotPasswordView.vue'), meta: { public: true } },
      { path: '/reset', name: 'reset', component: () => import('@/views/ResetPasswordView.vue'), meta: { public: true } },
      { path: '/invite', name: 'invite', component: () => import('@/views/InviteView.vue'), meta: { public: true } },
      { path: '/oauth', name: 'oauth', component: () => import('@/views/OAuthCallbackView.vue'), meta: { public: true } },
      // Un compte sans organisation : créer ou rejoindre, avant tout accès aux documents (jeton valide, sans tenant).
      { path: '/onboarding', name: 'onboarding', component: () => import('@/views/OnboardingView.vue') },
      { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
      // Tous les réglages des modules au même endroit : `/settings` (général), `/settings/<Type unique>` (un réglage, édité sur place).
      { path: '/profile', name: 'profile', component: () => import('@/views/ProfileView.vue') },
      { path: '/tasks', name: 'tasks', component: () => import('@/views/TasksView.vue') },
      { path: '/shared', name: 'shared', component: () => import('@/views/SharedView.vue') },
      // Les fichiers des documents que je peux lire (champs Image/Attach, pièces jointes) : lister, télécharger, détacher.
      { path: '/files', name: 'files', component: () => import('@/views/FilesView.vue') },
      { path: '/api/:doctype?', name: 'api-docs', component: () => import('@/views/ApiDocsView.vue'), props: true },
      { path: '/workflow/:doctype', name: 'workflow', component: () => import('@/views/WorkflowBuilderView.vue'), props: true },
      { path: '/settings/:doctype?', name: 'settings', component: () => import('@/views/SettingsView.vue'), props: true },
      { path: '/w/:name', name: 'workspace', component: () => import('@/views/WorkspaceView.vue'), props: true },
      {
        path: '/app/:doctype',
        name: 'list',
        component: () => import('@/views/ListView.vue'),
        // La vue (`?view=kanban`) et des filtres de départ (`?filters={…}`, ouverts par une carte de chiffres) viennent de l'adresse.
        props: listProps,
      },
      { path: '/app/:doctype/new', name: 'new', component: () => import('@/views/FormView.vue'), props: (r) => ({ doctype: r.params.doctype, name: null }) },
      { path: '/app/:doctype/:name', name: 'form', component: () => import('@/views/FormView.vue'), props: true },
      { path: '/:rest(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
    ],
  })

  router.beforeEach((to) => {
    const session = useSession(pinia)
    if (to.meta.public) {
      // Déjà connecté : l'écran de connexion n'a plus de raison d'être.
      return to.name === 'login' && session.authenticated ? { name: 'home' } : true
    }
    if (!session.authenticated) {
      // Un jeton périmé reste en mémoire jusqu'ici : on le jette pour ne plus l'envoyer.
      if (session.token) session.expire()
      return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
    }
    return true
  })

  return router
}

/** Où aller après la connexion : seulement un chemin interne du Desk (jamais une URL, jamais `//`). */
export function safeNext(next: unknown): string {
  return typeof next === 'string' && next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') ? next : '/'
}
