"""Le jeu de DocTypes d'ERP (`doctypes/`) doit rester chargeable par le moteur.

C'est la garantie de maintenance de la base déclarative : elle est faite pour être relue et modifiée à la main
(un champ ajouté, une série changée, un module de plus), et ce test dit tout de suite si une retouche la casse —
au lieu de le découvrir au démarrage d'une instance.

Il ne teste pas le moteur mais les DÉFINITIONS, exactement comme `plugin.py` les charge quand l'opérateur les
liste dans `doctype.doctypes_dirs` : un `load_dir` récursif, puis UN `validate_links()` quand tout est là.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest

from xcore_doctype.grants import GrantCatalog
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import RESERVED_GRANT_NAMESPACES

ERP_DIR = Path(__file__).resolve().parents[2] / "doctypes"

# Un champ de QUANTITÉ, et l'échelle unique du jeu : la même règle que `scripts/qty_to_decimal.py` (T14).
QTY_FIELD = re.compile(r"(^|_)(qty|quantity)($|_)")
QTY_DECIMALS = 3

pytestmark = pytest.mark.skipif(not ERP_DIR.is_dir(), reason="doctypes/ absent")


@pytest.fixture(scope="module")
def registry() -> DocTypeRegistry:
    """Le jeu complet, chargé comme le fait `plugin.py` pour les DocTypes de l'opérateur (`module` libre)."""
    reg = DocTypeRegistry()
    reg.load_dir(ERP_DIR)
    reg.validate_links()
    return reg


def test_the_whole_set_loads_and_every_link_resolves(registry: DocTypeRegistry) -> None:
    # Le chargement et `validate_links()` ont lieu dans la fixture : s'ils échouaient, le test ne démarrerait pas.
    # Ce qui est affirmé ici, c'est qu'il reste un jeu CONSÉQUENT (un dossier vidé par erreur ne passerait pas).
    assert len(registry.names()) > 700


def test_no_doctype_is_named_after_a_test_fixture(registry: DocTypeRegistry) -> None:
    """Le référentiel est du produit : plus un seul champ ne vise les DocTypes des jeux d'essai.

    `Devise`/`Pays` appartiennent à `tests/support/plugins/geo`, `Member`/`Role` à `iam` : la migration les citait,
    ce qui rendait tout le jeu inchargeable hors des tests.
    """
    fixtures = {"Devise", "Pays", "Member", "Role"}
    cited = {
        str(f.options)
        for name in registry.names()
        for f in registry.get(name).definition.fields
        if f.fieldtype in ("Link", "Table") and f.options
    }
    assert not (cited & fixtures)


def test_every_submittable_document_is_numbered(registry: DocTypeRegistry) -> None:
    """Une pièce soumise sans numéro de série est un défaut fonctionnel : pas de numéro de facture.

    `naming: hash` reste le défaut légitime d'un MAÎTRE (son `title_field` l'affiche), jamais d'une pièce.
    """
    unnumbered = [
        name
        for name in registry.names()
        if (d := registry.get(name).definition).submittable and not getattr(d.naming, "series", None)
    ]
    assert unnumbered == []


def test_series_prefixes_are_unique(registry: DocTypeRegistry) -> None:
    """Deux types qui partagent un préfixe partagent un compteur : leurs numéros s'entremêleraient."""
    prefixes = collections.Counter(
        series.split("-")[0]
        for name in registry.names()
        if (series := getattr(registry.get(name).definition.naming, "series", None))
    )
    assert [p for p, n in prefixes.items() if n > 1] == []


def test_a_grant_namespace_is_never_one_reserved_by_auth(registry: DocTypeRegistry) -> None:
    """Un grant déclaré dans un namespace réservé d'xauth part en silence : le `rbac.declare` serait vide."""
    namespaces = {spec.namespace for spec in GrantCatalog(registry).all().values()}
    assert not (namespaces - {"@authenticated"}) & RESERVED_GRANT_NAMESPACES


def test_the_shared_referential_is_readable_by_any_identified_user(registry: DocTypeRegistry) -> None:
    """Sans cela, choisir un pays dans un formulaire de crm exigerait un grant `setup` (motif du plugin `geo`)."""
    for name in ("Currency", "Country", "Gender"):
        permissions = registry.get(name).definition.permissions
        assert permissions is not None, name
        assert [e.grant for e in permissions.read] == ["@authenticated"], name


