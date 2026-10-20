/**
 * La documentation de l'API REST d'un type de document, GÉNÉRÉE de sa définition : routes, droits, champs, opérateurs de filtre, exemples. Rien n'est écrit à la
 * main : un champ ajouté au YAML apparaît ici sans toucher à rien. Sans interface : testé seul.
 */
import { t } from '@/i18n'
import { FILTER_OPS, type Action, type DocTypeMeta, type FieldMeta, type FieldType, type Right } from './types'

export interface Endpoint {
  id: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'
  path: string
  title: string
  /** L'action dont l'utilisateur doit avoir le droit (le serveur est l'autorité), ou `null` : il suffit d'être identifié. */
  needs: Action | null
  right: Right | null
  body?: unknown
  curl: string
}

/** Une valeur d'exemple d'un champ, de la forme que le serveur accepte (un Decimal en texte, une date ISO, un identifiant de fichier…). */
export function sampleValue(f: FieldMeta): unknown {
  switch (f.fieldtype as FieldType) {
    case 'Number':
      return 12.5
    case 'Int':
      return 3
    case 'Percent':
      return 20
    case 'Rating':
      return Math.max(1, Math.ceil((f.stars ?? 5) / 2))
    case 'Decimal':
    case 'Currency':
      return `12${(f.decimals ?? (f.fieldtype === 'Currency' ? 2 : 0)) > 0 ? `.${'5'.padEnd(f.decimals ?? 2, '0')}` : ''}`
    case 'Boolean':
      return true
    case 'Date':
      return '2026-10-20'
    case 'Datetime':
      return '2026-10-20T09:30:00Z'
    case 'Time':
      return '09:30:00'
    case 'Duration':
      return 5400
    case 'Select':
      return Array.isArray(f.options) ? f.options[0] : ''
    case 'Link':
    case 'DynamicLink':
      return 'NOM-DU-DOCUMENT'
    case 'Color':
      return '#2563eb'
    case 'Image':
    case 'Attach':
      return 'f'.repeat(32)
    case 'JSON':
      return { cle: 'valeur' }
    case 'Password':
      return 'secret'
    case 'TextEditor':
      return '<p>Texte <strong>enrichi</strong></p>'
    case 'Table':
      return []
    default:
      return f.format === 'email' ? 'nom@exemple.fr' : f.format === 'url' ? 'https://exemple.fr' : f.format === 'phone' ? '+33 1 23 45 67 89' : 'texte'
  }
}

/** Un document d'exemple : les champs obligatoires et ceux de la liste, sans ce que le serveur remplit (champs copiés, lecture seule, cachés). */
export function sampleData(meta: DocTypeMeta): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const f of meta.fields) {
    if (f.hidden || f.read_only || f.fetch_from) continue
    if (!(f.required || f.in_list_view || meta.list_columns.includes(f.fieldname))) continue
    if (f.fieldtype === 'Table') {
      const child = meta.children?.[String(f.options)]
      out[f.fieldname] = child ? [Object.fromEntries(child.fields.filter((c) => !c.hidden && !c.fetch_from && (c.required || c.in_list_view)).map((c) => [c.fieldname, sampleValue(c)]))] : []
    } else out[f.fieldname] = sampleValue(f)
  }
  return out
}

