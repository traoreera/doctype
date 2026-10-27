"""Le jeu de DocTypes d'ERP (`doctypes/`) doit rester chargeable par le moteur.

C'est la garantie de maintenance de la base déclarative : elle est faite pour être relue et modifiée à la main
(un champ ajouté, une série changée, un module de plus), et ce test dit tout de suite si une retouche la casse —
au lieu de le découvrir au démarrage d'une instance.

Il ne teste pas le moteur mais les DÉFINITIONS, exactement comme `plugin.py` les charge quand l'opérateur les
liste dans `doctype.doctypes_dirs` : un `load_dir` récursif, puis UN `validate_links()` quand tout est là.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from xcore_doctype.grants import GrantCatalog
from xcore_doctype.registry import DocTypeRegistry
from xcore_doctype.schema import RESERVED_GRANT_NAMESPACES

ERP_DIR = Path(__file__).resolve().parents[2] / "doctypes"

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
