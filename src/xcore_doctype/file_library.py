"""L'écran « Fichiers » (palier 70 %, consolidation C4) : les fichiers de l'organisation qu'un utilisateur peut voir, et les détacher.

Deux règles, celles de `files.py` et de `collaboration.py`, rien de nouveau :

- **voir un fichier, c'est pouvoir lire un document qui le cite**, par un champ Image/Attach ou comme pièce jointe. La liste est jugée dans le SQL
  (`Query.cited_files`), document citant par document citant, avec le périmètre de lecture de l'appelant sur chaque type (`if_owner` compris) : un
  fichier d'un document illisible n'apparaît pas, le total et les comptes par type non plus. Un fichier déposé que rien ne cite encore n'est pas listé
  (il n'a pas de document ; seul celui qui l'a déposé le lit, le temps d'enregistrer). Une ligne par CITATION : un fichier que deux documents citent
  (un amendement copie ses fichiers) paraît deux fois, une par document à modifier pour le retirer.
- **retirer un fichier, c'est le DÉTACHER par l'écriture ordinaire**, jamais un `DELETE` sur `xdt_file` : pour un champ, une mise à jour du document
  qui vide ce champ (`DocumentService.update` : droits, hooks, version lue par le client, historique, puis libération du fichier si plus rien ne le
  cite) ; pour une pièce jointe, `CollaborationService.detach` (qui l'a jointe, ou qui peut écrire le document). Un refus est celui de cette écriture
  (document soumis, champ obligatoire, hook, version périmée…), rendu tel quel.
"""

from __future__ import annotations

from typing import Any

from .collaboration import CollaborationService
from .context import RequestContext
from .document import DEFAULT_PAGE, MAX_PAGE, DocumentService
from .errors import DoctypeError, ValidationFailed
from .memory import visible
from .permissions import scope_for
from .query import DRAFT, FILE_SORTABLE, SUBMITTED, FileCiters
from .schema import FILE_TYPES

MAX_SEARCH = 100


