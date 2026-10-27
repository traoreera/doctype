"""Définition d'un DocType (doc/doctype.md §9) : le contrat dont tout le reste dérive.

On ne rejette pas en silence ce qu'on n'applique pas : une clé inconnue est refusée (`extra="forbid"`) plutôt
qu'ignorée, sinon la définition donnerait l'impression d'être validée alors qu'elle ne l'est pas. Les blocs
`permissions` et `hooks` sont appliqués depuis la phase 1, `layout` et les conditions depuis la phase 2, la
numérotation par série depuis la phase 3.

Permissions : par défaut AUCUN bloc. Les grants se déduisent de `module` et du nom du DocType (voir `conventional_grant`),
et xauth, qui résout déjà rôles et grants à chaque requête, reste la seule source de l'autorisation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StrictBool,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic import Field as PydanticField

from .dashboards import VIEW_NAMES, ViewsDef, default_dashboard
from .decimals import MAX_DECIMALS, MAX_DIGITS, canonical
from .errors import InvalidDefinition
from . import fieldtypes
from .expr import ExprError, compile_expr, referenced_fields
from .naming import SeriesPattern

FieldType = Literal[
    "Text", "LongText", "Number", "Decimal", "Currency", "Rating", "Date", "Datetime", "Boolean", "Select", "Link", "Color", "Image", "Attach", "Table",
    "Int", "Percent", "Time", "Duration", "Code", "JSON", "TextEditor", "Password", "DynamicLink",
]
# Image et Attach portent l'identifiant d'un fichier (files.py), pas le fichier.
FILE_TYPES = ("Image", "Attach")
# Des types dont la valeur ne se devine pas : ni défaut, ni pré-remplissage par l'adresse.
NO_DEFAULT_TYPES = ("JSON", "Password", "TextEditor", "DynamicLink")
# Deux types portent un nombre EXACT sous forme de chaîne canonique (decimals.py) : tout ce qui vaut pour l'un vaut pour l'autre.
DECIMAL_TYPES = ("Decimal", "Currency")

# Colonnes système : réservées, jamais acceptées comme nom de champ ni dans un payload client.
# Dans une condition d'un champ de LIGNE, `parent_<champ>` désigne le champ du document qui porte la table (`parent_purpose`).
PARENT_PREFIX = "parent_"

RESERVED_FIELDNAMES = frozenset(
    {"name", "doctype", "owner", "creation", "modified", "version", "tenant_id"}
    # Mots-clés du langage de conditions (expr.py) : un champ ainsi nommé serait introuvable dans `depends_on`.
    | {"and", "or", "not", "in", "true", "false", "null", "none"}
)

# Défaut d'un champ Date : la date du jour (fuseau de l'organisation), posée à la création si le champ est absent du payload.
TODAY = "today"

# Lignes d'un champ Table : bornées, car elles voyagent avec le document (stockage, réponse, hooks sandboxés à 64 Kio).
MAX_TABLE_ROWS = 500

_FIELDNAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_Fieldname = Annotated[str, StringConstraints(pattern=_FIELDNAME_RE.pattern)]

# `link_filters` (D29) : au plus ce nombre de champs filtrés, et le préfixe qui désigne un champ du document au lieu d'une valeur.
MAX_LINK_FILTERS = 10
# Un workflow (doc/programme.md §3.2) reste lisible d'un coup d'œil : au-delà, c'est un moteur de processus, pas un document.
MAX_WORKFLOW_STATES = 20
MAX_WORKFLOW_TRANSITIONS = 50
# Onglets d'une fiche : un `tab` par section (le même pour les sections qui se suivent). Au-delà, une fiche n'est plus lisible.
MAX_TABS = 12
# « Créer depuis » (doc/mapper.md) : le nombre de sources depuis lesquelles un type se préremplit. Au-delà, le menu « Créer > »
# de la source n'est plus un geste mais un annuaire.
MAX_MAPPINGS = 10
LINK_FILTER_REF = "$"
# Même forme que les grants d'auth (`plugin:ressource:action`, voir xauth `_GRANT_NAME_RE`). Les NOMS DE RÔLE sont des
# données d'auth, libres et propres à chaque tenant : un fichier de DocType ne peut pas les connaître. Un grant, si.
# Le grant spécial `@authenticated` : tout utilisateur IDENTIFIÉ, sans rien lui déclarer. Pour ce que chacun fait de soi-même (son profil) : aucun rôle n'est
# nécessaire, un membre sans le moindre grant peut donc modifier le sien. Il n'est jamais déclaré à auth (voir `grant_specs`). Jamais pour le contexte anonyme.
AUTHENTICATED = "@authenticated"
# Le rôle d'auth que porte le PROPRIÉTAIRE d'une organisation (xauth : `tenant_admin`, posé à la création du tenant). Les grants
# délégables lui sont demandés d'office (`GrantSpec.as_declaration`), sans quoi un propriétaire n'aurait aucun droit chez lui.
TENANT_OWNER_ROLE = "tenant_admin"
_Grant = Annotated[str, StringConstraints(pattern=r"^(@authenticated|[a-z0-9_]+:[a-z0-9_]+(:[a-z0-9_]+)*)$", max_length=140)]
# `module` est le NAMESPACE des grants déduits : nom du plugin propriétaire, forme imposée par auth (`[a-z0-9_]+`).
_Module = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", max_length=63)]
# Recopié d'xauth (app/auth/src/services/rbac/grants.py, RESERVED_NAMESPACES) : auth REFUSE en silence la déclaration d'un
# grant dans ces namespaces, et le `rbac.declare` partirait vide. On échoue au chargement plutôt que de le découvrir en production.
RESERVED_GRANT_NAMESPACES = frozenset({"admin", "rbac", "auth", "tenants", "user", "license", "plugins"})
_PrincipalAttr = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]

HOOK_EVENTS = (
    "validate", "before_save", "after_save", "on_submit", "on_cancel",
    "before_insert", "after_insert", "on_trash", "after_delete", "before_cancel", "on_update_after_submit",
)
# Hooks d'un changement d'ÉTAT (soumission, annulation, modification d'un document soumis) : dans la transaction du document, avec le droit d'écrire
# d'autres documents (hooks.HookTransaction). Ils exigent un DocType `submittable` et ne peuvent pas être des `action:` sandboxées.
TRANSITION_HOOKS = ("on_submit", "on_cancel", "before_cancel", "on_update_after_submit")
# Seuls `validate` et `before_save` acceptent un hook `action:` (sandboxé, appelé AVANT la transaction) : tout autre événement a lieu DANS la transaction ou APRÈS le commit.
ACTION_HOOK_EVENTS = ("validate", "before_save")
# Les types dont une valeur peut être UNIQUE (`unique: true`) : des valeurs qui se comparent exactement, jamais un flottant ni un texte libre long.
UNIQUE_TYPES = ("Text", "Int", "Select", "Link", "Date", "Datetime", "Time", "Duration", "Decimal", "Currency")
# Les types d'un champ de NOMMAGE (`naming: {field: …}`) : le nom du document est sa valeur.
NAMING_FIELD_TYPES = ("Text", "Int", "Select", "Link")
# Les états d'un document soumettable (`docstatus`) : leur nom dans les définitions, leur libellé en français, leur valeur.
DOCSTATUS_NAMES = {"draft": 0, "submitted": 1, "cancelled": 2}
DOCSTATUS_LABELS = {0: "brouillon", 1: "soumis", 2: "annulé"}
# Rétention d'historique : le défaut de `max_versions` (25 entrées conservées par document) et le maximum accepté.
DEFAULT_MAX_VERSIONS = 25
# D16 : un nom de hook est une CLÉ du registre, jamais un chemin importé. Aucune importation dynamique.
TRUSTED_HOOK_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
ACTION_HOOK_RE = re.compile(r"^action:([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)$")

# `match` compare une valeur de document à un attribut du principal : des chaînes seulement
# (pas de coercion entre types, pas d'égalité `None == None`).
MATCHABLE_TYPES = ("Text", "Select", "Link")

# `match` est GELÉ. Il lit `principal["user"][attr]`, or xauth ne met que `email` et `tenant_id` dans `user`. Le bon
# mécanisme chez nous est le scope d'auth (`scope_type`/`scope_id`), qui exige une action listant les scope_id d'un
# utilisateur pour un grant (voir doc/phase1.md §7). Le code reste en place et testé ; ce drapeau empêche seulement
# une définition de s'en servir tant qu'aucun backend ne peut l'alimenter. Les tests le lèvent explicitement.
MATCH_ENABLED = False
_DocTypeName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9 _-]{0,62}$")]


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fieldname: _Fieldname
    label: str | None = None
    fieldtype: FieldType
    required: StrictBool = False
    default: Any = None
    options: list[str] | str | None = None
    # Decimal seulement, et obligatoire pour lui : le nombre de décimales est une décision métier (3 pour des kilos, 2 pour des euros),
    # pas une valeur par défaut qu'on découvrirait en production.
    decimals: Annotated[int, PydanticField(ge=0, le=MAX_DECIMALS, strict=True)] | None = None
    # Rating seulement : le nombre d'étoiles (5 par défaut). Text seulement : `format` (courriel, téléphone, URL), contrôlé à l'écriture.
    stars: Annotated[int, PydanticField(ge=1, le=fieldtypes.MAX_STARS, strict=True)] | None = None
    format: Literal["email", "phone", "url"] | None = None
    # Image et Attach seulement : la taille maximale d'un fichier, en Mo (10 par défaut, 50 au plus).
    max_mb: Annotated[int, PydanticField(ge=1, le=fieldtypes.MAX_FILE_MB, strict=True)] | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    hidden: StrictBool = False  # jamais affiché (formulaire ni liste), mais stocké et validé
    # Table seulement : une « sélection multiple » (des étiquettes à saisir et à retirer) au lieu d'une grille. Le DocType des lignes n'a qu'UN champ, un Link.
    multiselect: StrictBool = False
    # Unicité (par organisation) : deux documents du même DocType ne portent jamais la même valeur ; garantie par la base (table `xdt_unique`, voir query.py), donc sans course.
    unique: StrictBool = False
    # Fixé une fois : une valeur posée ne change plus après l'enregistrement (le champ reste libre tant qu'il est vide).
    set_only_once: StrictBool = False
    # Jamais recopié par « Dupliquer » ni par un amendement (un numéro de série, une date de signature).
    no_copy: StrictBool = False
    # Modifiable même quand le document est soumis (un commentaire de suivi) ; exige un DocType `submittable`. Tout autre champ reste figé.
    allow_on_submit: StrictBool = False
    # `lien.champ` : la valeur est LUE du document que le Link `lien` désigne, à chaque enregistrement (jamais saisie : la valeur envoyée est écrasée). Le Desk la montre en lecture seule.
    fetch_from: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}\.[a-z][a-z0-9_]{0,62}$")] | None = None
    # Lecture seule : le document le MONTRE mais un client ne peut pas le changer (une mise à jour qui le modifie est refusée, `read_only`). Un hook de
    # confiance et la source d'un DocType virtuel l'écrivent, eux. Sans condition, contrairement à `read_only_depends_on`.
    read_only: StrictBool = False
    in_list_view: StrictBool = False
    # Conditions (doc/doctype.md D22), voir expr.py. Compilées et vérifiées par DocTypeDef (noms de champs connus).
    # Une quantité s'affiche avec son unité : `unit_from: uom` (un champ texte du même document ou de la même ligne) ou
    # `unit_from: item.uom` (le champ `uom` du document que le champ Link `item` désigne). Affichage seulement : rien n'est stocké.
    unit_from: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}(\.[a-z][a-z0-9_]{0,62})?$")] | None = None
    depends_on: str | None = None  # faux : le champ est masqué, et n'est plus obligatoire
    mandatory_depends_on: str | None = None  # vrai : le champ est obligatoire
    read_only_depends_on: str | None = None  # vrai (sur l'état AVANT modification) : le champ ne peut plus changer
    # Link et DynamicLink seulement : l'état exigé du document visé. `not_cancelled` = « pas annulé » (brouillon ou soumis).
    # Sans cela, un lien ne regarde que l'existence : la cible soumise par erreur reste citée (D23).
    target_status: Literal["draft", "submitted", "cancelled", "not_cancelled"] | list[Literal["draft", "submitted", "cancelled"]] | None = None
    # Link seulement (D29) : restreint les documents qu'on peut choisir, par des filtres sur la CIBLE — `{actif: true}` (une valeur
    # littérale) ou `{pays: "$pays"}` (la valeur d'un autre champ du même document, ou de la même ligne). Le Desk s'en sert pour
    # l'autocomplétion ET le serveur le vérifie à l'écriture (`link_filtered`) : c'est une contrainte, pas une aide d'interface.
    # Un DynamicLink en est exclu : sa cible change d'un document à l'autre, les champs filtrés ne seraient pas contrôlables au chargement.
    link_filters: dict[_Fieldname, Any] | None = None

    @property
    def allowed_docstatus(self) -> frozenset[int] | None:
        """Les `docstatus` admis chez la cible d'un Link, ou `None` (tous, sans contrainte)."""
        if self.target_status is None:
            return None
        if self.target_status == "not_cancelled":
            return frozenset({0, 1})
        names = [self.target_status] if isinstance(self.target_status, str) else self.target_status
        return frozenset(DOCSTATUS_NAMES[n] for n in names)

    @model_validator(mode="before")
    @classmethod
    def _type_defaults(cls, data: Any) -> Any:
        """Ce que le type décide quand on ne dit rien : 2 décimales pour une devise, 5 étoiles pour une note."""
        if isinstance(data, dict):
            kind = data.get("fieldtype")
            if kind == "Currency" and data.get("decimals") is None:
                data = {**data, "decimals": fieldtypes.DEFAULT_CURRENCY_DECIMALS}
            if kind == "Rating" and data.get("stars") is None:
                data = {**data, "stars": fieldtypes.DEFAULT_STARS}
        return data

    @model_validator(mode="after")
    def _check(self) -> "FieldDef":
        name, ftype, options = self.fieldname, self.fieldtype, self.options
        if name in RESERVED_FIELDNAMES:
            raise ValueError(f"fieldname réservé : {name!r}")

        if ftype == "Select":
            if not isinstance(options, list) or not options:
                raise ValueError(f"{name}: Select exige une liste `options` non vide")
            if len(set(options)) != len(options) or any(not o for o in options):
                raise ValueError(f"{name}: `options` doit contenir des valeurs uniques non vides")
        elif ftype == "DynamicLink":
            if not isinstance(options, str) or not re.match(r"^[a-z][a-z0-9_]{0,62}$", options):
                raise ValueError(f"{name}: DynamicLink exige `options` = nom du champ (Text ou Select, du même document ou de la même ligne) qui dit DANS quel DocType chercher")
        elif ftype in ("Link", "Table"):
            if not isinstance(options, str) or not options:
                what = "du DocType cible" if ftype == "Link" else "du DocType des lignes (`istable: true`)"
                raise ValueError(f"{name}: {ftype} exige `options` = nom {what}")
            if ftype == "Table" and self.in_list_view:
                raise ValueError(f"{name}: un champ Table ne peut pas figurer dans la liste (`in_list_view`)")
        elif ftype == "Currency":
            if options is not None and (not isinstance(options, str) or not fieldtypes.CURRENCY_CODE_RE.match(options)):
                raise ValueError(f"{name}: `options` d'une Currency est un code ISO 4217 (EUR, USD…)")
        elif ftype == "Code":
            if options is not None and options not in fieldtypes.CODE_LANGUAGES:
                raise ValueError(f"{name}: `options` d'un Code est son langage ({', '.join(fieldtypes.CODE_LANGUAGES)})")
        elif options is not None:
            raise ValueError(f"{name}: `options` n'a pas de sens pour {ftype}")

        if self.format is not None and ftype != "Text":
            raise ValueError(f"{name}: `format` n'a de sens que pour un Text")
        if self.max_mb is not None and ftype not in FILE_TYPES:
            raise ValueError(f"{name}: `max_mb` n'a de sens que pour une Image ou un Attach")
        if ftype in FILE_TYPES and self.default is not None:
            raise ValueError(f"{name}: un fichier n'a pas de valeur par défaut")
        if self.unique and ftype not in UNIQUE_TYPES:
            raise ValueError(f"{name}: `unique` n'a pas de sens pour {ftype} (permis : {', '.join(UNIQUE_TYPES)})")
        if self.fetch_from is not None and self.default is not None:
            raise ValueError(f"{name}: un champ `fetch_from` prend la valeur du document lié, il n'a pas de `default`")
        if self.fetch_from is not None and self.fetch_from.split(".")[0] == name:
            raise ValueError(f"{name}: `fetch_from` ne peut pas viser le champ lui-même")
        if self.fetch_from is not None and (self.required or self.mandatory_depends_on is not None):
            raise ValueError(f"{name}: un champ `fetch_from` est rempli par le serveur, il ne peut pas être obligatoire (le lien qui le fournit, lui, peut l'être)")
        if self.multiselect and ftype != "Table":
            raise ValueError(f"{name}: `multiselect` n'a de sens que pour un champ Table")
        if ftype in NO_DEFAULT_TYPES and self.default is not None:
            raise ValueError(f"{name}: un champ {ftype} n'a pas de valeur par défaut")
        if self.stars is not None and ftype != "Rating":
            raise ValueError(f"{name}: `stars` n'a de sens que pour un Rating")
        if self.target_status is not None:
            if ftype not in ("Link", "DynamicLink"):
                raise ValueError(f"{name}: `target_status` n'a de sens que pour un Link ou un DynamicLink")
            if isinstance(self.target_status, list):
                if not self.target_status:
                    raise ValueError(f"{name}: `target_status` exige au moins un état (draft, submitted, cancelled)")
                if len(set(self.target_status)) != len(self.target_status):
                    raise ValueError(f"{name}: `target_status` répète un état")
                if "not_cancelled" in self.target_status:
                    raise ValueError(f"{name}: `not_cancelled` se dit seul, à côté des états nommés (draft, submitted, cancelled)")

        if self.link_filters is not None:
            if ftype != "Link":
                raise ValueError(f"{name}: `link_filters` n'a de sens que pour un Link")
            if not self.link_filters:
                raise ValueError(f"{name}: `link_filters` vide ne restreint rien : retirez-le")
            if len(self.link_filters) > MAX_LINK_FILTERS:
                raise ValueError(f"{name}: `link_filters` est limité à {MAX_LINK_FILTERS} champs")
            for key, value in self.link_filters.items():
                if isinstance(value, str) and value.startswith(LINK_FILTER_REF):
                    source = value[len(LINK_FILTER_REF):]
                    if not _FIELDNAME_RE.match(source):
                        raise ValueError(f"{name}: `link_filters.{key}` : {value!r} doit nommer un champ (`$nom_de_champ`)")
                    if source == name:
                        raise ValueError(f"{name}: `link_filters.{key}` ne peut pas lire le champ Link lui-même")
                elif value is None or not isinstance(value, (str, int, float, bool)):
                    raise ValueError(f"{name}: `link_filters.{key}` : une valeur simple, ou `$champ` pour lire un champ de ce document")

        if ftype in DECIMAL_TYPES:
            if self.decimals is None:
                raise ValueError(f"{name}: Decimal exige `decimals` (0 à {MAX_DECIMALS}) : le nombre de chiffres après la virgule")
            if self.decimals > MAX_DIGITS:  # pragma: no cover - borné par MAX_DECIMALS
                raise ValueError(f"{name}: decimals trop grand")
        elif self.decimals is not None:
            raise ValueError(f"{name}: `decimals` n'a de sens que pour Decimal et Currency")

        if self.unit_from is not None and ftype not in ("Number", "Decimal", "Currency", "Int"):
            raise ValueError(f"{name}: `unit_from` n'a de sens que pour un Number ou un Decimal")
        if self.default is not None and not _default_matches(self):
            raise ValueError(f"{name}: `default` {self.default!r} incompatible avec {ftype}")
        return self


