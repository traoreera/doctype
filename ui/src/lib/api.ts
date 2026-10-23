import type { AssignmentItem, AssignmentStatus, BulkResult, Connection, Collab, CommentItem, AttachmentItem, FileLink, FileMeta, AggregateQuery, AggregateResult, DocData, DocRecord, DocTypeMeta, DocTypeSummary, FieldBase, FormOverride, GlobalSearchItem, HistoryResult, LayoutScope, ListQuery, OrgPreferences, Person, ListResult, SavedView, SavedViewInput, SearchItem, TreeResult, Workspace, WorkspaceOverride } from './types'
import type { HttpMethod, HttpResponse, Transport } from './transport'

/** Une réponse d'erreur du serveur, normalisée : le Desk ne voit jamais les quatre formes que xcore peut renvoyer. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: ApiErrorDetail[] = [],
  ) {
    super(message)
    this.name = 'ApiError'
  }

  get isConflict(): boolean {
    return this.status === 409 && this.code === 'version_conflict'
  }

  /** Le document a changé d'état entre-temps (soumis ailleurs, déjà annulé…) : ce que l'écran montre est périmé. */
  get isStale(): boolean {
    return this.status === 409 && (this.code === 'invalid_docstatus' || this.code === 'document_locked')
  }
}

export interface ApiErrorDetail {
  field?: string
  msg?: string
  type?: string
  [key: string]: unknown
}

/**
 * Formes d'erreur rencontrées :
 *  - xcore-doctype : `{"detail": {"code", "msg", "details"}}`
 *  - authentification d'xcore/xauth : `{"detail": "Token manquant"}`
 *  - TenantMiddleware d'xcore : `{"error": "invalid_tenant", "detail": "..."}`
 *  - validation FastAPI : `{"detail": [{"loc", "msg", "type"}, ...]}`
 */
export function normalizeError(status: number, body: unknown): ApiError {
  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  const detail = b.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const d = detail as Record<string, unknown>
    const details = Array.isArray(d.details) ? (d.details as ApiErrorDetail[]) : d.details && typeof d.details === 'object' ? [d.details as ApiErrorDetail] : []
    return new ApiError(status, String(d.code ?? `http_${status}`), String(d.msg ?? d.code ?? `Erreur ${status}`), details)
  }
  if (typeof detail === 'string') {
    return new ApiError(status, typeof b.error === 'string' ? b.error : `http_${status}`, detail)
  }
  if (Array.isArray(detail)) {
    const details = detail.map((e: Record<string, unknown>) => ({
      field: Array.isArray(e.loc) ? e.loc.filter((p) => p !== 'body').join('.') : undefined,
      msg: String(e.msg ?? ''),
      type: String(e.type ?? ''),
    }))
    return new ApiError(status, 'validation_failed', 'Requête invalide', details)
  }
  return new ApiError(status, `http_${status}`, `Erreur ${status}`)
}

export interface ClientContext {
  /** Racine de l'API de xcore-doctype, sans barre finale (ex. `/app/xcore-doctype`). */
  baseUrl: string
  token: string | null
  tenant: string | null
}

export class ApiClient {
  constructor(
    private readonly transport: Transport,
    private readonly context: () => ClientContext,
    /** Appelé sur un 401. Vrai : la session s'est renouvelée, la requête est rejouée UNE fois avec le nouveau jeton. */
    private readonly onUnauthorized: () => boolean | Promise<boolean> | void = () => false,
  ) {}

  // ── Préférences de l'organisation ───────────────────────
  /** Nom, logo, couleur et format de date de l'organisation : lisibles par tout utilisateur identifié (les réglages, eux, par un administrateur). */
  preferences(): Promise<OrgPreferences> {
    return this.call('GET', '/preferences')
  }

  // ── Métadonnées ─────────────────────────────────────────
  doctypes(): Promise<DocTypeSummary[]> {
    return this.call('GET', '/meta')
  }

  workspaces(): Promise<Workspace[]> {
    return this.call('GET', '/workspaces')
  }

  meta(doctype: string): Promise<DocTypeMeta> {
    return this.call('GET', `/meta/${encodeURIComponent(doctype)}`)
  }

