import type { DocTypeMeta, FieldMeta } from '@/lib/types'

const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({
  required: false,
  hidden: false,
  in_list_view: false,
  ...o,
})

const lit = (v: unknown) => ({ t: 'lit', v }) as never
const field = (name: string) => ({ t: 'field', name }) as never
const cmp = (op: string, l: unknown, r: unknown) => ({ t: 'cmp', op, l, r }) as never

/** Le DocType Customer de `doctypes/customer.yaml`, tel que `/meta` le renvoie (conditions déjà compilées en arbres). */
export const CUSTOMER: DocTypeMeta = {
  name: 'Customer',
  label: 'Client',
  module: 'sales',
  title_field: 'customer_name',
  fields: [
    f({ fieldname: 'customer_name', label: 'Nom', fieldtype: 'Text', required: true, in_list_view: true }),
    f({ fieldname: 'type', label: 'Type', fieldtype: 'Select', options: ['Particulier', 'Entreprise'], in_list_view: true }),
    f({ fieldname: 'company_id', label: 'SIREN', fieldtype: 'Text', required: true, depends_on: cmp('==', field('type'), lit('Entreprise')) }),
    f({ fieldname: 'country', label: 'Pays', fieldtype: 'Link', options: 'Country' }),
    f({ fieldname: 'credit_limit', label: 'Plafond de crédit', fieldtype: 'Number', default: 0, in_list_view: true, read_only_depends_on: field('blocked') }),
    f({ fieldname: 'approver', label: 'Approbateur', fieldtype: 'Text', depends_on: cmp('>', field('credit_limit'), lit(0)), mandatory_depends_on: cmp('>', field('credit_limit'), lit(10000)) }),
    f({ fieldname: 'blocked', label: 'Bloqué', fieldtype: 'Boolean' }),
    f({ fieldname: 'since', label: 'Client depuis', fieldtype: 'Date' }),
    f({ fieldname: 'secret', label: 'Interne', fieldtype: 'Text', hidden: true }),
  ],
  layout: [
    { section: null, collapsible: false, collapsed: false, columns: [['customer_name', 'type', 'company_id'], ['country', 'since']] },
    { section: 'Crédit', collapsible: true, collapsed: false, columns: [['credit_limit', 'approver'], ['blocked']] },
  ],
  list_columns: ['customer_name', 'type', 'credit_limit'],
  rights: { read: 'all', write: 'all', create: 'all', delete: 'none', submit: 'none', cancel: 'none' },
}

/** `doctypes/invoice.yaml` tel que `/meta` le renvoie : des lignes (champ Table) dont le DocType enfant accompagne le parent. */
export const INVOICE: DocTypeMeta = {
  name: 'Invoice',
  label: 'Facture',
  module: 'sales',
  fields: [
    f({ fieldname: 'customer', label: 'Client', fieldtype: 'Link', options: 'Customer', required: true, in_list_view: true }),
    f({ fieldname: 'posting_date', label: 'Date', fieldtype: 'Date', in_list_view: true }),
    f({ fieldname: 'items', label: 'Lignes', fieldtype: 'Table', options: 'InvoiceItem', required: true }),
    f({ fieldname: 'total', label: 'Total', fieldtype: 'Number', in_list_view: true }),
  ],
  layout: [
    { section: null, collapsible: false, collapsed: false, columns: [['customer', 'posting_date']] },
    { section: 'Lignes', collapsible: false, collapsed: false, columns: [['items']] },
    { section: null, collapsible: false, collapsed: false, columns: [['total']] },
  ],
  list_columns: ['customer', 'posting_date', 'total'],
  rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
  children: {
    InvoiceItem: {
      name: 'InvoiceItem',
      label: 'Ligne de facture',
      fields: [
        f({ fieldname: 'item_name', label: 'Article', fieldtype: 'Text', required: true }),
        f({ fieldname: 'qty', label: 'Quantité', fieldtype: 'Number', required: true, default: 1 }),
        f({ fieldname: 'rate', label: 'Prix', fieldtype: 'Number', required: true }),
        f({ fieldname: 'taxed', label: 'Taxé', fieldtype: 'Boolean' }),
        f({ fieldname: 'note', label: 'Note', fieldtype: 'Text', hidden: true }),
      ],
    },
  },
}

