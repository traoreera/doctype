/** Formes des réponses de l'API de xcore-doctype (voir process/doctype.md §10 et src/xcore_doctype/document.py). */
import type { ExprNode } from './expr'

export type FieldType =
  | 'Text'
  | 'LongText'
  | 'Number'
  | 'Decimal'
  | 'Currency'
  | 'Rating'
  | 'Date'
  | 'Datetime'
  | 'Boolean'
  | 'Select'
  | 'Link'
  | 'Color'
  | 'Image'
  | 'Attach'
  | 'Table'
  | 'Int'
  | 'Percent'
  | 'Time'
  | 'Duration'
  | 'Code'
  | 'JSON'
  | 'TextEditor'
  | 'Password'
  | 'DynamicLink'

/** Deux types portent un nombre EXACT en chaîne canonique : tout ce qui vaut pour l'un vaut pour l'autre. */
export const isDecimalType = (t: FieldType): boolean => t === 'Decimal' || t === 'Currency'
export type Right = 'all' | 'restricted' | 'none'
export type Action = 'read' | 'write' | 'create' | 'delete' | 'submit' | 'cancel'

/** `docstatus` d'un document soumettable : brouillon, soumis (figé), annulé (figé). Un DocType ordinaire reste toujours à 0. */
export const DRAFT = 0
export const SUBMITTED = 1
export const CANCELLED = 2

export interface DocTypeSummary {
  name: string
  label: string
  module: string | null
  /** L'utilisateur peut créer ce type de document (l'accueil propose « Nouveau » seulement là). */
  create?: boolean
  /** Icône PrimeIcons sans préfixe (`receipt` pour `pi pi-receipt`). */
  icon?: string | null
  /** Document unique (des réglages) : pas de liste, on l'ouvre directement. */
  single?: boolean
  /** Source externe (xauth…) : le serveur ne stocke pas ces documents. */
  virtual?: boolean
  /** Un document par utilisateur (`naming: owner`, un profil) : jamais proposé dans « Créer », sa propre page l'ouvre. */
  per_user?: boolean
  /** `general` : un réglage GLOBAL de l'application, édité dans la rubrique « Général » des Paramètres (pas dans une rubrique à lui). */
  settings_group?: 'general'
}

/** Un état du workflow et l'état de document qu'il impose (0 brouillon, 1 soumis, 2 annulé). */
export interface WorkflowState {
  name: string
  docstatus?: 'draft' | 'submitted' | 'cancelled'
}

/** Une transition déclarée. Le Desk ne juge PAS qui peut la faire : il demande au serveur (`api.transitions`). */
export interface WorkflowTransition {
  action: string
  from: string
  to: string
  not_owner?: boolean
  /** Condition métier jugée par le SERVEUR sur le document stocké : le Desk ne l'évalue pas, il lit `api.transitions`. */
  condition?: string
  description?: string
}

export interface WorkflowDef {
  states: WorkflowState[]
  transitions: WorkflowTransition[]
}

/** Ce que l'utilisateur peut faire sur CE document, maintenant : le serveur seul le sait (grants hors du jeton, `not_owner`). */
export interface AvailableTransition {
  action: string
  to: string
  docstatus: number
  description?: string
}

export interface AvailableTransitions {
  state: string | null
  docstatus?: number
  version?: number
  transitions: AvailableTransition[]
}

/** Un type que l'on peut créer DEPUIS le document ouvert (process/mapper.md). Le Desk ne juge rien : une source
 *  entièrement servie n'est simplement pas dans la réponse du serveur, donc le bouton n'existe pas. */
export interface AvailableMapping {
  doctype: string
  label: string
}

export interface AvailableMappings {
  mappings: AvailableMapping[]
}

/** Les valeurs d'un brouillon prérempli depuis un autre document. Rien n'est écrit : le client enregistre ensuite. */
export interface MappedDraft {
  doctype: string
  from: { doctype: string; name: string }
  values: Record<string, unknown>
}

// ── Impression (process/impression.md) ─────────────────────
/** Un gabarit d'impression proposé pour un type. `source` dit d'où il vient : seul « organisation » s'édite. */
export interface PrintTemplateChoice {
  name: string
  label: string
  source: 'organisation' | 'plugin' | 'fabriqué'
}

/** Ce que le menu « Imprimer » propose. `pdf` : le moteur est installé côté serveur (sinon, l'aperçu seul). */
export interface PrintTemplates {
  doctype: string
  default: string
  pdf: boolean
  templates: PrintTemplateChoice[]
}