  // ── Documents ───────────────────────────────────────────
  list(doctype: string, q: ListQuery = {}): Promise<ListResult> {
    const params = new URLSearchParams()
    if (q.filters && Object.keys(q.filters).length) params.set('filters', JSON.stringify(q.filters))
    if (q.order_by) params.set('order_by', q.order_by)
    if (q.order) params.set('order', q.order)
    if (q.limit !== undefined) params.set('limit', String(q.limit))
    if (q.offset !== undefined) params.set('offset', String(q.offset))
    return this.call('GET', `/list/${encodeURIComponent(doctype)}`, { params })
  }

  /** Compter, additionner, regrouper (cartes de nombres, graphiques, rapport) : ce que l'utilisateur pourrait lire, rien de plus. */
  aggregate(doctype: string, q: AggregateQuery = {}): Promise<AggregateResult> {
    const params = new URLSearchParams()
    if (q.group_by) params.set('group_by', q.group_by)
    if (q.bucket) params.set('bucket', q.bucket)
    if (q.metrics?.length) params.set('metrics', q.metrics.join(','))
    if (q.filters && Object.keys(q.filters).length) params.set('filters', JSON.stringify(q.filters))
    if (q.periods !== undefined) params.set('periods', String(q.periods))
    if (q.limit !== undefined) params.set('limit', String(q.limit))
    return this.call('GET', `/aggregate/${encodeURIComponent(doctype)}`, { params })
  }

  /** Déposer un fichier pour un champ Image ou Attach. Retourne son identifiant, que le document citera. */
  upload(doctype: string, fieldname: string, file: File): Promise<FileMeta> {
    const body = new FormData()
    body.append('file', file, file.name)
    return this.call('POST', `/upload/${encodeURIComponent(doctype)}/${encodeURIComponent(fieldname)}`, { body })
  }

  // ── Collaboration : commentaires, assignations, pièces jointes ───────────────────────
  private doc(doctype: string, name: string): string {
    return `${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`
  }

  collab(doctype: string, name: string): Promise<Collab> {
    return this.call('GET', `/collab/${this.doc(doctype, name)}`)
  }

  addComment(doctype: string, name: string, content: string): Promise<CommentItem> {
    return this.call('POST', `/comments/${this.doc(doctype, name)}`, { body: { content } })
  }

  editComment(id: string, content: string): Promise<CommentItem> {
    return this.call('PUT', `/comments/${encodeURIComponent(id)}`, { body: { content } })
  }

  async deleteComment(id: string): Promise<void> {
    await this.call('DELETE', `/comments/${encodeURIComponent(id)}`)
  }

  assign(doctype: string, name: string, body: { assignee: string; description?: string | null; due_date?: string | null }): Promise<AssignmentItem> {
    return this.call('POST', `/assignments/${this.doc(doctype, name)}`, { body })
  }

  updateAssignment(id: string, patch: { status?: AssignmentStatus; description?: string | null; due_date?: string | null }): Promise<AssignmentItem> {
    return this.call('PUT', `/assignments/${encodeURIComponent(id)}`, { body: patch })
  }

  /** Ce qui m'est confié (`status` : `open`, `done`, `cancelled`, ou `all`). */
  async myAssignments(status: AssignmentStatus | 'all' = 'open'): Promise<AssignmentItem[]> {
    return (await this.call<{ items: AssignmentItem[] }>('GET', '/my-assignments', { params: new URLSearchParams({ status }) })).items
  }

  /** Des personnes à qui assigner un document de ce type : les membres de l'organisation (xauth), sinon des identités déjà vues. */
  async people(doctype: string, q = ''): Promise<Person[]> {
    return (await this.call<{ items: Person[] }>('GET', `/people/${encodeURIComponent(doctype)}`, { params: new URLSearchParams({ q }) })).items
  }

  /** Des membres à citer (`@adresse`) dans un commentaire de ce document : il suffit de pouvoir le lire. `directory: false` : pas d'annuaire, personne à proposer. */
  async mentionable(doctype: string, name: string, q = ''): Promise<{ items: Person[]; directory: boolean }> {
    return this.call('GET', `/mentionable/${this.doc(doctype, name)}`, { params: new URLSearchParams({ q }) })
  }