def test_no_field_duplicates_a_system_column(registry: DocTypeRegistry) -> None:
    """`amended_from` est une colonne de `xdt_document` posée par `amend` : un champ du même nom en serait une
    seconde copie, écrivable par le client et jamais renseignée par le moteur."""
    offenders = [
        f"{name}.{f.fieldname}"
        for name in registry.names()
        for f in registry.get(name).definition.fields
        if f.fieldname == "amended_from"
    ]
    assert offenders == []


def test_the_declared_mapping_carries_what_it_needs_to_know_the_remainder(registry: DocTypeRegistry) -> None:
    """`Material Request -> Stock Entry` (process/mapper.md) : la chaîne complète, pas une recopie.

    Le chargement a déjà validé la déclaration (`registry._check_mappings`). Ce qui est affirmé ici, c'est
    qu'elle garde les trois pièces sans lesquelles le mapper reprendrait des quantités PLEINES : la référence de
    ligne, le document source, et le couple de quantités.
    """
    mappings = registry.get("Stock Entry").definition.created_from or []
    by_source = {m.source: m for m in mappings}
    assert "Material Request" in by_source
    m = by_source["Material Request"]
    assert m.source_status == "submitted", "une source non figée rendrait l'indice de ligne instable"
    lines = m.lines["items"]
    assert lines.reference == "material_request_item"
    assert lines.document == "material_request"
    assert lines.quantity is not None and (lines.quantity.source, lines.quantity.target) == ("qty", "qty")


def test_a_tree_view_points_at_a_real_hierarchy(registry: DocTypeRegistry) -> None:
    """La migration prenait tout auto-lien pour une arborescence (`return_against`, `amended_from`…).

    Une vue `tree` doit citer un champ de parenté : `parent_<type>`, ou l'organigramme `reports_to`.
    """
    wrong = [
        f"{name} -> {views.tree.parent}"
        for name in registry.names()
        if (views := registry.get(name).definition.views) is not None and views.tree is not None
        and not (views.tree.parent.startswith("parent_") or views.tree.parent == "reports_to")
    ]
    assert wrong == []


def test_no_child_doctype_is_orphaned(registry: DocTypeRegistry) -> None:
    """Un `istable` qu'aucun champ `Table` ne cite ne peut servir à rien : il charge et se compte pour rien.

    Les 17 qu'avait laissés la migration étaient des vestiges de parents non migrés, dont cinq de portail web —
    un périmètre explicitement exclu (`process/palier70.md`).
    """
    cited = {
        str(f.options)
        for name in registry.names()
        for f in registry.get(name).definition.fields
        if f.fieldtype == "Table"
    }
    orphans = [name for name in registry.names() if registry.get(name).definition.istable and name not in cited]
    assert orphans == []


def test_a_document_link_of_a_mapping_demands_a_submitted_target(registry: DocTypeRegistry) -> None:
    """Le champ `document` d'un mapping désigne la pièce d'origine : elle doit être SOUMISE, jamais annulée.

    Sans cette contrainte, une facture pouvait citer une commande annulée — le mapper exige une source soumise
    pour préremplir, mais rien ne l'imposait à l'écriture ni à la soumission du document citant (D23).
    """
    loose = []
    for name in registry.names():
        definition = registry.get(name).definition
        for mapping in definition.created_from or []:
            for table, lines in mapping.lines.items():
                if lines.document is None:
                    continue
                child = registry.get(str(definition.field_map[table].options)).definition
                field = child.field_map[lines.document]
                if field.target_status != "submitted":
                    loose.append(f"{child.name}.{field.fieldname}")
    assert sorted(set(loose)) == []


