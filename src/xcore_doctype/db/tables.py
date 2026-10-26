"""Tables système (doc/doctype.md §7.2, §7.3).

La table générique `Document` (colonne JSON) et, depuis la phase 3, le compteur des séries de numérotation. Les
`MetaData` sont construites au `on_load` et jetées au `on_unload` (D8) : jamais de table au niveau du module.
Toute table porte `tenant_id` dans sa clé : `Query` est la seule à les lire ou les écrire, avec le tenant du contexte.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB

JsonType = JSON().with_variant(JSONB, "postgresql")

DOCUMENT_TABLE = "xdt_document"
NAMING_TABLE = "xdt_naming_series"
VERSION_TABLE = "xdt_version"
SAVED_VIEW_TABLE = "xdt_saved_view"
OVERRIDE_TABLE = "xdt_layout_override"
FILE_TABLE = "xdt_file"
COMMENT_TABLE = "xdt_comment"
ASSIGNMENT_TABLE = "xdt_assignment"
ATTACHMENT_TABLE = "xdt_attachment"
UNIQUE_TABLE = "xdt_unique"
TAG_TABLE = "xdt_tag"
SHARE_TABLE = "xdt_share"


@dataclass(frozen=True, slots=True)
class SystemTables:
    metadata: MetaData
    documents: Table
    naming: Table
    versions: Table
    saved_views: Table
    overrides: Table
    files: Table
    comments: Table
    assignments: Table
    attachments: Table
    uniques: Table
    tags: Table
    shares: Table


def build_system_tables() -> SystemTables:
    md = MetaData()
    documents = Table(
        DOCUMENT_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("name", String(140), nullable=False),
        Column("data", JsonType, nullable=False),
        Column("owner", String(255), nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        Column("modified", DateTime(timezone=True), nullable=False),
        Column("version", Integer, nullable=False),
        # Documents soumettables : 0 brouillon, 1 soumis, 2 annulé. Une vraie colonne (pas une clé de `data`) : l'UPDATE d'une
        # modification porte `docstatus = 0` dans son WHERE, un document figé ne peut donc pas être écrit par une course.
        # `server_default` : les lignes d'une base antérieure à cette colonne sont des brouillons (db/access.py l'ajoute).
        Column("docstatus", SmallInteger, nullable=False, server_default="0"),
        Column("amended_from", String(140), nullable=True),  # le document annulé dont celui-ci est l'amendement
        # D13 : unicité composite, un `name` identique chez deux tenants n'est pas un conflit.
        PrimaryKeyConstraint("tenant_id", "doctype", "name", name="pk_xdt_document"),
        Index("ix_xdt_document_list", "tenant_id", "doctype", "modified"),
    )
    # D7, D13 : un compteur par (tenant, DocType, série résolue). Jamais partagé entre tenants (fuite d'information et bug).
    naming = Table(
        NAMING_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("series_key", String(200), nullable=False),
        Column("last_number", Integer, nullable=False),
        PrimaryKeyConstraint("tenant_id", "doctype", "series_key", name="pk_xdt_naming_series"),
    )
    # Historique : une ligne par création, modification utile ou suppression (versions.py). `id` ordonne (plus récent = plus grand).
    versions = Table(
        VERSION_TABLE,
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("docname", String(140), nullable=False),
        Column("version", Integer, nullable=False),  # la `version` du document après l'opération
        Column("action", String(10), nullable=False),
        Column("changed_by", String(255), nullable=False),  # `user` est un mot réservé de PostgreSQL
        Column("doc_owner", String(255), nullable=False),  # propriétaire du document : le périmètre `if_owner` se juge dessus
        Column("creation", DateTime(timezone=True), nullable=False),
        Column("changes", JsonType, nullable=False),
        Index("ix_xdt_version_doc", "tenant_id", "doctype", "docname", "id"),
    )
    # Vues sauvegardées (phase C) : une configuration de liste (colonnes, filtres, tri) rejouable, privée à son propriétaire.
    # Ce n'est pas un document du DocType : c'est un réglage, d'où une table système plutôt que la table générique.
    saved_views = Table(
        SAVED_VIEW_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("name", String(140), nullable=False),
        Column("owner", String(255), nullable=False),
        Column("label", String(140), nullable=False),
        Column("kind", String(20), nullable=False),  # list / report / kanban / calendar / gantt
        Column("config", JsonType, nullable=False),  # {columns, filters, order_by, descending}
        Column("is_default", Boolean, nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        Column("modified", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "doctype", "name", name="pk_xdt_saved_view"),
        Index("ix_xdt_saved_view_owner", "tenant_id", "doctype", "owner"),
    )
    # Habillages (phase C+) : l'override d'un espace de travail ou du layout d'un formulaire, saisi au Desk et stocké PAR TENANT.
    # Le YAML du plugin reste la source de vérité ; l'override, lui, est additif et versionné ici (jamais réécrit dans le dépôt).
    overrides = Table(
        OVERRIDE_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("kind", String(20), nullable=False),  # workspace / form
        Column("target", String(140), nullable=False),  # nom de l'espace (workspace) ou du DocType (form)
        Column("owner", String(255), nullable=False),
        Column("config", JsonType, nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        Column("modified", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "kind", "target", name="pk_xdt_layout_override"),
    )
    # Fichiers des champs Image et Attach : une ligne par fichier et par tenant. Le contenu est dans le magasin de blobs (files.py) ; ici, ce qu'il
    # faut pour le retrouver, le servir (nom, type, taille) et juger qui peut le lire (le DocType et le champ qui le citent).
    files = Table(
        FILE_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("id", String(32), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("fieldname", String(63), nullable=False),
        Column("filename", String(255), nullable=False),
        Column("content_type", String(127), nullable=False),
        Column("size", Integer, nullable=False),
        Column("namespace", String(255), nullable=False),
        Column("blob_id", String(64), nullable=False),
        Column("stored_name", String(255), nullable=False),
        Column("owner", String(255), nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_xdt_file"),
        Index("ix_xdt_file_field", "tenant_id", "doctype", "fieldname"),
    )
    # Collaboration (phase D) : ce que les gens ajoutent AUTOUR d'un document sans le modifier. Chaque ligne désigne un document (doctype, docname) ;
    # le droit de la lire est celui de lire ce document (jugé par le service, jamais ici). Supprimées avec lui.
    comments = Table(
        COMMENT_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("id", String(32), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("docname", String(140), nullable=False),
        Column("owner", String(255), nullable=False),
        Column("content", String(4000), nullable=False),
        Column("edited", Boolean, nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        Column("modified", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_xdt_comment"),
        Index("ix_xdt_comment_doc", "tenant_id", "doctype", "docname", "creation"),
    )
    # Assignations (« À faire ») : un document confié à quelqu'un, avec une échéance et un état (open / done / cancelled).
    assignments = Table(
        ASSIGNMENT_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("id", String(32), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("docname", String(140), nullable=False),
        Column("assignee", String(255), nullable=False),
        Column("assigned_by", String(255), nullable=False),
        Column("description", String(500), nullable=True),
        Column("due_date", String(10), nullable=True),  # AAAA-MM-JJ
        Column("status", String(10), nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        Column("modified", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_xdt_assignment"),
        Index("ix_xdt_assignment_doc", "tenant_id", "doctype", "docname"),
        Index("ix_xdt_assignment_user", "tenant_id", "assignee", "status"),
    )
    # Pièces jointes d'un document : le fichier est dans `xdt_file` (champ réservé `_attachment`), ici seulement son rattachement.
    attachments = Table(
        ATTACHMENT_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("id", String(32), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("docname", String(140), nullable=False),
        Column("file_id", String(32), nullable=False),
        Column("owner", String(255), nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "id", name="pk_xdt_attachment"),
        Index("ix_xdt_attachment_doc", "tenant_id", "doctype", "docname"),
        Index("ix_xdt_attachment_file", "tenant_id", "file_id"),
    )
    # Unicité (`unique: true`) : une ligne par (organisation, DocType, champ, valeur) qui désigne LE document porteur. La clé primaire EST la garantie : deux écritures
    # simultanées de la même valeur ne passent pas toutes les deux, quelle que soit la base (pas de contrôle « lire puis écrire »).
    uniques = Table(
        UNIQUE_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("fieldname", String(63), nullable=False),
        Column("value", String(500), nullable=False),
        Column("docname", String(140), nullable=False),
        PrimaryKeyConstraint("tenant_id", "doctype", "fieldname", "value", name="pk_xdt_unique"),
        Index("ix_xdt_unique_doc", "tenant_id", "doctype", "docname"),
    )
    # Étiquettes : comme la collaboration, autour du document (lire le document = voir ses étiquettes, l'écrire = en poser), supprimées avec lui.
    # `tag` est l'écriture affichée, `folded` sa forme repliée (`str.casefold`, calculée en Python : le `lower()` de SQLite ne connaît que
    # l'ASCII) : la clé primaire interdit « Urgent » et « urgent » sur un même document, l'index sert le filtre `_tag` de la liste. Un repli
    # peut allonger le texte (« ß » → « ss », 3 caractères au plus pour un) : 150 pour une étiquette de 50.
    tags = Table(
        TAG_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("docname", String(140), nullable=False),
        Column("folded", String(200), nullable=False),
        Column("tag", String(50), nullable=False),
        Column("owner", String(255), nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "doctype", "docname", "folded", name="pk_xdt_tag"),
        Index("ix_xdt_tag_folded", "tenant_id", "doctype", "folded"),
    )
    # Partages (permissions.py) : un document ouvert à UN membre, en lecture ou en écriture, même sans rôle sur le type. La clé primaire en
    # fait un seul par (document, membre) ; l'index sert « Partagés avec moi ». Supprimés avec le document, déplacés quand il est renommé.
    shares = Table(
        SHARE_TABLE,
        md,
        Column("tenant_id", String(63), nullable=False),
        Column("doctype", String(140), nullable=False),
        Column("docname", String(140), nullable=False),
        Column("member", String(255), nullable=False),  # pas `user` : mot réservé de PostgreSQL
        Column("write", Boolean, nullable=False, default=False),
        Column("owner", String(255), nullable=False),
        Column("creation", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("tenant_id", "doctype", "docname", "member", name="pk_xdt_share"),
        Index("ix_xdt_share_member", "tenant_id", "member"),
    )
    return SystemTables(md, documents, naming, versions, saved_views, overrides, files, comments, assignments, attachments, uniques, tags, shares)