const q = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`
const enc = encodeURIComponent

/** Les routes d'un type, avec le droit qu'elles demandent et un `curl` prêt à copier. `base` : l'adresse de l'API (sans barre finale). */
export function endpoints(meta: DocTypeMeta, base: string): Endpoint[] {
  const dt = enc(meta.name)
  const rights = meta.rights
  const auth = `-H 'Authorization: Bearer $TOKEN' -H 'X-Tenant-ID: $TENANT'`
  const json = `-H 'Content-Type: application/json'`
  const one = (id: string, method: Endpoint['method'], path: string, title: string, needs: Action | null, body?: unknown, query = ''): Endpoint => ({
    id, method, path, title, needs, right: needs ? rights[needs] : null, ...(body !== undefined ? { body } : {}),
    curl: `curl -X ${method} ${q(`${base}${path}${query}`)} ${auth}${body !== undefined ? ` ${json} -d ${q(JSON.stringify(body))}` : ''}`,
  })
  const sample = sampleData(meta)
  const prompt = meta.naming === 'prompt'
  const out: Endpoint[] = [
    one('list', 'GET', `/list/${dt}`, t.apiDocs.list, 'read', undefined, `?filters=${enc('{"name":["like","A"]}')}&order_by=modified&order=desc&limit=20&offset=0`),
    one('get', 'GET', `/doc/${dt}/{name}`, t.apiDocs.get, 'read'),
  ]
  if (!meta.single) {
    out.push(one('create', 'POST', `/doc/${dt}`, t.apiDocs.create, 'create', prompt ? { name: 'mon-nom', data: sample } : { data: sample }))
  }
  out.push(one('update', 'PUT', `/doc/${dt}/{name}`, t.apiDocs.update, 'write', { version: 1, data: sample }))
  if (!meta.single) out.push(one('delete', 'DELETE', `/doc/${dt}/{name}`, t.apiDocs.remove, 'delete'))
  if (meta.submittable) {
    out.push(one('submit', 'POST', `/doc/${dt}/{name}/submit`, t.apiDocs.submit, 'submit', { version: 1 }))
    out.push(one('cancel', 'POST', `/doc/${dt}/{name}/cancel`, t.apiDocs.cancel, 'cancel', { version: 2 }))
    out.push(one('amend', 'POST', `/doc/${dt}/{name}/amend`, t.apiDocs.amend, 'create'))
  }
  if (meta.allow_rename) out.push(one('rename', 'POST', `/doc/${dt}/{name}/rename`, t.apiDocs.rename, 'write', { new_name: 'nouveau-nom' }))
  if (!meta.single) {
    out.push(one('bulk', 'POST', `/bulk/${dt}`, t.apiDocs.bulk, 'write', { action: 'update', names: ['A', 'B'], data: Object.fromEntries(Object.entries(sample).slice(0, 1)) }))
    out.push(one('connections', 'GET', `/connections/${dt}/{name}`, t.apiDocs.connections, 'read'))
  }
  out.push(one('search', 'GET', `/search/${dt}`, t.apiDocs.search, 'read', undefined, '?q=texte&limit=10'))
  out.push(one('aggregate', 'GET', `/aggregate/${dt}`, t.apiDocs.aggregate, 'read', undefined, `?group_by=${enc(meta.fields.find((f) => ['Select', 'Link', 'Boolean'].includes(f.fieldtype))?.fieldname ?? 'docstatus')}&metrics=count`))
  if (meta.track_changes !== false && !meta.virtual) out.push(one('history', 'GET', `/history/${dt}/{name}`, t.apiDocs.history, 'read'))
  return out.filter((e) => e.right !== 'none')
}

export interface FieldDoc {
  fieldname: string
  label: string
  type: string
  required: boolean
  /** Ce qui borne la valeur : les choix d'un Select, la cible d'un Link, les décimales… */
  detail: string
  /** Ce qu'on peut filtrer (`=`, `like`…), vide : le champ ne se filtre pas. */
  filter: readonly string[]
  /** Règles : unique, valeur posée une fois, lecture seule, copié d'un autre document… */
  rules: string[]
}

export function fieldDocs(meta: DocTypeMeta): FieldDoc[] {
  return meta.fields.map((f) => {
    const rules: string[] = []
    if (f.unique) rules.push(t.apiDocs.ruleUnique)
    if (f.set_only_once) rules.push(t.apiDocs.ruleOnce)
    if (f.read_only) rules.push(t.apiDocs.ruleReadOnly)
    if (f.fetch_from) rules.push(t.apiDocs.ruleFetched(f.fetch_from))
    if (f.allow_on_submit) rules.push(t.apiDocs.ruleAfterSubmit)
    if (f.hidden) rules.push(t.apiDocs.ruleHidden)
    let detail = ''
    if (Array.isArray(f.options)) detail = f.options.join(' | ')
    else if (typeof f.options === 'string') detail = f.fieldtype === 'DynamicLink' ? t.apiDocs.dynamicOf(f.options) : f.options
    if (f.decimals !== undefined) detail = `${detail ? `${detail} · ` : ''}${t.apiDocs.decimals(f.decimals)}`
    return {
      fieldname: f.fieldname,
      label: f.label ?? f.fieldname,
      type: f.fieldtype,
      required: f.required,
      detail,
      filter: FILTER_OPS[f.fieldtype] ?? [],
      rules,
    }
  })
}