def _default_matches(f: FieldDef) -> bool:
    v = f.default
    match f.fieldtype:
        case "Number":
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        case "Int" | "Percent" | "Time" | "Duration":
            check = {"Int": fieldtypes.integer, "Percent": fieldtypes.percent, "Time": fieldtypes.time_of_day, "Duration": fieldtypes.duration}[f.fieldtype]
            try:
                check(v)
            except (TypeError, ValueError):
                return False
            return True
        case "JSON" | "Password" | "TextEditor":
            return False  # (déjà refusé plus haut)
        case "Decimal" | "Currency":
            try:
                canonical(v, f.decimals or 0)
            except ValueError:
                return False
            return True
        case "Rating":
            try:
                fieldtypes.rating(v, f.stars or fieldtypes.DEFAULT_STARS)
            except ValueError:
                return False
            return True
        case "Color":
            return isinstance(v, str) and bool(fieldtypes.COLOR_RE.match(v))
        case "Datetime":
            if v == fieldtypes.NOW:
                return True  # l'instant de la création, posé par le service (document.py)
            try:
                fieldtypes.iso_datetime(v)
            except (TypeError, ValueError):
                return False
            return True
        case "Boolean":
            return isinstance(v, bool)
        case "Select":
            return v in (f.options or [])
        case "Table":
            return False  # les lignes se saisissent, elles n'ont pas de valeur par défaut
        case "Date":
            if v == TODAY:
                return True  # « aujourd'hui » : résolu à la création (document.py), dans le fuseau de l'organisation
            try:
                return date.fromisoformat(v).isoformat() == v  # forme canonique seulement, comme à l'écriture
            except (TypeError, ValueError):
                return False
        case "Image" | "Attach":
            return False  # (déjà refusé plus haut ; un fichier ne se pré-remplit pas)
        case "Text":
            if not isinstance(v, str):
                return False
            try:
                return bool(not f.format or fieldtypes.text_format(f.format, v))
            except ValueError:
                return False
        case _:  # LongText, Code, Link
            return isinstance(v, str)