def test_every_quantity_is_an_exact_decimal(registry: DocTypeRegistry) -> None:
    """Une quantité est un `Decimal` exact, jamais un `Number` flottant (T14, décision 1 de `programme.md` §3.7).

    Le cas canonique est `amount = qty × rate` : multiplier un flottant par un décimal exact pour produire de
    l'argent exigerait une règle d'arrondi, et arrondir de l'argent est une question métier. Le moteur refuse
    déjà un flottant pour un montant ; on ne le fait pas entrer par la porte du calcul.

    L'échelle est la MÊME partout (3), et ce n'est pas une préférence : `mapping.same_number_kind` exige, pour
    deux `Decimal`, le même nombre de décimales de part et d'autre d'un mapping — et dix mappings recopient une
    quantité. Une échelle uniforme satisfait la règle sans table d'exceptions.
    """
    floats = []
    scales = collections.Counter()
    for name in registry.names():
        for field in registry.get(name).definition.fields:
            if not QTY_FIELD.search(field.fieldname):
                continue
            if field.fieldtype == "Number":
                floats.append(f"{name}.{field.fieldname}")
            elif field.fieldtype == "Decimal":
                scales[field.decimals] += 1
    assert floats == [], f"quantités encore flottantes : {floats[:10]}"
    assert set(scales) == {QTY_DECIMALS}, f"échelles de quantité trouvées : {dict(scales)}"
    assert scales[QTY_DECIMALS] > 250  # le jeu en compte 273 : un dossier vidé par erreur ne passerait pas


def test_a_quantity_default_is_a_canonical_string(registry: DocTypeRegistry) -> None:
    """Un `Decimal` n'accepte pas un nombre JSON : son défaut est une chaîne à l'échelle EXACTE du champ.

    C'est le moteur qui l'exige (`decimals.canonical` : ni `1`, ni `1.0`, ni `"1.0"` pour 3 décimales), donc un
    défaut mal écrit casserait le chargement — mais seulement à la création d'un document, pas au chargement du
    type. Ce test le dit tout de suite.
    """
    from xcore_doctype.decimals import canonical

    wrong = []
    for name in registry.names():
        for field in registry.get(name).definition.fields:
            if field.fieldtype != "Decimal" or field.default is None or not QTY_FIELD.search(field.fieldname):
                continue
            if not isinstance(field.default, str) or canonical(field.default, field.decimals) != field.default:
                wrong.append(f"{name}.{field.fieldname} = {field.default!r}")
    assert wrong == []


def test_a_sales_invoice_totals_itself(registry: DocTypeRegistry) -> None:
    """Le point de tout le chantier : une facture de l'ERP a un total, et il est EXACT (T15, `compute:`).

    Mesuré avant d'écrire : les 129 champs de montant clés du jeu étaient `read_only` et rien ne les
    remplissait — une facture ne pouvait donc pas avoir de total, et un hook de comptabilisation aurait posté
    des montants vides. Ce test est la preuve que ce n'est plus vrai, sur la VRAIE définition de `doctypes/`.
    """
    from xcore_doctype.compute import apply_computes

    compiled = registry.get("Sales Invoice")
    out = apply_computes(compiled.definition, compiled.children_by_field.get, {
        "items": [
            {"qty": "2.000", "rate": "250.00", "discount_amount": "0.00"},
            {"qty": "1.000", "rate": "300.00", "discount_amount": "50.00"},
        ],
        "total_taxes_and_charges": "110.00",
        "total_advance": "0.00", "paid_amount": "60.00", "write_off_amount": "0.00",
    })
    assert [r["amount"] for r in out["items"]] == ["500.00", "300.00"]
    assert [r["net_amount"] for r in out["items"]] == ["500.00", "250.00"]
    assert out["total_qty"] == "3.000"
    assert out["total"] == "800.00"
    assert out["net_total"] == "750.00"
    assert out["grand_total"] == "910.00"
    assert out["outstanding_amount"] == "850.00"


def test_the_computed_documents_cover_the_sales_and_purchase_chain(registry: DocTypeRegistry) -> None:
    """Les pièces qui portent un montant au client ou au fournisseur ont toutes leur total (T15).

    Pas une règle automatique sur les noms : une TABLE vérifiée pièce par pièce (`scripts/add_computes.py`).
    `base_*` en est absent à dessein — c'est un montant en devise de la société, obtenu par un taux de change
    qui est un flottant, donc hors du vocabulaire fermé de `compute:` (et un flottant ne produit pas d'argent).
    """
    expected = {
        "Sales Invoice", "Purchase Invoice", "Sales Order", "Quotation",
        "Purchase Order", "Delivery Note", "Purchase Receipt",
    }
    for name in sorted(expected):
        fields = registry.get(name).definition.field_map
        assert fields["grand_total"].compute == "total + total_taxes_and_charges", name
        assert fields["total"].compute == "sum(items.amount)", name
        assert fields["grand_total"].required is False, f"{name}: un champ calculé ne s'exige pas du client"


