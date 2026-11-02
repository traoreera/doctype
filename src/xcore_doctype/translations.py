"""Les traductions des définitions : libellés, descriptions, options, sections et onglets, dans la langue de l'utilisateur.

Un catalogue par plugin et par langue, `translations/<langue>.yaml` :

    messages:
      Account Name: Nom du compte
      Sales Invoice: Facture de vente

Les clés sont le TEXTE SOURCE (comme les catalogues de Frappe ou gettext), pas un chemin `DocType.champ` : un même libellé (« Company »,
« Posting Date ») revient dans des centaines de types et se traduit une fois — 3674 textes distincts pour les cinq modules de la première
version de l'ERP, au lieu de neuf mille chemins. La langue source n'est pas imposée : `doctypes/` est en anglais, le plugin `system` en
français ; le catalogue `fr` traduit le premier, `en` le second, et un texte absent reste tel quel (jamais une erreur, jamais vide).

Une ambiguïté (un même mot anglais, deux sens selon le type) se lève par une clé de CONTEXTE, qui gagne sur la clé simple :

    context:
      "Stock Entry|Purpose": Nature du mouvement

Ce qui n'est PAS traduit, exprès : les VALEURS (une option de Select, un état de workflow, un nom de document) — elles sont stockées et
comparées ; seul leur libellé l'est (`option_labels`, `state_labels`). Les messages d'erreur du serveur restent en français (v1).

La traduction s'applique au DERNIER moment, sur ce que `/meta`, la liste des types et les espaces de travail renvoient (`localize_*`),
dans la langue de la requête (`RequestContext.language`). Pur : rien n'est lu ni écrit ici.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import InvalidDefinition

LANGUAGE_RE = re.compile(r"^[a-z]{2}$")
MAX_TEXT = 2000
CONTEXT_SEP = "|"


def parse_language(header: str | None) -> str | None:
    """La première langue d'un en-tête `Accept-Language` (`fr-FR,fr;q=0.9,en` → `fr`), réduite à ses deux lettres ; None si illisible."""
    if not header or not isinstance(header, str):
        return None
    first = header.split(",", 1)[0].split(";", 1)[0].strip().lower()
    code = first.split("-", 1)[0]
    return code if LANGUAGE_RE.match(code) else None


def parse_catalog(raw: Any, *, source: str) -> tuple[dict[str, str], dict[str, str]]:
    """`(messages, contextes)` d'un catalogue, ou `InvalidDefinition` : une faute de catalogue est une erreur de CHARGEMENT."""
    if not isinstance(raw, Mapping) or set(raw) - {"messages", "context"}:
        raise InvalidDefinition(f"{source} : un catalogue a `messages` (et éventuellement `context`), rien d'autre")
    out: list[dict[str, str]] = []
    for key in ("messages", "context"):
        block = raw.get(key) or {}
        if not isinstance(block, Mapping):
            raise InvalidDefinition(f"{source} : `{key}` doit être un dictionnaire texte → traduction")
        clean: dict[str, str] = {}
        for k, v in block.items():
            if not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip():
                raise InvalidDefinition(f"{source} : `{key}` : {k!r} → {v!r} — deux textes non vides")
            if len(k) > MAX_TEXT or len(v) > MAX_TEXT:
                raise InvalidDefinition(f"{source} : `{key}` : un texte de plus de {MAX_TEXT} caractères")
            if key == "context" and CONTEXT_SEP not in k:
                raise InvalidDefinition(f"{source} : `context` : {k!r} doit s'écrire « DocType{CONTEXT_SEP}texte »")
            clean[k] = v
        out.append(clean)
    return out[0], out[1]