class MatchRule(BaseModel):
    """`doc[field] == principal["user"][principal_attr]` (D6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: _Fieldname
    principal_attr: _PrincipalAttr


_ACTIONS = ("read", "write", "create", "delete", "submit", "cancel")
# `submit` et `cancel` n'existent que pour un DocType `submittable` (voir `DocTypeDef.rules_for`). Amender un document annulé
# est une CRÉATION (droit `create`) : une copie en brouillon, sans droit propre.
SUBMIT_ACTIONS = ("submit", "cancel")

# Une action sans règle explicite retombe sur la convention `<module>:<doctype>:<action>`. `create` partage le grant de
# `write` : chez xauth, `xsales:customer:write` signifie « Créer/modifier un client ».
CONVENTION = {"read": "read", "write": "write", "create": "write", "delete": "delete", "submit": "submit", "cancel": "cancel"}
_CONVENTION_LABEL = {
    "read": "Consulter {label}",
    "write": "Créer/modifier {label}",
    "delete": "Supprimer {label}",
    "submit": "Soumettre {label}",
    "cancel": "Annuler {label}",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def conventional_grant(module: str, doctype: str, action: str) -> str:
    return f"{module}:{slugify(doctype)}:{CONVENTION[action]}"


class PermissionEntry(BaseModel):
    """Ce qui ouvre une action : un grant, éventuellement restreint aux documents dont on est propriétaire.

    Écrite en YAML, une entrée est une simple chaîne (`"sales:customer:read"`) ou, pour un cas particulier, un objet
    (`{ grant: "sales:customer:own", if_owner: true }`). Le grant se vérifie dans `principal["permissions"]` : auth a déjà
    composé les rôles en grants, par tenant. `description` et `tenant_grantable` ne servent qu'à la DÉCLARATION du grant
    auprès d'auth (voir `grants.GrantCatalog`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    grant: _Grant
    if_owner: StrictBool = False
    match: MatchRule | None = None
    description: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    tenant_grantable: StrictBool | None = None  # None = non précisé (accordable par un tenant)

    @model_validator(mode="after")
    def _not_a_role(self) -> "PermissionEntry":
        if self.grant.split(":", 1)[0] == "role":
            raise ValueError(
                f"{self.grant!r}: les rôles ne sont pas pris en charge, citez un grant (auth compose les rôles en grants)"
            )
        return self


def _entry_from_str(value: Any) -> Any:
    return {"grant": value} if isinstance(value, str) else value


_Entry = Annotated[PermissionEntry, BeforeValidator(_entry_from_str)]


class PermissionsDef(BaseModel):
    """`permissions: {read: [...], write: [...], create: [...], delete: [...], submit: [...], cancel: [...]}`.

    Une action absente retombe sur la convention ; `[]` ne l'ouvre à PERSONNE. Les entrées d'une action s'additionnent (OU).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    read: list[_Entry] | None = None
    write: list[_Entry] | None = None
    create: list[_Entry] | None = None
    delete: list[_Entry] | None = None
    submit: list[_Entry] | None = None  # DocType `submittable` seulement
    cancel: list[_Entry] | None = None  # id.

    @model_validator(mode="before")
    @classmethod
    def _reject_row_form(cls, data: Any) -> Any:
        if isinstance(data, list):
            raise ValueError(
                "`permissions` est un objet {read, write, create, delete, submit, cancel} dont chaque clé liste des grants ; "
                "l'ancienne liste de lignes {grant, read, write…} n'existe plus (doc/phase1.md §7)"
            )
        return data

    @model_validator(mode="after")
    def _no_duplicates(self) -> "PermissionsDef":
        for action in _ACTIONS:
            seen: set[tuple] = set()
            for e in getattr(self, action) or []:
                key = (e.grant, e.if_owner, e.match)
                if key in seen:
                    raise ValueError(f"permissions.{action}: entrée dupliquée {e.grant!r}")
                seen.add(key)
        return self


@dataclass(frozen=True, slots=True)
class GrantSpec:
    """Un grant tel qu'auth l'attend dans `rbac.declare` : nom, description, groupe d'affichage, délégable ou non."""

    name: str
    description: str
    group: str
    tenant_grantable: bool

    @property
    def namespace(self) -> str:
        return self.name.split(":", 1)[0]

    def as_declaration(self) -> dict[str, Any]:
        """Le grant au format de `rbac.declare`.

        `default_roles` (revue d'authentification du 26 oct. 2026) : auth verse d'office tout grant déclaré au rôle `admin` de la
        PLATEFORME, et à personne d'autre. Sans cette ligne, celui qui crée une organisation en est propriétaire — il invite, il
        lit le journal — mais ne peut ni lire ni créer un seul document chez lui : il faut qu'un administrateur plateforme le
        débloque. On demande donc que les grants DÉLÉGABLES aillent aussi à `tenant_admin`, le rôle du propriétaire. Un grant
        fermé (`tenant_grantable: false` — le journal de stock, les soldes) n'y va pas : ce qu'un tenant ne peut pas déléguer,
        il ne l'obtient pas d'office non plus.
        """
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "group": self.group,
            "tenant_grantable": self.tenant_grantable,
        }
        if self.tenant_grantable:
            out["default_roles"] = [TENANT_OWNER_ROLE]
        return out