class FileLibrary:
    def __init__(self, documents: DocumentService, collaboration: CollaborationService) -> None:
        self._docs, self._collab = documents, collaboration

    # ── Lister ────────────────────────────────────────────────
    async def list(
        self,
        ctx: RequestContext,
        *,
        doctype: str | None = None,
        q: Any = None,
        order_by: str = "creation",
        descending: bool = True,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Une page des fichiers que l'appelant peut lire, les plus récents d'abord. `doctype` : ceux que ce type cite ; `q` : un morceau du nom de fichier."""
        self._docs._enter(ctx, "file.library", doctype)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE:
            raise ValidationFailed(f"limit doit être entre 1 et {MAX_PAGE}")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValidationFailed("offset doit être un entier positif ou nul")
        if order_by not in FILE_SORTABLE:
            raise ValidationFailed(f"tri impossible sur {order_by!r} (permis : {', '.join(FILE_SORTABLE)})")
        if q is not None and not isinstance(q, str):
            raise ValidationFailed("q doit être un texte")
        search = (q or "").strip()
        if len(search) > MAX_SEARCH:
            raise ValidationFailed(f"q : {MAX_SEARCH} caractères au plus")
        if doctype is not None:
            self._docs._scope(ctx, self._docs._doctype(doctype), "read")  # un type inconnu (ou enfant) : 404 ; illisible : 403, comme sa liste
        async with self._docs._query(ctx) as query:
            citers, unavailable = await self._citers(ctx, query)
            rows, total, counts = await query.cited_files(
                citers, doctype=doctype, search=search or None, order_by=order_by, descending=descending, limit=limit, offset=offset
            )
        return {
            "items": [self._item(ctx, r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "doctypes": sorted(({"name": n, "label": self._label(n), "count": c} for n, c in counts.items()), key=lambda d: d["label"].lower()),
            "unavailable": unavailable,
        }

    async def _citers(self, ctx: RequestContext, query: Any) -> tuple[list[FileCiters], list[str]]:
        """Pour chaque DocType que l'appelant peut LIRE : où il cite des fichiers, et avec quels périmètres. Un DocType virtuel n'est demandé à sa source que
        s'il a des pièces jointes ; une source en panne ne fait pas échouer l'écran, elle est NOMMÉE dans la réponse (`unavailable`) : une liste qui
        paraîtrait complète alors qu'il lui manque un type serait pire."""
        registry = self._docs._registry
        stored: list[FileCiters] = []
        virtual: dict[str, Any] = {}
        for name in registry.names():
            compiled = registry.get(name)
            d = compiled.definition
            if d.istable:
                continue
            read = scope_for(d, ctx, "read")
            if not read.granted:
                continue
            write = scope_for(d, ctx, "write")
            if d.source is not None:
                virtual[name] = (compiled, read, write)
                continue
            fields = tuple(f.fieldname for f in d.fields if f.fieldtype in FILE_TYPES)
            if fields or not d.single:  # un document unique n'a pas de pièce jointe (collaboration.py)
                stored.append(FileCiters(name, read, write, fields=fields, attachments=not d.single))
        unavailable: list[str] = []
        for name in sorted(await query.attachment_doctypes(virtual.keys())):
            compiled, read, write = virtual[name]
            try:
                docs = visible(await self._docs._source_docs(ctx, compiled, self._docs._source(compiled)), read)
            except DoctypeError:
                unavailable.append(name)  # déjà journalisé par le service des documents
                continue
            stored.append(
                FileCiters(name, read, write, names=frozenset(d.name for d in docs), writable=frozenset(d.name for d in docs if write.allows(d.owner, d.data)))
            )
        return stored, unavailable

    def _label(self, doctype: str) -> str:
        return self._docs._registry.get(doctype).definition.label or doctype

    def _item(self, ctx: RequestContext, row: dict[str, Any]) -> dict[str, Any]:
        d = self._docs._registry.get(row["doctype"]).definition
        attachment = row["attachment_id"]
        if attachment is not None:
            # Une pièce jointe : qui l'a jointe la retire, qui peut écrire le document aussi (`CollaborationService.detach`).
            field_label, can_detach = None, row["writable"] or row["owner"] == ctx.owner_id
        else:
            f = d.field_map[row["fieldname"]]
            field_label = f.label or f.fieldname
            # Vider le champ est une modification : il faut pouvoir écrire CE document, qu'il soit encore modifiable et que le champ puisse être vide.
            editable = row["docstatus"] == DRAFT or (row["docstatus"] == SUBMITTED and f.fieldname in d.editable_after_submit)
            can_detach = row["writable"] and editable and not (f.required or f.read_only or f.set_only_once)
        return {
            "file_id": row["file_id"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "size": row["size"],
            "owner": row["owner"],
            "creation": row["creation"],
            "doctype": row["doctype"],
            "doctype_label": d.label or d.name,
            "docname": row["docname"],
            "field": row["fieldname"],
            "field_label": field_label,
            "attachment": attachment,
            "version": row["version"],
            "docstatus": row["docstatus"],
            "can_detach": bool(can_detach),
        }

    # ── Détacher ──────────────────────────────────────────────
    async def detach(
        self,
        ctx: RequestContext,
        *,
        attachment: Any = None,
        doctype: Any = None,
        name: Any = None,
        field: Any = None,
        file_id: Any = None,
        version: Any = None,
    ) -> dict[str, Any]:
        """Retire un fichier de ce qui le cite, par l'écriture ordinaire. Une pièce jointe : `attachment` seul. Un champ : `doctype`, `name`, `field`, `file_id`
        (le fichier que l'utilisateur a vu dans ce champ) et `version` (celle du document qu'il a vue) : si le document a changé depuis, 409."""
        self._docs._enter(ctx, "file.detach", doctype if isinstance(doctype, str) else None, name if isinstance(name, str) else None)
        by_field = {"doctype": doctype, "name": name, "field": field, "file_id": file_id, "version": version}
        if attachment is not None:
            if any(v is not None for v in by_field.values()):
                raise ValidationFailed("une pièce jointe se détache par `attachment` seul", code="detach_target")
            await self._collab.detach(ctx, attachment)
            return {"detached": "attachment", "version": None}
        missing = sorted(k for k, v in by_field.items() if v is None)
        if missing:
            raise ValidationFailed(f"champs obligatoires pour détacher le fichier d'un champ : {', '.join(missing)}", code="detach_target")
        if not all(isinstance(v, str) for v in (doctype, name, field, file_id)):
            raise ValidationFailed("doctype, name, field et file_id sont des textes", code="detach_target")
        compiled = self._docs._doctype(doctype)
        target = compiled.definition.field_map.get(field)
        if target is None or target.fieldtype not in FILE_TYPES:
            raise ValidationFailed(f"{doctype}.{field} n'est pas un champ Image ou Attach", code="not_a_file_field")
        current = await self._docs.get(ctx, doctype, name)  # le droit de LIRE d'abord : un document hors périmètre est introuvable
        if current["data"].get(field) != file_id:
            raise DoctypeError("file_not_cited", "Ce document ne cite plus ce fichier (il a changé depuis) : rechargez la liste", status=409)
        # Le document tel qu'il est, sans ce champ : ce que le formulaire enverrait si l'on y retirait le fichier. Seuls les champs que la définition connaît
        # repartent (une valeur laissée par une extension retirée serait refusée par le schéma strict) ; un secret part masqué et reste ce qu'il était.
        known = compiled.definition.field_map
        data = {k: v for k, v in current["data"].items() if k in known and k != field}
        doc = await self._docs.update(ctx, doctype, name, data, version)
        return {"detached": "field", "version": doc["version"]}