  /** De quoi afficher des identifiants (UUID) : `{ id: Person }`, les membres de l'organisation seulement. */
  async users(ids: readonly string[]): Promise<Record<string, Person>> {
    const params = new URLSearchParams()
    for (const id of ids) params.append('ids', id)
    return (await this.call<{ items: Record<string, Person> }>('GET', '/users', { params })).items
  }

  attach(doctype: string, name: string, file: File): Promise<AttachmentItem> {
    const body = new FormData()
    body.append('file', file, file.name)
    return this.call('POST', `/attachments/${this.doc(doctype, name)}`, { body })
  }

  async detach(id: string): Promise<void> {
    await this.call('DELETE', `/attachments/${encodeURIComponent(id)}`)
  }

  /** Des liens signés (courte durée) pour afficher ou télécharger des fichiers. Un fichier qu'on ne peut pas lire est absent de la réponse. */
  fileLinks(ids: string[]): Promise<Record<string, FileLink>> {
    return this.call('POST', '/file-links', { body: { ids } })
  }

  history(doctype: string, name: string, page: { limit?: number; offset?: number } = {}): Promise<HistoryResult> {
    const params = new URLSearchParams()
    if (page.limit !== undefined) params.set('limit', String(page.limit))
    if (page.offset !== undefined) params.set('offset', String(page.offset))
    return this.call('GET', `/history/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`, { params })
  }

  get(doctype: string, name: string): Promise<DocRecord> {
    return this.call('GET', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`)
  }

  /** `name` : le nom choisi, pour un DocType dont les noms se saisissent (`naming: prompt`). */
  create(doctype: string, data: DocData, name?: string): Promise<DocRecord> {
    return this.call('POST', `/doc/${encodeURIComponent(doctype)}`, { body: name === undefined ? { data } : { data, name } })
  }

  /** Une même action sur plusieurs documents (`update` avec `data`, `delete`, `submit`, `cancel`) : le compte rendu dit, pour chacun, s'il est passé. */
  bulk(doctype: string, action: 'update' | 'delete' | 'submit' | 'cancel', names: readonly string[], data?: Record<string, unknown>): Promise<BulkResult> {
    return this.call('POST', `/bulk/${encodeURIComponent(doctype)}`, { body: data === undefined ? { action, names } : { action, names, data } })
  }

  /** Assigne plusieurs documents à la même personne. */
  assignMany(doctype: string, names: readonly string[], body: { assignee: string; description?: string | null; due_date?: string | null }): Promise<BulkResult> {
    return this.call('POST', `/assign-many/${encodeURIComponent(doctype)}`, { body: { names, ...body } })
  }

  /** Les documents qui citent celui-ci (par type et par champ), avec leur nombre. */
  async connections(doctype: string, name: string): Promise<Connection[]> {
    return (await this.call<{ items: Connection[] }>('GET', `/connections/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`)).items
  }

  /** Renomme un brouillon : les documents qui le citent suivent. */
  rename(doctype: string, name: string, newName: string): Promise<DocRecord> {
    return this.call('POST', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}/rename`, { body: { new_name: newName } })
  }

  update(doctype: string, name: string, version: number, data: DocData): Promise<DocRecord> {
    return this.call('PUT', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`, { body: { version, data } })
  }

  /** Soumettre : brouillon -> soumis. `version` : celle que le client a lue (comme une modification). */
  submit(doctype: string, name: string, version: number): Promise<DocRecord> {
    return this.call('POST', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}/submit`, { body: { version } })
  }