/** Le document rendu en HTML. `background` : un fond PDF complète ce rendu, mais seulement dans le PDF. */
export interface PrintedDocument {
  doctype: string
  name: string
  template: string
  html: string
  background: boolean
}

/** Les préférences d'affichage de l'organisation (nom, logo, couleur, format de date), servies à tout utilisateur identifié. */
export interface OrgPreferences {
  organization_name?: string
  brand_color?: string
  date_format?: string
  logo?: FileLink
}

/** Les blocs d'un espace de travail ou du tableau de bord d'un type (voir `dashboards.py`) : des DESCRIPTIONS, le Desk calcule. */
export type AggFunction = 'count' | 'sum' | 'avg' | 'min' | 'max'
export type ChartKind = 'bar' | 'line' | 'pie' | 'donut' | 'percentage'
export type BlockColor = 'blue' | 'green' | 'orange' | 'red' | 'purple' | 'teal' | 'gray'
/** Une teinte de la palette (nom), ou une couleur libre `#rrggbb`. */
export type BlockTone = BlockColor | (string & {})

/** Un titre de section d'un espace de travail. */
export interface WorkspaceHeader {
  type: 'header'
  text: string
}

export interface WorkspaceParagraph {
  type: 'paragraph'
  text: string
  cols?: number
}

export interface WorkspaceSpacer {
  type: 'spacer'
}

/** Un raccourci vers la liste d'un type de document ; `create` : l'utilisateur peut en créer un (le « + »). */
export interface WorkspaceShortcut {
  type: 'shortcut'
  doctype: string
  label: string
  icon?: string | null
  create: boolean
  single?: boolean
}

/** Un chiffre : combien de documents, ou la somme / moyenne / minimum / maximum d'un champ. */
export interface NumberCardBlock {
  type: 'number_card'
  label: string
  doctype: string
  function: AggFunction
  field?: string
  filters?: Filters
  color?: BlockTone
  cols?: number
}

/** Un graphique : la mesure PAR valeur d'un champ, ou par période si ce champ est une date. */
export interface ChartBlock {
  type: 'chart'
  label: string
  doctype: string
  kind: ChartKind
  group_by: string
  bucket?: 'day' | 'month' | 'year'
  periods?: number
  function: AggFunction
  field?: string
  filters?: Filters
  limit: number
  /** La teinte des barres ou de la courbe (`bar`, `line`) ; un camembert garde la palette. */
  color?: BlockTone
  cols?: number
}

/** Les derniers documents d'un type. */
export interface QuickListBlock {
  type: 'quick_list'
  label: string
  doctype: string
  filters?: Filters
  limit: number
  title_field?: string | null
  submittable?: boolean
  cols?: number
}

export interface LinksCardLink {
  doctype: string
  label: string
  icon?: string | null
}

export interface LinksCardBlock {
  type: 'card'
  title: string
  links: LinksCardLink[]
  cols?: number
}

/** Un bloc dessiné par une extension du Desk (D28) : `component` = `<plugin>.<nom>`, `props` lui est transmis tel quel. */
export interface ExtensionBlock {
  type: 'extension'
  component: string
  label?: string
  props?: Record<string, unknown>
  cols?: number
}

export type WorkspaceBlock =
  | WorkspaceHeader
  | WorkspaceParagraph
  | WorkspaceSpacer
  | WorkspaceShortcut
  | NumberCardBlock
  | ChartBlock
  | QuickListBlock
  | LinksCardBlock
  | ExtensionBlock

/** Ce qu'un tableau de bord de type accepte. */
export type DashboardBlock = WorkspaceHeader | NumberCardBlock | ChartBlock

/** La page d'accueil d'un module, déjà FILTRÉE par les droits de l'utilisateur (le serveur ne renvoie que ce qu'il peut voir). */
export interface Workspace {
  /** D'où vient la disposition : `user` (la mienne), `org` (celle de l'organisation), absent : celle du plugin. */
  layer?: 'user' | 'org' | null
  /** Je peux l'enregistrer pour toute l'organisation (j'écris ce qu'elle montre). */
  shareable?: boolean
  name: string
  label: string
  icon?: string | null
  description?: string | null
  blocks: WorkspaceBlock[]
}

// ── Agrégats ────────────────────────────────────────────────

export interface AggregateQuery {
  group_by?: string
  bucket?: 'day' | 'month' | 'year'
  /** `count`, `sum:champ`, `avg:champ`, `min:champ`, `max:champ`. */
  metrics?: string[]
  filters?: Filters
  periods?: number
  limit?: number
}