class WorkflowState(BaseModel):
    """Un état du workflow, et l'état de document (`docstatus`) qu'il porte.

    C'est la décision centrale (doc/programme.md §3.2) : un document a UN automate, pas deux. Un état déclare donc dans quel
    `docstatus` il met le document, et c'est la transition qui fait passer les deux ensemble — plutôt qu'un champ d'état
    parallèle à `docstatus`, qui autoriserait « Approuvé » sur un document annulé.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
    docstatus: Literal["draft", "submitted", "cancelled"] = "draft"


class WorkflowTransition(BaseModel):
    """Le passage d'un état à un autre, et qui a le droit de le faire.

    Le droit est un GRANT, jamais un rôle : un nom de rôle est du texte libre propre à chaque organisation chez auth, et aucune
    règle de DocType ne s'y fie (`PermissionEntry._not_a_role`). Sans `grant`, la convention s'applique :
    `<module>:<doctype>:workflow_<action>`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
    from_: Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)] = PydanticField(alias="from")
    to: Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
    grant: _Grant | None = None
    # Une condition du langage `expr.py` jugée sur le document STOCKÉ : la transition n'est ouverte que si elle est vraie. C'est une
    # règle métier (« on n'approuve pas un congé sans motif »), pas un droit : qui a le grant sans la condition ne voit pas l'action.
    condition: str | None = None
    # Séparation des pouvoirs : celui qui a créé le document ne peut pas faire cette transition (approuver sa propre demande).
    not_owner: StrictBool = False
    description: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None


