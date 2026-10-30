"""Le gabarit d'impression comme DOCUMENT LIBRE, façon traitement de texte (process/impression.md §12).

Un gabarit peut être une pile de blocs (`printing.PrintTemplate.blocks`) ou, depuis le 29 oct., un document
écrit à l'écran comme dans Word : du texte mis en forme, des tableaux de mise en page, des images (dans le texte,
habillées, ou posées librement sur la page), les VALEURS du document insérées dans le texte, et le tableau des
lignes. Ce module est PUR, comme `printing.py` : il valide ce HTML et le rend, sans base ni droits.

Trois règles, qui sont celles du reste de l'impression :

- **Un sous-ensemble FERMÉ, refusé et non nettoyé.** Toute balise, tout attribut, toute propriété de style hors
  liste est une FAUTE dite à l'enregistrement — la règle de `fieldtypes.rich_text`, jamais un « nettoyage » qui
  laisserait croire qu'un gabarit dit ce qu'il ne dit plus. Et le rendu ne recopie JAMAIS l'entrée : il
  réécrit l'arbre analysé, attribut par attribut, depuis des valeurs vérifiées. Un `style` n'est pas une chaîne
  qu'on laisse passer, c'est une liste de couples propriété/valeur reconstruits.
- **Aucun code, aucune URL.** Pas de `<script>`, pas d'événement, pas de `href`, pas de `src` : une image est
  un IDENTIFIANT de fichier du tenant (`data-file`), que le service résout lui-même en URL `data:` (§6 : le
  moteur n'a pas de réseau). Un `url(...)` dans un style n'a aucune propriété où se loger.
- **Les valeurs sont celles du document, échappées.** Un champ inséré (`<span data-value="doc.client">`) est
  remplacé à l'impression par la valeur, rendue comme une cellule (`printing._cell` : le titre d'un lien, un
  décimal à son échelle). Le tableau des lignes, les totaux et une grille de champs sont des blocs du
  vocabulaire existant, INCORPORÉS (`<div data-block='{…}'>`) et rendus par le même code : un seul rendu de
  tableau, sous-totaux exacts compris.

Une image « devant le texte » se place en millimètres depuis le coin haut-gauche de la zone imprimable de la
PREMIÈRE page (`left`/`top`), comme dans l'éditeur : mesuré, PlutoPrint suit `position:absolute` dans un
conteneur `position:relative` au millimètre (tests/unit/test_pdf_engine.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from typing import Any, Callable, Iterator

from pydantic import TypeAdapter, ValidationError

# Bornes : un gabarit reste un gabarit. 200 000 caractères tiennent dans un habillage (256 Kio).
MAX_CONTENT = 200_000
MAX_NODES = 20_000
MAX_DEPTH = 40
MAX_IMAGES = 30
MAX_EMBEDDED_BLOCKS = 40

FILE_ID = re.compile(r"^[0-9a-f]{32}$")
VALUE = re.compile(r"^(name|doctype|doc\.[a-z][a-z0-9_]{0,62})$")

# Balise -> attributs permis. Rien d'autre n'existe.
_TEXT_BLOCK = frozenset({"style"})
TAGS: dict[str, frozenset[str]] = {
    "p": _TEXT_BLOCK, "h1": _TEXT_BLOCK, "h2": _TEXT_BLOCK, "h3": _TEXT_BLOCK, "blockquote": frozenset(),
    "ul": frozenset(), "ol": frozenset({"start"}), "li": _TEXT_BLOCK,
    "strong": frozenset(), "b": frozenset(), "em": frozenset(), "i": frozenset(), "u": frozenset(), "s": frozenset(),
    "sub": frozenset(), "sup": frozenset(), "br": frozenset(), "hr": frozenset(),
    "span": frozenset({"style", "data-value"}),
    "table": frozenset({"style", "data-borders"}), "colgroup": frozenset(), "col": frozenset({"style"}),
    "thead": frozenset(), "tbody": frozenset(), "tr": frozenset(),
    "td": frozenset({"style", "colspan", "rowspan", "colwidth"}), "th": frozenset({"style", "colspan", "rowspan", "colwidth"}),
    "img": frozenset({"data-file", "data-wrap", "style"}),
    "div": frozenset({"data-block", "data-page-break"}),
}
VOID = frozenset({"br", "hr", "img", "col"})
WRAPS = ("inline", "left", "right", "front")

# Les propriétés de style permises, et la forme de leur valeur. Une couleur est une couleur, une taille une
# taille BORNÉE : jamais un `url(`, une `expression(`, un `position` choisi par l'auteur.
# Un nom de couleur CSS (`red`, `navy`) est un mot : sans parenthèse ni deux-points, il ne cache rien.
_COLOR = r"#[0-9a-f]{3}|#[0-9a-f]{6}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d{1,3})\s*)?\)|[a-z]{3,20}"
_LEN = r"\d{1,4}(\.\d{1,3})?(mm|px|%)"
STYLES: dict[str, re.Pattern[str]] = {
    "color": re.compile(rf"^({_COLOR})$"),
    "background-color": re.compile(rf"^({_COLOR})$"),
    "font-size": re.compile(r"^\d{1,2}(\.\d{1,2})?(pt|px)$"),
    "line-height": re.compile(r"^[0-4](\.\d{1,2})?$"),
    "text-align": re.compile(r"^(left|right|center|justify)$"),
    "vertical-align": re.compile(r"^(top|middle|bottom)$"),
    "width": re.compile(rf"^({_LEN})$"),
    "min-width": re.compile(rf"^({_LEN})$"),
    "height": re.compile(rf"^({_LEN})$"),
    "left": re.compile(r"^-?\d{1,3}(\.\d{1,2})?mm$"),
    "top": re.compile(r"^-?\d{1,3}(\.\d{1,2})?mm$"),
}
# Où chaque propriété a un sens : une position n'existe que sur une image « devant le texte ».
_STYLE_ON: dict[str, frozenset[str]] = {
    "p": frozenset({"text-align", "line-height", "color", "background-color", "font-size"}),
    "h1": frozenset({"text-align", "line-height", "color", "background-color", "font-size"}),
    "h2": frozenset({"text-align", "line-height", "color", "background-color", "font-size"}),
    "h3": frozenset({"text-align", "line-height", "color", "background-color", "font-size"}),
    "li": frozenset({"text-align", "line-height"}),
    "span": frozenset({"color", "background-color", "font-size", "line-height"}),
    "table": frozenset({"width", "min-width"}),
    "col": frozenset({"width", "min-width"}),
    "td": frozenset({"width", "background-color", "vertical-align", "text-align"}),
    "th": frozenset({"width", "background-color", "vertical-align", "text-align"}),
    "img": frozenset({"width", "height", "left", "top"}),
}
_FONT_PX = (6.0, 96.0)  # en points comme en pixels : un caractère illisible ou une page entière sont des fautes
_MAX_MM, _MAX_PX = 500.0, 2000.0


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    style: list[tuple[str, str]] = field(default_factory=list)
    children: list["Node | str"] = field(default_factory=list)
    block: Any = None  # le bloc incorporé, une fois validé (`data-block`)


class _Builder(HTMLParser):
    """Construit l'arbre en notant CHAQUE faute : un gabarit refusé dit tout ce qui ne va pas, pas seulement la première."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack: list[Node] = [self.root]
        self.problems: list[str] = []
        self.count = 0

    def _fault(self, msg: str) -> None:
        if len(self.problems) < 20:
            self.problems.append(msg)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.count += 1
        if tag not in TAGS:
            self._fault(f"la balise <{tag}> n'est pas permise dans un gabarit")
            tag = "span"  # on continue l'analyse pour signaler le reste ; le gabarit est refusé de toute façon
            attrs = []
        node = Node(tag)
        allowed = TAGS[tag]
        for key, value in attrs:
            if key not in allowed:
                self._fault(f"<{tag}> : l'attribut {key!r} n'est pas permis")
                continue
            value = value or ""
            if key == "style":
                node.style = _parse_style(tag, value, self._fault)
            else:
                node.attrs[key] = value
        if len(self.stack) > MAX_DEPTH:
            self._fault(f"le document est imbriqué trop profondément (plus de {MAX_DEPTH} niveaux)")
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        # Une balise fermée sans avoir été ouverte, ou dans le désordre : HTML que l'éditeur ne produit pas.
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        self._fault(f"</{tag}> ferme une balise qui n'est pas ouverte")

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:
        self._fault("un commentaire HTML n'est pas permis dans un gabarit")

    def handle_decl(self, decl: str) -> None:
        self._fault("une déclaration (<!…>) n'est pas permise dans un gabarit")

    def handle_pi(self, data: str) -> None:
        self._fault("une instruction (<?…>) n'est pas permise dans un gabarit")

    def unknown_decl(self, data: str) -> None:
        self._fault("une section <![…]> n'est pas permise dans un gabarit")