  cancel(doctype: string, name: string, version: number): Promise<DocRecord> {
    return this.call('POST', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}/cancel`, { body: { version } })
  }

  /** Une copie en brouillon d'un document annulé. */
  amend(doctype: string, name: string): Promise<DocRecord> {
    return this.call('POST', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}/amend`)
  }

  async remove(doctype: string, name: string): Promise<void> {
    await this.call('DELETE', `/doc/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`)
  }

  async search(doctype: string, q: string, limit = 10): Promise<SearchItem[]> {
    const params = new URLSearchParams({ q, limit: String(limit) })
    const res = await this.call<{ items: SearchItem[] }>('GET', `/search/${encodeURIComponent(doctype)}`, { params })
    return res.items
  }

  /** Recherche globale (Awesomebar) : les documents lisibles contenant `q`, tous types confondus. */
  async globalSearch(q: string, limit = 10): Promise<GlobalSearchItem[]> {
    const params = new URLSearchParams({ q, limit: String(limit) })
    const res = await this.call<{ items: GlobalSearchItem[] }>('GET', '/search', { params })
    return res.items
  }

  /** La forêt d'un type arborescent : chaque document lisible rattaché à son parent. */
  async tree(doctype: string): Promise<TreeResult> {
    return this.call('GET', `/tree/${encodeURIComponent(doctype)}`)
  }

  // ── Vues sauvegardées (phase C) ─────────────────────────────
  /** Les vues de liste privées de l'appelant pour ce type. */
  async listViews(doctype: string): Promise<SavedView[]> {
    const res = await this.call<{ items: SavedView[] }>('GET', `/views/${encodeURIComponent(doctype)}`)
    return res.items
  }

  /** Crée ou remplace une vue (`previous` : ancien nom, en cas de renommage). */
  saveView(doctype: string, name: string, body: SavedViewInput): Promise<SavedView> {
    return this.call('POST', `/views/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`, { body })
  }

  async removeView(doctype: string, name: string): Promise<void> {
    await this.call('DELETE', `/views/${encodeURIComponent(doctype)}/${encodeURIComponent(name)}`)
  }

  // ── Habillages (overrides) ─────────────────────────────────
  /** L'habillage de formulaire du tenant pour ce type (vide s'il n'y en a pas). */
  /** `definition` : ce que le YAML impose à chaque champ (l'éditeur grise d'après lui ce qu'un habillage ne desserre pas). */
  formOverride(doctype: string): Promise<{ doctype: string; config: FormOverride; definition?: Record<string, FieldBase> }> {
    return this.call('GET', `/override/form/${encodeURIComponent(doctype)}`)
  }

  saveFormOverride(doctype: string, config: FormOverride): Promise<{ doctype: string; config: FormOverride }> {
    return this.call('PUT', `/override/form/${encodeURIComponent(doctype)}`, { body: { config } })
  }

  async removeFormOverride(doctype: string): Promise<void> {
    await this.call('DELETE', `/override/form/${encodeURIComponent(doctype)}`)
  }

  /** Le tableau de bord personnalisé d'un type (ses blocs, sans `doctype` : c'est implicite). */
  saveDashboardOverride(doctype: string, config: { blocks: unknown[] }, scope: LayoutScope = 'org'): Promise<{ doctype: string; config: { blocks: unknown[] } }> {
    return this.call('PUT', `/override/dashboard/${encodeURIComponent(doctype)}`, { body: { config }, params: new URLSearchParams({ scope }) })
  }

  async removeDashboardOverride(doctype: string, scope: LayoutScope = 'org'): Promise<void> {
    await this.call('DELETE', `/override/dashboard/${encodeURIComponent(doctype)}`, { params: new URLSearchParams({ scope }) })
  }

  /** `scope` : `user` = ma disposition (il suffit de lire ce qu'elle montre), `org` = celle de toute l'organisation (il faut l'écrire). */
  saveWorkspaceOverride(name: string, config: WorkspaceOverride, scope: LayoutScope = 'org'): Promise<{ name: string; config: WorkspaceOverride }> {
    return this.call('PUT', `/override/workspace/${encodeURIComponent(name)}`, { body: { config }, params: new URLSearchParams({ scope }) })
  }

  async removeWorkspaceOverride(name: string, scope: LayoutScope = 'org'): Promise<void> {
    await this.call('DELETE', `/override/workspace/${encodeURIComponent(name)}`, { params: new URLSearchParams({ scope }) })
  }

  // ── Interne ─────────────────────────────────────────────
  protected async call<T>(method: HttpMethod, path: string, opts: { params?: URLSearchParams; body?: unknown; retried?: boolean } = {}): Promise<T> {
    const { baseUrl, token, tenant } = this.context()
    const query = opts.params && [...opts.params.keys()].length ? `?${opts.params.toString()}` : ''
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (token) headers.Authorization = `Bearer ${token}`
    if (tenant) headers['X-Tenant-ID'] = tenant
    if (opts.body !== undefined && !(typeof FormData !== 'undefined' && opts.body instanceof FormData)) headers['Content-Type'] = 'application/json'
    const res: HttpResponse = await this.transport.request({
      method,
      url: `${baseUrl}${path}${query}`,
      headers,
      ...(opts.body !== undefined ? { body: opts.body } : {}),
    })
    if (res.status >= 200 && res.status < 300) return res.json as T
    const err = normalizeError(res.status, res.json)
    if (res.status === 401) {
      // Jeton périmé : on tente de le renouveler (une seule fois, sinon une session révoquée bouclerait) avant d'abandonner.
      if ((await this.onUnauthorized()) === true && !opts.retried) return this.call(method, path, { ...opts, retried: true })
    }
    throw err
  }
}