export interface AggregateMetric {
  function: AggFunction
  field: string | null
  label: string | null
  /** Decimal : le nombre de décimales (la valeur arrive en chaîne exacte). */
  decimals: number | null
}

export interface AggregateGroup {
  field: string
  bucket: 'day' | 'month' | 'year' | null
  fieldtype: FieldType | null
  /** Select : ses valeurs. Link : le DocType cible. */
  options?: string[] | string
  label?: string
}

export interface AggregateRow {
  key: string | number | boolean | null
  /** Un entier (`count`), une chaîne exacte (Decimal), un nombre, ou `null` (moyenne d'aucun document). */
  values: (number | string | null)[]
}

export interface AggregateResult {
  group: AggregateGroup | null
  metrics: AggregateMetric[]
  rows: AggregateRow[]
  /** Il y avait plus de groupes que la limite : seuls les premiers sont là. */
  truncated: boolean
}

// ── Vues d'un type ──────────────────────────────────────────

export type ViewName = 'list' | 'report' | 'dashboard' | 'kanban' | 'calendar' | 'gantt' | 'tree'

export interface ViewsMeta {
  available: ViewName[]
  default: ViewName
  kanban?: { field: string; card_fields: string[] }
  calendar?: { start: string; end?: string; title?: string; color?: string }
  gantt?: { start: string; end: string; title?: string; color?: string }
  tree?: { parent: string }
  dashboard: DashboardBlock[]
  /** D'où vient le tableau de bord : `user` (le mien), `org` (celui de l'organisation), absent : celui du type ou le généré. */
  dashboard_layer?: 'user' | 'org'
  /** Je peux enregistrer le tableau de bord pour toute l'organisation (j'écris ce type). */
  dashboard_shareable?: boolean
}

/** L'état exigé de la cible d'un lien : un état, une liste d'états, ou `not_cancelled` (brouillon ou soumis). */
export type TargetStatus = 'draft' | 'submitted' | 'cancelled' | 'not_cancelled' | ('draft' | 'submitted' | 'cancelled')[]

export interface FieldMeta {
  fieldname: string
  label?: string
  fieldtype: FieldType
  required: boolean
  default?: unknown
  /** Decimal et Currency : le nombre de chiffres après la virgule (Decimal l'exige, Currency en a 2 par défaut). */
  decimals?: number
  /** Image, Attach : la taille maximale d'un fichier, en Mo. */
  max_mb?: number
  /** Rating : le nombre d'étoiles. */
  stars?: number
  /** Text : un format contrôlé à l'écriture et rendu comme un lien (`mailto:`, `tel:`, `https://`). */
  format?: 'email' | 'phone' | 'url'
  /** L'unité affichée à côté d'une quantité : `uom` (un champ du même document ou de la même ligne) ou `item.uom` (champ de la cible d'un Link). */
  unit_from?: string
  /** Link : les documents qu'on peut choisir (D29). Une valeur, ou `$champ` pour celle d'un autre champ du document (ou de la ligne). Le serveur l'exige aussi à l'écriture. */
  link_filters?: Record<string, unknown>
  /** Select : la liste des valeurs. Link : le nom du DocType cible. Table : le nom du DocType des lignes (voir `children`). Currency : le code ISO (EUR). */
  options?: string[] | string
  description?: string
  hidden: boolean
  in_list_view: boolean
  /** Montré mais jamais modifiable par l'utilisateur (le serveur refuse une mise à jour qui le change). */
  read_only?: boolean
  /** Table seulement : une sélection multiple (des étiquettes à saisir et à retirer) au lieu d'une grille. */
  multiselect?: boolean
  /** Une valeur ne se retrouve jamais sur deux documents (le serveur refuse le doublon). */
  unique?: boolean
  /** Libre tant qu'il est vide, figé dès qu'il a une valeur. */
  set_only_once?: boolean
  /** Jamais recopié par « Dupliquer » ni par un amendement. */
  no_copy?: boolean
  /** Reste modifiable quand le document est soumis. */
  allow_on_submit?: boolean
  /** `lien.champ` : la valeur est LUE du document que le Link désigne (jamais saisie). */
  fetch_from?: string
  /** Link, DynamicLink : l'état que doit avoir la cible (D23) ; le serveur refuse les autres (`link_status`). Voir `lib/docstatus.allowedDocstatus`. */
  target_status?: TargetStatus
  /** Arbres compilés par le serveur : le navigateur ne parse jamais de chaîne. */
  depends_on?: ExprNode
  mandatory_depends_on?: ExprNode
  read_only_depends_on?: ExprNode
}