def _parse_style(tag: str, raw: str, fault: Callable[[str], None]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    allowed = _STYLE_ON.get(tag, frozenset())
    for part in raw.split(";"):
        if not part.strip():
            continue
        prop, sep, value = part.partition(":")
        prop, value = prop.strip().lower(), value.strip().lower()
        if not sep or prop not in STYLES or prop not in allowed:
            fault(f"<{tag}> : la propriété de style {prop or part.strip()!r} n'est pas permise ici")
            continue
        if not STYLES[prop].match(value) or not _bounded(prop, value):
            fault(f"<{tag}> : {prop}: {value!r} n'est pas une valeur permise")
            continue
        out.append((prop, value))
    return out


def _bounded(prop: str, value: str) -> bool:
    """Une valeur NUMÉRIQUE dans ses bornes ; un mot-clé (`center`) ou une couleur l'est déjà par son motif."""
    if prop in ("text-align", "vertical-align", "color", "background-color"):
        return True
    number = float(re.match(r"-?[\d.]+", value).group(0))  # type: ignore[union-attr]  # le motif l'a garanti
    if prop == "font-size":
        return _FONT_PX[0] <= number <= _FONT_PX[1]
    if prop == "line-height":
        return 0.5 <= number <= 4
    if prop in ("left", "top"):
        return -50 <= number <= _MAX_MM
    if value.endswith("%"):
        return number <= 100
    return number <= (_MAX_MM if value.endswith("mm") else _MAX_PX)


@dataclass
class Parsed:
    """Le document analysé : son arbre, ses fautes, et ce que le service doit résoudre (images, liens)."""

    root: Node
    problems: list[str]
    files: list[str]


def parse(content: str, block_adapter: TypeAdapter[Any] | None = None) -> Parsed:
    """Analyse le HTML d'un gabarit. Ne lève pas : rend les fautes (`problems`), que l'appelant transforme en refus.

    `block_adapter` valide un bloc incorporé (`printing` le fournit : le module ne l'importe pas, pour éviter le
    cycle `printing` → `print_document` → `printing`).
    """
    if not isinstance(content, str):
        return Parsed(Node("#root"), ["le contenu d'un gabarit est du texte (HTML)"], [])
    if len(content) > MAX_CONTENT:
        return Parsed(Node("#root"), [f"le contenu dépasse {MAX_CONTENT} caractères"], [])
    if "\x00" in content:
        return Parsed(Node("#root"), ["le contenu contient un caractère nul"], [])
    b = _Builder()
    b.feed(content)
    b.close()
    if b.count > MAX_NODES:
        b._fault(f"le document compte plus de {MAX_NODES} balises")
    files: list[str] = []
    blocks = 0

    def walk(node: Node) -> None:
        nonlocal blocks
        for child in node.children:
            if isinstance(child, str):
                continue
            a = child.attrs
            if child.tag == "span" and "data-value" in a and not VALUE.match(a["data-value"]):
                b._fault(f"valeur insérée inconnue : {a['data-value']!r} (permis : name, doctype, doc.<champ>)")
            if child.tag == "img":
                fid = a.get("data-file", "")
                if not FILE_ID.match(fid):
                    b._fault("une image cite un fichier du tenant par son identifiant (data-file), jamais une adresse")
                elif fid not in files:
                    files.append(fid)
                if a.get("data-wrap", "inline") not in WRAPS:
                    b._fault(f"image : data-wrap doit être {', '.join(WRAPS)}")
                if a.get("data-wrap") != "front" and any(p in ("left", "top") for p, _ in child.style):
                    b._fault("image : une position (left/top) n'existe que pour une image « devant le texte »")
            if child.tag in ("td", "th"):
                for key in ("colspan", "rowspan"):
                    if key in a and not (a[key].isdigit() and 1 <= int(a[key]) <= 20):
                        b._fault(f"<{child.tag}> : {key} doit être un entier de 1 à 20")
                if "colwidth" in a and not re.fullmatch(r"\d{1,4}(,\d{1,4})*", a["colwidth"]):
                    b._fault(f"<{child.tag}> : colwidth doit être une liste d'entiers")
            if child.tag == "ol" and "start" in a and not (a["start"].isdigit() and int(a["start"]) <= 10_000):
                b._fault("<ol> : start doit être un entier")
            if child.tag == "table" and a.get("data-borders", "all") not in ("all", "none"):
                b._fault("<table> : data-borders doit être all ou none")
            if child.tag == "div":
                if ("data-block" in a) == ("data-page-break" in a):
                    b._fault("<div> : seulement un bloc incorporé (data-block) ou un saut de page (data-page-break)")
                elif "data-block" in a:
                    blocks += 1
                    child.block = _embedded(a["data-block"], block_adapter, b._fault)
                if child.children and any(not isinstance(c, str) or c.strip() for c in child.children):
                    b._fault("<div> : un bloc incorporé ou un saut de page n'a pas de contenu")
                child.children = []
            walk(child)

    walk(b.root)
    if len(files) > MAX_IMAGES:
        b._fault(f"{MAX_IMAGES} images au plus dans un gabarit")
    if blocks > MAX_EMBEDDED_BLOCKS:
        b._fault(f"{MAX_EMBEDDED_BLOCKS} blocs incorporés au plus dans un gabarit")
    return Parsed(b.root, b.problems, files)


def _embedded(raw: str, adapter: TypeAdapter[Any] | None, fault: Callable[[str], None]) -> Any:
    try:
        data = json.loads(raw)
    except ValueError:
        fault("bloc incorporé : data-block n'est pas du JSON")
        return None
    if not isinstance(data, dict) or data.get("type") not in ("table", "totals", "fields"):
        fault("bloc incorporé : seulement un tableau des lignes, des totaux ou une grille de champs")
        return None
    if adapter is None:
        return data
    try:
        return adapter.validate_python(data)
    except ValidationError as e:
        fault("bloc incorporé invalide : " + "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:3]))
        return None


def iter_nodes(node: Node) -> Iterator[Node]:
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from iter_nodes(child)


def values_cited(root: Node) -> list[str]:
    """Les `doc.<champ>` que le texte insère, dans l'ordre (pour les refus au chargement et la résolution des titres)."""
    out: list[str] = []
    for n in iter_nodes(root):
        v = n.attrs.get("data-value") if n.tag == "span" else None
        if v and v.startswith("doc.") and v[4:] not in out:
            out.append(v[4:])
    return out


def embedded_blocks(root: Node) -> list[Any]:
    return [n.block for n in iter_nodes(root) if n.tag == "div" and n.block is not None]


# ── Rendu ─────────────────────────────────────────────────────────────────────


def render(
    root: Node,
    *,
    value: Callable[[str], str],
    block: Callable[[Any], str],
    image: Callable[[str], str | None],
) -> str:
    """Réécrit l'arbre en HTML pour le moteur. `value(token)` rend une valeur DÉJÀ échappée, `block(b)` un bloc
    incorporé, `image(id)` une URL `data:` (ou `None` : l'image absente ne s'imprime pas, rien ne casse)."""
    return "".join(_render(c, value, block, image) for c in root.children)


def _style(pairs: list[tuple[str, str]]) -> str:
    return ";".join(f"{p}:{v}" for p, v in pairs)


def _render(node: Node | str, value: Callable[[str], str], block: Callable[[Any], str], image: Callable[[str], str | None]) -> str:
    if isinstance(node, str):
        return escape(node, quote=False)
    tag, a = node.tag, node.attrs
    if tag == "span" and "data-value" in a:
        return value(a["data-value"])
    if tag == "div":
        if "data-page-break" in a:
            return '<div class="pb"></div>'
        return block(node.block) if node.block is not None else ""
    if tag == "img":
        src = image(a.get("data-file", ""))
        if src is None:
            return ""
        wrap = a.get("data-wrap", "inline")
        style = _style(node.style)
        return f'<img class="im {wrap}" src="{src}" alt=""' + (f' style="{style}"' if style else "") + ">"
    attrs = ""
    if node.style:
        attrs += f' style="{_style(node.style)}"'
    for key in ("colspan", "rowspan", "start"):
        if key in a and a[key].isdigit():
            attrs += f' {key}="{int(a[key])}"'
    if tag == "table":
        attrs += f' class="lay {"none" if a.get("data-borders") == "none" else "all"}"'
    if tag in VOID:
        return f"<{tag}{attrs}>"
    inner = "".join(_render(c, value, block, image) for c in node.children)
    return f"<{tag}{attrs}>{inner}</{tag}>"


# La feuille du document libre. Le Desk applique LES MÊMES règles à sa page d'édition (`PrintDocEditor.vue`) :
# ce qu'on voit en écrivant est ce qui s'imprime.
STYLESHEET = (
    # `padding-top` : sans lui la marge haute du premier titre « traverse » le conteneur et décale TOUTES les
    # images placées (mesuré : 4,2 mm sous un <p> par défaut). `display:flow-root` ferait pareil ailleurs, mais
    # PlutoPrint l'ignore — mesuré aussi.
    "div.doc{position:relative;padding-top:0.01mm}"
    "div.doc p{margin:0 0 2mm}div.doc h1{font-size:18pt;margin:0 0 3mm}div.doc h2{font-size:14pt;margin:3mm 0 2mm}"
    "div.doc h3{font-size:11pt;margin:2mm 0 1mm}div.doc ul,div.doc ol{margin:0 0 2mm;padding-left:6mm}"
    "div.doc li p{margin:0}div.doc blockquote{margin:0 0 2mm;padding-left:3mm;border-left:0.6mm solid #bbb}"
    "div.doc hr{border:0;border-top:0.2mm solid #888;margin:3mm 0}"
    "table.lay{border-collapse:collapse;width:100%;margin:0 0 2mm}"
    "table.lay td,table.lay th{padding:1mm 1.5mm;vertical-align:top}"
    "table.lay.all td,table.lay.all th{border:0.2mm solid #888}"
    "table.lay p{margin:0}"
    "img.im{max-width:100%}img.im.left{float:left;margin:0 3mm 2mm 0}img.im.right{float:right;margin:0 0 2mm 3mm}"
    "img.im.front{position:absolute;max-width:none;z-index:2}"
)
