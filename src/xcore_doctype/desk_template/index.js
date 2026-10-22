/**
 * Extension du Desk du plugin `__PLUGIN__` (doc/extensions.md, D28), posée par `xcore-doctype new-desk-extension`.
 *
 * Un module ES chargé par le Desk au démarrage : `vue` et `@xcore-doctype/desk` sont résolus vers la copie du Desk par son import map.
 * Ce fichier marche tel quel (pas de build : des fonctions `h()` plutôt que des templates) ; pour écrire des composants `.vue`, voir
 * README.md à côté. Le plugin le déclare dans son `on_load` :
 *
 *     self.get_service("doctype_registry").load_desk(self.ctx.name, Path(__file__).resolve().parent.parent / "desk")
 *
 * et l'opérateur l'autorise dans le manifeste d'xcore-doctype : `doctype.desk.extensions: [__PLUGIN__]`.
 */
import { defineComponent, h, onMounted, ref } from 'vue'
import { Button, defineExtension, useDesk } from '@xcore-doctype/desk'

const LABEL = '__LABEL__' // posé par la commande (une chaîne JavaScript échappée)

/** Une page à soi, sous `#/x/__PLUGIN__/home` : elle lit l'API avec la session (et les droits) de l'utilisateur. */
const Home = defineComponent({
  setup() {
    const { api, notify } = useDesk()
    const types = ref(null)
    onMounted(async () => {
      try {
        types.value = (await api.doctypes()).filter((d) => d.module === '__PLUGIN__')
      } catch (e) {
        notify.error(e)
      }
    })
    return () =>
      h('section', { class: 'desk-card', style: 'padding: 1rem' }, [
        h('p', types.value === null ? 'Chargement…' : `${types.value.length} type(s) de documents dans ce module.`),
        h(Button, { label: 'Dire bonjour', size: 'small', onClick: () => notify.success('Bonjour depuis __PLUGIN__') }),
      ])
  },
})

export default defineExtension({
  name: '__PLUGIN__',
  apiVersion: 1,
  setup(desk) {
    desk.routes.add({ path: 'home', title: LABEL, component: Home })
    desk.sidebar.add({ workspace: '__PLUGIN__', label: LABEL, icon: 'bolt', to: '/x/__PLUGIN__/home' })
    desk.commands.add({ label: `Ouvrir ${LABEL}`, icon: 'bolt', run: ({ router }) => router.push('/x/__PLUGIN__/home') })
    // D'autres points d'extension (doc/extensions.md) :
    //   desk.form.action({ doctype: 'Mon Type', label: 'Faire…', when: ({ record }) => !!record, run: async ({ record, api, notify, reload }) => { … } })
    //   desk.form.panel({ doctype: 'Mon Type', title: 'En plus', component: MonPanneau })   // props : doctype, name, record, meta
    //   desk.list.action({ doctype: 'Mon Type', label: 'Traiter la sélection', run: async ({ names, plugin }) => plugin.post('/traiter', { names }) })
    //   desk.blocks.add('resume', MonBloc)   // bloc d'espace YAML : { type: extension, component: __PLUGIN__.resume, props: { … } } ;
    //                                         MonBloc reçoit les props `config` (cet objet) et `label`
    //   desk.styles.add(new URL('./style.css', import.meta.url).href)   // le CSS d'une extension construite avec des `.vue`
  },
})