/**
 * Les routes HTTP d'un PLUGIN (pas celles de xcore-doctype), avec la même session : jeton, organisation, renouvellement sur un 401.
 * C'est le `plugin` qu'une extension du Desk reçoit (D28) : `desk.plugin.get('/pipeline')` appelle `<préfixe>/<plugin>/pipeline`.
 */
export class PluginClient {
  private readonly raw: RawClient

  constructor(transport: Transport, context: () => ClientContext, onUnauthorized?: () => boolean | Promise<boolean> | void) {
    this.raw = new RawClient(transport, context, onUnauthorized)
  }

  get<T = unknown>(path: string, query?: Record<string, string | number | boolean>): Promise<T> {
    const params = new URLSearchParams()
    for (const [k, v] of Object.entries(query ?? {})) params.set(k, String(v))
    return this.raw.request('GET', pluginPath(path), { params })
  }

  post<T = unknown>(path: string, body?: unknown): Promise<T> {
    return this.raw.request('POST', pluginPath(path), body === undefined ? {} : { body })
  }

  put<T = unknown>(path: string, body?: unknown): Promise<T> {
    return this.raw.request('PUT', pluginPath(path), body === undefined ? {} : { body })
  }

  patch<T = unknown>(path: string, body?: unknown): Promise<T> {
    return this.raw.request('PATCH', pluginPath(path), body === undefined ? {} : { body })
  }

  delete<T = unknown>(path: string): Promise<T> {
    return this.raw.request('DELETE', pluginPath(path))
  }
}

/** Le transport authentifié d'`ApiClient` (jeton, organisation, renouvellement), sans ses méthodes de documents. */
class RawClient extends ApiClient {
  request<T>(method: HttpMethod, path: string, opts: { params?: URLSearchParams; body?: unknown } = {}): Promise<T> {
    return this.call(method, path, opts)
  }
}

/**
 * Un chemin RELATIF au plugin : jamais une URL, jamais une remontée vers les routes d'un autre plugin. Jugé comme le lira le parseur
 * d'URL du navigateur (WHATWG) : il retire tabulations et sauts de ligne, lit `%2e%2e` comme `..`, et un `..` suivi de `?` remonte aussi.
 */
function pluginPath(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`
  const bad = () => new TypeError(`chemin de plugin invalide : ${path}`)
  if (/[\u0000-\u001f\u007f\\#]/.test(p) || p.startsWith('//') || /^\/[a-z][a-z0-9+.-]*:/i.test(p)) throw bad()
  const pathname = p.split('?', 1)[0]!
  for (const segment of pathname.split('/')) {
    let decoded: string
    try {
      decoded = decodeURIComponent(segment)
    } catch {
      throw bad()
    }
    if (decoded === '.' || decoded === '..' || decoded.includes('/') || decoded.includes('\\')) throw bad()
  }
  return p
}

/** La racine des routes d'un plugin : celle de xcore-doctype (`/app/xcore-doctype`) donne le préfixe d'xcore (`/app`). */
export function pluginBase(apiBase: string, plugin: string): string {
  const i = apiBase.lastIndexOf('/')
  return `${i >= 0 ? apiBase.slice(0, i) : ''}/${plugin}`
}

