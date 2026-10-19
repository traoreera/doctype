"""Le service des fichiers : téléverser, délivrer des liens signés, servir, libérer (voir `files.py` pour les règles de sécurité)."""

from __future__ import annotations

import secrets
from typing import Any, Mapping, Sequence

from . import files as F
from .context import RequestContext
from .document import DocumentService
from .errors import NotFound, PermissionDenied, ValidationFailed
from .permissions import Scope, scope_for
from .schema import FILE_TYPES, FieldDef


def _namespace(tenant: str, doctype: str) -> str:
    # Le tenant ET le DocType dans l'espace de noms : deux tenants ne partagent jamais un répertoire, un nom d'objet de stockage non plus.
    return f"xcore-doctype/{tenant}/{doctype}"


class FileService:
    def __init__(self, documents: DocumentService, store: F.BlobStore, signer: F.UrlSigner) -> None:
        self._docs, self._store, self._signer = documents, store, signer

    # ── Téléverser ────────────────────────────────────────────
    def _file_field(self, doctype: str, fieldname: str) -> tuple[Any, FieldDef]:
        compiled = self._docs._doctype(doctype)
        field = compiled.definition.field_map.get(fieldname)
        if field is None or field.fieldtype not in FILE_TYPES:
            raise ValidationFailed(f"{doctype}.{fieldname} n'est pas un champ Image ou Attach", code="not_a_file_field")
        return compiled, field

    async def upload(
        self, ctx: RequestContext, doctype: str, fieldname: str, *, content: bytes, filename: str | None, content_type: str | None
    ) -> dict[str, Any]:
        """Dépose un fichier pour ce champ. Il faut pouvoir CRÉER ou MODIFIER ce type de document. Le fichier n'est lisible, tant qu'aucun
        document ne le cite, que par celui qui l'a déposé."""
        self._docs._enter(ctx, "file.upload", doctype)
        compiled, field = self._file_field(doctype, fieldname)
        definition = compiled.definition
        if not (scope_for(definition, ctx, "create").granted or scope_for(definition, ctx, "write").granted):
            raise PermissionDenied()
        limit = field.max_mb or F.DEFAULT_MAX_MB
        mime, name = F.check_upload(kind=field.fieldtype, content=content, filename=filename or "", declared_type=content_type, limit_mb=limit)
        namespace = _namespace(ctx.tenant_id, definition.name)
        blob = await self._store.save(content, name, namespace)
        file_id = _new_id()
        try:
            async with self._docs._query(ctx) as q:
                await q.add_file(
                    file_id, doctype=definition.name, fieldname=fieldname, filename=name, content_type=mime, size=len(content),
                    namespace=namespace, blob_id=str(blob.file_id), stored_name=str(blob.stored_name),
                )
        except Exception:
            await self._store.delete(str(blob.file_id), namespace, str(blob.stored_name))  # pas de blob sans ligne
            raise
        return {"id": file_id, "filename": name, "content_type": mime, "size": len(content)}

    async def save_attachment(
        self, ctx: RequestContext, doctype: str, docname: str, *, content: bytes, filename: str | None, content_type: str | None
    ) -> dict[str, Any]:
        """Dépose un fichier ET le joint au document, en une transaction. Les droits sont jugés par l'appelant (`collaboration.py`), pas ici.
        Un fichier joint n'est jamais une image « en ligne » inconnue : seul un vrai PNG/JPEG/GIF/WebP s'affiche, le reste se télécharge."""
        mime, name = F.check_upload(kind="Attach", content=content, filename=filename or "", declared_type=content_type, limit_mb=F.DEFAULT_MAX_MB)
        namespace = _namespace(ctx.tenant_id, doctype)
        blob = await self._store.save(content, name, namespace)
        file_id = _new_id()
        try:
            async with self._docs._query(ctx) as q:
                await q.add_file(
                    file_id, doctype=doctype, fieldname=F.ATTACHMENT_FIELD, filename=name, content_type=mime, size=len(content),
                    namespace=namespace, blob_id=str(blob.file_id), stored_name=str(blob.stored_name),
                )
                att = await q.add_attachment(doctype, docname, file_id)
        except Exception:
            await self._store.delete(str(blob.file_id), namespace, str(blob.stored_name))  # pas de blob sans ligne
            raise
        return {"id": att["id"], "file_id": file_id, "owner": att["owner"], "creation": att["creation"], "filename": name, "content_type": mime, "size": len(content)}

    # ── Lire : liens signés ───────────────────────────────────
    async def links(self, ctx: RequestContext, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Pour chaque fichier que l'appelant a le droit de lire : un lien signé de courte durée et ses métadonnées. Un fichier absent, d'un autre
        tenant ou illisible est ABSENT de la réponse (on ne révèle pas qu'il existe)."""
        self._docs._enter(ctx, "file.links")
        if not isinstance(ids, (list, tuple)) or len(ids) > F.MAX_IDS or not all(isinstance(i, str) and F.FILE_ID_RE.match(i) for i in ids):
            raise ValidationFailed(f"ids : une liste d'au plus {F.MAX_IDS} identifiants de fichier")
        out: dict[str, dict[str, Any]] = {}
        async with self._docs._query(ctx) as q:
            rows = await q.get_files(set(ids))
            scopes: dict[str, Scope] = {}
            for file_id, row in rows.items():
                if not await self._may_read(ctx, q, row, scopes):
                    continue
                exp, sig = self._signer.sign(ctx.tenant_id, file_id)
                out[file_id] = {
                    "path": f"files/{ctx.tenant_id}/{file_id}?exp={exp}&sig={sig}",
                    "filename": row["filename"],
                    "content_type": row["content_type"],
                    "size": row["size"],
                    "expires": exp,
                }
        return out

    async def public_link(self, tenant: str, file_id: str) -> dict[str, Any] | None:
        """Un lien signé pour un fichier que l'APPELANT a déjà jugé montrable (le logo de l'organisation, lu par `preferences.py`) : pas de contrôle de
        droit ici, seulement l'existence dans CE tenant. Jamais appelé avec un identifiant venu d'un client."""
        if not F.FILE_ID_RE.match(file_id):
            return None
        sys = RequestContext.system(tenant, "organisation display preferences")
        async with self._docs._query(sys) as q:
            row = (await q.get_files({file_id})).get(file_id)
        if row is None or not row["content_type"].startswith("image/"):
            return None
        exp, sig = self._signer.sign(tenant, file_id)
        return {"path": f"files/{tenant}/{file_id}?exp={exp}&sig={sig}", "filename": row["filename"], "content_type": row["content_type"], "size": row["size"], "expires": exp}

    async def _may_read(self, ctx: RequestContext, q: Any, row: Mapping[str, Any], scopes: dict[str, Scope]) -> bool:
        if row["owner"] == ctx.owner_id:
            return True  # celui qui l'a déposé le voit, même avant d'enregistrer le document
        doctype = row["doctype"]
        if row["fieldname"] == F.ATTACHMENT_FIELD:
            # Une pièce jointe se lit comme le document auquel elle est jointe : il faut pouvoir LIRE ce document.
            att = await q.attachment_of_file(row["id"])
            if att is None:
                return False
            try:
                scope = scope_for(self._docs._doctype(att["doctype"]).definition, ctx, "read")
            except NotFound:
                return False
            return scope.granted and await self._docs.read_document(ctx, q, att["doctype"], att["docname"], scope) is not None
        if doctype not in scopes:
            try:
                scopes[doctype] = scope_for(self._docs._doctype(doctype).definition, ctx, "read")
            except NotFound:
                scopes[doctype] = Scope("read")  # le type a disparu : plus personne ne lit ce fichier (sauf celui qui l'a déposé, plus haut)
        scope = scopes[doctype]
        return scope.granted and await q.file_cited(doctype, row["fieldname"], row["id"], scope=scope)

    # ── Servir ────────────────────────────────────────────────
    async def serve(self, tenant: str, file_id: str, exp: int, sig: str) -> tuple[bytes, str, str, bool]:
        """Public : on ne présente PAS de jeton, la signature fait foi. Retourne `(contenu, type, nom, en ligne)` ou `NotFound`."""
        if not F.FILE_ID_RE.match(file_id) or not self._signer.verify(tenant, file_id, exp, sig):
            raise NotFound("file_not_found", "Fichier introuvable")
        ctx = RequestContext.system(tenant, "file download (signed link)")
        async with self._docs._query(ctx) as q:
            row = (await q.get_files({file_id})).get(file_id)
        if row is None:
            raise NotFound("file_not_found", "Fichier introuvable")
        content = await self._store.read(row["blob_id"], row["namespace"], row["stored_name"])
        if content is None:
            raise NotFound("file_not_found", "Fichier introuvable")
        inline = row["content_type"] in ("image/png", "image/jpeg", "image/gif", "image/webp")  # ce qui est certainement une image
        return content, row["content_type"], row["filename"], inline

    # ── Libérer ───────────────────────────────────────────────
    async def release(self, ctx: RequestContext, candidates: Mapping[str, tuple[str, str]]) -> int:
        """Supprime les fichiers que plus aucun document ne cite (`id -> (doctype, champ)`). Après une suppression ou un remplacement ; jamais
        un fichier encore cité ailleurs. Au mieux : un échec n'annule pas l'opération du document, il laisse un fichier orphelin."""
        if not candidates:
            return 0
        removed = 0
        sys = RequestContext.system(ctx.tenant_id, "file release")
        async with self._docs._query(sys) as q:
            rows = await q.get_files(set(candidates))
            gone = []
            for file_id, row in rows.items():
                if row["fieldname"] == F.ATTACHMENT_FIELD:
                    cited = await q.attachment_of_file(file_id) is not None  # encore jointe à un document ?
                else:
                    cited = await q.file_cited(row["doctype"], row["fieldname"], file_id, scope=Scope.everything("read"))
                if not cited:
                    gone.append(file_id)
            for file_id in gone:
                row = rows[file_id]
                await self._store.delete(row["blob_id"], row["namespace"], row["stored_name"])
            removed = await q.remove_files(gone)
        return removed


def _new_id() -> str:
    import secrets

    return secrets.token_hex(16)