class Translations:
    """Les catalogues chargés, par plugin propriétaire (retirés avec lui) puis par langue."""

    def __init__(self) -> None:
        self._owned: dict[str, dict[str, tuple[dict[str, str], dict[str, str]]]] = {}
        self._merged: dict[str, tuple[dict[str, str], dict[str, str]]] = {}

    def load_dir(self, owner: str, directory: Path) -> list[str]:
        """Charge `<langue>.yaml` de `directory` pour `owner` (tout ou rien). Rend les langues chargées. Un dossier absent : rien."""
        directory = Path(directory)
        if not directory.is_dir():
            return []
        found: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
        for path in sorted(directory.glob("*.yaml")):
            lang = path.stem
            if not LANGUAGE_RE.match(lang):
                raise InvalidDefinition(f"{path} : un catalogue se nomme d'après sa langue (fr.yaml, en.yaml)")
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise InvalidDefinition(f"{path} : YAML invalide ({e})") from None
            found[lang] = parse_catalog(raw, source=str(path))
        self._owned[owner] = found
        self._rebuild()
        return sorted(found)

    def unload(self, owner: str) -> None:
        if self._owned.pop(owner, None) is not None:
            self._rebuild()

    def _rebuild(self) -> None:
        merged: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
        for owner in sorted(self._owned):  # un ordre stable : deux plugins qui traduisent le même texte, le premier par nom gagne
            for lang, (messages, context) in self._owned[owner].items():
                m, c = merged.setdefault(lang, ({}, {}))
                for k, v in messages.items():
                    m.setdefault(k, v)
                for k, v in context.items():
                    c.setdefault(k, v)
        self._merged = merged

    def languages(self) -> list[str]:
        return sorted(self._merged)

    def text(self, lang: str | None, value: Any, doctype: str | None = None) -> Any:
        """La traduction de `value` (un texte), d'abord dans le contexte de `doctype`, sinon telle quelle. Autre chose qu'un texte : inchangé."""
        if not lang or not isinstance(value, str) or not value:
            return value
        found = self._merged.get(lang)
        if found is None:
            return value
        messages, context = found
        if doctype is not None and (hit := context.get(f"{doctype}{CONTEXT_SEP}{value}")) is not None:
            return hit
        return messages.get(value, value)

    # ── Ce que le moteur renvoie ──────────────────────────────
    def localize_meta(self, meta: dict[str, Any], lang: str | None) -> dict[str, Any]:
        """`/meta` dans la langue : le type, ses champs (et ceux des lignes), ses sections et onglets, les libellés de ses options et
        des états de son workflow. Les valeurs ne bougent pas. Modifie et rend `meta`."""
        if not lang or lang not in self._merged:
            return meta
        dt = meta.get("name")
        tr = lambda v: self.text(lang, v, dt)  # noqa: E731
        for key in ("label", "description"):
            if key in meta:
                meta[key] = tr(meta[key])
        self._localize_fields(meta.get("fields") or [], lang, dt)
        for section in meta.get("layout") or []:
            for key in ("section", "tab"):
                if section.get(key):
                    section[key] = tr(section[key])
        for child in (meta.get("children") or {}).values():
            child["label"] = self.text(lang, child.get("label"), child.get("name"))
            self._localize_fields(child.get("fields") or [], lang, child.get("name"))
        flow = meta.get("workflow")
        if isinstance(flow, Mapping):
            labels = {st["name"]: tr(st["name"]) for st in flow.get("states") or [] if isinstance(st, Mapping) and tr(st["name"]) != st["name"]}
            actions = {t["action"]: tr(t["action"]) for t in flow.get("transitions") or [] if isinstance(t, Mapping) and tr(t["action"]) != t["action"]}
            if labels:
                meta["state_labels"] = labels
            if actions:
                meta["action_labels"] = actions
        return meta

    def _localize_fields(self, fields: list[dict[str, Any]], lang: str, doctype: str | None) -> None:
        for f in fields:
            for key in ("label", "description"):
                if key in f:
                    f[key] = self.text(lang, f[key], doctype)
            if f.get("fieldtype") == "Select" and isinstance(f.get("options"), list):
                labels = {str(o): self.text(lang, str(o), doctype) for o in f["options"]}
                labels = {k: v for k, v in labels.items() if v != k}
                if labels:
                    f["option_labels"] = labels

    def localize_summaries(self, items: list[dict[str, Any]], lang: str | None) -> list[dict[str, Any]]:
        """La liste des types (`/meta`) : leur libellé."""
        if lang and lang in self._merged:
            for item in items:
                item["label"] = self.text(lang, item.get("label"), item.get("name"))
        return items

    def localize_workspaces(self, items: list[dict[str, Any]], lang: str | None) -> list[dict[str, Any]]:
        """Les espaces de travail : leur libellé, leur description, et les libellés de leurs blocs (titres, raccourcis, cartes)."""
        if not lang or lang not in self._merged:
            return items
        for ws in items:
            for key in ("label", "description"):
                if ws.get(key):
                    ws[key] = self.text(lang, ws[key])
            for block in ws.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                for key in ("label", "text", "title"):
                    if isinstance(block.get(key), str):
                        block[key] = self.text(lang, block[key], block.get("doctype"))
        return items
