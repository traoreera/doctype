"""Collaboration (palier 70 %, phase D) : ce que les gens ajoutent AUTOUR d'un document sans le modifier.

- des **commentaires** (un fil de discussion par document) ;
- des **assignations** (« À faire » : ce document est confié à quelqu'un, avec une échéance, jusqu'à ce qu'il soit fait) ;
- des **pièces jointes** (des fichiers rattachés au document, pas à un de ses champs).

Une règle domine : **tout se juge sur le document**. On ne lit les commentaires, assignations et pièces jointes d'un document que si on peut LE
lire (le périmètre `if_owner` s'applique, un document hors périmètre est « introuvable »). Commenter ne demande que de le lire (un relecteur
doit pouvoir répondre) ; assigner et joindre demandent de pouvoir l'ÉCRIRE ; un document soumis ou annulé reste commentable (ce n'est pas le
modifier). Un commentaire ne se modifie que par son auteur. Tout disparaît avec le document.

L'assigné est une identité (celle que l'authentification donne : `sub`). Avec xauth, c'est un membre de l'organisation (annuaire : `directory.py`),
proposé par son e-mail et vérifié à l'assignation ; sans annuaire, une identité déjà vue dans l'organisation, ou n'importe laquelle.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import quote

from . import files as F
from .context import RequestContext
from .directory import Directory, NoDirectory, Person
from .document import DocumentService
from .errors import DoctypeError, NotFound, PermissionDenied, ValidationFailed
from .file_service import FileService
from .files import ATTACHMENT_FIELD
from .notifier import NoNotifier, Notifier
from .permissions import Scope, scope_for

MAX_COMMENT = 4000
MAX_COMMENTS_PER_DOC = 1000
MAX_ASSIGNMENTS_PER_DOC = 50
MAX_ATTACHMENTS_PER_DOC = 100
MAX_MINE = 200
MAX_PEOPLE = 20
STATUSES = ("open", "done", "cancelled")
_ID = re.compile(r"^[0-9a-f]{32}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # tout sauf la tabulation et les sauts de ligne


def _text(value: Any, what: str, *, limit: int, required: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationFailed(f"{what} est obligatoire")
        return None
    if not isinstance(value, str):
        raise ValidationFailed(f"{what} doit être un texte")
    value = value.strip()
    if len(value) > limit:
        raise ValidationFailed(f"{what} : {limit} caractères au plus")
    if _CONTROL.search(value):
        raise ValidationFailed(f"{what} contient un caractère de contrôle")
    return value


def _due(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValidationFailed("due_date doit être une date AAAA-MM-JJ")
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise ValidationFailed("due_date n'est pas une date valide") from e
    return value


def _check_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise NotFound("not_found", f"{what} introuvable")
    return value


MAX_RESOLVE = 100
MAX_MENTIONS = 10
MAX_MENTION_SUGGESTIONS = 8
MAX_BULK_ASSIGN = 100
# Une mention s'écrit @adresse@exemple.fr : seul un membre de l'organisation, désigné par son e-mail, est prévenu.
_MENTION = re.compile(r"(?<![\w.+-])@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


class CollaborationService:
    def __init__(self, documents: DocumentService, files: FileService, directory: Directory | None = None, notifier: Notifier | None = None) -> None:
        self._docs, self._files = documents, files
        self._directory: Directory = directory or NoDirectory()
        self._notifier: Notifier = notifier or NoNotifier()

    # ── Le document d'abord ───────────────────────────────────
    async def _document(self, ctx: RequestContext, q: Any, doctype: str, name: str) -> tuple[Any, Scope]:
        """Le type, et le périmètre de lecture de l'appelant, APRÈS avoir vérifié que le document existe dans ce périmètre."""
        compiled = self._docs._doctype(doctype)
        if compiled.definition.single:
            raise ValidationFailed(f"{doctype} est un document unique : il n'a ni commentaires, ni assignations, ni pièces jointes", code="single_document")
        read = self._docs._scope(ctx, compiled, "read")
        if not isinstance(name, str) or await self._docs.read_document(ctx, q, doctype, name, read) is None:
            raise NotFound("document_not_found", f"{doctype} {name!r} introuvable")
        return compiled, read

    def _can_write(self, ctx: RequestContext, compiled: Any) -> bool:
        return scope_for(compiled.definition, ctx, "write").granted

    # ── Tout ce qui entoure un document ───────────────────────
    async def get(self, ctx: RequestContext, doctype: str, name: str) -> dict[str, Any]:
        """Commentaires, assignations et pièces jointes du document, du plus ancien au plus récent. Il faut pouvoir LIRE le document."""
        self._docs._enter(ctx, "collab.get", doctype, name)
        async with self._docs._query(ctx) as q:
            compiled, _ = await self._document(ctx, q, doctype, name)
            comments = await q.comments(doctype, name, limit=MAX_COMMENTS_PER_DOC)
            assignments = await q.assignments(doctype, name)
            attached = await q.attachments(doctype, name)
            meta = await q.get_files({a["file_id"] for a in attached})
        files = [
            {"id": a["id"], "file_id": a["file_id"], "owner": a["owner"], "creation": a["creation"], **{k: meta[a["file_id"]][k] for k in ("filename", "content_type", "size")}}
            for a in attached if a["file_id"] in meta
        ]
        writable = self._can_write(ctx, compiled)
        return {"comments": comments, "assignments": assignments, "attachments": files, "can_assign": writable, "can_attach": writable, "me": ctx.owner_id}

    # ── Commentaires ──────────────────────────────────────────
    async def add_comment(self, ctx: RequestContext, doctype: str, name: str, content: Any) -> dict[str, Any]:
        self._docs._enter(ctx, "collab.comment.add", doctype, name)
        text = _text(content, "content", limit=MAX_COMMENT)
        async with self._docs._query(ctx) as q:
            await self._document(ctx, q, doctype, name)
            if await q.count_comments(doctype, name) >= MAX_COMMENTS_PER_DOC:
                raise ValidationFailed(f"{MAX_COMMENTS_PER_DOC} commentaires au plus par document", code="too_many_comments")
            comment = await q.add_comment(doctype, name, text or "")
        await self._mentioned(ctx, doctype, name, text or "")
        return comment

    async def edit_comment(self, ctx: RequestContext, comment_id: Any, content: Any) -> dict[str, Any]:
        """Seul l'auteur modifie son commentaire."""
        self._docs._enter(ctx, "collab.comment.edit")
        cid, text = _check_id(comment_id, "commentaire"), _text(content, "content", limit=MAX_COMMENT)
        async with self._docs._query(ctx) as q:
            found = await q.get_comment(cid)
            if found is None:
                raise NotFound("comment_not_found", "commentaire introuvable")
            await self._document(ctx, q, found["doctype"], found["docname"])  # un commentaire d'un document qu'on ne lit pas n'existe pas
            if found["owner"] != ctx.owner_id:
                raise PermissionDenied()
            await q.update_comment(cid, text or "")
            return {**found, "content": text, "edited": True}

    async def delete_comment(self, ctx: RequestContext, comment_id: Any) -> None:
        """L'auteur retire son commentaire ; qui peut ÉCRIRE le document aussi (modérer)."""
        self._docs._enter(ctx, "collab.comment.delete")
        cid = _check_id(comment_id, "commentaire")
        async with self._docs._query(ctx) as q:
            found = await q.get_comment(cid)
            if found is None:
                raise NotFound("comment_not_found", "commentaire introuvable")
            compiled, _ = await self._document(ctx, q, found["doctype"], found["docname"])
            if found["owner"] != ctx.owner_id and not self._can_write(ctx, compiled):
                raise PermissionDenied()
            await q.delete_comment(cid)

    # ── Assignations ──────────────────────────────────────────
    async def assign(self, ctx: RequestContext, doctype: str, name: str, *, assignee: Any, description: Any = None, due_date: Any = None, notify: bool = True) -> dict[str, Any]:
        """Confie le document à quelqu'un. Il faut pouvoir l'ÉCRIRE. Une même personne n'a qu'une assignation ouverte par document."""
        self._docs._enter(ctx, "collab.assign", doctype, name)
        note, due = _text(description, "description", limit=500, required=False), _due(due_date)
        async with self._docs._query(ctx) as q:  # les droits d'abord : on ne sonde pas l'annuaire pour qui n'a pas le droit d'assigner
            compiled, _ = await self._document(ctx, q, doctype, name)
            if not self._can_write(ctx, compiled):
                raise PermissionDenied()
        who = await self._assignee(ctx, assignee)
        async with self._docs._query(ctx) as q:
            await self._document(ctx, q, doctype, name)  # relu : le document a pu disparaître pendant l'appel à l'annuaire
            current = [a for a in await q.assignments(doctype, name) if a["status"] == "open"]
            if any(a["assignee"] == who for a in current):
                raise DoctypeError("already_assigned", f"{who} a déjà une assignation ouverte sur ce document", status=409)
            if len(current) >= MAX_ASSIGNMENTS_PER_DOC:
                raise ValidationFailed(f"{MAX_ASSIGNMENTS_PER_DOC} assignations ouvertes au plus par document", code="too_many_assignments")
            assignment = await q.add_assignment(doctype, name, assignee=who, description=note, due_date=due)
        if notify and who != ctx.owner_id:
            await self._notifier.notify(
                ctx.tenant_id, [who], title=f"{await self._author(ctx)} vous a assigné {self._label(doctype)} {name}", message=note, link=self._link(doctype, name), kind="ASSIGNMENT"
            )
        return assignment

    async def assign_many(self, ctx: RequestContext, doctype: str, names: Any, *, assignee: Any, description: Any = None, due_date: Any = None) -> dict[str, Any]:
        """Assigne plusieurs documents à la même personne (liste « Actions »). Chacun est jugé SEUL, comme `assign` : un refus (droit, déjà assigné) n'arrête pas les autres."""
        self._docs._enter(ctx, "collab.assign_many", doctype)
        if not isinstance(names, list) or not names or len(names) > MAX_BULK_ASSIGN or not all(isinstance(n, str) and n for n in names):
            raise ValidationFailed(f"names : une liste de 1 à {MAX_BULK_ASSIGN} noms", code="bulk_names")
        results = []
        for name in dict.fromkeys(names):
            try:
                await self.assign(ctx, doctype, name, assignee=assignee, description=description, due_date=due_date, notify=False)
                results.append({"name": name, "ok": True})
            except DoctypeError as e:
                if e.code == "unknown_user" or e.status in (401, 403) and not results:
                    raise  # la personne n'existe pas / aucun droit : inutile de le répéter pour chaque document
                results.append({"name": name, "ok": False, "code": e.code, "message": e.message})
        done = sum(1 for r in results if r["ok"])
        if done:
            who = (await self._assignee(ctx, assignee)) if done else ""
            if who != ctx.owner_id:  # UNE notification pour le lot, pas une par document
                n = f"{done} documents" if done > 1 else "1 document"
                await self._notifier.notify(ctx.tenant_id, [who], title=f"{await self._author(ctx)} vous a assigné {n} ({self._label(doctype)})", message=_text(description, "description", limit=500, required=False), link="#/tasks", kind="ASSIGNMENT")
        return {"results": results, "done": done, "failed": len(results) - done}

    # ── Prévenir ──────────────────────────────────────────────
    def _label(self, doctype: str) -> str:
        return self._docs._doctype(doctype).definition.label or doctype

    @staticmethod
    def _link(doctype: str, name: str) -> str:
        return f"#/app/{quote(doctype, safe='')}/{quote(name, safe='')}"

    async def _author(self, ctx: RequestContext) -> str:
        members = await self._members(ctx)
        found = next((p for p in members or [] if p.id == ctx.owner_id), None)
        return found.label if found else ctx.owner_id

    async def _mentioned(self, ctx: RequestContext, doctype: str, name: str, text: str) -> None:
        """Les membres cités par `@adresse` dans un commentaire sont prévenus (sauf l'auteur). Sans annuaire, personne : une adresse ne désigne alors aucun compte."""
        emails = {m.lower() for m in _MENTION.findall(text)}
        if not emails:
            return
        members = await self._members(ctx)
        if members is None:
            return
        ids = [p.id for p in members if p.email and p.email.lower() in emails and p.id != ctx.owner_id][:MAX_MENTIONS]
        if ids:
            await self._notifier.notify(
                ctx.tenant_id, ids, title=f"{await self._author(ctx)} vous a mentionné dans {self._label(doctype)} {name}", link=self._link(doctype, name), kind="MENTION"
            )

    async def update_assignment(self, ctx: RequestContext, assignment_id: Any, *, status: Any = None, description: Any = None, due_date: Any = None, **extra: Any) -> dict[str, Any]:
        """Termine, annule ou rouvre une assignation (l'assigné, ou qui peut écrire le document), ou en change la description / l'échéance (qui
        l'a faite, ou qui peut écrire le document). L'assigné peut clore la sienne même s'il ne peut plus lire le document."""
        self._docs._enter(ctx, "collab.assignment.update")
        if extra:
            raise ValidationFailed(f"champs inconnus : {sorted(extra)}")
        aid = _check_id(assignment_id, "assignation")
        if status is not None and status not in STATUSES:
            raise ValidationFailed(f"status : {', '.join(STATUSES)}")
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if description is not None:
            values["description"] = _text(description, "description", limit=500, required=False)
        if due_date is not None:
            values["due_date"] = _due(due_date)
        if not values:
            raise ValidationFailed("rien à modifier")
        async with self._docs._query(ctx) as q:
            found = await q.get_assignment(aid)
            if found is None:
                raise NotFound("assignment_not_found", "assignation introuvable")
            writer = False
            try:
                compiled, _ = await self._document(ctx, q, found["doctype"], found["docname"])
                writer = self._can_write(ctx, compiled)
            except NotFound:
                pass  # le document n'est pas lisible par l'appelant : il ne reste que ses droits d'assigné
            me = ctx.owner_id
            if "status" in values and not (writer or found["assignee"] == me):
                raise PermissionDenied()
            if ("description" in values or "due_date" in values) and not (writer or found["assigned_by"] == me):
                raise PermissionDenied()
            await q.update_assignment(aid, **values)
            return {**found, **values}

    async def mine(self, ctx: RequestContext, *, status: Any = "open", limit: Any = 100) -> dict[str, Any]:
        """Ce qui m'est confié, l'échéance la plus proche d'abord. Une assignation sur un document que je ne peux plus lire n'apparaît pas."""
        self._docs._enter(ctx, "collab.assignments.mine")
        if status not in (None, *STATUSES):
            raise ValidationFailed(f"status : {', '.join(STATUSES)}")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_MINE:
            raise ValidationFailed(f"limit doit être entre 1 et {MAX_MINE}")
        async with self._docs._query(ctx) as q:
            rows = await q.assignments_of(ctx.owner_id, status=status, limit=limit)
            by_type: dict[str, list[str]] = {}
            for r in rows:
                by_type.setdefault(r["doctype"], []).append(r["docname"])
            readable: dict[str, set[str]] = {}
            for doctype, names in by_type.items():
                try:
                    compiled = self._docs._doctype(doctype)
                    read = self._docs._scope(ctx, compiled, "read")
                except Exception:  # noqa: BLE001 - un type disparu ou refusé : ses assignations ne s'affichent pas
                    continue
                readable[doctype] = await self._docs.existing_documents(ctx, q, doctype, names, read)
        items = []
        for r in rows:
            if r["docname"] in readable.get(r["doctype"], ()):
                items.append({**r, "doctype_label": self._docs._doctype(r["doctype"]).definition.label or r["doctype"]})
        return {"items": items}

    async def _members(self, ctx: RequestContext) -> list[Person] | None:
        return await self._directory.members(ctx.tenant_id)

    async def people(self, ctx: RequestContext, doctype: str, *, q: Any = "") -> dict[str, Any]:
        """Des personnes à qui assigner : les membres de l'organisation (annuaire), sinon des identités que l'organisation a déjà fait apparaître. Il
        faut pouvoir ÉCRIRE le type (c'est pour assigner)."""
        self._docs._enter(ctx, "collab.people", doctype)
        compiled = self._docs._doctype(doctype)
        self._docs._scope(ctx, compiled, "write")
        prefix = _text(q, "q", limit=100, required=False) or ""
        members = await self._members(ctx)
        if members is not None:
            found = [p for p in members if p.matches(prefix)][:MAX_PEOPLE]
            return {"items": [p.as_dict() for p in found], "directory": True}
        async with self._docs._query(ctx) as query:
            seen = await query.people(prefix=prefix, limit=MAX_PEOPLE)
        if ctx.owner_id.lower().startswith(prefix.lower()) and ctx.owner_id not in seen:
            seen = [ctx.owner_id, *seen][:MAX_PEOPLE]
        return {"items": [Person(i).as_dict() for i in seen], "directory": False}

    async def mentionable(self, ctx: RequestContext, doctype: str, name: str, *, q: Any = "") -> dict[str, Any]:
        """Des membres à citer (`@adresse`) dans un commentaire de CE document : il suffit de pouvoir LE LIRE (un relecteur doit pouvoir mentionner, contrairement à
        l'assignation qui demande d'écrire). Seuls des membres avec une adresse, jamais l'auteur ; sans annuaire (pas d'adresses), personne. L'adresse d'un membre
        n'est donc montrée qu'à qui peut déjà lire un document de l'organisation (comme `resolve`, qui l'affiche à tout membre identifié)."""
        self._docs._enter(ctx, "collab.mentionable", doctype, name)
        prefix = (_text(q, "q", limit=100, required=False) or "").lower()
        async with self._docs._query(ctx) as query:
            await self._document(ctx, query, doctype, name)
        members = await self._members(ctx)
        if members is None:
            return {"items": [], "directory": False}
        found = [p for p in members if p.email and p.id != ctx.owner_id and prefix in p.email.lower()]
        found.sort(key=lambda p: (not (p.email or "").lower().startswith(prefix), (p.email or "").lower()))
        return {"items": [p.as_dict() for p in found[:MAX_MENTION_SUGGESTIONS]], "directory": True}

    async def resolve(self, ctx: RequestContext, ids: Any) -> dict[str, Any]:
        """De quoi AFFICHER des identifiants : `{id: {id, email, name}}`. Avec un annuaire, seuls les membres de l'organisation de l'appelant sont
        connus (un identifiant d'une autre organisation est absent) ; sans annuaire, l'identifiant lui-même sert de nom."""
        self._docs._enter(ctx, "collab.users")
        if not isinstance(ids, (list, tuple)) or len(ids) > MAX_RESOLVE or not all(isinstance(i, str) and 0 < len(i) <= 255 for i in ids):
            raise ValidationFailed(f"ids : une liste d'au plus {MAX_RESOLVE} identifiants")
        members = await self._members(ctx)
        if members is None:
            return {"items": {i: Person(i).as_dict() for i in ids}, "directory": False}
        known = {p.id: p for p in members}
        return {"items": {i: known[i].as_dict() for i in ids if i in known}, "directory": True}

    async def _assignee(self, ctx: RequestContext, value: Any) -> str:
        """L'identifiant de la personne à assigner. Avec un annuaire : un membre de l'organisation, désigné par son identifiant ou par son e-mail ;
        sans annuaire : le texte tel quel."""
        who = _text(value, "assignee", limit=255) or ""
        members = await self._members(ctx)
        if members is None:
            return who
        if any(p.id == who for p in members):
            return who
        by_mail = [p for p in members if p.email and p.email.lower() == who.lower()]
        if len(by_mail) == 1:
            return by_mail[0].id
        raise ValidationFailed(f"{who!r} n'est pas membre de cette organisation", code="unknown_user")

    # ── Pièces jointes ────────────────────────────────────────
    async def attach(self, ctx: RequestContext, doctype: str, name: str, *, content: bytes, filename: str | None, content_type: str | None) -> dict[str, Any]:
        """Joint un fichier au document. Il faut pouvoir l'ÉCRIRE. Un fichier est SERVI en téléchargement, jamais affiché dans la page."""
        self._docs._enter(ctx, "collab.attach", doctype, name)
        async with self._docs._query(ctx) as q:
            compiled, _ = await self._document(ctx, q, doctype, name)
            if not self._can_write(ctx, compiled):
                raise PermissionDenied()
            if len(await q.attachments(doctype, name)) >= MAX_ATTACHMENTS_PER_DOC:
                raise ValidationFailed(f"{MAX_ATTACHMENTS_PER_DOC} pièces jointes au plus par document", code="too_many_attachments")
        return await self._files.save_attachment(ctx, doctype, name, content=content, filename=filename, content_type=content_type)

    async def detach(self, ctx: RequestContext, attachment_id: Any) -> None:
        """Qui l'a jointe la retire ; qui peut ÉCRIRE le document aussi."""
        self._docs._enter(ctx, "collab.detach")
        aid = _check_id(attachment_id, "pièce jointe")
        async with self._docs._query(ctx) as q:
            found = await q.get_attachment(aid)
            if found is None:
                raise NotFound("attachment_not_found", "pièce jointe introuvable")
            compiled, _ = await self._document(ctx, q, found["doctype"], found["docname"])
            if found["owner"] != ctx.owner_id and not self._can_write(ctx, compiled):
                raise PermissionDenied()
            await q.delete_attachment(aid)
        await self._files.release(ctx, {found["file_id"]: (found["doctype"], ATTACHMENT_FIELD)})  # le fichier ne sert plus : on le libère
