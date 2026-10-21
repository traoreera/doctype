/**
 * Extension du Desk du plugin `sales` (D28, doc/extensions.md) : l'exemple de ce qu'un plugin ajoute à l'interface.
 *
 * Un module ES déjà prêt (ici écrit à la main, sans build) : `vue` et `@xcore-doctype/desk` sont des noms nus que l'import map du Desk
 * résout vers SA copie de Vue et son SDK. Un vrai plugin construirait ce fichier (Vite, `external: ['vue', '@xcore-doctype/desk']`).
 * Servie seulement si l'opérateur l'autorise : `doctype.desk.extensions: [sales]`.
 */
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { Button, defineExtension, useDesk } from '@xcore-doctype/desk'

/** Panneau de la fiche Client : ses factures, comptées par le serveur (avec les droits de celui qui regarde). */
const CustomerInvoices = defineComponent({
  props: { name: { type: String, required: true } },
  setup(props) {
    const { api, router } = useDesk()
    const state = ref(null)
    onMounted(async () => {
      state.value = { count: (await api.list('Invoice', { filters: { customer: props.name }, limit: 1 })).total }
    })
    return () =>
      h('div', { 'data-testid': 'sales-customer-invoices', style: 'display:flex;gap:1rem;align-items:center' }, [
        h('span', state.value ? `${state.value.count} facture(s)` : 'Chargement…'),
        h(Button, {
          label: 'Voir les factures',
          size: 'small',
          text: true,
          onClick: () => router.push({ name: 'list', params: { doctype: 'Invoice' }, query: { filters: JSON.stringify({ customer: props.name }) } }),
        }),
      ])
  },
})

/** Une page à soi : `#/x/sales/overview`. */
const Overview = defineComponent({
  setup() {
    const { api } = useDesk()
    const total = ref(null)
    onMounted(async () => {
      total.value = (await api.list('Invoice', { limit: 1 })).total
    })
    const text = computed(() => (total.value === null ? 'Chargement…' : `${total.value} facture(s) au total`))
    return () => h('section', { class: 'desk-card', style: 'padding:1rem', 'data-testid': 'sales-overview' }, text.value)
  },
})

export default defineExtension({
  name: 'sales',
  apiVersion: 1,
  setup(desk) {
    desk.form.panel({ doctype: 'Customer', title: 'Factures', component: CustomerInvoices })
    desk.form.action({
      doctype: 'Customer',
      label: 'Nouvelle facture',
      icon: 'receipt',
      when: ({ record, meta }) => !!record && meta.rights.write !== 'none',
      run: ({ record, router }) => router.push({ name: 'new', params: { doctype: 'Invoice' }, query: { customer: record.name } }),
    })
    desk.routes.add({ path: 'overview', title: 'Synthèse des ventes', component: Overview })
    desk.sidebar.add({ workspace: 'sales', label: 'Synthèse', icon: 'chart-bar', to: '/x/sales/overview' })
    desk.commands.add({ label: 'Synthèse des ventes', icon: 'chart-bar', keywords: 'ventes resume', run: ({ router }) => router.push('/x/sales/overview') })
  },
})
