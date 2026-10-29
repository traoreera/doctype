/** Les jeux de données des tests de l'impression (`printedit.spec.ts`, `printdoc.spec.ts`). */
import type { DocTypeMeta, FieldMeta } from '@/lib/types'

export const f = (o: Partial<FieldMeta> & Pick<FieldMeta, 'fieldname' | 'fieldtype'>): FieldMeta => ({ required: false, hidden: false, in_list_view: false, ...o })

export const LINE: DocTypeMeta = {
  name: 'Ligne', label: 'Ligne', module: 'sales', timezone: 'UTC',
  fields: [
    f({ fieldname: 'article', label: 'Article', fieldtype: 'Link', options: 'Article' }),
    f({ fieldname: 'entrepot', label: 'Dépôt', fieldtype: 'Select', options: ['A', 'B'] }),
    f({ fieldname: 'qty', label: 'Quantité', fieldtype: 'Decimal', decimals: 3 }),
    f({ fieldname: 'amount', label: 'Montant', fieldtype: 'Currency', decimals: 2 }),
    f({ fieldname: 'photo', label: 'Photo', fieldtype: 'Image' }),
    f({ fieldname: 'notes', label: 'Notes', fieldtype: 'TextEditor' }),
  ],
  layout: [], list_columns: [], rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
}
export const INVOICE: DocTypeMeta = {
  name: 'Facture', label: 'Facture', module: 'sales', timezone: 'UTC',
  fields: [
    f({ fieldname: 'client', label: 'Client', fieldtype: 'Link', options: 'Client' }),
    f({ fieldname: 'date', label: 'Date', fieldtype: 'Date' }),
    f({ fieldname: 'items', label: 'Lignes', fieldtype: 'Table', options: 'Ligne' }),
    f({ fieldname: 'total', label: 'Total', fieldtype: 'Currency', decimals: 2 }),
    f({ fieldname: 'secret', label: 'Secret', fieldtype: 'Password' }),
    f({ fieldname: 'piece', label: 'Pièce', fieldtype: 'Attach' }),
  ],
  children: { Ligne: LINE },
  layout: [], list_columns: [], rights: { read: 'all', write: 'all', create: 'all', delete: 'all', submit: 'none', cancel: 'none' },
}

export const TEMPLATE = {
  schema_version: 1, doctype: 'Facture', name: 'standard', label: 'Facture',
  page: { size: 'A4', orientation: 'portrait', top: 20, bottom: 20, left: 15, right: 15 },
  blocks: [
    { type: 'title', text: 'Facture {{ name }}' },
    { type: 'fields', columns: 2, fields: ['client', 'date'] },
    { type: 'table', field: 'items', style: 'bordered', density: 'normal', repeat_header: true, columns: [{ field: 'article' }, { field: 'qty', align: 'right' }] },
    { type: 'totals', fields: ['total'] },
  ],
}