/** `app/stock/doctypes/stock_entry.yaml` tel que `/meta` le renvoie : soumettable, avec des quantités Decimal (3 décimales) dans ses lignes. */
export const STOCK_ENTRY: DocTypeMeta = {
  name: 'Stock Entry',
  label: 'Mouvement de stock',
  module: 'stock',
  submittable: true,
  fields: [
    f({ fieldname: 'purpose', label: 'Nature', fieldtype: 'Select', options: ['Réception', 'Sortie', 'Transfert'], required: true, in_list_view: true }),
    f({ fieldname: 'posting_date', label: 'Date du mouvement', fieldtype: 'Date', required: true, in_list_view: true }),
    f({ fieldname: 'items', label: 'Lignes', fieldtype: 'Table', options: 'Stock Entry Line', required: true }),
    f({ fieldname: 'remarks', label: 'Remarques', fieldtype: 'Text' }),
  ],
  layout: [
    { section: 'Mouvement', collapsible: false, collapsed: false, columns: [['purpose'], ['posting_date']] },
    { section: 'Lignes', collapsible: false, collapsed: false, columns: [['items']] },
    { section: null, collapsible: false, collapsed: false, columns: [['remarks']] },
  ],
  list_columns: ['purpose', 'posting_date'],
  rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'all', cancel: 'all' },
  children: {
    'Stock Entry Line': {
      name: 'Stock Entry Line',
      label: 'Ligne de mouvement',
      fields: [
        f({ fieldname: 'item', label: 'Article', fieldtype: 'Link', options: 'Item', required: true }),
        f({ fieldname: 'qty', label: 'Quantité', fieldtype: 'Decimal', decimals: 3, required: true }),
        f({ fieldname: 'target_warehouse', label: 'Entrepôt cible', fieldtype: 'Link', options: 'Warehouse' }),
      ],
    },
  },
}

const parentIs = (...values: string[]) => ({ t: 'in', neg: false, l: field('parent_purpose'), r: { t: 'list', items: values.map((v) => lit(v)) } }) as never
const parentIsNot = (value: string) => cmp('!=', field('parent_purpose'), lit(value))

/**
 * `app/stock/doctypes/stock_entry*.yaml` tel que `/meta` le renvoie APRÈS les conditions de ligne : les entrepôts dépendent de la nature du
 * mouvement (champ du parent, lu par `parent_purpose`), les quantités portent l'unité de leur article (`unit_from`).
 */
export const MOVE: DocTypeMeta = {
  ...STOCK_ENTRY,
  timezone: 'Europe/Paris',
  naming: { series: 'MS-{YYYY}-{#####}' },
  fields: [
    ...STOCK_ENTRY.fields.filter((x) => x.fieldname !== 'posting_date'),
    f({ fieldname: 'posting_date', label: 'Date du mouvement', fieldtype: 'Date', required: true, default: 'today', in_list_view: true }),
  ],
  children: {
    'Stock Entry Line': {
      name: 'Stock Entry Line',
      label: 'Ligne de mouvement',
      fields: [
        f({ fieldname: 'item', label: 'Article', fieldtype: 'Link', options: 'Item', required: true }),
        f({ fieldname: 'qty', label: 'Quantité', fieldtype: 'Decimal', decimals: 3, required: true, unit_from: 'item.uom' }),
        f({ fieldname: 'source_warehouse', label: 'Entrepôt source', fieldtype: 'Link', options: 'Warehouse', depends_on: parentIsNot('Réception'), mandatory_depends_on: parentIs('Sortie', 'Transfert') }),
        f({ fieldname: 'target_warehouse', label: 'Entrepôt cible', fieldtype: 'Link', options: 'Warehouse', depends_on: parentIsNot('Sortie'), mandatory_depends_on: parentIs('Réception', 'Transfert') }),
      ],
    },
  },
}
