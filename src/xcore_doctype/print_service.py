"""Imprimer un document : les gabarits disponibles, l'aperçu HTML, le PDF (process/impression.md §7).

Le partage avec `printing.py` est celui de `mapping.py` contre `document.py` : là-bas tout est PUR (le
vocabulaire des blocs, le rendu HTML, les refus au chargement), ici tout ce qui exige la base — les droits, le
document, l'habillage de l'organisation, les fichiers.

Trois règles gouvernent ce fichier :

- **Imprimer, c'est LIRE** (§6). La route exige le droit de lire le document, avec le périmètre de l'appelant,
  et rien de plus : aucun grant d'impression. Un document hors du périmètre est « introuvable », comme partout.
- **Aucun document n'est inimprimable** (§2). Trois niveaux, du plus fort au plus faible : l'habillage de
  l'ORGANISATION, le gabarit LIVRÉ par le plugin, et à défaut le gabarit FABRIQUÉ depuis le `layout`.
- **Rien n'est écrit.** Imprimer est une lecture : `GET` ne stocke rien, et l'archivage légal d'un PDF est un
  geste distinct qui reste dehors en v1 (§8).

Le papier à en-tête se lit en contexte SYSTÈME, sur une liste FERMÉE de champs — le précédent exact de
`preferences.py` : le logo de l'organisation s'affiche pour tout le monde alors que `System Settings` est
réservé aux administrateurs. Un en-tête est de la présentation d'entreprise, pas un document métier : on ne
peut pas refuser d'imprimer une facture parce que son lecteur n'a pas de grant sur `Letter Head`.
"""

from __future__ import annotations

import base64
from typing import Any, Mapping, NamedTuple

from . import pdf as pdf_engine
from . import printing
from .context import RequestContext
from .document import DocumentService
from .errors import DoctypeError, InvalidDefinition, NotFound, ValidationFailed
from .file_service import FileService
from .overrides import KIND_PRINT, validate_print_override
from .permissions import scope_for
from .printing import PrintTemplate

LETTERHEAD_DOCTYPE = "Letter Head"
# Les champs d'un `Letter Head` que l'impression lit, et AUCUN autre : un champ sensible ajouté plus tard à ce
# DocType ne sortira jamais par ici sans qu'on l'ajoute exprès (la règle de `preferences.py`).
LETTERHEAD_FIELDS = ("image", "footer_text", "is_default", "disabled")
DEFAULT_LETTERHEAD = "default"  # le sentinelle : « celui que l'organisation a marqué par défaut »
NO_LETTERHEAD = "none"  # ce que le gabarit (§4) et la requête (§7) disent pour imprimer sans en-tête
# Ce qu'un <img> incorporé peut porter. Le reste (un SVG, un PDF) n'entre pas dans une balise image.
EMBEDDABLE = ("image/png", "image/jpeg", "image/gif", "image/webp")
# Autant de titres par DocType visé que le filtre `in` en accepte (`FILTER_OPS` : 100). Au-delà, les liens
# restants gardent leur identifiant : un document qui cite cent articles différents est déjà hors du cas usuel.
MAX_TITLES = 100

# D'où vient un gabarit, dans la réponse de `/print-templates` : le Desk le dit à l'utilisateur, et l'éditeur
# (§10) s'en sert pour savoir ce qu'il peut modifier (seul `organisation` l'est).
SOURCE_ORG = "organisation"
SOURCE_PLUGIN = "plugin"
SOURCE_GENERATED = "fabriqué"


class Background(NamedTuple):
    """Le fond préimprimé d'un papier à en-tête PDF (§5), avec les zones qu'il occupe et que le contenu s'interdit."""

    content: bytes
    every_page: bool
    top: int
    bottom: int


def target_of(doctype: str, name: str) -> str:
    """La cible de l'habillage `print` d'un gabarit (`xdt_layout_override`).

    Toujours `<DocType>:<nom du gabarit>`, jamais `<DocType>` seul : §2 offrait les deux, mais une organisation
    qui ajoute un « duplicata » à côté de son « standard » n'aurait alors pas la même cible pour les deux, donc
    une reprise de données le jour où un second gabarit apparaît. Une seule orthographe.
    """
    return f"{doctype}:{name}"