class WorkflowDef(BaseModel):
    """L'automate d'un DocType : des états, des transitions, et rien d'exécutable.

    Le PREMIER état est celui d'un document qui vient d'être créé. Dès qu'un état porte un `docstatus` autre que `draft`, le
    DocType doit être `submittable` : c'est le workflow qui soumet et annule, et `/submit` et `/cancel` lui laissent la place.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    states: Annotated[list[WorkflowState], PydanticField(min_length=2, max_length=MAX_WORKFLOW_STATES)]
    transitions: Annotated[list[WorkflowTransition], PydanticField(min_length=1, max_length=MAX_WORKFLOW_TRANSITIONS)]

    @model_validator(mode="after")
    def _check(self) -> "WorkflowDef":
        names = [st.name for st in self.states]
        if len(set(names)) != len(names):
            raise ValueError("workflow.states: deux états portent le même nom")
        known = set(names)
        actions: set[str] = set()
        for t in self.transitions:
            if t.action in actions:
                raise ValueError(f"workflow.transitions: deux transitions s'appellent {t.action!r}")
            actions.add(t.action)
            for where, state in (("from", t.from_), ("to", t.to)):
                if state not in known:
                    raise ValueError(f"workflow.transitions[{t.action}].{where}: état inconnu {state!r}")
            if t.from_ == t.to:
                raise ValueError(f"workflow.transitions[{t.action}]: `from` et `to` sont le même état")
        reachable = {self.states[0].name} | {t.to for t in self.transitions}
        if orphans := known - reachable:
            raise ValueError(f"workflow.states: {', '.join(sorted(orphans))} : aucun chemin n'y mène (ni état initial, ni cible d'une transition)")
        return self

    @property
    def initial(self) -> str:
        return self.states[0].name

    def docstatus_of(self, state: str) -> int:
        """Le `docstatus` que cet état impose (0 brouillon, 1 soumis, 2 annulé)."""
        word = next(st.docstatus for st in self.states if st.name == state)
        return {"draft": 0, "submitted": 1, "cancelled": 2}[word]

    def transition(self, action: str) -> WorkflowTransition | None:
        return next((t for t in self.transitions if t.action == action), None)


class LayoutSection(BaseModel):
    """Une section du formulaire : des colonnes de champs (doc/doctype.md §12), rangée dans un onglet quand la fiche en a (`tab`, voir
    `layout_tab_problems`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tab: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None  # « » seul n'est pas un nom
    section: Annotated[str, StringConstraints(min_length=1, max_length=140)] | None = None
    collapsible: StrictBool = False
    collapsed: StrictBool = False
    columns: list[list[_Fieldname]] = PydanticField(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _check(self) -> "LayoutSection":
        if self.collapsed and not self.collapsible:
            raise ValueError("`collapsed` exige `collapsible: true`")
        if any(not col for col in self.columns):
            raise ValueError("une colonne ne peut pas être vide")
        return self


def layout_tab_problems(tabs: list[str | None]) -> list[str]:
    """Les onglets d'une disposition, donnés par le `tab` de chaque section dans l'ordre : soit aucune section n'en nomme, soit toutes ; les
    sections d'un même onglet se suivent (un onglet n'apparaît pas deux fois) ; au plus `MAX_TABS`. La MÊME règle pour le YAML et pour
    l'habillage de formulaire d'une organisation (`overrides.validate_form_override`)."""
    named = [t for t in tabs if t is not None]
    if not named:
        return []
    problems: list[str] = []
    if len(named) != len(tabs):
        missing = ", ".join(str(i) for i, t in enumerate(tabs) if t is None)
        problems.append(f"une fiche à onglets range CHAQUE section dans un onglet (`tab`) : section(s) {missing} sans onglet")
    seen: list[str] = []
    for t in named:
        if seen and seen[-1] == t:
            continue
        if t in seen:
            problems.append(f"les sections de l'onglet {t!r} doivent se suivre")
            continue
        seen.append(t)
    if len(seen) > MAX_TABS:
        problems.append(f"au plus {MAX_TABS} onglets ({len(seen)})")
    return problems


def complete_layout(sections: list[dict[str, Any]], visible: list[str]) -> list[dict[str, Any]]:
    """Ajoute à une disposition les champs `visible` qu'elle ne place pas, dans un dernier bloc sans titre : ajouter un champ ne le cache
    jamais en silence. Sur une fiche à onglets, ce bloc ferme le PREMIER onglet, celui qui s'ouvre. Sert au YAML (`effective_layout`) ET à
    l'habillage d'une organisation (`overrides.apply_form_override`), qui ne place pas les tables et ignore les champs ajoutés après lui."""
    # `tab` n'apparaît que sur une fiche à onglets : une disposition sans onglet garde exactement sa forme d'avant.
    out = [dict(sec) for sec in sections]
    placed = {n for sec in out for col in sec.get("columns") or [] for n in col}
    rest = [n for n in visible if n not in placed]
    if rest:
        first = out[0].get("tab") if out else None
        at = next((i for i, sec in enumerate(out) if sec.get("tab") != first), len(out))  # après la dernière section du premier onglet
        out.insert(at, {**({"tab": first} if first is not None else {}), "section": None, "collapsible": False, "collapsed": False, "columns": [rest]})
    return out


class HooksDef(BaseModel):
    """Hooks d'un DocType (D2, D4, D16).

    - `"sales.check_credit"` : nom d'un hook de confiance enregistré dans le registre (voir `hooks.HookRegistry`) ;
    - `"action:sales_rules.check_credit"` : action d'un plugin (typiquement sandboxé), appelée AVANT la transaction (D21).
    `after_save` n'accepte que des hooks de confiance : une notification ne peut rien annuler. `on_submit` et `on_cancel`
    aussi : ils tournent DANS la transaction (ce qu'un plugin sandboxé, appelé hors transaction, ne peut pas faire).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    on_validate: list[str] = PydanticField(default_factory=list, alias="validate")
    before_save: list[str] = PydanticField(default_factory=list)
    after_save: list[str] = PydanticField(default_factory=list)
    on_submit: list[str] = PydanticField(default_factory=list)
    on_cancel: list[str] = PydanticField(default_factory=list)
    before_insert: list[str] = PydanticField(default_factory=list)  # création seulement, dans la transaction, après before_save : peut modifier et refuser
    after_insert: list[str] = PydanticField(default_factory=list)  # création seulement, après le commit
    on_trash: list[str] = PydanticField(default_factory=list)  # dans la transaction, juste avant la suppression : peut la refuser
    after_delete: list[str] = PydanticField(default_factory=list)  # après le commit de la suppression
    before_cancel: list[str] = PydanticField(default_factory=list)  # `submittable` : dans la transaction de l'annulation, avant `on_cancel`
    on_update_after_submit: list[str] = PydanticField(default_factory=list)  # `submittable` : un document soumis dont un champ `allow_on_submit` a changé

    @model_validator(mode="after")
    def _check(self) -> "HooksDef":
        for event in HOOK_EVENTS:
            seen: set[str] = set()
            for ref in self.for_event(event):
                if ref in seen:
                    raise ValueError(f"hooks.{event}: hook dupliqué {ref!r}")
                seen.add(ref)
                if ref.startswith("action:"):
                    if event not in ACTION_HOOK_EVENTS:
                        raise ValueError(
                            f"hooks.{event}: `action:` interdit (les hooks sandboxés précèdent la transaction, D21 : "
                            "ils ne peuvent ni la rejoindre ni suivre le commit)"
                        )
                    if not ACTION_HOOK_RE.match(ref):
                        raise ValueError(f"hooks.{event}: référence d'action invalide {ref!r} (attendu action:plugin.action)")
                elif not TRUSTED_HOOK_RE.match(ref):
                    raise ValueError(f"hooks.{event}: nom de hook invalide {ref!r}")
        return self

    def for_event(self, event: str) -> list[str]:
        return list(self.on_validate if event == "validate" else getattr(self, event))

    def all_refs(self) -> list[str]:
        return [r for e in HOOK_EVENTS for r in self.for_event(e)]


class NamingField(BaseModel):
    """`naming: {field: code}` : le nom du document est la valeur de ce champ (obligatoire, unique par nature : une seconde création est refusée, 409)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: _Fieldname


class NamingSeries(BaseModel):
    """`naming: {series: "FA-{YYYY}-{#####}"}` : les noms viennent d'un compteur par tenant (voir naming.py, D7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    series: str

    @model_validator(mode="after")
    def _parse(self) -> "NamingSeries":
        try:
            SeriesPattern.parse(self.series)
        except ValueError as e:
            raise ValueError(f"naming.series: {e}") from None
        return self


class MappingQuantity(BaseModel):
    """Le couple de quantités qui fait le « reste à faire » : le champ de la ligne SOURCE, celui de la ligne CIBLE.

    Sans lui, le mapper recopie des quantités pleines et rien n'empêche de transférer deux fois. Avec lui,
    `restant = quantité source − somme des quantités déjà citées` (doc/mapper.md §5).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: _Fieldname
    target: _Fieldname


class MappingLines(BaseModel):
    """Comment les lignes d'une table de la source remplissent une table de la cible.

    `reference` et `document` nomment les deux champs de la ligne CIBLE qui gardent la trace de son origine (l'indice
    de la ligne source, et le document source). Ils sont normalement `read_only` — l'utilisateur ne doit pas les
    retoucher — et le moteur les écrit lui-même, puis les VÉRIFIE à l'écriture (doc/mapper.md §4).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_: _Fieldname = PydanticField(alias="from")
    reference: _Fieldname | None = None
    document: _Fieldname | None = None
    quantity: MappingQuantity | None = None
    # Comme au niveau du document : seulement les EXCEPTIONS à la recopie par même nom (`champ cible: champ source`).
    fields: dict[_Fieldname, _Fieldname] = PydanticField(default_factory=dict)
    exclude: list[_Fieldname] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "MappingLines":
        if self.quantity is not None and self.reference is None:
            raise ValueError("quantity: exige `reference` (sans elle, on ne sait pas quelle ligne source a déjà été servie)")
        if self.document is not None and self.document == self.reference:
            raise ValueError("`document` et `reference` ne peuvent pas être le même champ")
        for target_field in self.fields:
            if target_field in self.exclude:
                raise ValueError(f"fields: {target_field!r} est aussi dans `exclude`")
        return self


class CreatedFrom(BaseModel):
    """« Ce type se crée depuis celui-là » : de quoi préremplir un brouillon (doc/mapper.md).

    Déclaré sur la CIBLE : son plugin possède ses champs et sait les remplir, un module nouveau se branche sans
    toucher au YAML de la source, et le droit en jeu est son propre `create`. Le bouton, lui, s'affiche sur la source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: _DocTypeName
    label: Annotated[str, StringConstraints(min_length=1, max_length=140)] | None = None
    # L'état qu'il faut à la source. `submitted` par défaut : on ne transfère pas un brouillon, et un document soumis
    # est FIGÉ, ce qui rend l'indice de ses lignes stable — toute la référence de ligne en dépend.
    source_status: Literal["submitted", "draft", "any"] = "submitted"
    fields: dict[_Fieldname, _Fieldname] = PydanticField(default_factory=dict)
    exclude: list[_Fieldname] = PydanticField(default_factory=list)
    # `champ Table de la cible: comment le remplir`.
    lines: dict[_Fieldname, MappingLines] = PydanticField(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "CreatedFrom":
        for target_field in self.fields:
            if target_field in self.exclude:
                raise ValueError(f"fields: {target_field!r} est aussi dans `exclude`")
        return self


class DocTypeDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    name: _DocTypeName
    label: str | None = None
    module: _Module | None = None
    # `hash` : identifiant aléatoire. `prompt` : l'utilisateur le choisit à la création. `{field: x}` : la valeur du champ `x`. `owner` : l'identifiant de celui qui crée (un document PAR utilisateur : son profil ; une seconde création est refusée,
    # 409). `{series: ...}` : numéro séquentiel par tenant, sans doublon ni trou (naming.py).
    naming: Literal["hash", "owner", "prompt"] | NamingField | NamingSeries = "hash"
    # Renommer un document (`POST …/rename`) : son nom change et tous les Link qui le citent le suivent. Pas pour un nom qui n'est pas libre (`owner`, `field`, série).
    allow_rename: StrictBool = False
    # L'automate métier du document (doc/programme.md §3.2) : des états, des transitions, et qui a le droit de les faire.
    # Il PILOTE `docstatus` au lieu de s'y ajouter : un document a un seul état. `/submit` et `/cancel` lui cèdent la place.
    workflow: WorkflowDef | None = None
    # DocType « enfant » : ses documents sont les LIGNES d'un champ Table d'un autre DocType (tableau JSON dans le parent).
    # Il n'a ni route, ni droits, ni hooks, ni nom propre : voir `_child_rules`.
    istable: StrictBool = False
    # Historique des créations, modifications et suppressions (versions.py). Désactivable pour un DocType très bavard.
    track_changes: StrictBool = True
    # Rétention d'historique : les N entrées les plus récentes survivent au document (au moins 1, au plus 1000), les plus vieilles sont
    # purgées dans la même transaction que l'écriture (pas d'entretien de nuit).
    max_versions: Annotated[int, PydanticField(ge=1, le=1000, strict=True)] = DEFAULT_MAX_VERSIONS
    # Document « soumettable » : brouillon (0) -> soumis (1) -> annulé (2). Un document soumis est figé : plus de modification
    # ni de suppression, on l'annule (et on l'amende : une copie en brouillon). Voir document.py (submit, cancel, amend).
    submittable: StrictBool = False
    # Document UNIQUE (« Paramètres ») : un seul enregistrement par organisation, nommé comme son type. Il n'a ni liste, ni création, ni suppression :
    # le Desk l'ouvre directement, vide de valeurs tant qu'on ne l'a pas enregistré (ses défauts). Exemple : les réglages d'un module.
    single: StrictBool = False
    # Un réglage GLOBAL de l'application (pas celui d'un module) : il s'édite dans la rubrique « Général » des Paramètres, à côté des réglages de
    # base (`System Settings`, plugin `system`), au lieu d'avoir sa propre rubrique. C'est par là qu'un plugin ajoute un réglage global.
    settings_group: Literal["general"] | None = None
    # DocType VIRTUEL : ses documents ne sont pas dans la table générique mais chez une SOURCE enregistrée sous ce nom (`doctype_sources`, voir sources.py) :
    # les comptes, rôles et permissions d'xauth, par exemple. Il a liste, filtres, tri, recherche, liens, rapports et droits comme n'importe quel DocType ;
    # la source reste l'unique vérité (rien n'est copié). Voir `_source_rules` pour ce qu'il ne peut pas avoir.
    source: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,62}$")] | None = None
    fields: list[FieldDef] = PydanticField(min_length=1)
    layout: list[LayoutSection] | None = None  # absent : un seul bloc, les champs dans l'ordre
    title_field: _Fieldname | None = None  # champ qui désigne un document (liste, lien) ; défaut : son nom
    # Le champ Image qui sert de photo au document (profil, article) : l'avatar de la liste, de la fiche et des cartes.
    image_field: _Fieldname | None = None
    # Icône du type dans la barre latérale et les raccourcis : un nom PrimeIcons sans préfixe (`receipt` pour `pi pi-receipt`).
    icon: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")] | None = None
    # Absent : convention `<module>:<doctype>:<action>` (voir `rules_for`). Sans `module` ni règle : personne, hors contexte système.
    permissions: PermissionsDef = PydanticField(default_factory=PermissionsDef)
    hooks: HooksDef = PydanticField(default_factory=HooksDef)
    # Les vues du type dans le Desk : la liste et le rapport existent toujours, le tableau de bord est généré ; on déclare ici le Kanban,
    # le Calendrier, le Gantt (ils exigent des champs précis) ou un tableau de bord à soi. Voir `dashboards.py`.
    views: ViewsDef | None = None
    # « Créer depuis » (doc/mapper.md) : les types depuis lesquels CE type se préremplit. Le bouton s'affiche sur la source,
    # mais la déclaration vit ici. Une seule par source. Vérifié au CHARGEMENT (`registry._check_mappings`).
    created_from: Annotated[list[CreatedFrom], PydanticField(max_length=MAX_MAPPINGS)] | None = None

    @model_validator(mode="after")
    def _one_mapping_per_source(self) -> "DocTypeDef":
        sources = [m.source for m in self.created_from or []]
        if len(set(sources)) != len(sources):
            raise ValueError("created_from: deux déclarations pour la même source")
        if self.created_from and self.istable:
            raise ValueError("created_from: un DocType enfant ne se crée pas, il est une ligne de son parent")
        if self.created_from and self.single:
            raise ValueError("created_from: un document unique ne se crée pas depuis un autre")
        if self.created_from and self.source is not None:
            raise ValueError("created_from: un DocType virtuel n'est pas écrit par le moteur")
        return self

    @model_validator(mode="after")
    def _module_is_declarable(self) -> "DocTypeDef":
        if self.module in RESERVED_GRANT_NAMESPACES:
            raise ValueError(
                f"module {self.module!r} est un namespace réservé d'auth : les grants déduits ne pourraient pas être déclarés"
            )
        return self

    @model_validator(mode="after")
    def _unique_fieldnames(self) -> "DocTypeDef":
        seen: set[str] = set()
        for f in self.fields:
            if f.fieldname in seen:
                raise ValueError(f"fieldname dupliqué : {f.fieldname!r}")
            seen.add(f.fieldname)
        return self

    @model_validator(mode="after")
    def _child_rules(self) -> "DocTypeDef":
        """Un DocType enfant ne garde que des champs : tout ce qu'il ne pourrait pas appliquer est refusé, pas ignoré."""
        if not self.istable:
            return self
        refused = {
            "module": self.module is not None,
            "permissions": any(getattr(self.permissions, a) is not None for a in _ACTIONS),
            "hooks": bool(self.hooks.all_refs()),
            "layout": self.layout is not None,
            "title_field": self.title_field is not None,
            "image_field": self.image_field is not None,
            "icon": self.icon is not None,
            "naming": self.naming != "hash",
            "views": self.views is not None,
            "single": self.single,
        }
        refused["submittable"] = self.submittable
        bad = [k for k, v in refused.items() if v]
        if bad:
            raise ValueError(
                f"istable: {', '.join(bad)} n'a pas de sens pour un DocType enfant (ses lignes héritent des droits, "
                "des hooks et de la mise en page du parent)"
            )
        for f in self.fields:
            if f.fieldtype == "Table":
                raise ValueError(f"istable: {f.fieldname!r} est un champ Table, les tables imbriquées ne sont pas prises en charge")
            if f.fieldtype in FILE_TYPES:
                raise ValueError(f"istable: {f.fieldname!r} est un champ {f.fieldtype} : les fichiers ne sont pas pris en charge dans les lignes d'une table")
            if f.fieldname.startswith(PARENT_PREFIX):
                raise ValueError(f"istable: {f.fieldname}: le préfixe `{PARENT_PREFIX}` est réservé aux conditions qui lisent le document parent")
            if f.read_only_depends_on is not None:
                raise ValueError(f"istable: {f.fieldname}.read_only_depends_on : pas pris en charge dans les lignes (une ligne n'a pas d'identité à comparer)")
            if f.default == TODAY:
                raise ValueError(f"istable: {f.fieldname}: le défaut `today` n'est pas pris en charge dans les lignes")
        return self

    @model_validator(mode="after")
    def _single_rules(self) -> "DocTypeDef":
        """Un document unique n'a ni numérotation, ni état soumis, ni liste : ce qui n'aurait aucun sens est refusé, pas ignoré."""
        if self.settings_group is not None and not self.single:
            raise ValueError("settings_group: seul un document unique (`single: true`) est un réglage")
        if not self.single:
            return self
        if self.submittable:
            raise ValueError("single: un document unique n'est pas `submittable` (il n'y en a qu'un, on le modifie)")
        if self.naming != "hash":
            raise ValueError("single: un document unique est nommé comme son type : `naming` n'a pas de sens")
        if self.views is not None and (self.views.kanban or self.views.calendar or self.views.gantt or self.views.tree):
            raise ValueError("single: un seul document, pas de Kanban, de calendrier, de Gantt ni d'arbre")
        return self

    @model_validator(mode="after")
    def _property_rules(self) -> "DocTypeDef":
        """Les propriétés de champ et de DocType ajoutées au palier 70 % : ce qu'elles exigent de leur voisinage, vérifié au chargement (jamais découvert à la première saisie)."""
        by_name = {f.fieldname: f for f in self.fields}
        for f in self.fields:
            if self.istable:
                for prop in ("unique", "set_only_once", "allow_on_submit", "no_copy"):
                    if getattr(f, prop):
                        raise ValueError(f"istable: {f.fieldname}.{prop} : pas pris en charge dans les lignes (une ligne n'a pas d'identité propre)")
            if self.source is not None:
                for prop in ("unique", "allow_on_submit"):
                    if getattr(f, prop):
                        raise ValueError(f"source: {f.fieldname}.{prop} n'a pas de sens pour un DocType virtuel (le moteur ne stocke pas ses documents)")
                if f.fetch_from is not None or f.fieldtype == "DynamicLink":
                    raise ValueError(f"source: {f.fieldname} ({'fetch_from' if f.fetch_from else 'DynamicLink'}) n'a pas de sens pour un DocType virtuel")
            if f.allow_on_submit and not self.submittable:
                raise ValueError(f"{f.fieldname}: `allow_on_submit` n'a de sens que pour un DocType `submittable: true`")
            if f.allow_on_submit and f.fetch_from is not None:
                raise ValueError(f"{f.fieldname}: `allow_on_submit` et `fetch_from` ne vont pas ensemble (un document soumis ne relit plus ses liens)")
            if f.fetch_from is not None:
                link = by_name.get(f.fetch_from.split(".")[0])
                if link is None or link.fieldtype != "Link":
                    raise ValueError(f"{f.fieldname}: `fetch_from` {f.fetch_from!r} : {f.fetch_from.split('.')[0]!r} doit être un champ Link de ce DocType")
                if f.fieldtype in ("Table", "Password", *FILE_TYPES):
                    raise ValueError(f"{f.fieldname}: un champ {f.fieldtype} ne peut pas être rempli par `fetch_from`")
            if f.fieldtype == "DynamicLink":
                holder = by_name.get(str(f.options))
                if holder is None or holder.fieldtype not in ("Text", "Select"):
                    raise ValueError(f"{f.fieldname}: DynamicLink `options` {f.options!r} doit nommer un champ Text ou Select de ce DocType (le nom du DocType visé)")
            # `link_filters: {champ: "$autre"}` : `$autre` est un champ de CE document (ou de cette ligne). Les champs de la CIBLE,
            # eux, sont vérifiés au chargement du registre (`registry._check_link_filters`), qui seul connaît les autres DocTypes.
            for key, value in (f.link_filters or {}).items():
                if not (isinstance(value, str) and value.startswith(LINK_FILTER_REF)):
                    continue
                source = by_name.get(value[len(LINK_FILTER_REF):])
                if source is None:
                    raise ValueError(f"{f.fieldname}: `link_filters.{key}` : {value!r} ne nomme aucun champ de ce DocType")
                if source.fieldtype in ("Table", "Password", *FILE_TYPES):
                    raise ValueError(f"{f.fieldname}: `link_filters.{key}` : un champ {source.fieldtype} ne peut pas servir de valeur de filtre")
        if (wf := self.workflow) is not None:
            if self.istable or self.single or self.source is not None:
                raise ValueError("workflow: pas de sens pour un DocType enfant, unique ou virtuel")
            if not self.track_changes:
                raise ValueError("workflow: exige `track_changes` (qui a fait passer le document d'un état à l'autre est le sujet même)")
            if any(st.docstatus != "draft" for st in wf.states) and not self.submittable:
                raise ValueError("workflow: un état `submitted` ou `cancelled` exige `submittable: true` (c'est le workflow qui soumet)")
            for t in wf.transitions:
                if t.grant is None:
                    continue
                if t.grant == AUTHENTICATED:
                    raise ValueError(f"workflow.transitions[{t.action}].grant: `@authenticated` n'ouvre pas une transition (il faut un grant)")
                # Même règle que partout ailleurs (`PermissionEntry._not_a_role`) : un nom de rôle est du texte libre, propre à
                # chaque organisation chez auth. Une transition s'ouvre par un grant, qu'auth compose depuis les rôles.
                if t.grant.split(":", 1)[0] == "role":
                    raise ValueError(f"workflow.transitions[{t.action}].grant: les rôles ne sont pas pris en charge, citez un grant")
        if "max_versions" in self.model_fields_set and not self.track_changes:
            raise ValueError("max_versions: exige `track_changes: true` (sans historique, rien à purger)")
        naming = self.naming
        if isinstance(naming, NamingField):
            target = by_name.get(naming.field)
            if target is None:
                raise ValueError(f"naming.field: champ inconnu {naming.field!r}")
            if target.fieldtype not in NAMING_FIELD_TYPES or not target.required or target.depends_on is not None:
                raise ValueError(f"naming.field: {naming.field!r} doit être `required: true`, sans `depends_on`, de type {', '.join(NAMING_FIELD_TYPES)}")
            if target.fetch_from is not None:
                raise ValueError(f"naming.field: {naming.field!r} ne peut pas être rempli par `fetch_from` (le nom doit exister avant l'écriture)")
        if self.allow_rename:
            bad = [k for k, v in {"istable": self.istable, "single": self.single, "source": self.source is not None,
                                  "naming owner": naming == "owner", "naming series": isinstance(naming, NamingSeries), "naming field": isinstance(naming, NamingField)}.items() if v]
            if bad:
                raise ValueError(f"allow_rename: n'a pas de sens avec {', '.join(bad)} (le nom n'est pas libre)")
        return self

    @model_validator(mode="after")
    def _source_rules(self) -> "DocTypeDef":
        """Un DocType virtuel n'a ni numérotation, ni état soumis, ni hook, ni historique, ni fichier : ce que le moteur ne saurait pas appliquer à un
        document qu'il ne stocke pas est refusé, pas ignoré. (Les noms viennent de la source, ses tables de lignes restent dans le document.)"""
        if self.source is None:
            return self
        refused = {
            "single": self.single,
            "istable": self.istable,
            "submittable": self.submittable,
            "naming": self.naming != "hash",
            "hooks": bool(self.hooks.all_refs()),
            "settings_group": self.settings_group is not None,
            "track_changes": self.track_changes is True and "track_changes" in self.model_fields_set,
            "max_versions": "max_versions" in self.model_fields_set,
        }
        bad = [k for k, v in refused.items() if v]
        if bad:
            raise ValueError(f"source: {', '.join(bad)} n'a de sens pour un DocType virtuel (le moteur ne stocke pas ses documents)")
        for f in self.fields:
            if f.fieldtype in FILE_TYPES:
                raise ValueError(f"source: {f.fieldname!r} est un champ {f.fieldtype} : pas de fichier dans un DocType virtuel")
            if f.fieldtype == "Password":
                raise ValueError(f"source: {f.fieldname!r} est un Password : la source garde ses propres secrets")
            if f.default == TODAY:
                raise ValueError(f"source: {f.fieldname}: le défaut `today` n'est pas pris en charge dans un DocType virtuel")
            if f.target_status is not None:
                raise ValueError(f"source: {f.fieldname}.target_status : un DocType virtuel n'a pas d'état que le moteur pourrait juger")
        return self

    @model_validator(mode="after")
    def _submittable_rules(self) -> "DocTypeDef":
        """Ce qui n'a de sens que pour un DocType soumettable est refusé ailleurs, et inversement ce que l'état figé exige."""
        if self.submittable:
            if not self.track_changes:
                # Qui a soumis, qui a annulé : la trace est ce qui rend un document figé opposable. Pas de DocType soumettable sans.
                raise ValueError("submittable: exige `track_changes: true` (l'historique garde qui a soumis et annulé)")
            return self
        stray = [a for a in SUBMIT_ACTIONS if getattr(self.permissions, a) is not None]
        stray += [e for e in TRANSITION_HOOKS if self.hooks.for_event(e)]
        if stray:
            raise ValueError(f"{', '.join(stray)} n'a de sens que pour un DocType `submittable: true`")
        return self

    @model_validator(mode="after")
    def _check_conditions_and_layout(self) -> "DocTypeDef":
        names = [f.fieldname for f in self.fields]
        tables = {f.fieldname for f in self.fields if f.fieldtype == "Table"}
        decimals = {f.fieldname for f in self.fields if f.fieldtype in DECIMAL_TYPES}
        for f in self.fields:
            for kind in ("depends_on", "mandatory_depends_on", "read_only_depends_on"):
                src = getattr(f, kind)
                if src is None:
                    continue
                try:
                    # Une ligne peut lire son parent (`parent_purpose`) : ces noms-là ne se vérifient qu'à l'enregistrement du parent.
                    used = referenced_fields(compile_expr(src, names, allow_prefix=PARENT_PREFIX if self.istable else None))
                except ExprError as e:
                    raise ValueError(f"{f.fieldname}.{kind}: {e}") from None
                if used & tables:
                    raise ValueError(f"{f.fieldname}.{kind}: un champ Table ({', '.join(sorted(used & tables))}) n'a pas de valeur comparable")
                opaque = {x.fieldname for x in self.fields if x.fieldtype in ("Password", "JSON")}
                if used & opaque:
                    raise ValueError(f"{f.fieldname}.{kind}: un champ Password ou JSON ({', '.join(sorted(used & opaque))}) n'a pas de valeur comparable")
                if used & decimals:
                    # Une chaîne décimale ne se compare pas à un nombre sans perdre l'exactitude, et la grammaire est typée strictement.
                    raise ValueError(f"{f.fieldname}.{kind}: un champ Decimal ({', '.join(sorted(used & decimals))}) ne peut pas servir dans une condition")
        if (wf := self.workflow) is not None:
            opaque = {x.fieldname for x in self.fields if x.fieldtype in ("Password", "JSON")}
            for t in wf.transitions:
                if t.condition is None:
                    continue
                try:
                    used = referenced_fields(compile_expr(t.condition, names))
                except ExprError as e:
                    raise ValueError(f"workflow.transitions[{t.action}].condition: {e}") from None
                if bad := used & (tables | opaque | decimals):
                    raise ValueError(
                        f"workflow.transitions[{t.action}].condition: {', '.join(sorted(bad))} n'a pas de valeur comparable "
                        "(table, secret, JSON ou décimal)"
                    )
        if self.title_field is not None:
            target = next((f for f in self.fields if f.fieldname == self.title_field), None)
            if target is None:
                raise ValueError(f"title_field: champ inconnu {self.title_field!r}")
            if target.fieldtype not in MATCHABLE_TYPES:
                raise ValueError(f"title_field: {target.fieldname!r} ({target.fieldtype}) doit être textuel")
        if self.image_field is not None:
            image = next((f for f in self.fields if f.fieldname == self.image_field), None)
            if image is None:
                raise ValueError(f"image_field: champ inconnu {self.image_field!r}")
            if image.fieldtype != "Image":
                raise ValueError(f"image_field: {image.fieldname!r} est un {image.fieldtype}, il faut une Image")
        hidden = {f.fieldname for f in self.fields if f.hidden}
        for problem in layout_tab_problems([s.tab for s in self.layout or []]):
            raise ValueError(f"layout: {problem}")
        placed: set[str] = set()
        for section in self.layout or []:
            for column in section.columns:
                for name in column:
                    if name not in names:
                        raise ValueError(f"layout: champ inconnu {name!r}")
                    if name in hidden:
                        raise ValueError(f"layout: le champ caché {name!r} ne peut pas être placé")
                    if name in placed:
                        raise ValueError(f"layout: champ placé deux fois {name!r}")
                    placed.add(name)
        return self

    @model_validator(mode="after")
    def _check_views(self) -> "DocTypeDef":
        """Chaque vue déclarée désigne des champs qui existent et qui ont le bon type : sinon elle serait vide ou cassée en production."""
        v = self.views
        if v is None:
            return self
        by_name = {f.fieldname: f for f in self.fields}

        def field(where: str, name: str, types: tuple[str, ...], what: str) -> FieldDef:
            target = by_name.get(name)
            if target is None:
                raise ValueError(f"views.{where}: champ inconnu {name!r}")
            if target.fieldtype not in types:
                raise ValueError(f"views.{where}: {name!r} est un {target.fieldtype}, il faut {what}")
            return target

        scalar = ("Text", "LongText", "Number", "Decimal", "Currency", "Rating", "Date", "Datetime", "Boolean", "Select", "Link", "Color", "Int", "Percent", "Time", "Duration")
        if v.kanban is not None:
            column = field("kanban.field", v.kanban.field, ("Select",), "un Select (une colonne par valeur)")
            if not column.required and column.default is None:
                # Un document sans valeur n'aurait aucune colonne : il disparaîtrait du tableau sans que rien ne le dise.
                raise ValueError(f"views.kanban.field: {column.fieldname!r} doit être `required: true` ou avoir un `default` (chaque document doit avoir une colonne)")
            for name in v.kanban.card_fields or []:
                field("kanban.card_fields", name, scalar, "un champ simple")
        if v.calendar is not None:
            field("calendar.start", v.calendar.start, ("Date",), "une Date")
            if v.calendar.end is not None:
                field("calendar.end", v.calendar.end, ("Date",), "une Date")
            if v.calendar.title is not None:
                field("calendar.title", v.calendar.title, scalar, "un champ simple")
            if v.calendar.color is not None:
                field("calendar.color", v.calendar.color, ("Select",), "un Select (une teinte par valeur)")
        if v.gantt is not None:
            field("gantt.start", v.gantt.start, ("Date",), "une Date")
            field("gantt.end", v.gantt.end, ("Date",), "une Date")
            if v.gantt.start == v.gantt.end:
                raise ValueError("views.gantt: `start` et `end` doivent être deux champs différents")
            if v.gantt.title is not None:
                field("gantt.title", v.gantt.title, scalar, "un champ simple")
            if v.gantt.color is not None:
                field("gantt.color", v.gantt.color, ("Select",), "un Select (une teinte par valeur)")
        if v.tree is not None:
            parent = field("tree.parent", v.tree.parent, ("Link",), "un Link vers ce même type")
            if parent.options != self.name:
                raise ValueError(f"views.tree.parent: {parent.fieldname!r} est un Link vers {parent.options!r}, il faut un Link vers {self.name!r}")
        if v.default not in self.available_views():
            raise ValueError(f"views.default: la vue {v.default!r} n'est pas déclarée (disponibles : {', '.join(self.available_views())})")
        return self

    def available_views(self) -> list[str]:
        """Les vues que le Desk propose pour ce type, dans l'ordre du sélecteur."""
        v = self.views
        declared = {
            "kanban": v.kanban if v else None,
            "calendar": v.calendar if v else None,
            "gantt": v.gantt if v else None,
            "tree": v.tree if v else None,
        }
        return [name for name in VIEW_NAMES if name in ("list", "report", "dashboard") or declared.get(name) is not None]

    def effective_views(self) -> dict[str, Any]:
        """Ce que `/meta` envoie : les vues disponibles, celle qui s'ouvre d'abord, la configuration de chacune et le tableau de bord
        (celui du type, ou le généré). Les blocs du tableau de bord citent toujours leur `doctype`, comme ceux d'un espace."""
        v = self.views or ViewsDef()
        out: dict[str, Any] = {"available": self.available_views(), "default": v.default}
        if v.kanban is not None:
            cards = v.kanban.card_fields or [n for n in self.list_columns() if n != v.kanban.field][:3]
            out["kanban"] = {"field": v.kanban.field, "card_fields": cards}
        if v.calendar is not None:
            out["calendar"] = v.calendar.model_dump(mode="json", exclude_none=True)
        if v.gantt is not None:
            out["gantt"] = v.gantt.model_dump(mode="json", exclude_none=True)
        if v.tree is not None:
            out["tree"] = {"parent": v.tree.parent}
        blocks = [b.model_dump(mode="json", exclude_none=True) for b in v.dashboard] if v.dashboard else default_dashboard(self)
        out["dashboard"] = [{**b, "doctype": self.name} if b["type"] in ("number_card", "chart") else b for b in blocks]
        return out

    @model_validator(mode="after")
    def _check_permissions(self) -> "DocTypeDef":
        by_name = {f.fieldname: f for f in self.fields}
        for action in _ACTIONS:
            for entry in getattr(self.permissions, action) or []:
                if entry.match is None:
                    continue
                if not MATCH_ENABLED:
                    raise ValueError(
                        "permissions: `match` est gelé (aucun backend d'auth ne fournit d'attribut ou de scope par "
                        "utilisateur) ; utilisez `if_owner`, voir doc/phase1.md §7"
                    )
                target = by_name.get(entry.match.field)
                if target is None:
                    raise ValueError(f"permissions: match sur un champ inconnu {entry.match.field!r}")
                if target.fieldtype not in MATCHABLE_TYPES:
                    raise ValueError(
                        f"permissions: match sur {target.fieldname!r} ({target.fieldtype}) impossible, "
                        f"types acceptés : {', '.join(MATCHABLE_TYPES)}"
                    )
        self.grant_specs()  # métadonnées contradictoires pour un même grant : refusées dès le chargement
        return self

    def effective_layout(self) -> list[dict[str, Any]]:
        """Le formulaire tel que le Desk l'affiche : les sections déclarées, puis les champs non placés dans un dernier bloc
        (ajouter un champ ne le cache donc jamais en silence ; pour le cacher, `hidden: true`). Sur une fiche à onglets, ce bloc ferme le
        PREMIER onglet, celui qui s'ouvre : un champ oublié par la disposition reste sous les yeux, pas au fond d'un onglet."""
        sections = [
            {**({"tab": s.tab} if s.tab is not None else {}),
             "section": s.section, "collapsible": s.collapsible, "collapsed": s.collapsed, "columns": [list(c) for c in s.columns]}
            for s in (self.layout or [])
        ]
        return complete_layout(sections, [f.fieldname for f in self.fields if not f.hidden])

    def list_columns(self) -> list[str]:
        """Colonnes de la liste : les champs `in_list_view`, sinon les quatre premiers champs visibles (jamais une table)."""
        visible = [f for f in self.fields if not f.hidden and f.fieldtype != "Table"]
        flagged = [f.fieldname for f in visible if f.in_list_view]
        return flagged or [f.fieldname for f in visible[:4]]

    def rules_for(self, action: str) -> tuple[PermissionEntry, ...]:
        """Les entrées qui ouvrent `action` : celles de `permissions`, sinon la convention (si `module`), sinon aucune."""
        if action in SUBMIT_ACTIONS and not self.submittable:
            return ()  # pas de grant `submit`/`cancel` pour un DocType qui ne se soumet pas : rien à déclarer à auth
        explicit = getattr(self.permissions, action)
        if explicit is not None:
            return tuple(explicit)
        if self.module is None:
            return ()
        return (PermissionEntry(grant=conventional_grant(self.module, self.name, action)),)

    def grant_specs(self) -> dict[str, GrantSpec]:
        """Tous les grants cités par ce DocType (déduits ou explicites), prêts pour `rbac.declare`."""
        label, slug = self.label or self.name, slugify(self.name)
        explicit: dict[str, dict[str, Any]] = {}
        defaults: dict[str, str] = {}
        for action in _ACTIONS:
            for e in self.rules_for(action):
                if e.grant == AUTHENTICATED:
                    continue  # pas un grant d'auth : rien à déclarer
                mine = explicit.setdefault(e.grant, {})
                for key, value in (("description", e.description), ("tenant_grantable", e.tenant_grantable)):
                    if value is None:
                        continue
                    if key in mine and mine[key] != value:
                        raise ValueError(f"permissions: {e.grant!r} a un {key} contradictoire ({mine[key]!r} / {value!r})")
                    mine[key] = value
                if e.grant not in defaults:
                    conventional = self.module is not None and e.grant == conventional_grant(self.module, self.name, action)
                    defaults[e.grant] = (
                        _CONVENTION_LABEL[CONVENTION[action]].format(label=label) if conventional else f"{label} : {e.grant}"
                    )
        # Les transitions du workflow ouvrent leurs propres grants : sans eux, auth ne les connaît pas et personne ne peut
        # jamais faire passer un document d'un état à l'autre. Un grant par transition dit exactement qui fait quoi.
        for t in self.workflow.transitions if self.workflow is not None else ():
            grant = self.transition_grant(t)
            if grant is None:
                continue
            defaults.setdefault(grant, f"{t.action} {label}")
            mine = explicit.setdefault(grant, {})  # tout nom de `defaults` doit aussi exister ici (voir la construction ci-dessous)
            if t.description is not None:
                mine.setdefault("description", t.description)
        return {
            name: GrantSpec(
                name=name,
                description=explicit[name].get("description", defaults[name]),
                group=f"{name.split(':', 1)[0]}-{slug}",
                tenant_grantable=explicit[name].get("tenant_grantable", True),
            )
            for name in sorted(defaults)
        }

    def transition_grant(self, t: "WorkflowTransition") -> str | None:
        """Le grant qui ouvre cette transition : celui que le YAML nomme, sinon la convention `<module>:<doctype>:workflow_<action>`."""
        if t.grant is not None:
            return t.grant
        if self.module is None:
            return None
        return f"{self.module}:{slugify(self.name)}:workflow_{slugify(t.action)}"

    @property
    def unique_fields(self) -> tuple[str, ...]:
        """Les champs `unique: true` : une valeur ne se retrouve jamais sur deux documents de l'organisation (voir `Query.sync_unique`)."""
        return tuple(f.fieldname for f in self.fields if f.unique)

    @property
    def editable_after_submit(self) -> tuple[str, ...]:
        """Les champs `allow_on_submit` : ce qu'un document soumis laisse modifier."""
        return tuple(f.fieldname for f in self.fields if f.allow_on_submit)

    @property
    def secret_fields(self) -> tuple[str, ...]:
        """Les champs `Password` : chiffrés au repos, jamais renvoyés (voir sealing.py)."""
        return tuple(f.fieldname for f in self.fields if f.fieldtype == "Password")

    @property
    def series(self) -> SeriesPattern | None:
        return SeriesPattern.parse(self.naming.series) if isinstance(self.naming, NamingSeries) else None

    @property
    def field_map(self) -> dict[str, FieldDef]:
        return {f.fieldname: f for f in self.fields}


def parse_definition(raw: Any, *, source: str = "<définition>") -> DocTypeDef:
    """Valide une définition brute (dict issu de YAML/JSON) ou lève `InvalidDefinition`."""
    if not isinstance(raw, dict):
        raise InvalidDefinition(f"{source}: la définition doit être un objet")
    try:
        return DocTypeDef.model_validate(raw)
    except ValidationError as e:
        lines = []
        for err in e.errors(include_url=False, include_input=False):
            where = ".".join(str(p) for p in err["loc"]) or "(racine)"
            lines.append(f"  - {where}: {err['msg']}")
        raise InvalidDefinition(f"{source}: définition invalide\n" + "\n".join(lines)) from e
