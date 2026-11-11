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

Le papier à en-tête est UN PDF de fond (en-tête, pied de page, mentions : tout est dedans, §14) : celui du
gabarit s'il en a un, sinon celui de l'ORGANISATION, déposé dans Paramètres › Général (`System Settings`). Ce
dernier se lit en contexte SYSTÈME, sur une liste FERMÉE de champs — le précédent exact de `preferences.py` : le
papier de l'entreprise s'imprime pour tout le monde alors que `System Settings` est réservé aux administrateurs.
Un en-tête est de la présentation d'entreprise, pas un document métier : on ne refuse pas d'imprimer une facture
parce que son lecteur n'a aucun grant sur les réglages.
"""

from __future__ import annotations

import asyncio
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

# Le fond PDF de l'ORGANISATION vit dans ses réglages de base. Les champs que l'impression en lit, et AUCUN
# autre : un réglage sensible ajouté plus tard à `System Settings` ne sortira jamais par ici (la règle de
# `preferences.py`).
SETTINGS_DOCTYPE = "System Settings"
BACKGROUND_FIELD = "print_background"
BACKGROUND_FIELDS = (BACKGROUND_FIELD, "print_background_pages", "print_offset_top", "print_offset_bottom")
FIRST_PAGE_ONLY = "Première page seulement"  # la valeur du Select `print_background_pages` (sinon : toutes)
NO_LETTERHEAD = "none"  # ce que la requête (§7) dit pour imprimer SANS le fond
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
        compiled = self._printable(doctype)
        record = await self._docs.get(ctx, doctype, name)  # LE contrôle de droit : lire le document (§6)
        chosen = await self._all(ctx, compiled)
        wanted = template or self._default_name(chosen)
        if wanted not in chosen:
            raise NotFound("print_template_not_found", f"Gabarit {wanted!r} introuvable pour {doctype}")
        found = chosen[wanted][0]
        html, background = await self._compose(ctx, compiled, found, record, letterhead)
        return html, found, background

    async def _compose(
        self, ctx: RequestContext, compiled: Any, found: PrintTemplate, record: Mapping[str, Any], letterhead: Any
    ) -> tuple[str, Background | None]:
        """Le HTML d'un document LU (droit déjà jugé) avec un gabarit : UN seul chemin, pour l'impression comme pour
        l'aperçu d'un brouillon — l'aperçu de l'éditeur ne peut donc pas diverger de ce qui s'imprime."""
        if letterhead is not None and not isinstance(letterhead, str):
            raise ValidationFailed("letterhead doit être un texte (le nom d'un papier à en-tête, ou « none »)")
        titles = await self._titles(ctx, found, compiled, record["data"])
        background = await self._background(ctx, compiled, found, letterhead)
        extra = "" if background is None else printing.reserved_margins_css(found.page, top=background.top, bottom=background.bottom)
        html = printing.render_html(
            found,
            compiled.definition,
            record["data"],
            name=record["name"],
            children=compiled.children_by_field,
            css_extra=extra,
            titles=titles,
            images=await self._images(ctx, compiled, found),
            linked=await self._currencies(ctx, compiled, record["data"]),
        )
        return html, background

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

    async def _currencies(self, ctx: RequestContext, compiled: Any, data: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        """Les documents d'où vient la devise d'un montant (`company.default_currency`, M3). Lus avec le périmètre de
        l'APPELANT, comme les titres : une société qu'il ne lit pas laisse le montant sans devise, jamais une erreur."""
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for doctype, names in printing.currency_links(compiled.definition, data, compiled.children_by_field).items():
            try:
                page = await self._docs.list(ctx, doctype, filters={"name": ["in", sorted(names)[:MAX_TITLES]]}, limit=MAX_TITLES)
            except DoctypeError:
                continue
            for item in page["items"]:
                out[(doctype, item["name"])] = item["data"]
        return out

    # ── Le PDF ────────────────────────────────────────────────
    async def pdf(self, ctx: RequestContext, doctype: str, name: str, *, template: Any = None, letterhead: Any = None) -> tuple[bytes, str]:
        """Le PDF et son nom de fichier. `503 pdf_engine_missing` si le paquet manque — l'aperçu HTML, lui, marche."""
        html, found, background = await self._html(ctx, doctype, name, template=template, letterhead=letterhead)
        return await _render_pdf(html, background), _filename(doctype, name, found)

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
        html, background, record = await self._draft(ctx, doctype, name, config, letterhead)
        return {"doctype": doctype, "name": record["name"], "html": html, "background": background is not None}

    async def preview_pdf(self, ctx: RequestContext, doctype: str, name: str, config: Any, letterhead: Any = None) -> tuple[bytes, str]:
        """Le PDF d'un gabarit en BROUILLON : le seul aperçu qui montre le FOND PDF (il n'existe qu'au PDF, §5)."""
        self._docs._enter(ctx, "doc.print.preview.pdf", doctype, name)
        html, background, record = await self._draft(ctx, doctype, name, config, letterhead)
        return await _render_pdf(html, background), f"{doctype} {record['name']} (aperçu).pdf"

    async def _draft(self, ctx: RequestContext, doctype: str, name: str, config: Any, letterhead: Any) -> tuple[str, Background | None, dict[str, Any]]:
        compiled = self._printable(doctype)
        cleaned = validate_print_override(compiled, config)
        found = printing.parse_template(cleaned, source="gabarit en brouillon")
        record = await self._docs.get(ctx, doctype, name)  # LE contrôle de droit : lire le document
        html, background = await self._compose(ctx, compiled, found, record, letterhead)
        return html, background, record

    async def upload_image(
        self, ctx: RequestContext, doctype: str, *, content: bytes, filename: str | None, content_type: str | None
    ) -> dict[str, Any]:
        """Dépose une image pour un gabarit de ce type (le logo, un tampon, une signature : §12)."""
        return await self._upload(ctx, doctype, "doc.print.image", content=content, filename=filename, content_type=content_type, kind="Image")

    async def upload_background(
        self, ctx: RequestContext, doctype: str, *, content: bytes, filename: str | None, content_type: str | None
    ) -> dict[str, Any]:
        """Dépose le FOND PDF d'un gabarit de ce type (le papier préimprimé de l'entreprise, §5)."""
        return await self._upload(ctx, doctype, "doc.print.background", content=content, filename=filename, content_type=content_type, kind="Pdf")

    async def _upload(
        self, ctx: RequestContext, doctype: str, action: str, *, content: bytes, filename: str | None, content_type: str | None, kind: str
    ) -> dict[str, Any]:
        """Le MÊME verrou que l'enregistrement du gabarit (périmètre d'écriture total) : un fichier de gabarit ne
        sert qu'à un gabarit, et qui ne peut pas l'enregistrer n'a rien à y déposer."""
        self._docs._enter(ctx, action, doctype)
        compiled = self._printable(doctype)
        write = self._docs._scope(ctx, compiled, "write")
        async with self._docs._query(ctx) as q:
            self._check_author(write, compiled, restricted=await q.restricted(write))
        return await self._files.save_print_file(
            ctx, compiled.definition.name, content=content, filename=filename, content_type=content_type, kind=kind
        )

    async def _images(self, ctx: RequestContext, compiled: Any, template: PrintTemplate) -> dict[str, str]:
        """Les images d'un document libre en URL `data:`, lues par NOUS (§6 : le moteur n'a pas de réseau).

        Une image absente, effacée, ou qui n'est pas une image de gabarit de CE type est simplement omise : une
        facture ne devient pas inimprimable parce qu'un logo a disparu.
        """
        out: dict[str, str] = {}
        for file_id in printing.content_files(template):
            found = await self._files.print_file(ctx.tenant_id, compiled.definition.name, file_id)
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

    # ── Le fond PDF (le papier à en-tête) ─────────────────────
    async def _background(self, ctx: RequestContext, compiled: Any, template: PrintTemplate, asked: Any) -> Background | None:
        """Le fond sur lequel ce document s'imprime : celui du GABARIT s'il en a un, sinon celui de l'ORGANISATION
        (Paramètres › Général), ou aucun. `?letterhead=none` imprime sans.

        Un fond absent n'est JAMAIS une erreur — fichier effacé, pas un PDF, plugin `system` pas installé : on
        imprime sans. Une facture ne devient pas inimprimable parce que son papier a disparu ; les mentions
        obligatoires qui comptent sont dans le document.
        """
        if asked == NO_LETTERHEAD:
            return None
        ref = template.letterhead
        if ref is not None and ref.pdf is not None:
            # Seulement un fond déposé pour un gabarit de CE type (`print_file`) : l'identifiant vient d'un gabarit,
            # donc d'un client, et sans ce contrôle un auteur de gabarit ferait sortir n'importe quel PDF du tenant
            # (un bulletin de paie) en fond de facture. La règle des images (§12), et pour la même raison.
            found = await self._files.print_file(ctx.tenant_id, compiled.definition.name, ref.pdf)
            if found is None or not _is_pdf(found):
                return None
            return Background(found[0], ref.pages == "all", ref.offset_top, ref.offset_bottom)
        return await self._organisation_background(ctx)

    async def _organisation_background(self, ctx: RequestContext) -> Background | None:
        """Le fond de l'organisation : `System Settings`, lu en contexte SYSTÈME sur `BACKGROUND_FIELDS` seulement."""
        if SETTINGS_DOCTYPE not in self._docs._registry.names():
            return None  # le plugin `system` n'est pas chargé : pas de fond, ce n'est pas une faute
        sys = RequestContext.system(ctx.tenant_id, "print background")
        try:
            record = await self._docs.get(sys, SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
        except DoctypeError:
            return None
        data = {k: record["data"].get(k) for k in BACKGROUND_FIELDS}
        file_id = data[BACKGROUND_FIELD]
        if not isinstance(file_id, str) or not file_id:
            return None
        # Le fichier de CE champ de CE document, et pas un autre : la valeur est écrite par un administrateur à
        # travers un champ Attach validé, mais le contrôle ne coûte rien et ferme la porte pour de bon.
        found = await self._files.print_file(ctx.tenant_id, SETTINGS_DOCTYPE, file_id, fieldname=BACKGROUND_FIELD)
        if found is None or not _is_pdf(found):
            return None  # un Attach accepte tout fichier : ce qui n'est pas un PDF est ignoré, pas une erreur
        return Background(found[0], data["print_background_pages"] != FIRST_PAGE_ONLY,
                          _zone(data["print_offset_top"]), _zone(data["print_offset_bottom"]))


def _is_pdf(found: tuple[bytes, str]) -> bool:
    """Un PDF reconnu à ses OCTETS : un Attach garde le type que le navigateur a DÉCLARÉ, qui ne prouve rien."""
    return found[0].startswith(b"%PDF-")


def _zone(value: Any) -> int:
    """Une zone réservée en mm, bornée comme celle d'un gabarit (0-150) : un réglage aberrant ne vide pas la page."""
    return min(max(value, 0), 150) if isinstance(value, int) and not isinstance(value, bool) else 0


def _filename(doctype: str, name: str, template: PrintTemplate) -> str:
    """Le nom du fichier proposé au téléchargement. `content_disposition` le nettoie ensuite pour l'en-tête HTTP."""
    label = template.label or doctype
    return f"{label} {name}.pdf"


async def _render_pdf(html: str, background: Background | None) -> bytes:
    """Le PDF, rendu HORS de la boucle d'événements : PlutoPrint calcule en C pendant des dizaines de millisecondes
    (une demi-seconde au premier appel, le temps de charger les polices), et pendant ce temps-là l'application
    entière ne répondait plus. Un fil de travail par impression ; le moteur ne partage rien entre deux `Book`.

    Sous le MÊME plafond que les impressions d'un plugin (`pdf.off_loop` / `pdf.render_slot`) : dix impressions lancées ensemble
    font la queue au lieu d'ouvrir dix moteurs de rendu, et une attente abandonnée ne rend sa place qu'au bout du rendu."""
    return await pdf_engine.off_loop(_pdf_bytes, html, background)


def _pdf_bytes(html: str, background: Background | None) -> bytes:
    content = pdf_engine.to_pdf(html)
    if background is not None:
        content = pdf_engine.overlay(content, background.content, every_page=background.every_page)
    return content