def test_no_computed_field_is_demanded_from_the_client(registry: DocTypeRegistry) -> None:
    """Le schéma le refuse déjà type par type ; ici on le dit de TOUT le jeu, d'un coup."""
    wrong = [
        f"{name}.{f.fieldname}"
        for name in registry.names()
        for f in registry.get(name).definition.fields
        if f.compute is not None and (f.required or f.default is not None or f.fetch_from is not None)
    ]
    assert wrong == []


# ── T16 : les formulaires s'allègent et se remplissent seuls (`scripts/add_conditions.py`) ──


def _errors_on(registry: DocTypeRegistry, doctype: str, data: dict, field: str) -> list[dict]:
    """Les refus que les conditions du type portent sur `field` (les autres obligations du formulaire ne comptent pas ici)."""
    return [e for e in registry.get(doctype).rule_errors(data, None) if e["field"] == field]


def test_a_credit_note_must_cite_the_invoice_it_returns(registry: DocTypeRegistry) -> None:
    """Un avoir sans facture d'origine n'est pas un avoir : obligatoire QUAND c'est un retour, invisible sinon."""
    assert _errors_on(registry, "Sales Invoice", {"is_return": True}, "return_against")
    assert _errors_on(registry, "Sales Invoice", {"is_return": False}, "return_against") == []
    assert _errors_on(registry, "Purchase Invoice", {"is_return": True}, "return_against")


def test_an_internal_transfer_has_no_party(registry: DocTypeRegistry) -> None:
    assert _errors_on(registry, "Payment Entry", {"payment_type": "Receive"}, "party")
    assert _errors_on(registry, "Payment Entry", {"payment_type": "Internal Transfer"}, "party") == []


def test_an_employee_who_left_has_a_relieving_date(registry: DocTypeRegistry) -> None:
    assert _errors_on(registry, "Employee", {"status": "Left"}, "relieving_date")
    assert _errors_on(registry, "Employee", {"status": "Active"}, "relieving_date") == []


def test_a_deferred_revenue_line_has_its_service_period(registry: DocTypeRegistry) -> None:
    """Une condition DANS UNE LIGNE : l'erreur s'adresse à la cellule (`items.0.service_start_date`)."""
    errors = registry.get("Sales Invoice").rule_errors({"items": [{"enable_deferred_revenue": True}, {}]}, None)
    fields = {e["field"] for e in errors}
    assert "items.0.service_start_date" in fields and "items.0.deferred_revenue_account" in fields
    deferred = ("service_start_date", "service_end_date", "deferred_revenue_account")
    assert not any(f"items.1.{d}" in fields for d in deferred)  # la ligne sans revenu différé ne demande rien de tel


def test_identity_copies_fill_the_main_documents(registry: DocTypeRegistry) -> None:
    """Le nom du tiers, de l'employé et de l'article se recopient depuis leur référentiel (T16)."""
    expected = {
        ("Sales Invoice", "customer_name"): "customer.customer_name",
        ("Purchase Invoice", "supplier_name"): "supplier.supplier_name",
        ("Purchase Order", "supplier_name"): "supplier.supplier_name",
        ("Leave Application", "employee_name"): "employee.employee_name",
        ("Expense Claim", "employee_name"): "employee.employee_name",
        ("Sales Order Item", "item_name"): "item_code.item_name",
        ("Purchase Order Item", "item_name"): "item_code.item_name",
    }
    for (doctype, field), source in expected.items():
        assert registry.get(doctype).definition.field_map[field].fetch_from == source, f"{doctype}.{field}"


def test_a_copy_never_erases_what_a_user_typed(registry: DocTypeRegistry) -> None:
    """Avec un lien VIDE, le serveur vide la copie (`DocumentService._fetched`). Une copie n'est donc permise que sur
    un champ que personne ne saisit (lecture seule) ou derrière un lien obligatoire — sinon le libellé libre d'une
    ligne sans article serait effacé à l'enregistrement. Toutes les copies du jeu, sans exception."""
    wrong = []
    for name in registry.names():
        fields = registry.get(name).definition.field_map
        for f in fields.values():
            if f.fetch_from is None:
                continue
            link = fields[f.fetch_from.split(".", 1)[0]]
            if not (f.read_only or link.required):
                wrong.append(f"{name}.{f.fieldname}")
    assert wrong == []