class PrintService:
    def __init__(self, documents: DocumentService, files: FileService) -> None:
        self._docs, self._files = documents, files

    # ── Les gabarits disponibles ──────────────────────────────
    async def templates(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """Les gabarits de ce type, pour peupler le menu « Imprimer » du Desk. Il faut pouvoir LIRE le type."""
        self._docs._enter(ctx, "doc.print.templates", doctype)
        compiled = self._printable(doctype)
        self._docs._scope(ctx, compiled, "read")
        chosen = await self._all(ctx, compiled)
        return {
            "doctype": compiled.definition.name,
            "default": self._default_name(chosen),
            "pdf": pdf_engine.engine_available(),
            "templates": [
                {"name": name, "label": template.label or name, "source": source}
                for name, (template, source) in chosen.items()
            ],
        }

    async def _all(self, ctx: RequestContext, compiled: Any) -> dict[str, tuple[PrintTemplate, str]]:
        """Les gabarits par nom, l'habillage de l'organisation PRIMANT sur celui du plugin, le fabriqué à défaut de tout.

        Un habillage devenu illisible (le DocType a changé sous lui) est IGNORÉ et le gabarit du plugin reprend
        la main : la règle des autres habillages (`apply_dashboard_override`), et la seule qui garde une facture
        imprimable le jour où un champ cité disparaît.
        """
        doctype = compiled.definition.name
        out: dict[str, tuple[PrintTemplate, str]] = {}
        for template in self._docs._registry.print_templates(doctype):
            out[template.name] = (template, SOURCE_PLUGIN)
        prefix = f"{doctype}:"
        async with self._docs._query(ctx) as q:
            rows = await q.overrides(KIND_PRINT)
        for row in rows:
            if not str(row["target"]).startswith(prefix):
                continue  # un gabarit d'un AUTRE type : les habillages d'un genre se lisent d'un coup
            try:
                template = printing.parse_template(row["config"], source="habillage d'impression")
            except InvalidDefinition:  # un habillage illisible n'empêche pas d'imprimer ; une autre erreur, si
                continue  # ...elle existe, doit remonter : on n'avale pas ce qu'on n'a pas prévu
            out[template.name] = (template, SOURCE_ORG)
        if not out:
            made = printing.generated_template(compiled.definition, compiled.children_by_field)
            out[made.name] = (made, SOURCE_GENERATED)
        return dict(sorted(out.items()))

    @staticmethod
    def _default_name(chosen: Mapping[str, tuple[PrintTemplate, str]]) -> str:
        """Celui qu'on imprime quand la requête ne nomme rien : « standard » s'il existe, sinon le premier par nom."""
        return "standard" if "standard" in chosen else next(iter(chosen))

    def _printable(self, doctype: str) -> Any:
        compiled = self._docs._doctype(doctype)  # type inconnu ou enfant : 404, comme partout
        d = compiled.definition
        if d.single or d.source is not None:
            what = "unique" if d.single else "virtuel"
            raise ValidationFailed(f"{d.name} est un DocType {what} : il ne s'imprime pas", code="not_printable")
        return compiled

    # ── L'aperçu HTML ─────────────────────────────────────────
    async def render(self, ctx: RequestContext, doctype: str, name: str, *, template: Any = None, letterhead: Any = None) -> dict[str, Any]:
        """Le document en HTML. Ne demande PAS le moteur PDF : l'aperçu marche sans le paquet (§1)."""
        html, found, background = await self._html(ctx, doctype, name, template=template, letterhead=letterhead)
        # `background` dit au client qu'un FOND PDF complète ce HTML : l'aperçu à l'écran ne le montre pas (il
        # n'existe qu'au moment du PDF), et le Desk doit pouvoir le dire plutôt que laisser croire à un oubli.
        return {"doctype": doctype, "name": name, "template": found.name, "html": html, "background": background is not None}

    async def _html(
        self, ctx: RequestContext, doctype: str, name: str, *, template: Any = None, letterhead: Any = None
    ) -> tuple[str, PrintTemplate, Background | None]:
        """Le HTML, le gabarit retenu, et le fond PDF s'il y en a un."""
        self._docs._enter(ctx, "doc.print", doctype, name)
        if template is not None and not isinstance(template, str):
            raise ValidationFailed("template doit être un texte (le nom d'un gabarit)")
        if letterhead is not None and not isinstance(letterhead, str):
            raise ValidationFailed("letterhead doit être un texte (le nom d'un papier à en-tête, ou « none »)")
        compiled = self._printable(doctype)
        record = await self._docs.get(ctx, doctype, name)  # LE contrôle de droit : lire le document (§6)
        chosen = await self._all(ctx, compiled)
        wanted = template or self._default_name(chosen)
        if wanted not in chosen:
            raise NotFound("print_template_not_found", f"Gabarit {wanted!r} introuvable pour {doctype}")
        found = chosen[wanted][0]
        titles = await self._titles(ctx, found, compiled, record["data"])
        head, foot, background = await self._letterhead(ctx, found, letterhead)
        extra = "" if background is None else printing.reserved_margins_css(found.page, top=background.top, bottom=background.bottom)
        html = printing.render_html(
            found,
            compiled.definition,
            record["data"],
            name=record["name"],
            children=compiled.children_by_field,
            letterhead_html=head,
            footer_html=foot,
            css_extra=extra,
            titles=titles,
            images=await self._images(ctx, compiled, found),
        )
        return html, found, background

    async def _titles(self, ctx: RequestContext, template: PrintTemplate, compiled: Any, data: Mapping[str, Any]) -> dict[tuple[str, str], str]:
        """Les TITRES des documents que les liens du gabarit désignent : « Société Durand », pas « c99464b32801 ».

        Sans cela un document imprimé est illisible — c'est ce que le premier PDF de bout en bout a montré. Une
        lecture par DocType visé, et seulement ce que le gabarit MONTRE (`printing.linked_values`) : un document
        a cent champs, une facture en imprime dix.

        Lu avec le périmètre de l'APPELANT, comme `fetch_from` : une cible qu'il ne peut pas lire garde son
        identifiant sur le papier. Imprimer ne contourne aucun droit (§6), et un titre n'est pas une fuite qu'on
        s'autorise parce qu'il est pratique.
        """
        wanted = printing.linked_values(template, compiled.definition, data, compiled.children_by_field)
        if not wanted:
            return {}
        out: dict[tuple[str, str], str] = {}
        for doctype, names in wanted.items():
            try:
                target = self._docs._registry.get(doctype)
            except DoctypeError:
                continue  # un DynamicLink peut nommer un type parti : l'identifiant reste
            field = target.definition.title_field
            if not field:
                continue  # pas de titre déclaré : l'identifiant EST le nom lisible (un code devise, un pays)
            try:
                page = await self._docs.list(
                    ctx, doctype, filters={"name": ["in", sorted(names)[:MAX_TITLES]]}, limit=MAX_TITLES
                )
            except DoctypeError:
                continue  # illisible pour cet appelant : on n'insiste pas
            for item in page["items"]:
                title = item["data"].get(field)
                if isinstance(title, str) and title:
                    out[(doctype, item["name"])] = title
        return out

    # ── Le PDF ────────────────────────────────────────────────
    async def pdf(self, ctx: RequestContext, doctype: str, name: str, *, template: Any = None, letterhead: Any = None) -> tuple[bytes, str]:
        """Le PDF et son nom de fichier. `503 pdf_engine_missing` si le paquet manque — l'aperçu HTML, lui, marche."""
        html, found, background = await self._html(ctx, doctype, name, template=template, letterhead=letterhead)
        content = pdf_engine.to_pdf(html)
        if background is not None:
            content = pdf_engine.overlay(content, background.content, every_page=background.every_page)
        return content, _filename(doctype, name, found)

    # ── L'éditeur de gabarits (§10) ───────────────────────────
    async def override(self, ctx: RequestContext, doctype: str, template: Any = None) -> dict[str, Any]:
        """Ce que l'éditeur ouvre : le gabarit à éditer, d'où il vient, et si l'appelant peut l'enregistrer.

        Tout lecteur du type l'ouvre — en lecture seule s'il ne peut pas écrire, comme l'éditeur d'automate. On
        rend le gabarit de l'ORGANISATION s'il existe, sinon celui du plugin ou le fabriqué, qui servent de
        point de départ : partir d'une page blanche obligerait à tout réécrire pour changer une colonne.
        """
        self._docs._enter(ctx, "doc.print.override.get", doctype)
        compiled = self._printable(doctype)
        self._docs._scope(ctx, compiled, "read")
        chosen = await self._all(ctx, compiled)
        wanted = template if isinstance(template, str) and template else self._default_name(chosen)
        if wanted not in chosen:
            raise NotFound("print_template_not_found", f"Gabarit {wanted!r} introuvable pour {doctype}")
        found, source = chosen[wanted]
        # Le périmètre d'écriture SANS lever : un lecteur ouvre l'éditeur en lecture seule, il n'est pas refusé
        # (c'est `scope_for` et non `_scope`, comme `/meta` pour ses droits effectifs).
        write = scope_for(compiled.definition, ctx, "write")
        async with self._docs._query(ctx) as q:
            total = write.level == "all" and not await q.restricted(write)
        return {
            "doctype": compiled.definition.name,
            "template": found.model_dump(mode="json", exclude_none=True),
            "source": source,
            "editable": total,
            "templates": [{"name": n, "label": t.label or n, "source": s} for n, (t, s) in chosen.items()],
        }

    async def save_override(self, ctx: RequestContext, doctype: str, config: Any) -> dict[str, Any]:
        """Enregistre le gabarit d'une ORGANISATION. Exige un périmètre d'écriture TOTAL sur le type."""
        self._docs._enter(ctx, "doc.print.override.save", doctype)
        compiled = self._printable(doctype)
        write = self._docs._scope(ctx, compiled, "write")
        cleaned = validate_print_override(compiled, config)
        async with self._docs._query(ctx) as q:
            self._check_author(write, compiled, restricted=await q.restricted(write))
            made = await q.put_override(KIND_PRINT, target_of(compiled.definition.name, cleaned["name"]), owner=ctx.owner_id, config=cleaned)
        return {"doctype": compiled.definition.name, "template": made["config"]}

    async def delete_override(self, ctx: RequestContext, doctype: str, template: Any = None) -> dict[str, Any]:
        """Retire le gabarit de l'organisation : celui du plugin (ou le fabriqué) reprend la main."""
        self._docs._enter(ctx, "doc.print.override.delete", doctype)
        compiled = self._printable(doctype)
        write = self._docs._scope(ctx, compiled, "write")
        wanted = template if isinstance(template, str) and template else "standard"
        async with self._docs._query(ctx) as q:
            self._check_author(write, compiled, restricted=await q.restricted(write))
            await q.delete_override(KIND_PRINT, target_of(compiled.definition.name, wanted))
        return {"doctype": compiled.definition.name, "template": wanted}

    @staticmethod
    def _check_author(write: Any, compiled: Any, *, restricted: bool = False) -> None:
        """Éditer un gabarit exige un périmètre d'écriture TOTAL sur le type (§10).

        La raison n'est pas « une facture engage l'entreprise » — c'est vrai et cela ne prouve rien, puisqu'une
        disposition de formulaire engage aussi et ne demande qu'un droit ordinaire. La raison est qu'**un
        gabarit rend des documents que son éditeur n'a pas forcément le droit de LIRE** : quelqu'un dont le
        périmètre est `if_owner` déciderait de la présentation des factures des autres. C'est la forme exacte du
        verrou des règles de notification, dont l'auteur doit pouvoir lire TOUS les documents du type.
        """
        if write.level == "all" and not restricted:
            return
        name = compiled.definition.name
        raise ValidationFailed(
            f"Le gabarit de {name} sert à imprimer TOUS ses documents : seul qui peut les modifier tous le change",
            code="print_template_reserved",
            details=[{"field": "blocks", "msg": "Réservé à qui peut modifier tous les documents du type", "type": "print_template_reserved"}],
        )

    async def preview(self, ctx: RequestContext, doctype: str, name: str, config: Any, letterhead: Any = None) -> dict[str, Any]:
        """L'aperçu d'un gabarit en BROUILLON, non enregistré (§10). N'écrit rien.

        C'est ce qui distingue cet éditeur des autres : on ne règle pas une mise en page abstraite, on regarde
        la facture. Le brouillon passe par la MÊME validation qu'un enregistrement (`validate_print_override`),
        donc un gabarit non enregistré n'est pas une porte : ni URL externe, ni champ qui ne s'imprime pas.

        Il faut pouvoir LIRE le document d'exemple, et rien de plus — un aperçu est une lecture. Enregistrer,
        lui, exige le périmètre total ; voir ce qu'on obtiendrait ne le demande pas.
        """
        self._docs._enter(ctx, "doc.print.preview", doctype, name)
        compiled = self._printable(doctype)
        cleaned = validate_print_override(compiled, config)
        found = printing.parse_template(cleaned, source="gabarit en brouillon")
        record = await self._docs.get(ctx, doctype, name)  # LE contrôle de droit : lire le document
        titles = await self._titles(ctx, found, compiled, record["data"])
        head, foot, background = await self._letterhead(ctx, found, letterhead)
        extra = "" if background is None else printing.reserved_margins_css(found.page, top=background.top, bottom=background.bottom)
        html = printing.render_html(
            found, compiled.definition, record["data"], name=record["name"], children=compiled.children_by_field,
            letterhead_html=head, footer_html=foot, css_extra=extra, titles=titles,
            images=await self._images(ctx, compiled, found),
        )
        return {"doctype": doctype, "name": record["name"], "html": html, "background": background is not None}

    async def upload_image(
        self, ctx: RequestContext, doctype: str, *, content: bytes, filename: str | None, content_type: str | None
    ) -> dict[str, Any]:
        """Dépose une image pour un gabarit de ce type (le logo, un tampon, une signature : §12).

        Le MÊME verrou que l'enregistrement du gabarit (périmètre d'écriture total) : une image ne sert qu'à un
        gabarit, et qui ne peut pas l'enregistrer n'a rien à y déposer.
        """
        self._docs._enter(ctx, "doc.print.image", doctype)
        compiled = self._printable(doctype)
        write = self._docs._scope(ctx, compiled, "write")
        async with self._docs._query(ctx) as q:
            self._check_author(write, compiled, restricted=await q.restricted(write))
        return await self._files.save_print_image(ctx, compiled.definition.name, content=content, filename=filename, content_type=content_type)

    async def _images(self, ctx: RequestContext, compiled: Any, template: PrintTemplate) -> dict[str, str]:
        """Les images d'un document libre en URL `data:`, lues par NOUS (§6 : le moteur n'a pas de réseau).

        Une image absente, effacée, ou qui n'est pas une image de gabarit de CE type est simplement omise : une
        facture ne devient pas inimprimable parce qu'un logo a disparu.
        """
        out: dict[str, str] = {}
        for file_id in printing.content_files(template):
            found = await self._files.print_image(ctx.tenant_id, compiled.definition.name, file_id)
            if found is not None and found[1] in EMBEDDABLE:
                out[file_id] = f"data:{found[1]};base64,{base64.b64encode(found[0]).decode('ascii')}"
        return out

    async def example(self, ctx: RequestContext, doctype: str) -> dict[str, Any]:
        """Un document d'exemple pour l'aperçu : le dernier que l'appelant peut LIRE, ou rien.

        « Rien » est une réponse : un gabarit s'édite alors sans aperçu, plutôt que pas du tout (§10).
        """
        self._docs._enter(ctx, "doc.print.example", doctype)
        compiled = self._printable(doctype)
        self._docs._scope(ctx, compiled, "read")
        page = await self._docs.list(ctx, doctype, limit=1)
        return {"doctype": compiled.definition.name, "name": page["items"][0]["name"] if page["items"] else None}

    # ── Le papier à en-tête ───────────────────────────────────
    async def _letterhead(self, ctx: RequestContext, template: PrintTemplate, asked: Any) -> tuple[str, str, Background | None]:
        """`(en-tête HTML, pied HTML, fond PDF)`. Un en-tête absent n'est jamais une erreur : on imprime sans.

        C'est délibéré — un papier à en-tête supprimé, désactivé, ou un plugin `system` pas encore installé ne
        doit pas rendre une facture inimprimable. La mention obligatoire est dans les BLOCS, pas dans l'en-tête.
        """
        ref = template.letterhead
        if asked == NO_LETTERHEAD:
            return "", "", None
        if asked:
            ref = printing.LetterheadRef(name=asked)  # la requête remplace ce que le gabarit dit (§7)
        if ref is None:
            return "", "", None
        if ref.pdf is not None:
            found = await self._files.content(ctx.tenant_id, ref.pdf, reason="print letterhead background")
            if found is None or found[1] != "application/pdf":
                return "", "", None
            return "", "", Background(found[0], ref.pages == "all", ref.offset_top, ref.offset_bottom)
        if ref.name is None:
            return "", "", None
        row = await self._letterhead_doc(ctx, ref.name)
        if row is None or row.get("disabled"):
            return "", "", None
        image = None
        if isinstance(row.get("image"), str) and row["image"]:
            image = await self._embedded(ctx, row["image"])
        return printing.letterhead_markup(image), printing.footer_markup(row.get("footer_text")), None

    async def _letterhead_doc(self, ctx: RequestContext, wanted: str) -> dict[str, Any] | None:
        """Le `Letter Head` demandé, réduit à `LETTERHEAD_FIELDS`, lu en contexte système (voir l'en-tête du module).

        `default` est un sentinelle : celui que l'organisation a MARQUÉ par défaut. S'il n'y en a aucun, on
        retombe sur un document qui s'appellerait littéralement « default » — le sentinelle ne crée donc pas
        d'impasse (le choix de `api/desk.py`, dont `login_url: auth` est aussi un sentinelle).
        """
        if LETTERHEAD_DOCTYPE not in self._docs._registry.names():
            return None  # le plugin `system` n'est pas chargé : il n'y a pas d'en-tête, ce n'est pas une faute
        sys = RequestContext.system(ctx.tenant_id, "print letterhead")
        try:
            if wanted == DEFAULT_LETTERHEAD:
                page = await self._docs.list(sys, LETTERHEAD_DOCTYPE, filters={"is_default": True}, limit=1)
                if page["items"]:
                    return _only(page["items"][0]["data"])
            record = await self._docs.get(sys, LETTERHEAD_DOCTYPE, wanted)
        except DoctypeError:
            return None
        return _only(record["data"])

    async def _embedded(self, ctx: RequestContext, file_id: str) -> str | None:
        """L'image d'un en-tête en URL `data:` : le moteur n'a pas d'accès réseau, c'est NOUS qui lisons (§6).

        Seulement un fichier `xdt_file` de CE tenant, et seulement un type d'image qu'une balise `<img>` sait
        montrer : un SVG est refusé ici comme il l'est au téléversement (il porte du script).
        """
        found = await self._files.content(ctx.tenant_id, file_id, reason="print letterhead image")
        if found is None or found[1] not in EMBEDDABLE:
            return None
        return f"data:{found[1]};base64,{base64.b64encode(found[0]).decode('ascii')}"


def _only(data: Mapping[str, Any]) -> dict[str, Any]:
    return {k: data.get(k) for k in LETTERHEAD_FIELDS}


def _filename(doctype: str, name: str, template: PrintTemplate) -> str:
    """Le nom du fichier proposé au téléchargement. `content_disposition` le nettoie ensuite pour l'en-tête HTTP."""
    label = template.label or doctype
    return f"{label} {name}.pdf"