export interface LayoutSection {
  /** L'onglet de la section, sur une fiche à onglets (absent sinon) : les sections d'un onglet se suivent. */
  tab?: string | null
  section: string | null
  collapsible: boolean
  collapsed: boolean
  columns: string[][]
}

/** Pour qui on enregistre une disposition : `user` (la mienne seulement) ou `org` (toute l'organisation). */
export type LayoutScope = 'user' | 'org'

/**
 * Les retouches d'un champ par l'habillage d'un formulaire. `label`, `hidden`, `description` ne changent que l'affichage ; `read_only`, `mandatory`
 * et `default` (création seulement) sont APPLIQUÉS par le serveur à l'écriture pour l'organisation, et ne font que resserrer le YAML.
 */
export interface FieldTweak {
  label?: string
  hidden?: boolean
  description?: string
  read_only?: boolean
  mandatory?: boolean
  default?: unknown
}

/** Ce que la DÉFINITION (le YAML) impose à un champ, habillage à part (`GET /override/form`, clé `definition` : seuls les champs qui en ont). */
export interface FieldBase {
  required?: boolean
  read_only?: boolean
  hidden?: boolean
  default?: unknown
}

/** Habillage d'un formulaire (`xdt_layout_override`, genre `form`) : disposition et retouches de champ, par-dessus le YAML. */
export interface FormOverride {
  layout?: LayoutSection[]
  fields?: Record<string, FieldTweak>
}

/** Ce qu'une organisation RESSERRE sur une transition (genre `workflow`, process/programme.md §3.6) : jamais un desserrage. */
export interface TransitionTweak {
  closed?: boolean
  condition?: string
  not_owner?: boolean
}

/** Habillage d'automate : des retouches par ACTION, sur les transitions que le YAML déclare déjà. */
export interface WorkflowOverride {
  transitions?: Record<string, TransitionTweak>
}

/** Habillage d'un espace de travail (genre `workspace`) : le corps d'un espace, sans `name`/`schema_version` (ils viennent de la cible). */
export interface WorkspaceOverride {
  label?: string
  icon?: string | null
  description?: string | null
  order?: number
  blocks?: WorkspaceBlock[]
}

/** Les lignes d'un champ Table : un DocType « enfant », sans droits ni routes propres. */
export interface ChildMeta {
  name: string
  label?: string
  fields: FieldMeta[]
}

export interface DocTypeMeta {
  name: string
  label?: string
  module?: string
  title_field?: string
  /** Document unique : un seul par organisation, nommé comme son type ; il s'ouvre sans liste et ne se supprime pas. */
  single?: boolean
  /** `general` : réglage global, édité dans la rubrique « Général » des Paramètres. */
  settings_group?: 'general'
  /** Le champ Image qui sert de photo au document (l'avatar de la liste, de la fiche, des cartes). */
  image_field?: string
  fields: FieldMeta[]
  layout: LayoutSection[]
  list_columns: string[]
  /** Les vues proposées (liste, rapport, tableau de bord, et celles que le type déclare). Absent d'un serveur plus ancien : liste seule. */
  views?: ViewsMeta
  rights: Record<Action, Right>
  /** `false` : ce DocType n'enregistre pas d'historique. */
  track_changes?: boolean
  /** DocType VIRTUEL : ses documents vivent chez une source externe (xauth…), le serveur ne les stocke pas : ni date de modification, ni historique. */
  virtual?: boolean
  source?: string
  /** Brouillon -> soumis -> annulé : un document soumis est figé (voir `docstatus`). */
  submittable?: boolean
  /** L'automate métier du type : c'est lui qui soumet et annule, `/submit` est alors refusé par le serveur. */
  workflow?: WorkflowDef
  /** Fuseau de l'organisation : « aujourd'hui » (défaut `today` des dates) s'y lit, comme sur le serveur. */
  timezone?: string
  /** `"hash"` : un identifiant aléatoire (sans sens pour un humain) ; `{series}` : un numéro lisible (`FA-2026-00001`). */
  naming?: 'hash' | 'owner' | 'prompt' | { series: string } | { field: string }
  /** Le document se renomme (`POST …/rename`) : les documents qui le citent suivent. */
  allow_rename?: boolean
  /** Par nom de DocType enfant, pour les champs Table de ce DocType. */
  children?: Record<string, ChildMeta>
}

