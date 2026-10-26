/**
 * Extension du Desk du plugin `biblio` (D28, doc/extensions.md) : la partie JavaScript d'un plugin.
 *
 * Un module ES prêt à servir (écrit à la main, sans build) : `vue` et `@xcore-doctype/desk` sont des noms nus que l'import map du Desk
 * résout vers SA copie de Vue et son SDK. Servie seulement si l'opérateur l'autorise : `doctype.desk.extensions: [biblio]`.
 * Tout ce qu'elle lit passe par l'API avec la session de l'utilisateur : ses droits s'appliquent, le serveur reste juge.
 */
import { defineComponent, h, onMounted, ref } from 'vue'
import { Button, defineExtension, useDesk } from '@xcore-doctype/desk'

/** Le jour LOCAL (AAAA-MM-JJ) : `toISOString()` donnerait celui de Greenwich, la veille au soir à Abidjan… ou à Auckland. */
const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Panneau de la fiche Livre : est-il sorti en ce moment, et combien de fois a-t-il été emprunté ? */
const LivreEmprunts = defineComponent({
  props: { name: { type: String, required: true } },
  setup(props) {
    const { api } = useDesk()
    const state = ref(null)
    onMounted(async () => {
      try {
        const [total, enCours] = await Promise.all([
          api.list('Emprunt', { filters: { livre: props.name }, limit: 1 }),
          api.list('Emprunt', { filters: { livre: props.name, statut: 'En cours' }, limit: 1 }),
        ])
        state.value = { total: total.total, sorti: enCours.total > 0 }
      } catch {
        state.value = { refuse: true } // qui lit le livre sans lire les emprunts : on le dit, on ne tombe pas en panne
      }
    })
    const texte = () =>
      !state.value ? 'Chargement…'
        : state.value.refuse ? 'Emprunts non accessibles'
        : `${state.value.sorti ? 'Sorti en ce moment' : 'Disponible'} · ${state.value.total} emprunt(s) au total`
    return () => h('p', { 'data-testid': 'biblio-livre-emprunts', style: 'margin:0' }, texte())
  },
})

/** Une page à soi, `#/x/biblio/retards` : les emprunts dont le retour prévu est passé. */
const Retards = defineComponent({
  setup() {
    const { api, router } = useDesk()
    const lignes = ref(null)
    onMounted(async () => {
      lignes.value = (await api.list('Emprunt', { filters: { statut: 'En cours', retour_prevu: ['<', today()] }, order_by: 'retour_prevu', order: 'asc', limit: 50 })).items
    })
    return () =>
      // Pas de `h1` ici : le Desk le pose lui-même avec le `title` de la route (`desk.routes.add`).
      h('section', { class: 'desk-card', style: 'padding:1rem' }, [
        lignes.value === null ? h('p', 'Chargement…')
          : !lignes.value.length ? h('p', 'Aucun retard.')
          : h('ul', lignes.value.map((e) =>
              h('li', { key: e.name }, [
                h(Button, { label: e.name, link: true, onClick: () => router.push({ name: 'form', params: { doctype: 'Emprunt', name: e.name } }) }),
                ` ${e.data.emprunteur}, prévu le ${e.data.retour_prevu}`,
              ]),
            )),
      ])
  },
})

export default defineExtension({
  name: 'biblio', // = le nom du plugin (le chargeur le vérifie)
  apiVersion: 1,
  setup(desk) {
    desk.form.panel({ doctype: 'Livre', title: 'Emprunts', component: LivreEmprunts })
    desk.form.action({
      doctype: 'Livre',
      label: 'Prêter ce livre',
      icon: 'arrow-right',
      when: ({ record }) => !!record, // un livre enregistré seulement
      run: ({ record, router }) => router.push({ name: 'new', params: { doctype: 'Emprunt' }, query: { livre: record.name } }),
    })
    desk.form.action({
      doctype: 'Emprunt',
      label: 'Marquer rendu',
      icon: 'check',
      when: ({ record }) => !!record && record.data.statut === 'En cours',
      // On écrit par l'API ordinaire : droits, hooks (la durée se recalcule), version optimiste, tout s'applique.
      run: async ({ record, api, reload }) => {
        await api.update('Emprunt', record.name, record.version, { ...record.data, statut: 'Rendu', rendu_le: today() })
        await reload()
      },
    })
    desk.routes.add({ path: 'retards', title: 'Emprunts en retard', component: Retards })
    desk.sidebar.add({ workspace: 'biblio', label: 'Retards', icon: 'clock', to: '/x/biblio/retards' })
    desk.commands.add({ label: 'Emprunts en retard', icon: 'clock', keywords: 'retard relance', run: ({ router }) => router.push('/x/biblio/retards') })
  },
})