export type DocData = Record<string, unknown>

export interface DocRecord {
  doctype: string
  name: string
  owner: string
  creation: string
  modified: string
  version: number
  /** 0 brouillon, 1 soumis, 2 annulé. */
  docstatus: number
  /** Le document annulé dont celui-ci est l'amendement. */
  amended_from: string | null
  /** L'état du workflow, pour un type qui en a un (absent sinon). */
  workflow_state?: string | null
  data: DocData
}

export interface ListResult {
  items: DocRecord[]
  total: number
  limit: number
  offset: number
}

/** Une entrée d'historique : la DIFFÉRENCE d'une création (`old` nul partout), d'une modification ou d'une suppression. */
export interface VersionEntry {
  id: number
  version: number
  action: 'create' | 'update' | 'delete' | 'submit' | 'cancel' | 'rename'
  user: string
  owner: string
  creation: string
  changes: Record<string, { old: unknown; new: unknown }>
}

export interface HistoryResult {
  items: VersionEntry[]
  total: number
  limit: number
  offset: number
}

export interface SearchItem {
  name: string
  title: string | null
}

/** Un résultat de la recherche GLOBALE (Awesomebar) : un document, son type et le libellé de ce type. */
export interface GlobalSearchItem {
  doctype: string
  label: string
  name: string
  title: string | null
}

/** Une valeur (égalité) ou `[opérateur, valeur]` : `!=`, `like`, `>`, `>=`, `<`, `<=`, `in`. */
export type FilterValue = unknown
export type Filters = Record<string, FilterValue>

export interface ListQuery {
  filters?: Filters
  order_by?: string
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

/** La configuration d'une liste, telle qu'une vue sauvegardée la rejoue. */
export interface ViewConfig {
  columns?: string[]
  filters?: Filters
  order_by?: string
  descending?: boolean
}

/** Une vue de liste sauvegardée : privée à son propriétaire, rejouable sur le type. */
export interface SavedView {
  name: string
  label: string
  kind: ViewName
  config: ViewConfig
  is_default: boolean
  owner: string
  creation: string
  modified: string
}

export interface SavedViewInput {
  label: string
  kind?: ViewName
  config?: ViewConfig
  is_default?: boolean
  /** En cas de renommage : l'ancien nom, supprimé après la copie. */
  previous?: string
}

/** Un nœud de la forêt d'un type arborescent (`views.tree`). */
export interface TreeNode {
  name: string
  title: string
  children: TreeNode[]
}

export interface TreeResult {
  parent: string
  items: TreeNode[]
  total: number
  truncated: boolean
}

/** Opérateurs permis par type de champ : miroir de `builder.FILTER_OPS` côté serveur. */
export const FILTER_OPS: Record<FieldType, readonly string[]> = {
  Text: ['=', '!=', 'like', 'in'],
  Select: ['=', '!=', 'in'],
  Link: ['=', '!=', 'like', 'in'],
  Number: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Decimal: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Currency: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Rating: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Datetime: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  LongText: ['like'],
  Color: ['=', '!=', 'in'],
  Image: [], // un identifiant de fichier ne se filtre pas
  Attach: [],
  Date: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Boolean: ['=', '!='],
  Table: [], // un tableau de lignes ne se filtre ni ne se trie
  Int: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Percent: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Duration: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Time: ['=', '!=', '>', '>=', '<', '<=', 'in'],
  Code: ['like'],
  JSON: [],
  TextEditor: [], // du HTML ne se filtre pas
  Password: [], // un secret ne se compare jamais
  DynamicLink: ['=', '!=', 'like', 'in'],
}

/** Ce que le serveur rend d'un téléversement. */
export interface FileMeta {
  id: string
  filename: string
  content_type: string
  size: number
}

/**
 * Une ligne de l'écran « Fichiers » (`GET /file-library`) : un fichier ET le document qui le cite (un fichier que deux documents citent donne
 * deux lignes). `attachment` : l'identifiant de la pièce jointe, `null` pour un fichier de champ (`field` est alors le champ, `version` celle du document).
 */
export interface FileLibraryItem {
  file_id: string
  filename: string
  content_type: string
  size: number
  owner: string
  creation: string
  doctype: string
  doctype_label: string
  docname: string
  field: string
  field_label: string | null
  attachment: string | null
  version: number | null
  docstatus: number
  /** Le type garde-t-il un historique (`track_changes`) : détacher un champ y laisse alors une trace. */
  track_changes: boolean
  can_detach: boolean
}

export interface FileLibraryResult {
  items: FileLibraryItem[]
  total: number
  limit: number
  offset: number
  /** Les types qui citent des fichiers lisibles (selon la recherche, pas selon le filtre de type) : de quoi proposer le filtre. */
  doctypes: { name: string; label: string; count: number }[]
  /** Des types dont la source ne répond pas : leurs pièces jointes manquent à la liste (on le dit, par leur libellé). */
  unavailable: { name: string; label: string }[]
}

export interface FileLibraryQuery {
  doctype?: string | null
  q?: string
  order_by?: 'creation' | 'filename' | 'size'
  order?: 'asc' | 'desc'
  limit?: number
  offset?: number
}

/** Détacher : une pièce jointe par son identifiant, un fichier de champ par le document qui le cite et la version qu'on en a lue. */
export type DetachTarget = { attachment: string } | { doctype: string; name: string; field: string; file_id: string; version: number }

/** Un lien signé de courte durée vers un fichier : `path` est relatif à la racine de l'API. */
export interface FileLink {
  path: string
  filename: string
  content_type: string
  size: number
  /** Secondes epoch : au-delà, le lien ne sert plus. */
  expires: number
}


// ── Collaboration : commentaires, assignations, pièces jointes d'un document ─────────────────────────

export interface CommentItem {
  id: string
  doctype: string
  docname: string
  owner: string
  content: string
  edited: boolean
  creation: string
  modified: string
}

export type AssignmentStatus = 'open' | 'done' | 'cancelled'

export interface AssignmentItem {
  id: string
  doctype: string
  docname: string
  assignee: string
  assigned_by: string
  description: string | null
  due_date: string | null
  status: AssignmentStatus
  creation: string
  modified: string
  /** Présent dans « mes tâches » : le libellé du type du document. */
  doctype_label?: string
}

export interface AttachmentItem {
  id: string
  file_id: string
  owner: string
  creation: string
  filename: string
  content_type: string
  size: number
}

export interface Collab {
  comments: CommentItem[]
  assignments: AssignmentItem[]
  attachments: AttachmentItem[]
  /** Je peux assigner et joindre (j'écris ce document). */
  can_assign: boolean
  can_attach: boolean
  /** Mon identité : ce qui me désigne comme auteur ou assigné. */
  me: string
}

/** Une action en lot, document par document : `ok`, ou le code et le message du refus (écrit pour l'utilisateur). */
export interface BulkItem {
  name: string
  ok: boolean
  code?: string
  message?: string
  details?: { field?: string; msg?: string }[]
}

/** Un partage d'un document avec un membre (`write` : il peut aussi le modifier). */
export interface ShareItem {
  member: string
  write: boolean
  owner: string
  creation: string
}

export interface SharesResult {
  items: ShareItem[]
  /** L'appelant peut-il partager ou retirer un partage (il peut ÉCRIRE le document) ? Sinon il ne voit que le sien. */
  can_share: boolean
  me: string
}

/** Un document partagé avec moi (« Partagés avec moi »). */
export interface SharedDoc {
  doctype: string
  label: string
  name: string
  title: string | null
  write: boolean
  owner: string
  creation: string
}

/** Une ligne d'un import CSV : `row` est son numéro dans le fichier (l'en-tête est la ligne 1). */
export interface ImportItem {
  row: number
  ok: boolean
  name?: string
  code?: string
  message?: string
  details?: { field?: string; msg?: string }[]
}

export interface ImportResult {
  results: ImportItem[]
  done: number
  failed: number
}

/** Une étiquette en usage et son nombre de documents (lisibles, dans les filtres demandés). */
export interface TagCount {
  tag: string
  count: number
}

export interface BulkResult {
  results: BulkItem[]
  done: number
  failed: number
}

/** Les documents d'un type qui en citent un autre : `table` renseigné = par les lignes d'un champ Table (la liste ne s'y filtre pas). */
export interface Connection {
  doctype: string
  label: string
  field: string
  field_label: string
  table: string | null
  count: number
  /** DynamicLink : le champ qui dit DANS quel type de document le lien est lu (la liste filtrée pose aussi celui-là). */
  type_field?: string
}

/** Une personne de l'organisation : `id` est l'identité (un UUID chez xauth, jamais fait pour être lu), `name` ce qu'on affiche (l'e-mail). */
export interface Person {
  id: string
  email: string | null
  name: string
}
